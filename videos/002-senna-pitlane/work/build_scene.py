"""Build the whole Senna pit-lane video scene -> build/donington/scene.blend
    blender -b -P work/build_scene.py -- <video_dir> [--sheet out_dir]
One render camera keyed per frame (cuts = guarded jumps, motion blur never smears across a cut), cars driven by a wet
lap simulation on the real OSM/LIDAR track, race clock remapped per shot (narration timing), map overlay in 3D."""
import bpy, sys, os, math, json, time, random, zlib
import numpy as np
from mathutils import Vector, Matrix, Quaternion, Euler, noise

ARGS = sys.argv[sys.argv.index("--") + 1:]
VD = os.path.abspath(ARGS[0]); SHEET = ARGS[ARGS.index("--sheet") + 1] if "--sheet" in ARGS else None
SKILL = os.path.abspath(os.path.join(VD, "..", "..", ".claude", "skills", "shorts3d-studio"))
sys.path.insert(0, os.path.join(VD, "work")); sys.path.insert(0, SKILL)
import world, racesim
from engine import vehicle, core

FPS = 30
TL = json.load(open(os.path.join(VD, "build", "timeline.json")))
SH = {s["id"]: (s["f0"], s["f1"]) for s in TL["shots"]}
NF = SH["S15"][1] + 1                       # 1730 frames, 0..1729
T0 = time.time()
log = lambda *a: print(f"[scene {time.time() - T0:6.1f}s]", *a, flush=True)

bpy.ops.wm.read_factory_settings(use_empty=True)
sc = bpy.context.scene; sc.render.fps = FPS; sc.frame_start = 0; sc.frame_end = NF - 1

# ------------------------------------------------------------------ world
W = world.World(VD); W.build_roads(); world.build_terrain(W); world.build_kerbs(W)
world.build_barriers(W); world.build_fences(W); world.build_buildings(W); world.build_pit_garages(W); print("pit floor terrain verts lowered", world.flatten_pits(W))
world.build_grandstand(W); world.build_pitwall_fence(W); world.build_start_gantry(W); world.build_marshal_posts(W)
world.build_tv_tower(W, (36.0, -96.0), float(W.h(36.0, -96.0)) + 6.5); world.build_paddock_trucks(W)
world.build_trees(W); world.build_boards(W)
world.setup_world(sc, strength=1.15); MIST = world.setup_mist(sc)
log("world built")


def H(x, y):
    return float(W.h(x, y))


# ------------------------------------------------------------------ routes + wet lap simulation
R = W.roads


def chain(keys, start_xy=None):
    out = []
    for k in keys:
        P = R[k]["P"]
        if out:
            if np.linalg.norm(out[-1][-1, :2] - P[0, :2]) > np.linalg.norm(out[-1][-1, :2] - P[-1, :2]):
                P = P[::-1]
            P = P[1:]
        out.append(P)
    P = np.vstack(out)
    return P


def nearest_idx(P, xy):
    return int(np.argmin(np.linalg.norm(P[:, :2] - np.asarray(xy), axis=1)))


up = R["Melbourne Loop (up)"]["P"]; e = R["old_entry"]["P"]; pl = R["pit_lane"]["P"]
i_split = nearest_idx(up, e[0, :2])
pre = chain(["Melbourne Loop (down)", "Melbourne Hairpin"])
pit_route = np.vstack([pre, up[1:i_split + 1], e[1:], pl[1:], chain(["Redgate", "Hollywood", "Craner Curves"])[1:]])
trk_route = np.vstack([pre, up[1:], chain(["Goddards", "Wheatcroft Straight", "Redgate", "Hollywood", "Craner Curves"])[1:]])
pit_route = W.smooth_line(W.resample(pit_route, 1.0), 2.0); trk_route = W.smooth_line(W.resample(trk_route, 1.0), 2.0)
# pit lane speed cap (crews on the apron)
s_tmp = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(pit_route[:, :2], axis=0), axis=1))])
i_e0 = nearest_idx(pit_route, e[0, :2]); i_pl0 = nearest_idx(pit_route, pl[0, :2]); i_pl1 = nearest_idx(pit_route, pl[-1, :2])
cap = np.full(len(pit_route), 1e9); cap[i_pl0:i_pl1] = 56.0
SEN = racesim.Route(pit_route, cap).profile()
RIV = racesim.Route(trk_route).profile(a_lat=1.5 * 9.81, a_brake=2.0 * 9.81)
s_split_sen = SEN.s[i_e0]; s_split_riv = RIV.s[nearest_idx(trk_route, e[0, :2])]
s_merge_sen = SEN.s[i_pl1]; s_merge_riv = RIV.s[nearest_idx(trk_route, pl[-1, :2])]
t_split_sen = SEN.t_at_s(s_split_sen)
RIV_LEAD = 24.0                                       # rival is 24 m ahead at the split moment
t_riv_at_split = RIV.t_at_s(s_split_riv + RIV_LEAD)
log(f"Senna split->merge {SEN.t_at_s(s_merge_sen) - SEN.t_at_s(s_split_sen):.2f}s  rival {RIV.t_at_s(s_merge_riv) - RIV.t_at_s(s_split_riv):.2f}s")
PATHS = {"pit": vehicle.Path([Vector(p) for p in pit_route]), "trk": vehicle.Path([Vector(p) for p in trk_route])}
SIMS = {"pit": SEN, "trk": RIV}


S_PL0, S_PL1 = float(SEN.s[i_pl0]), float(SEN.s[i_pl1])


def _sm(a, b, x):
    t = min(max((x - a) / (b - a), 0.0), 1.0); return t * t * (3 - 2 * t)


def sen_lat(s1):
    """Senna's lateral offset (+left): left side of the straight before the split, then the pit-lane FAST lane
    (3 m toward the pit wall = right of travel), well clear of the crews in the working lane."""
    before = 1.8 * float(np.clip((s_split_sen - s1) / 60.0, 0, 1))
    fast = -3.0 * _sm(S_PL0, S_PL0 + 45, s1) * (1 - _sm(S_PL1 - 70, S_PL1 - 5, s1))
    return before + fast


def car_s(sim, tau, t_ref):
    """arc length on its route at race clock tau (tau=0: Senna at the split). Extrapolates at the end speeds."""
    t = t_ref + tau
    if t < sim.t[0]:
        return sim.s[0] + sim.v[0] * (t - sim.t[0])
    if t > sim.t[-1]:
        return sim.s[-1] + sim.v[-1] * (t - sim.t[-1])
    return float(np.interp(t, sim.t, sim.s))


def sen_state(tau):
    return "pit", car_s(SEN, tau, t_split_sen)


def riv_state(tau):
    return "trk", car_s(RIV, tau, t_riv_at_split)


# ------------------------------------------------------------------ shot list: (name, f0, f1, tau(f) or None)
def lin(t0, f0, rate=1.0):
    return lambda f: t0 + (f - f0) / FPS * rate


S = SH
CUTS = [
    ("hook", 0, 95, lin(-4.4, 0)), ("split", 96, S["S01"][1], lin(-4.4, 0)),
    ("est_air", S["S02"][0], 266, None), ("est_crowd", 267, S["S02"][1], None),
    ("chase_entry", S["S03"][0], S["S03"][1], lin(0.35, S["S03"][0])),
    ("box_wait", S["S04"][0], S["S04"][1], lin(5.4, S["S04"][0])),
    ("box_pass", S["S05"][0], S["S05"][1], lin(7.6, S["S05"][0])),
    ("pit_side", S["S06"][0], S["S06"][1], lin(9.3, S["S06"][0])),
    ("rise", S["S07"][0], S["S07"][1], lin(12.6, S["S07"][0])),
    ("map", S["S08"][0], S["S11"][1], None),
    ("descend", S["S12"][0], 1250, lin(14.2, S["S12"][0])), ("track_side", 1251, S["S12"][1], lin(16.7, 1251)),
    ("hero_slow", S["S13"][0], 1436, lin(17.6, S["S13"][0], 0.38)), ("drone", 1437, S["S13"][1], lin(18.9, 1437)),
    ("limit", S["S14"][0], S["S14"][1], None),
    ("return", S["S15"][0], S["S15"][1], lambda f: -4.4 - (NF - f) / FPS),
]
CUT_OF = {}
for c in CUTS:
    for f in range(c[1], c[2] + 1): CUT_OF[f] = c
assert len(CUT_OF) == NF, (len(CUT_OF), NF)


def est_tau(f):                    # S02: both cars on the normal track route (an earlier lap), Goddards -> straight
    return (f - S["S02"][0]) / FPS


def states(f):
    """Per frame: Senna (route, s, lateral), rival (route, s, lateral) or None when parked out of shot."""
    c = CUT_OF[f]; name = c[0]
    if name in ("est_air", "est_crowd"):
        t = est_tau(f); sA = RIV.s[nearest_idx(trk_route, R["Goddards"]["P"][0, :2])] - 70.0
        return ("trk", sA - 32 + _adv(RIV, sA - 32, t), 1.2), ("trk", sA + _adv(RIV, sA, t), -1.2)
    if c[3] is None:
        return None, None
    tau = c[3](f)
    r1, s1 = sen_state(tau); r2, s2 = riv_state(tau)
    lat2 = -1.8 * float(np.clip((s_split_riv + 120 - s2) / 120.0, 0, 1))
    return (r1, s1, sen_lat(s1)), (r2, s2, lat2)


def _adv(sim, s0, t):
    """distance travelled from s0 in t seconds along the simulated speed profile."""
    t0 = sim.t_at_s(s0); return float(np.interp(t0 + t, sim.t, sim.s)) - s0


log("shots mapped")
json.dump({"cuts": [(c[0], c[1], c[2]) for c in CUTS], "split_merge": {
    "senna_s": [float(s_split_sen), float(s_merge_sen)], "rival_s": [float(s_split_riv), float(s_merge_riv)]}},
    open(os.path.join(VD, "build", "cuts.json"), "w"), indent=1)


# ------------------------------------------------------------------ cars: import, rig, realistic paint
def load_cars():
    with bpy.data.libraries.load(os.path.join(VD, "assets", "cars_raw.blend"), link=False) as (src, dst):
        dst.collections = [c for c in src.collections if c in ("MP48_RAW", "RIVAL_RAW")]
    cars = {}
    for cname, key in (("MP48_RAW", "senna"), ("RIVAL_RAW", "rival")):
        c = bpy.data.collections[cname]; sc.collection.children.link(c)
        car = vehicle.Car(key); car.meshes = [o for o in c.objects if o.type == "MESH"]
        vehicle._rig(car, c); cars[key] = car
    return cars


def _bsdf(m):
    return next((n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)


def _grime_and_wet(m, b, coat_mask=None, grime=0.85):
    """Rain-race realism on any car material: clear coat (paint only), road-spray grime low and at the back,
    streaks along the car, water film."""
    nt = m.node_tree; N, L = nt.nodes, nt.links
    tc = N.new("ShaderNodeTexCoord"); sep = N.new("ShaderNodeSeparateXYZ"); L.new(tc.outputs["Object"], sep.inputs[0])
    low = world.math_node(nt, "SUBTRACT", 1.0, world.math_node(nt, "MULTIPLY", sep.outputs[2], 2.4), clamp=True)    # below ~0.42 m
    rear = world.math_node(nt, "MULTIPLY", world.math_node(nt, "ADD", sep.outputs[1], 0.6), 0.28, clamp=True)       # +Y = back
    mp = N.new("ShaderNodeMapping"); mp.inputs["Scale"].default_value = (3.0, 0.35, 4.0); L.new(tc.outputs["Object"], mp.inputs[0])
    st = N.new("ShaderNodeTexNoise"); st.inputs["Scale"].default_value = 6.0; st.inputs["Detail"].default_value = 6; L.new(mp.outputs[0], st.inputs[0])
    streak = N.new("ShaderNodeMapRange"); streak.inputs["From Min"].default_value = 0.35; streak.inputs["From Max"].default_value = 0.75
    L.new(st.outputs["Fac"], streak.inputs["Value"])
    g = world.math_node(nt, "MULTIPLY", world.math_node(nt, "ADD", world.math_node(nt, "MULTIPLY", low, 0.8), world.math_node(nt, "MULTIPLY", rear, 0.35)), streak.outputs[0])
    g = world.math_node(nt, "MULTIPLY", g, grime, clamp=True)
    base_in = b.inputs["Base Color"]
    src = base_in.links[0].from_socket if base_in.links else None
    dirt = (0.045, 0.04, 0.035, 1)
    mixc = N.new("ShaderNodeMix"); mixc.data_type = "RGBA"; L.new(g, mixc.inputs[0])
    if src: L.new(src, mixc.inputs[6])
    else: mixc.inputs[6].default_value = tuple(base_in.default_value)
    mixc.inputs[7].default_value = dirt; L.new(mixc.outputs[2], base_in)
    r_in = b.inputs["Roughness"]; rsrc = r_in.links[0].from_socket if r_in.links else None
    mr = N.new("ShaderNodeMix"); mr.data_type = "FLOAT"; L.new(g, mr.inputs[0])
    if rsrc: L.new(rsrc, mr.inputs[2])
    else: mr.inputs[2].default_value = r_in.default_value
    mr.inputs[3].default_value = 0.62; L.new(mr.outputs[0], r_in)
    if "Coat Weight" in b.inputs:
        cw = world.math_node(nt, "MULTIPLY", world.math_node(nt, "SUBTRACT", 1.0, world.math_node(nt, "MULTIPLY", g, 0.7)), 1.0)
        if coat_mask is not None:
            cw = world.math_node(nt, "MULTIPLY", cw, coat_mask)
        L.new(cw, b.inputs["Coat Weight"]); b.inputs["Coat Roughness"].default_value = 0.035
        b.inputs["Coat IOR"].default_value = 1.52
    # water film: fine voronoi beads -> glints
    vb = N.new("ShaderNodeTexVoronoi"); vb.inputs["Scale"].default_value = 140.0; L.new(tc.outputs["Object"], vb.inputs["Vector"])
    bump = N.new("ShaderNodeBump"); bump.inputs["Strength"].default_value = 0.05; L.new(vb.outputs["Distance"], bump.inputs["Height"])
    n_in = b.inputs["Normal"]
    if n_in.links:
        L.new(n_in.links[0].from_socket, bump.inputs["Normal"])
    if "Coat Normal" in b.inputs:
        L.new(bump.outputs[0], b.inputs["Coat Normal"])


def car_materials(cars):
    mask = bpy.data.images.load(os.path.join(VD, "assets", "mp48_paintmask.png")); mask.colorspace_settings.name = "Non-Color"
    done = set()
    for o in cars["senna"].meshes:
        for m in o.data.materials:
            if not m or m.name in done: continue
            done.add(m.name); b = _bsdf(m)
            if b is None: continue
            if m.name == "RTcar":
                nt = m.node_tree; tex = next(n for n in nt.nodes if n.type == "TEX_IMAGE" and n.outputs[0].links and
                                             any(l.to_socket.name == "Base Color" for l in n.outputs[0].links))
                mk = nt.nodes.new("ShaderNodeTexImage"); mk.image = mask
                if tex.inputs[0].links: nt.links.new(tex.inputs[0].links[0].from_socket, mk.inputs[0])
                _grime_and_wet(m, b, coat_mask=mk.outputs[0])
                b.inputs["Specular IOR Level"].default_value = 0.55
            elif "Helmet" in m.name:
                b.inputs["Coat Weight"].default_value = 1.0; b.inputs["Coat Roughness"].default_value = 0.03
            elif "Body" in m.name:
                b.inputs["Roughness"].default_value = 0.78; b.inputs["Sheen Weight"].default_value = 0.3
    for o in cars["rival"].meshes:
        for m in o.data.materials:
            if not m or m.name in done: continue
            done.add(m.name); b = _bsdf(m)
            if b is None: continue
            if m.name in ("RIV_paint", "RIV_accent"):
                nt = m.node_tree; N, L = nt.nodes, nt.links
                tc = N.new("ShaderNodeTexCoord"); sep = N.new("ShaderNodeSeparateXYZ"); L.new(tc.outputs["Object"], sep.inputs[0])
                lowz = world.math_node(nt, "LESS_THAN", sep.outputs[2], 0.43)
                stripe = world.math_node(nt, "MULTIPLY", world.math_node(nt, "GREATER_THAN", sep.outputs[2], 0.43),
                                         world.math_node(nt, "LESS_THAN", sep.outputs[2], 0.47))
                nose = world.math_node(nt, "LESS_THAN", sep.outputs[1], -1.65)
                c = world.mix_rgb(nt, lowz, (0.82, 0.83, 0.84, 1), (0.015, 0.03, 0.11, 1))
                c = world.mix_rgb(nt, stripe, c, (0.85, 0.62, 0.04, 1))
                c = world.mix_rgb(nt, nose, c, (0.85, 0.62, 0.04, 1))
                L.new(c, b.inputs["Base Color"]); b.inputs["Metallic"].default_value = 0.0; b.inputs["Roughness"].default_value = 0.32
                _grime_and_wet(m, b)
            elif m.name in ("Tire_Tread",):
                b.inputs["Roughness"].default_value = 0.55
    # rear rain lights (red LED box, lit in the wet)
    for key, y, z in (("senna", 2.18, 0.28), ("rival", 2.12, 0.3)):
        car = cars[key]
        me = bpy.data.meshes.new(f"{key}_rainlight"); import bmesh
        bm = bmesh.new(); bmesh.ops.create_cube(bm, size=1.0); bm.to_mesh(me); bm.free()
        lo = bpy.data.objects.new(f"{key}_rainlight", me); car.root.users_collection[0].objects.link(lo)
        lo.parent = car.body; lo.scale = (0.09, 0.02, 0.06); lo.location = (0, y, z - 0.5)
        lm = world.flat_mat("rain_light", (0.5, 0.02, 0.02), emit=(1.0, 0.05, 0.03), emit_s=40.0); me.materials.append(lm)


CARS = load_cars(); car_materials(CARS)
log("cars rigged", {k: (round(c.wheelbase, 2), round(c.length, 2)) for k, c in CARS.items()})


# ------------------------------------------------------------------ per-frame keying with guarded cuts
def cut_bounds():
    """frames f where a cut happens between f and f+1"""
    return [c[2] for c in CUTS[:-1]]


CUT_AT = set(cut_bounds())


S_STAND = None


def states_at(cut, fv):
    name = cut[0]
    if name == "est_crowd":
        global S_STAND
        if S_STAND is None:
            P_ = R["Wheatcroft Straight"]["P"]; mid = P_[min(len(P_) - 1, 236), :2]
            S_STAND = float(RIV.s[nearest_idx(trk_route, mid)])
        t_c = RIV.t_at_s(S_STAND); t = t_c + (fv - (cut[1] + cut[2]) / 2) / FPS
        s_r = float(np.interp(t + 0.35, RIV.t, RIV.s)); s_s = float(np.interp(t - 0.25, RIV.t, RIV.s))
        return ("trk", s_s, 1.4), ("trk", s_r, -1.4)
    if name in ("est_air",):
        t = est_tau(fv); sA = RIV.s[nearest_idx(trk_route, R["Goddards"]["P"][0, :2])] - 70.0
        return ("trk", sA - 32 + _adv(RIV, sA - 32, t), 1.2), ("trk", sA + _adv(RIV, sA, t), -1.2)
    if cut[3] is None:
        return None, None
    tau = cut[3](fv)
    r1, s1 = sen_state(tau); r2, s2 = riv_state(tau)
    lat2 = -1.8 * float(np.clip((s_split_riv + 120 - s2) / 120.0, 0, 1))
    return (r1, s1, sen_lat(s1)), (r2, s2, lat2)


def sample_frames():
    """(frame value, cut) pairs: every frame + guard samples just after / before each cut."""
    out = []
    for f in range(NF):
        c = CUT_OF[f]; out.append((float(f), c))
        if f in CUT_AT:
            nxt = CUT_OF[f + 1]; out.append((f + 0.35, c)); out.append((f + 0.65, nxt))
    return out


SAMPLES = sample_frames()


class KW:
    """Fast keyframe writer: collect (datapath, index) -> [(frame, value)] then write fcurves in one go (linear)."""
    def __init__(self): self.d = {}
    def put(self, ob, path, idx, f, v):
        self.d.setdefault((ob.name, path, idx), []).append((f, v))
    def write(self):
        for (name, path, idx), keys in self.d.items():
            ob = bpy.data.objects[name]
            if ob.animation_data is None: ob.animation_data_create()
            if ob.animation_data.action is None:
                ob.animation_data.action = bpy.data.actions.new(name + "_act")
            act = ob.animation_data.action
            fc = None
            try:
                fc = act.fcurve_ensure_for_datablock(ob, path, index=idx)
            except Exception:
                try: fc = act.fcurves.new(path, index=idx)
                except Exception: fc = act.fcurves.find(path, index=idx)
            fc.keyframe_points.add(len(keys))
            flat = np.array(keys, np.float64).ravel()
            fc.keyframe_points.foreach_set("co", flat)
            fc.keyframe_points.foreach_set("interpolation", [1] * len(keys))     # LINEAR
            fc.update()


KWR = KW()
HIDDEN = Matrix.Translation((0, 0, -200))


from mathutils.bvhtree import BVHTree
_dg = bpy.context.evaluated_depsgraph_get()
ROAD_BVH = [BVHTree.FromObject(o, _dg) for o in bpy.data.objects if o.name.startswith("road_") or o.name == "kerbs"]


def road_z(x, y, z_hint):
    best = None
    for b in ROAD_BVH:
        hit = b.ray_cast(Vector((x, y, z_hint + 3.0)), Vector((0, 0, -1)), 8.0)
        if hit[0] is not None and (best is None or hit[0].z > best):
            best = hit[0].z
    return best


def seat_on_road(M, car):
    """Rigid placement on the real asphalt: raycast the 4 tyre contact patches, fit the car frame to them."""
    pts = {}
    for k, w in car.wheels.items():
        c = w["center"]; p = M @ Vector((c.x, c.y, 0.0))
        z = road_z(p.x, p.y, p.z); pts[k] = Vector((p.x, p.y, z if z is not None else p.z))
    mf = (pts["FL"] + pts["FR"]) / 2; mr = (pts["RL"] + pts["RR"]) / 2
    ml = (pts["FL"] + pts["RL"]) / 2; mrt = (pts["FR"] + pts["RR"]) / 2
    fwd = (mf - mr).normalized(); left = (ml - mrt); left = (left - fwd * left.dot(fwd)).normalized()
    up = fwd.cross(left).normalized()
    if up.z < 0: up = -up
    left = up.cross(fwd).normalized()
    origin = (mf + mr) / 2
    R = Matrix((left, -fwd, up)).transposed().to_4x4(); R.translation = origin
    return R


def key_cars():
    for which, key in ((0, "senna"), (1, "rival")):
        car = CARS[key]; root, body = car.root, car.body
        root.rotation_mode = "QUATERNION"; body.rotation_mode = "XYZ"
        for w in car.wheels.values(): w["spin"].rotation_mode = "XYZ"; w["steer"].rotation_mode = "XYZ"
        prev_q = None
        for fv, cut in SAMPLES:
            st = states_at(cut, fv)[which]
            if st is None:
                M = HIDDEN; s = 0.0; v = 0.0; k = 0.0; acc = 0.0
            else:
                route, s, lat = st; sim = SIMS[route]; path = PATHS[route]
                M = seat_on_road(vehicle.frame_matrix(path, s, lat), car)
                v = sim.v_at_s(min(max(s, 0.0), sim.L)); dv = (sim.v_at_s(min(max(s + 2, 0), sim.L)) - sim.v_at_s(min(max(s - 2, 0), sim.L))) / 4.0
                acc = v * dv; k = path.curvature(s)
            q = M.to_quaternion()
            if prev_q is not None and prev_q.dot(q) < 0: q = -q
            prev_q = q
            loc = M.translation
            for i in range(3): KWR.put(root, "location", i, fv, loc[i])
            for i in range(4): KWR.put(root, "rotation_quaternion", i, fv, q[i])
            pitch = float(np.clip(-0.0009 * acc, -0.035, 0.035)); roll = float(np.clip(-0.0009 * v * v * k, -0.035, 0.035))
            KWR.put(body, "rotation_euler", 0, fv, pitch); KWR.put(body, "rotation_euler", 1, fv, roll)
            delta = math.atan(car.wheelbase * k) if st else 0.0
            for wk, w in car.wheels.items():
                KWR.put(w["spin"], "rotation_euler", 0, fv, s / w["r"])
                if wk[0] == "F": KWR.put(w["steer"], "rotation_euler", 2, fv, delta)


def car_matrix(which, cut, fv):
    st = states_at(cut, fv)[which]
    if st is None: return None
    route, s, lat = st
    return seat_on_road(vehicle.frame_matrix(PATHS[route], s, lat), CARS["senna" if which == 0 else "rival"])


key_cars(); log("cars keyed")


# ------------------------------------------------------------------ cameras (one camera, keyed per frame)
def V3(x, y, dz=0.0):
    return Vector((x, y, H(x, y) + dz))


def look_quat(loc, tgt, up=Vector((0, 0, 1))):
    d = (tgt - loc).normalized(); z = -d; x = up.cross(z)
    if x.length < 1e-6: x = Vector((1, 0, 0)).cross(z)
    x.normalize(); y = z.cross(x)
    return Matrix((x, y, z)).transposed().to_quaternion()


def ss(a, b, x):
    t = min(max((x - a) / (b - a), 0.0), 1.0); return t * t * (3 - 2 * t)


BAY = W.bays[21]; BC, BUX, BN, BZ = Vector((*BAY[0], 0)), Vector((*BAY[1], 0)), Vector((*BAY[2], 0)), BAY[3]
EAST = BUX if BUX.x > 0 else -BUX; WEST = -EAST
BOX = Vector((BC.x, BC.y, BZ))                     # garage opening centre, floor level
MAP_C = Vector((0.0, -15.0, 0.0))
reg = [H(x, y) for x in np.linspace(-300, 260, 30) for y in np.linspace(-150, 120, 20)]
Z_MAP = max(reg) + 18.0; MAP_ALT = 700.0
MAP_POSE = (Vector((MAP_C.x, MAP_C.y, Z_MAP + MAP_ALT)), Vector((MAP_C.x, MAP_C.y, Z_MAP)), 50.0, Vector((1, 0, 0)))
def car_pt(which, cut, fv, off=(0, 0, 0)):
    M = car_matrix(which, cut, fv)
    if M is None: return None
    return M @ Vector(off)


def car_xy_fwd(which, cut, fv):
    M = car_matrix(which, cut, fv)
    if M is None: return None, None
    return M.translation.copy(), -Vector(M.col[1][:3]).normalized()


def avg_pt(fn, fv, half=0.35, n=7, shift=0.0):
    """centred moving average over +-half seconds: a camera operator / drone that is smooth but never ahead of time"""
    acc = Vector((0, 0, 0)); k = 0
    for i in range(n):
        f = fv + shift * FPS + (i / (n - 1) - 0.5) * 2 * half * FPS
        p = fn(f)
        if p is not None: acc += p; k += 1
    return acc / max(k, 1)


def shake(fv, amp_pos, amp_rot, freq, seed):
    """handheld / vehicle micro motion: smooth noise (cm, degrees)"""
    t = fv / FPS
    n3 = lambda o: noise.noise(Vector((t * freq + o, seed * 1.7, 0.0)))
    return Vector((n3(0.0), n3(11.3), n3(23.9))) * amp_pos, Vector((n3(41.0), n3(57.0), n3(73.0))) * math.radians(amp_rot)


def drone_follow(cut, fv):
    """high-angle drone behind the two cars (the hook + the loop back to it)"""
    def mid(f):
        a, _ = car_xy_fwd(0, cut, f); b, _ = car_xy_fwd(1, cut, f)
        return (a + b) / 2 if (a and b) else (a or b)
    def fwd(f):
        _, fa = car_xy_fwd(0, cut, f); _, fb = car_xy_fwd(1, cut, f)
        v = (fa + fb) if (fa and fb) else (fa or fb); v.z = 0; return v.normalized()
    m = avg_pt(mid, fv, 0.6); f_ = avg_pt(fwd, fv, 0.9).normalized()
    L = m - f_ * 46.0 + Vector((0, 0, 27.0)); T = m + f_ * 34.0
    L.z = max(L.z, H(L.x, L.y) + 20.0)
    return L, T, 30.0, Vector((0, 0, 1)), ("drone", 0.05, 0.12, 0.25)


def cam_pose(cut, fv):
    """-> (location, target or quaternion, lens, up, (noise kind, pos m, rot deg, Hz))"""
    name, f0, f1 = cut[0], cut[1], cut[2]; u = (fv - f0) / max(1, f1 - f0)
    if name in ("hook", "return"):
        return drone_follow(cut, fv)
    if name == "split":                      # TV tower outside the straight, operator pans with the cars
        tgt = avg_pt(lambda f: (lambda a, b: (a.lerp(b, 0.4) if (a and b) else (a or b)))(car_xy_fwd(0, cut, f)[0], car_xy_fwd(1, cut, f)[0]), fv, 0.3, shift=-0.08)
        return V3(36, -96, 6.5), tgt + Vector((0, 0, 0.6)), 45.0, Vector((0, 0, 1)), ("tripod", 0.0, 0.03, 0.8)
    if name == "est_air":                    # helicopter orbit, gentle bank
        a = math.radians(-58 + 14 * u); C = Vector((150, -20, 0))
        L = Vector((C.x + 260 * math.cos(a), C.y + 260 * math.sin(a), 0)); L.z = H(L.x, L.y) + 78 - 8 * u
        up = Vector((0, 0, 1)) + (V3(C.x, C.y, 0) - L).normalized().cross(Vector((0, 0, 1))) * 0.04
        return L, V3(C.x, C.y, 0), 30.0, up.normalized(), ("drone", 0.12, 0.08, 0.2)
    if name == "est_crowd":                  # from the back of the grandstand, over the spectators' heads, panning with the cars
        rows = W.stand_rows; top = rows[-1][0]; mid = top[len(top) // 2]
        frontp = rows[0][0][len(rows[0][0]) // 2]
        back = Vector((mid[0], mid[1], 0)) - (Vector((mid[0], mid[1], 0)) - Vector((frontp[0], frontp[1], 0))).normalized() * 0.45
        L = Vector((back.x, back.y, mid[2] + 1.8))
        tgt = avg_pt(lambda f: (lambda a, b: (a.lerp(b, 0.5) if (a and b) else (a or b)))(car_xy_fwd(0, cut, f)[0], car_xy_fwd(1, cut, f)[0]), fv, 0.25, shift=-0.05)
        return L, tgt + Vector((0, 0, 0.6)), 32.0, Vector((0, 0, 1)), ("hand", 0.006, 0.2, 0.8)
    if name == "chase_entry":                # 1993 roll-hoop onboard camera (looking over Senna's helmet)
        M = car_matrix(0, cut, fv)
        L = M @ Vector((0.0, 0.12, 1.16)); T = M @ Vector((0.0, -12.0, 0.55))
        return L, T, 18.0, Vector(M.col[2][:3]), ("onboard", 0.002, 0.25, 11.0)
    if name == "box_wait":                   # handheld in the lane, facing the McLaren crew waiting at the box (slow creep in)
        L = BOX + EAST * (-9.5 + 1.2 * ss(0.0, 1.0, u)) + BN * 11.8; L.z = W.pit_zf(L) + 1.45
        T = BOX + EAST * 3.5 + BN * 5.2; T.z = W.pit_zf(T) + 1.15
        return L, T, 38.0, Vector((0, 0, 1)), ("hand", 0.01, 0.3, 0.7)
    if name == "box_pass":                   # beside the crew: he comes down the hill and blasts past the box; operator pans with him
        L = BOX + EAST * -3.0 + BN * 1.4; L.z = W.pit_zf(L) + 1.5
        tgt = avg_pt(lambda f: car_xy_fwd(0, cut, f)[0], fv, 0.15, shift=-0.06)
        return L, tgt + Vector((0, 0, 0.5)), 32.0, Vector((0, 0, 1)), ("hand", 0.008, 0.25, 0.9)
    if name == "pit_side":                   # low tracking drone on the garage side, ahead of him: flat out up the pit lane
        # (the lane dips ~12 m to the McLaren box: fixed cameras along it only see crests and walls)
        m = avg_pt(lambda f: car_xy_fwd(0, cut, f)[0], fv, 0.4)
        L = m + EAST * -8.5 + BN * -6.0; L.z = m.z + 2.6
        return L, m + Vector((0, 0, 0.35)), 30.0, Vector((0, 0, 1)), ("drone", 0.04, 0.08, 0.35)
    if name == "rise":                       # drone climbs over the pit lane to the top-down map
        k = ss(0.0, 1.0, u)
        L0 = V3(-40, 52, 24.0); T0_ = V3(60, 15, 0.0)
        L1, T1, lens1, up1 = MAP_POSE
        L = L0.lerp(L1, k); L.z = L0.z + (L1.z - L0.z) * (k ** 2.0)
        q0 = look_quat(L0, T0_); q1 = look_quat(L1, T1, up1); q = q0.slerp(q1, ss(0.1, 0.95, u))
        return L, q, 28.0 + (lens1 - 28.0) * k, None, ("drone", 0.05 * (1 - k), 0.05 * (1 - k), 0.3)
    if name == "map":
        L, T, lens, up = MAP_POSE
        return L - Vector((0, 0, 30 * u)), T, lens, up, None
    if name == "descend":                    # drone drops back down to the pit exit
        k = ss(0.0, 1.0, u)
        L1 = V3(-232, 66, 6.0); T1 = V3(-300, 104, 1.0)
        L0, T0_, lens0, up0 = MAP_POSE; L0 = L0 - Vector((0, 0, 30))
        L = L0.lerp(L1, k); L.z = L1.z + (L0.z - L1.z) * ((1 - k) ** 2.0)
        q = look_quat(L0, T0_, up0).slerp(look_quat(L1, T1), ss(0.0, 0.85, u))
        return L, q, lens0 + (30.0 - lens0) * k, None, ("drone", 0.05 * k, 0.05 * k, 0.3)
    if name == "track_side":                 # TV camera on a scaffold outside Redgate (high enough to see over the crest)
        L = V3(-338, 118, 6.2)
        tgt = avg_pt(lambda f: car_xy_fwd(0, cut, f)[0], fv, 0.25, shift=-0.06)
        return L, tgt + Vector((0, 0, 0.5)), 70.0, Vector((0, 0, 1)), ("tripod", 0.0, 0.03, 0.9)
    if name == "hero_slow":                  # super slow-motion camera at track level, long lens, car coming at it
        st = states_at(cut, float(f1))[0]; path = PATHS[st[0]]; s_cam = st[1] + 38.0
        tt = path.tangent(s_cam); side = 1.0 if path.curvature(s_cam) < 0 else -1.0
        nrm = Vector((-tt.y, tt.x, 0)).normalized() * side
        L = path.point(s_cam) + nrm * 8.0; zr = road_z(L.x, L.y, L.z) or path.point(s_cam).z; L.z = max(zr, H(L.x, L.y)) + 0.95
        tgt = avg_pt(lambda f: car_xy_fwd(0, cut, f)[0], fv, 0.2)
        return L, tgt + Vector((0, 0, 0.45)), 150.0, Vector((0, 0, 1)), ("tripod", 0.0, 0.015, 0.6)
    if name == "drone":                      # helicopter following above and behind
        m = avg_pt(lambda f: car_xy_fwd(0, cut, f)[0], fv, 0.6)
        fw = avg_pt(lambda f: car_xy_fwd(0, cut, f)[1], fv, 0.9); fw.z = 0; fw.normalize()
        L = m - fw * (26 + 18 * u) + Vector((0, 0, 14 + 14 * u)); L.z = max(L.z, H(L.x, L.y) + 12)
        return L, m + fw * 25, 28.0, Vector((0, 0, 1)), ("drone", 0.08, 0.1, 0.25)
    if name == "limit":                      # handheld at the old pit entry, slow push toward the new limit sign
        P = R["old_entry"]["P"]; t, n = world._frame(P[:, :2])
        L = V3(P[11, 0] + n[11, 0] * 1.0, P[11, 1] + n[11, 1] * 1.0, 1.6) + Vector((t[11, 0], t[11, 1], 0)) * (3.0 * u)
        T = V3(P[24, 0] + n[24, 0] * 5.4, P[24, 1] + n[24, 1] * 5.4, 1.9)
        return L, T, 40.0, Vector((0, 0, 1)), ("hand", 0.01, 0.3, 0.6)
    raise KeyError(name)


def apply_noise(L, q, kind, fv):
    if not kind: return L, q
    k, ap, ar, hz = kind
    dp, dr = shake(fv, ap, ar, hz, zlib.crc32(k.encode()) % 97)     # (str hash() is salted per process)
    if k == "onboard":                                   # engine vibration: high frequency, tiny
        dp2, dr2 = shake(fv * 3.1, ap, ar * 0.6, 17.0, 5); dp += dp2; dr += dr2
    return L + dp, q @ Euler((dr.x, dr.y, dr.z * 0.5)).to_quaternion()


CAM = bpy.data.objects.new("CAM", bpy.data.cameras.new("CAM")); sc.collection.objects.link(CAM); sc.camera = CAM
CAM.rotation_mode = "QUATERNION"; CAM.data.clip_start = 0.08; CAM.data.clip_end = 6000.0; CAM.data.sensor_fit = "AUTO"
CAMLOG = {}


def key_camera():
    prev = None
    for fv, cut in SAMPLES:
        L, T, lens, up, kind = cam_pose(cut, fv)
        q = T if isinstance(T, Quaternion) else look_quat(L, T, up)
        L, q = apply_noise(L, q, kind, fv - NF if cut[0] == "return" else fv)   # loop: frame NF == frame 0
        if prev is not None and prev.dot(q) < 0: q = -q
        prev = q
        for i in range(3): KWR.put(CAM, "location", i, fv, L[i])
        for i in range(4): KWR.put(CAM, "rotation_quaternion", i, fv, q[i])
        if float(fv).is_integer():
            CAMLOG[int(fv)] = {"loc": list(L), "q": list(q), "lens": lens, "cut": cut[0]}
        cam_data_keys.append((fv, lens))


cam_data_keys = []
key_camera()
for fv, lens in cam_data_keys:
    CAM.data.lens = lens; CAM.data.keyframe_insert("lens", frame=fv)
for fc in (CAM.data.animation_data.action.fcurves if hasattr(CAM.data.animation_data.action, "fcurves") else []):
    for kp in fc.keyframe_points: kp.interpolation = "LINEAR"
log("camera keyed")


# ------------------------------------------------------------------ top-down 3D -> 2D map overlay
MAPCOL = world.coll("MAP")
FONT = bpy.data.fonts.load(os.path.join(SKILL, "library", "fonts", "Montserrat-ExtraBold.ttf"))
F_RISE_END = S["S07"][1]; F_DESC = S["S12"][0]
FADE_KEYS = [(F_RISE_END - 30, 0.0), (F_RISE_END - 2, 1.0), (F_DESC + 4, 1.0), (F_DESC + 34, 0.0)]
fade_nodes = []


def fade_mat(name, rgb, strength, alpha_max=1.0, image=None):
    m = bpy.data.materials.new(name); m.use_nodes = True; nt = m.node_tree; N, L = nt.nodes, nt.links; N.clear()
    out = N.new("ShaderNodeOutputMaterial"); em = N.new("ShaderNodeEmission"); tr = N.new("ShaderNodeBsdfTransparent")
    mix = N.new("ShaderNodeMixShader"); fade = N.new("ShaderNodeValue"); fade.outputs[0].default_value = 0.0
    em.inputs["Color"].default_value = (*rgb, 1); em.inputs["Strength"].default_value = strength
    a = world.math_node(nt, "MULTIPLY", fade.outputs[0], alpha_max)
    if image is not None:
        it = N.new("ShaderNodeTexImage"); it.image = image; it.extension = "CLIP"
        L.new(it.outputs["Color"], em.inputs["Color"]); a = world.math_node(nt, "MULTIPLY", a, it.outputs["Alpha"])
    L.new(a, mix.inputs[0]); L.new(tr.outputs[0], mix.inputs[1]); L.new(em.outputs[0], mix.inputs[2]); L.new(mix.outputs[0], out.inputs[0])
    try: m.surface_render_method = "BLENDED"
    except Exception: pass
    fade_nodes.append(fade); m["fade"] = 1
    return m, fade


def curve_obj(name, P, z, bevel, mat, lateral=0.0):
    cu = bpy.data.curves.new(name, "CURVE"); cu.dimensions = "3D"; cu.bevel_depth = bevel; cu.bevel_resolution = 1
    sp = cu.splines.new("POLY"); P = np.asarray(P)
    if lateral:
        t, n = world._frame(P[:, :2]); P = P.copy(); P[:, :2] += n * lateral
    sp.points.add(len(P) - 1)
    for i, p in enumerate(P): sp.points[i].co = (p[0], p[1], z, 1)
    o = bpy.data.objects.new(name, cu); MAPCOL.objects.link(o); cu.materials.append(mat)
    return o


def pop_keys(o, f_in, f_out=None, s=1.0):
    o.scale = (0, 0, 0); o.keyframe_insert("scale", frame=f_in - 1)
    o.scale = (1.15 * s,) * 3; o.keyframe_insert("scale", frame=f_in + 5)
    o.scale = (s,) * 3; o.keyframe_insert("scale", frame=f_in + 9)
    if f_out:
        o.keyframe_insert("scale", frame=f_out - 6); o.scale = (0, 0, 0); o.keyframe_insert("scale", frame=f_out)


def build_map():
    zm = Z_MAP
    slate, _ = fade_mat("map_slate", (0.010, 0.017, 0.026), 1.0, alpha_max=0.93)
    nt = slate.node_tree; N, L = nt.nodes, nt.links; em = next(n for n in N if n.type == "EMISSION")
    tc = N.new("ShaderNodeTexCoord"); sep = N.new("ShaderNodeSeparateXYZ"); L.new(tc.outputs["Object"], sep.inputs[0])
    gx = world.math_node(nt, "LESS_THAN", world.math_node(nt, "PINGPONG", world.math_node(nt, "DIVIDE", sep.outputs[0], 25.0), 0.5), 0.012)
    gy = world.math_node(nt, "LESS_THAN", world.math_node(nt, "PINGPONG", world.math_node(nt, "DIVIDE", sep.outputs[1], 25.0), 0.5), 0.012)
    L.new(world.mix_rgb(nt, world.math_node(nt, "MAXIMUM", gx, gy), (0.010, 0.017, 0.026, 1), (0.03, 0.06, 0.09, 1)), em.inputs["Color"])
    world.mesh_obj("map_slate", [(-700, -400, zm), (700, -400, zm), (700, 400, zm), (-700, 400, zm)], [(0, 1, 2, 3)], MAPCOL, mat=slate, smooth=False)
    band, _ = fade_mat("map_band", (0.09, 0.11, 0.13), 1.0); edge, _ = fade_mat("map_edge", (0.85, 0.9, 0.95), 2.5)
    pitb, _ = fade_mat("map_pit", (0.06, 0.075, 0.09), 1.0)
    for k, r in R.items():
        P = r["P"]; w = r["w"]
        if np.min(np.hypot(P[:, 0] - MAP_C.x, P[:, 1] - MAP_C.y)) > 420: continue
        t, n = world._frame(P[:, :2]); V, F = [], []
        for i in range(len(P)):
            for o in (-w / 2, w / 2): V.append((P[i, 0] + n[i, 0] * o, P[i, 1] + n[i, 1] * o, zm + 0.3))
        for i in range(len(P) - 1): a = 2 * i; F.append((a, a + 1, a + 3, a + 2))
        world.mesh_obj(f"map_band_{k}", V, F, MAPCOL, mat=band if r["kind"] == "raceway" else pitb, smooth=False)
        if r["kind"] == "raceway":
            for side in (-1, 1): curve_obj(f"map_edge_{k}_{side}", P, zm + 0.6, 0.55, edge, lateral=side * w / 2)
    # routes: split -> end line (x = -160 on the straight / pit lane)
    def upto(route, s0):
        i0 = nearest_idx(route, e[0, :2]); i1 = next(i for i in range(i0 + 50, len(route)) if route[i, 0] < -160)
        return route[i0:i1 + 1]
    red_P = upto(trk_route, 0); gold_P = upto(pit_route, 0)
    Lr = float(np.sum(np.linalg.norm(np.diff(red_P[:, :2], axis=0), axis=1))); Lg = float(np.sum(np.linalg.norm(np.diff(gold_P[:, :2], axis=0), axis=1)))
    red, _ = fade_mat("map_red", (1.0, 0.06, 0.03), 3.2); gold, _ = fade_mat("map_gold", (1.0, 0.62, 0.05), 3.6)
    rc = curve_obj("route_red", red_P, zm + 1.2, 1.7, red, lateral=-1.6); gc = curve_obj("route_gold", gold_P, zm + 1.4, 1.9, gold, lateral=0.0)
    f9a, f9b = S["S09"][0] + 6, S["S09"][1] - 20; f10a, f10b = S["S10"][0] + 4, S["S10"][1] - 12
    for cobj, fa, fb in ((rc, f9a, f9b), (gc, f10a, f10b)):
        cobj.data.bevel_factor_end = 0.0; cobj.data.keyframe_insert("bevel_factor_end", frame=fa)
        cobj.data.bevel_factor_end = 1.0; cobj.data.keyframe_insert("bevel_factor_end", frame=fb)
    # markers riding the tips (ghost = red ring, Senna = head sticker)
    import bmesh
    me = bpy.data.meshes.new("ghost"); bm = bmesh.new(); bmesh.ops.create_circle(bm, cap_ends=True, segments=24, radius=9.0); bm.to_mesh(me); bm.free()
    ghost = bpy.data.objects.new("ghost_marker", me); MAPCOL.objects.link(ghost); me.materials.append(red)
    img = bpy.data.images.load(os.path.join(VD, "assets", "senna_head.png"))
    hm, _ = fade_mat("map_head", (1, 1, 1), 1.15, image=img)
    w_h = 62.0; ar = img.size[1] / img.size[0]
    head = world.mesh_obj("senna_head", [(-w_h / 2, -w_h * ar / 2, 0), (w_h / 2, -w_h * ar / 2, 0), (w_h / 2, w_h * ar / 2, 0), (-w_h / 2, w_h * ar / 2, 0)],
                          [(0, 1, 2, 3)], MAPCOL, uvs=[(0, 0), (1, 0), (1, 1), (0, 1)], mat=hm, smooth=False)
    head.rotation_euler = (0, 0, -math.pi / 2)
    def tip(P, u):
        s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(P[:, :2], axis=0), axis=1))])
        return np.interp(u * s[-1], s, P[:, 0]), np.interp(u * s[-1], s, P[:, 1])
    for f in range(S["S08"][0], S["S12"][0] + 40):
        ur = ss(f9a, f9b, f) if f < S["S11"][0] else 1.0
        ug = ss(f10a, f10b, f) if f >= S["S10"][0] else 0.0
        x, y = tip(red_P, ur); ghost.location = (x, y, Z_MAP + 2.0); ghost.keyframe_insert("location", frame=f)
        x, y = tip(gold_P, ug); head.location = (x, y, Z_MAP + 3.0 + 1.5 * math.sin(f * 0.25)); head.keyframe_insert("location", frame=f)
    pop_keys(ghost, S["S09"][0] + 2, S["S10"][0] + 6, 1.0)   # the red ring hands over to the head (never sits under it)
    pop_keys(head, S["S10"][0], None, 1.0)
    # labels: drawn in post (work/post.py map_labels), each placed clear of every line, marker, caption and UI zone
    for fnode in fade_nodes:
        for fr, v in FADE_KEYS:
            fnode.outputs[0].default_value = v; fnode.outputs[0].keyframe_insert("default_value", frame=fr)
    log(f"map: red {Lr:.0f} m, gold {Lg:.0f} m, diff {Lr - Lg:.0f} m")
    return Lr, Lg


MAP_LEN = build_map()

# mist off for the high map camera (the blueprint must stay crisp): mist pass x k(f)
try:
    nt = MIST; mixn = next(n for n in nt.nodes if n.bl_idname in ("CompositorNodeMixRGB", "ShaderNodeMix"))
    lk = next(l for l in nt.links if l.to_node == mixn and l.to_socket == mixn.inputs[0])
    src = lk.from_socket; nt.links.remove(lk)
    try: mul = nt.nodes.new("CompositorNodeMath")
    except Exception: mul = nt.nodes.new("ShaderNodeMath")
    mul.operation = "MULTIPLY"; nt.links.new(src, mul.inputs[0]); nt.links.new(mul.outputs[0], mixn.inputs[0])
    for fr, v in [(0, 1.0), (S["S07"][0] + 20, 1.0), (F_RISE_END - 10, 0.0), (F_DESC + 10, 0.0), (F_DESC + 50, 1.0)]:
        mul.inputs[1].default_value = v; mul.inputs[1].keyframe_insert("default_value", frame=fr)
except Exception as ex:
    log("mist keys skipped", ex)
log("map built")


# ------------------------------------------------------------------ McLaren pit box: crew (Rocketbox actors) + equipment
def recolour_overalls(img):
    """Rocketbox clothes -> red team overalls, skin/hair untouched, shoes stay dark."""
    px = np.array(img.pixels[:], np.float32).reshape(-1, 4)
    r, g, b = px[:, 0], px[:, 1], px[:, 2]; mx = px[:, :3].max(1); mn = px[:, :3].min(1)
    lum = 0.3 * r + 0.59 * g + 0.11 * b
    skin = (r > g) & (g > b) & ((r - b) > 0.06) & ((r - b) < 0.45) & (mx > 0.18)
    dark = mx < 0.07
    cloth = ~skin & ~dark & (px[:, 3] > 0.1)
    k = np.clip(lum / max(np.percentile(lum[cloth], 70), 1e-3), 0.25, 1.4) if cloth.any() else 1.0
    px[cloth, 0] = 0.55 * k[cloth]; px[cloth, 1] = 0.035 * k[cloth]; px[cloth, 2] = 0.03 * k[cloth]
    img.pixels[:] = px.ravel(); img.update()


def build_crew():
    from engine import build as EB, rigkit, motion
    HOME = os.path.expanduser("~/.shorts3d")
    paths = {"chars": os.path.join(HOME, "library", "characters"), "mh_data": os.path.join(HOME, "mh", "data"),
             "mh_assets": os.path.join(HOME, "mh", "assets"), "cache": HOME, "clips": os.path.join(HOME, "clips")}
    car_dir = WEST
    # stop point the crew expected (front of the box, working lane - the car passes 3 m further out, in the fast lane)
    stop = BOX + BN * 5.4
    spots = [("crew02", stop + EAST * 2.4 + BN * -1.6, "tyre"), ("crew03", stop + WEST * 2.6 + BN * -1.6, "tyre"),
             ("crew04", stop + EAST * 2.4 + BN * 2.2, "tyre"), ("crew05", stop + WEST * 2.6 + BN * 2.2, "lolly"),
             ("crew06", stop + EAST * 4.6 - BN * 3.0, "chief")]
    perfs = []
    kw = core.KeyWriter()
    frames = list(range(S["S03"][0] - 10, S["S06"][1] + 2))
    for i, (key, p, role) in enumerate(spots):
        rig = EB.load_character(key, {"char": key}, paths, {})
        zf = W.pit_zf(p); floor = bpy.data.objects.new(f"PIT_FLOOR_{key}", None); sc.collection.objects.link(floor)
        floor.location = (0, 0, zf); rig.parent = floor
        local = Vector((p.x, p.y, 0.0))
        face_pt = (BOX + BN * 12.0 + EAST * 20.0)
        yaw = math.atan2(face_pt.x - p.x, -(face_pt.y - p.y))
        rig.location = local; rig.rotation_euler = (0, 0, yaw); bpy.context.view_layer.update()
        ch = rigkit.Character(key, rig); ch.setup_controls()
        pf = motion.Performer(ch, [p.x, p.y], yaw, FPS, seed=i + 1, scene_props={})
        pf.clips_dir = paths["clips"]
        pf.add_idle("idle", 0.75)
        # follow the car with eyes + head: look targets sampled along its pass (floor-local frame)
        for f in range(S["S04"][0] - 5, S["S05"][1] + 1, 6):
            cpos = car_pt(0, CUT_OF[f], float(f), (0, 0, 0.7))
            if cpos is None: continue
            tgt = [cpos.x, cpos.y, cpos.z - W.pit_zf(p)]
            pf.do("look_at", {"target": tgt, "at_s": f / FPS})
        if role in ("lolly", "chief"):
            pf.do("clip", {"name": "shrug", "at_s": (S["S05"][1] - 22) / FPS, "dur": 1.6})
        perfs.append(pf)
    for pf in perfs: pf.bake(kw, frames)
    kw.write()
    for pf in perfs: pf.solve_contacts(kw, frames)
    kw.write()
    # the performer keys its IK/look controls in floor-local space (like the rig): hang them under the pit floor too,
    # otherwise every hand/foot target sits a whole pit-floor height (~19 m) above its body (arms straight up)
    for key, _p, _r in spots:
        fl = bpy.data.objects[f"PIT_FLOOR_{key}"]
        for o in bpy.data.collections[f"CTRL_{key}"].objects:
            o.parent = fl; o.matrix_parent_inverse.identity()
    # red team overalls: recolour a private copy of each crew body texture and SAVE it (pixels edited in memory
    # are lost when the .blend reloads a file-based image)
    tex_dir = os.path.join(VD, "build", "donington", "tex"); os.makedirs(tex_dir, exist_ok=True)
    for key, _p, _r in spots:
        rig = bpy.data.objects.get(f"{key}_rig")
        for o in (rig.children if rig else []):
            if o.type != "MESH": continue
            for i, m in enumerate(o.data.materials):
                if not (m and m.node_tree): continue
                nodes = [n for n in m.node_tree.nodes if n.type == "TEX_IMAGE" and n.image and "body_color" in os.path.basename(n.image.filepath)]
                if not nodes: continue
                if m.users > 1: m = m.copy(); o.data.materials[i] = m
                for n in [n for n in m.node_tree.nodes if n.type == "TEX_IMAGE" and n.image and "body_color" in os.path.basename(n.image.filepath)]:
                    im = n.image.copy(); recolour_overalls(im)
                    fp = os.path.join(tex_dir, f"{key}_overalls.png")
                    im.filepath_raw = fp; im.file_format = "PNG"; im.save(); im.filepath = fp; im.name = f"{key}_overalls"
                    n.image = im
    return spots


def _ground_obj(o, z_floor):
    bpy.context.view_layer.update()
    mn = min((o.matrix_world @ Vector(c)).z for c in o.bound_box)
    o.location.z += z_floor - mn + 0.002


def build_pit_props(spots):
    import bmesh
    Cp = world.coll("PIT_PROPS")
    rub = world.flat_mat("wet_tyre", (0.014, 0.014, 0.015), rough=0.38)
    blanket = world.flat_mat("tyre_blanket", (0.42, 0.03, 0.03), rough=0.85)
    strap = world.flat_mat("blanket_strap", (0.05, 0.05, 0.05), rough=0.6)
    rim = world.flat_mat("rim_magnesium", (0.5, 0.5, 0.52), rough=0.3, metal=1.0)
    def wheel_mesh():
        me = bpy.data.meshes.new("wet_wheel"); bm = bmesh.new()
        bmesh.ops.create_cone(bm, cap_ends=True, segments=28, radius1=0.33, radius2=0.33, depth=0.36)      # tyre
        bmesh.ops.create_cone(bm, cap_ends=True, segments=20, radius1=0.21, radius2=0.21, depth=0.362)     # rim face
        bm.to_mesh(me); bm.free(); me.materials.append(rub); me.materials.append(rim)
        n_t = 28 + 2; me.polygons.foreach_set("material_index", [0 if i < n_t else 1 for i in range(len(me.polygons))])
        return me
    def blanket_mesh():
        me = bpy.data.meshes.new("blanket"); bm = bmesh.new()
        bmesh.ops.create_cone(bm, cap_ends=False, segments=28, radius1=0.345, radius2=0.345, depth=0.3)
        bm.to_mesh(me); bm.free(); me.materials.append(blanket); return me
    wm, bmh = wheel_mesh(), blanket_mesh()
    for k, (key, p, role) in enumerate(spots):
        if role != "tyre": continue
        base = p + EAST * 0.75
        t = bpy.data.objects.new(f"wheel_{k}", wm); Cp.objects.link(t)
        t.location = (base.x, base.y, 0); t.rotation_euler = (math.pi / 2, 0, math.atan2(BN.y, BN.x) + 0.25)
        _ground_obj(t, W.pit_zf(base))
        bl = bpy.data.objects.new(f"blanket_{k}", bmh); Cp.objects.link(bl); bl.location = t.location; bl.rotation_euler = t.rotation_euler
    # wheel guns (the uploaded model): apply its parent transforms, scale to 0.34 m, lie it on the floor
    try:
        before = set(bpy.data.objects)
        bpy.ops.import_scene.gltf(filepath="/root/.claude/uploads/9d2567ac-2881-58a8-b77d-52de40e0710d/3333a52b-impact_wrench.glb")
        new = [o for o in bpy.data.objects if o not in before]
        bpy.context.view_layer.update()
        meshes = [o for o in new if o.type == "MESH"]
        for o in meshes:
            mw = o.matrix_world.copy(); o.parent = None; o.data.transform(mw); o.matrix_world = Matrix.Identity(4)
            for c in list(o.users_collection): c.objects.unlink(o)
            Cp.objects.link(o)
        for o in new:
            if o.type != "MESH": bpy.data.objects.remove(o, do_unlink=True)
        if meshes:
            import bmesh
            g = meshes[0]; bm = bmesh.new(); bm.from_mesh(g.data)
            for o in meshes[1:]:                       # merge the other parts into one mesh (data level, no operators)
                tmp = bmesh.new(); tmp.from_mesh(o.data)
                off = len(g.data.materials)
                for m in o.data.materials: g.data.materials.append(m)
                for fc in tmp.faces: fc.material_index += off
                me_tmp = bpy.data.meshes.new("tmp"); tmp.to_mesh(me_tmp); tmp.free()
                bm.from_mesh(me_tmp); bpy.data.meshes.remove(me_tmp)
                bpy.data.objects.remove(o, do_unlink=True)
            bm.to_mesh(g.data); bm.free()
            Pv = np.array([v.co[:] for v in g.data.vertices]); c = (Pv.min(0) + Pv.max(0)) / 2; size = (Pv.max(0) - Pv.min(0)).max()
            g.data.transform(Matrix.Scale(0.34 / size, 4) @ Matrix.Translation(Vector(-c)))
            for i, (key, p, role) in enumerate([sp for sp in spots if sp[2] == "tyre"][:2]):
                gg = g if i == 0 else g.copy()
                if i: Cp.objects.link(gg)
                pos = p + EAST * 0.2 + BN * 0.7; gg.location = (pos.x, pos.y, 0); gg.rotation_euler = (math.pi / 2, 0, 0.6 + i)
                _ground_obj(gg, W.pit_zf(pos))
    except Exception as ex:
        log("wheel gun skipped", ex)
    # lollipop (aluminium pole + round BRAKES board) leaning on the garage pillar beside the lollipop man
    pole = bpy.data.meshes.new("lolly_pole"); bm = bmesh.new(); bmesh.ops.create_cone(bm, cap_ends=True, segments=10, radius1=0.016, radius2=0.016, depth=1.7)
    bm.to_mesh(pole); bm.free(); pole.materials.append(world.flat_mat("alu", (0.7, 0.7, 0.72), metal=1.0, rough=0.25))
    disc = bpy.data.meshes.new("lolly_disc"); bm = bmesh.new(); bmesh.ops.create_cone(bm, cap_ends=True, segments=32, radius1=0.25, radius2=0.25, depth=0.012)
    bm.to_mesh(disc); bm.free(); disc.materials.append(world.flat_mat("lolly_disc", (0.85, 0.06, 0.04), rough=0.35))
    pil = BOX + WEST * 2.3 + BN * 0.32
    zf = W.pit_zf(pil); tilt = 0.17
    po = bpy.data.objects.new("lolly_pole", pole); Cp.objects.link(po)
    po.location = (pil.x, pil.y, zf + 0.85 * math.cos(tilt)); po.rotation_euler = (tilt * BN.y, -tilt * BN.x, 0)
    do = bpy.data.objects.new("lolly_disc", disc); Cp.objects.link(do); do.parent = po
    do.location = (0, 0, 0.85 + 0.2); do.rotation_euler = (math.pi / 2, 0, math.atan2(BN.y, BN.x) + math.pi / 2)


def garage_lights():
    """fluorescent ceiling light in the McLaren box + a softer one in the neighbouring boxes (lit garages in the rain)"""
    for k, (c, ux, n_out, z0) in enumerate(W.bays):
        if abs(k - 21) > 3: continue
        L_ = bpy.data.lights.new(f"garage_light_{k}", "AREA"); L_.shape = "RECTANGLE"; L_.size = 3.2; L_.size_y = 6.0
        L_.energy = 900.0 if k == 21 else 350.0; L_.color = (1.0, 0.97, 0.9)
        o = bpy.data.objects.new(f"garage_light_{k}", L_); sc.collection.objects.link(o)
        p = Vector((c[0], c[1], 0)) - Vector((n_out[0], n_out[1], 0)) * 4.5; o.location = (p.x, p.y, z0 + 3.15)


garage_lights()
try:
    SPOTS = build_crew(); build_pit_props(SPOTS); log("crew + pit props")
except Exception as ex:
    import traceback; traceback.print_exc(); log("CREW FAILED", ex); SPOTS = []


# ------------------------------------------------------------------ rain (stateless, world-anchored around the camera) + spray
def build_rain(n=26000, box=(46.0, 46.0, 26.0), speed=8.5, wind=(1.4, 0.6)):
    import bmesh
    Cr = world.coll("RAIN")
    rnd = np.random.default_rng(5)
    P = np.column_stack([rnd.uniform(0, box[0], n), rnd.uniform(0, box[1], n), rnd.uniform(0, 1, n)])
    po = world.mesh_obj("rain_pts", [tuple(p) for p in P], [], Cr, smooth=False)
    me = bpy.data.meshes.new("streak"); bm = bmesh.new()
    for ang in (0.0, math.pi / 2):
        c, s_ = math.cos(ang) * 0.0035, math.sin(ang) * 0.0035
        vs = [bm.verts.new((-c, -s_, -0.33)), bm.verts.new((c, s_, -0.33)), bm.verts.new((c, s_, 0.33)), bm.verts.new((-c, -s_, 0.33))]
        bm.faces.new(vs)
    bm.to_mesh(me); bm.free()
    m = bpy.data.materials.new("rain_streak"); m.use_nodes = True; nt = m.node_tree; N, L = nt.nodes, nt.links; N.clear()
    out = N.new("ShaderNodeOutputMaterial"); em = N.new("ShaderNodeEmission"); em.inputs["Strength"].default_value = 0.55
    em.inputs["Color"].default_value = (0.8, 0.85, 0.9, 1); tr = N.new("ShaderNodeBsdfTransparent"); mx = N.new("ShaderNodeMixShader")
    mx.inputs[0].default_value = 0.28; L.new(tr.outputs[0], mx.inputs[1]); L.new(em.outputs[0], mx.inputs[2]); L.new(mx.outputs[0], out.inputs[0])
    me.materials.append(m)
    streak = bpy.data.objects.new("rain_streak", me); Cr.objects.link(streak); streak.hide_render = True; streak.hide_viewport = True
    ng = bpy.data.node_groups.new("rain_gn", "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    G, GL = ng.nodes, ng.links
    gi = G.new("NodeGroupInput"); go = G.new("NodeGroupOutput"); m2p = G.new("GeometryNodeMeshToPoints"); GL.new(gi.outputs[0], m2p.inputs[0])
    pos = G.new("GeometryNodeInputPosition"); sep = G.new("ShaderNodeSeparateXYZ"); GL.new(pos.outputs[0], sep.inputs[0])
    cam = G.new("GeometryNodeObjectInfo"); cam.inputs[0].default_value = CAM; cam.transform_space = "ORIGINAL"
    csep = G.new("ShaderNodeSeparateXYZ"); GL.new(cam.outputs["Location"], csep.inputs[0])
    t = G.new("GeometryNodeInputSceneTime")
    def m_(op, a, b=None):
        n_ = G.new("ShaderNodeMath"); n_.operation = op
        for i_, v in enumerate((a, b)):
            if v is None: continue
            if isinstance(v, (int, float)): n_.inputs[i_].default_value = v
            else: GL.new(v, n_.inputs[i_])
        return n_.outputs[0]
    fall = m_("MULTIPLY", m_("FRACT", m_("ADD", m_("MULTIPLY", t.outputs["Seconds"], speed / box[2]), sep.outputs[2])), box[2])
    wx = m_("MULTIPLY", fall, wind[0] / speed); wy = m_("MULTIPLY", fall, wind[1] / speed)
    def wrap(base, c, size, drift):
        rel = m_("SUBTRACT", m_("ADD", base, drift), c)
        return m_("ADD", c, m_("SUBTRACT", m_("FLOORED_MODULO", rel, size), size / 2))
    X = wrap(sep.outputs[0], csep.outputs[0], box[0], wx); Y = wrap(sep.outputs[1], csep.outputs[1], box[1], wy)
    Z = m_("SUBTRACT", m_("ADD", csep.outputs[2], 14.0), fall)
    comb = G.new("ShaderNodeCombineXYZ"); GL.new(X, comb.inputs[0]); GL.new(Y, comb.inputs[1]); GL.new(Z, comb.inputs[2])
    setp = G.new("GeometryNodeSetPosition"); GL.new(m2p.outputs[0], setp.inputs["Geometry"]); GL.new(comb.outputs[0], setp.inputs["Position"])
    # drop the drops right at the lens (they would be huge blurs)
    # (fields are evaluated on the geometry the Delete node receives: use its own, already moved, positions)
    pos2 = G.new("GeometryNodeInputPosition")
    dist = G.new("ShaderNodeVectorMath"); dist.operation = "DISTANCE"; GL.new(pos2.outputs[0], dist.inputs[0]); GL.new(cam.outputs["Location"], dist.inputs[1])
    nv = G.new("ShaderNodeValue"); nv.name = "near_clip"; nv.outputs[0].default_value = 1.6
    dr = nv.outputs[0].driver_add("default_value").driver; dr.type = "SCRIPTED"
    v_ = dr.variables.new(); v_.name = "lens"; v_.targets[0].id_type = "CAMERA"; v_.targets[0].id = CAM.data; v_.targets[0].data_path = "lens"
    dr.expression = "max(1.6, lens * 0.08)"                              # 21 mm -> 1.7 m, 70 mm -> 5.6 m, 160 mm -> 12.8 m
    near = m_("LESS_THAN", dist.outputs["Value"], nv.outputs[0])         # drops a real lens would blur away (driven by focal length)
    dele = G.new("GeometryNodeDeleteGeometry"); dele.domain = "POINT"; GL.new(setp.outputs[0], dele.inputs["Geometry"]); GL.new(near, dele.inputs["Selection"])
    oi = G.new("GeometryNodeObjectInfo"); oi.inputs[0].default_value = streak
    iop = G.new("GeometryNodeInstanceOnPoints"); GL.new(dele.outputs[0], iop.inputs["Points"]); GL.new(oi.outputs["Geometry"], iop.inputs["Instance"])
    rot = G.new("ShaderNodeCombineXYZ"); rot.inputs[0].default_value = -0.16; rot.inputs[1].default_value = 0.08
    rz = G.new("FunctionNodeRandomValue"); rz.data_type = "FLOAT"; rz.inputs["Min"].default_value = 0; rz.inputs["Max"].default_value = 3.14
    GL.new(rz.outputs["Value"], rot.inputs[2]); GL.new(rot.outputs[0], iop.inputs["Rotation"])
    GL.new(iop.outputs[0], go.inputs[0])
    md = po.modifiers.new("rain", "NODES"); md.node_group = ng
    try: po.cycles.use_motion_blur = False
    except Exception: pass
    for v in ("visible_shadow", "visible_diffuse", "visible_glossy", "visible_transmission", "visible_volume_scatter"):
        setattr(po, v, False)                       # streaks are seen by the camera only (no shadows / light sampling)
    m.cycles.emission_sampling = "NONE"
    return po


def build_spray(car, key):
    """Rear spray plume (volume mist) riding the car; density follows the car speed."""
    import bmesh
    me = bpy.data.meshes.new(f"{key}_spray"); bm = bmesh.new()
    prof = [(0.0, 1.0, 0.0, 0.9), (3.0, 1.9, 0.0, 1.9), (8.0, 3.0, 0.0, 3.0), (16.0, 4.3, 0.0, 3.9)]   # bounding hull (back y, half w, z0, z1)
    rings = []
    for y, hw, z0, z1 in prof:
        rings.append([bm.verts.new((x, y + 2.0, z)) for x, z in ((-hw, z0), (hw, z0), (hw, z1), (-hw, z1))])
    for a, b in zip(rings, rings[1:]):
        for i in range(4):
            bm.faces.new((a[i], a[(i + 1) % 4], b[(i + 1) % 4], b[i]))
    bm.faces.new(rings[0][::-1]); bm.faces.new(rings[-1])
    bm.to_mesh(me); bm.free()
    o = bpy.data.objects.new(f"{key}_spray", me); car.root.users_collection[0].objects.link(o); o.parent = car.root
    m = bpy.data.materials.new(f"{key}_spray_vol"); m.use_nodes = True; nt = m.node_tree; N, L = nt.nodes, nt.links; N.clear()
    out = N.new("ShaderNodeOutputMaterial"); vol = N.new("ShaderNodeVolumePrincipled"); vol.inputs["Color"].default_value = (0.86, 0.88, 0.9, 1)
    vol.inputs["Anisotropy"].default_value = 0.3
    tc = N.new("ShaderNodeTexCoord"); mp = N.new("ShaderNodeMapping"); L.new(tc.outputs["Object"], mp.inputs[0])
    drv = mp.inputs["Location"].driver_add("default_value", 1); drv.driver.expression = "-frame*1.1"
    nz = N.new("ShaderNodeTexNoise"); nz.inputs["Scale"].default_value = 0.7; nz.inputs["Detail"].default_value = 5; L.new(mp.outputs[0], nz.inputs[0])
    sep = N.new("ShaderNodeSeparateXYZ"); L.new(tc.outputs["Object"], sep.inputs[0])
    M_ = world.math_node
    yy = M_(nt, "DIVIDE", M_(nt, "SUBTRACT", sep.outputs[1], 2.0), 16.0, clamp=True)                  # 0 at the gearbox .. 1 far back
    hw = M_(nt, "ADD", 0.9, M_(nt, "MULTIPLY", yy, 3.2)); hh = M_(nt, "ADD", 0.45, M_(nt, "MULTIPLY", yy, 2.4))
    zc = M_(nt, "ADD", 0.35, M_(nt, "MULTIPLY", yy, 0.95))
    rx = M_(nt, "POWER", M_(nt, "DIVIDE", sep.outputs[0], hw), 2.0)
    rz = M_(nt, "POWER", M_(nt, "DIVIDE", M_(nt, "SUBTRACT", sep.outputs[2], zc), hh), 2.0)
    radial = M_(nt, "SUBTRACT", 1.0, M_(nt, "ADD", rx, rz), clamp=True)
    along = M_(nt, "MULTIPLY", M_(nt, "POWER", M_(nt, "SUBTRACT", 1.0, yy), 1.3), M_(nt, "MINIMUM", M_(nt, "MULTIPLY", yy, 14.0), 1.0))
    shape = M_(nt, "MULTIPLY", M_(nt, "POWER", radial, 1.6), along)
    d = M_(nt, "MULTIPLY", shape, M_(nt, "POWER", nz.outputs["Fac"], 1.8))
    amt = N.new("ShaderNodeValue"); amt.name = "spray_amt"
    L.new(M_(nt, "MULTIPLY", M_(nt, "MULTIPLY", d, 2.4), amt.outputs[0]), vol.inputs["Density"])
    L.new(vol.outputs[0], out.inputs["Volume"]); me.materials.append(m)
    return o, amt


SPRAY = {}
for which, key in ((0, "senna"), (1, "rival")):
    o, amt = build_spray(CARS[key], key); SPRAY[key] = amt
    for f in range(0, NF, 2):
        st = states_at(CUT_OF[f], float(f))[which]
        v = SIMS[st[0]].v_at_s(min(max(st[1], 0), SIMS[st[0]].L)) if st else 0.0
        amt.outputs[0].default_value = float(np.clip((v - 8.0) / 40.0, 0, 1)); amt.outputs[0].keyframe_insert("default_value", frame=f)
# ------------------------------------------------------------------ realistic audience (Rocketbox people, randomized simple gestures)
import crowd
QUALITY = "final" if "--final" in ARGS else "preview"
try:
    crowd_cam = []
    for f in range(S["S02"][0], S["S02"][1] + 1, 5):
        c = CUT_OF[f]
        if c[0] == "est_crowd":
            L = cam_pose(c, float(f))[0]; crowd_cam.append((L.x, L.y, 1.6))
    _ok0 = crowd.Exclusions.ok
    avoid = crowd_cam + [(x_, y_, r_ + 0.6) for x_, y_, r_ in W.structures]
    def _ok(self, x, y, road_clear=7.0, line_clear=0.8, tree_clear=1.8):
        if any((x - cx) ** 2 + (y - cy) ** 2 < r * r for cx, cy, r in avoid): return False
        return _ok0(self, x, y, road_clear, line_clear, tree_clear)
    crowd.Exclusions.ok = _ok
    crowd.Exclusions.avoid_fn = lambda self, x, y: not any((x - cx) ** 2 + (y - cy) ** 2 < r * r for cx, cy, r in avoid)
    AUD = crowd.build_audience(W, sc, FPS, list(range(0, NF)), [
        ("straight_w", "Wheatcroft Straight", -1, 21, 27, 0, 165), ("straight_e", "Wheatcroft Straight", -1, 21, 27, 310, 480),
        ("goddards", "Goddards", -1, 30, 37, 0, 95), ("melb_down", "Melbourne Loop (down)", 1, 15, 21, 0, 360),
        ("redgate", "Redgate", -1, 18, 24, 0, 190), ("hollywood", "Hollywood", -1, 16, 22, 0, 200)],
        quality=QUALITY, cheer_times=(9.5, 10.5, 41.0, 42.0), seed=7, spacing=0.95, density=0.42)
    log("audience built")
except Exception as ex:
    import traceback; traceback.print_exc(); log("AUDIENCE FAILED", ex)


def build_limit_sign():
    import bmesh
    Cs = world.coll("LIMIT_SIGN")
    P = R["old_entry"]["P"]; t, n = world._frame(P[:, :2]); i = 24
    base = Vector((P[i, 0] + n[i, 0] * 5.4, P[i, 1] + n[i, 1] * 5.4, 0.0)); base.z = H(base.x, base.y)
    face = -Vector((t[i, 0], t[i, 1], 0)).normalized()          # faces the arriving cars
    yaw = math.atan2(face.y, face.x) - math.pi / 2
    steel = bpy.data.materials.get("armco_steel")
    me = bpy.data.meshes.new("sign_post"); bm = bmesh.new(); bmesh.ops.create_cone(bm, cap_ends=True, segments=12, radius1=0.045, radius2=0.045, depth=2.6)
    bm.to_mesh(me); bm.free(); me.materials.append(steel)
    post = bpy.data.objects.new("sign_post", me); Cs.objects.link(post); post.location = base + Vector((0, 0, 1.25))
    def disc(name, r, z, mat, dy):
        m2 = bpy.data.meshes.new(name); bm = bmesh.new(); bmesh.ops.create_cone(bm, cap_ends=True, segments=48, radius1=r, radius2=r, depth=0.02)
        bm.to_mesh(m2); bm.free(); m2.materials.append(mat)
        o = bpy.data.objects.new(name, m2); Cs.objects.link(o)
        o.location = base + Vector((0, 0, z)) + face * dy; o.rotation_euler = (math.pi / 2, 0, yaw); return o
    disc("sign_ring", 0.42, 2.35, world.flat_mat("sign_red", (0.6, 0.02, 0.02), rough=0.35), 0.06)
    disc("sign_face", 0.33, 2.35, world.flat_mat("sign_white", (0.85, 0.85, 0.85), rough=0.35), 0.075)
    cu = bpy.data.curves.new("sign_txt", "FONT"); cu.body = "80"; cu.font = FONT; cu.size = 0.34; cu.align_x = "CENTER"; cu.align_y = "CENTER"
    tx = bpy.data.objects.new("sign_txt", cu); Cs.objects.link(tx); cu.materials.append(world.flat_mat("sign_black", (0.01, 0.01, 0.01), rough=0.5))
    tx.location = base + Vector((0, 0, 2.35)) + face * 0.09; tx.rotation_euler = (math.pi / 2, 0, yaw + math.pi)   # text front = +face (else mirrored)
    plate = bpy.data.meshes.new("sign_plate"); bm = bmesh.new(); bmesh.ops.create_cube(bm, size=1.0); bm.to_mesh(plate); bm.free()
    plate.materials.append(world.flat_mat("sign_blue", (0.02, 0.06, 0.25), rough=0.4))
    pl = bpy.data.objects.new("sign_plate", plate); Cs.objects.link(pl); pl.scale = (0.9, 0.02, 0.26)
    pl.location = base + Vector((0, 0, 1.78)) + face * 0.07; pl.rotation_euler = (0, 0, yaw)
    cu2 = bpy.data.curves.new("sign_txt2", "FONT"); cu2.body = "PIT LANE"; cu2.font = FONT; cu2.size = 0.17; cu2.align_x = "CENTER"; cu2.align_y = "CENTER"
    t2 = bpy.data.objects.new("sign_txt2", cu2); Cs.objects.link(t2); cu2.materials.append(world.flat_mat("sign_white2", (0.9, 0.9, 0.9), rough=0.4))
    t2.location = base + Vector((0, 0, 1.78)) + face * 0.095; t2.rotation_euler = (math.pi / 2, 0, yaw + math.pi)
    f0, f1 = S["S14"][0], S["S14"][1]
    for o in Cs.objects:                      # 1994: only in the speed-limit shot
        for fr, hid in ((0, True), (f0, False), (f1 + 1, True)):
            o.hide_render = hid; o.keyframe_insert("hide_render", frame=fr)


build_limit_sign()
RAIN = build_rain()
for fr, hid in ((0, False), (S["S07"][0] + 45, True), (S["S12"][0] + 30, False)):
    RAIN.hide_render = hid; RAIN.keyframe_insert("hide_render", frame=fr)
KWR.write(); log("keys written, rain + spray")

# ------------------------------------------------------------------ QA: physics, boundaries, clearances on EVERY frame
def qa_report():
    from mathutils.bvhtree import BVHTree
    rep = {"car_crew": [], "car_car": [], "car_barrier": [], "cam_ground": [], "cam_geometry": []}
    def rect(M, half=(1.0, 2.2)):
        return [M @ Vector((sx * half[0], sy * half[1], 0)) for sx in (-1, 1) for sy in (-1, 1)]
    def seg_dist(p, a, b):
        ab = b - a; t = max(0.0, min(1.0, (p - a).dot(ab) / max(ab.length_squared, 1e-9))); return (a + ab * t - p).length
    def rect_point(M, p, half=(1.0, 2.2)):
        q = M.inverted() @ Vector((p.x, p.y, M.translation.z))
        dx = max(abs(q.x) - half[0], 0.0); dy = max(abs(q.y) - half[1], 0.0); return math.hypot(dx, dy)
    crew_pts = [Vector((p.x, p.y, 0)) for _, p, _ in (SPOTS or [])]
    prop_pts = [o.matrix_world.translation.copy() for o in bpy.data.collections.get("PIT_PROPS").objects] if bpy.data.collections.get("PIT_PROPS") else []
    lines = [np.array(W.resample(w["pts"], 0.5)) for w in W.ways if w["kind"] in ("barrier=wall", "barrier=guard_rail", "barrier=tyres", "barrier=fence")]
    LP = np.vstack(lines) if lines else np.zeros((0, 2))
    dg = bpy.context.evaluated_depsgraph_get()
    static = [o for o in bpy.data.objects if o.type == "MESH" and o.name in ("terrain", "walls", "armco_rails", "armco_posts",
              "catch_fence_posts", "buildings", "pit_garages", "stand_terraces", "stand_steel", "stand_roof", "hoardings")]
    sb = [(o.name, BVHTree.FromObject(o, dg)) for o in static]
    terr = dict(sb)["terrain"]
    for f in range(NF):
        cut = CUT_OF[f]
        M0 = car_matrix(0, cut, float(f)); M1 = car_matrix(1, cut, float(f))
        if M0 is not None and S["S03"][0] <= f <= S["S06"][1]:
            for p in crew_pts + [Vector((q.x, q.y, 0)) for q in prop_pts]:
                d = rect_point(M0, p)
                if d < 1.0: rep["car_crew"].append((f, round(d, 2)))
        if M0 is not None and M1 is not None:
            d = min(rect_point(M0, c) for c in rect(M1))
            if d < 0.6: rep["car_car"].append((f, round(d, 2)))
        for M in (M0, M1):
            if M is None or not len(LP): continue
            for c in rect(M, (1.0, 2.25)):
                dmin = float(np.min(np.hypot(LP[:, 0] - c.x, LP[:, 1] - c.y)))
                if dmin < 0.25: rep["car_barrier"].append((f, round(dmin, 2))); break
        cl = CAMLOG.get(f)
        if cl and cl["cut"] not in ("map", "chase_entry"):
            L = Vector(cl["loc"]); hit = terr.ray_cast(L + Vector((0, 0, 500)), Vector((0, 0, -1)))
            if hit[0] is not None and L.z - hit[0].z < 0.5: rep["cam_ground"].append((f, round(L.z - hit[0].z, 2)))
            for nm, b in sb:
                nf = b.find_nearest(L, 0.3)
                if nf[0] is not None: rep["cam_geometry"].append((f, nm, round(nf[3], 2))); break
    summary = {k: len(v) for k, v in rep.items()}
    json.dump({"summary": summary, "details": {k: v[:60] for k, v in rep.items()}}, open(os.path.join(VD, "build", "qa.json"), "w"), indent=1)
    log("QA", summary)
    return rep


QA = qa_report()

# ------------------------------------------------------------------ render settings (Cycles; preview by default)
def render_settings(q="preview"):
    r = sc.render; cy = sc.cycles
    r.engine = "CYCLES"; cy.device = "CPU"
    r.resolution_x, r.resolution_y = (432, 768) if q == "preview" else (1080, 1920); r.resolution_percentage = 100
    cy.samples = 12 if q == "preview" else 160; cy.use_adaptive_sampling = True; cy.adaptive_threshold = 0.05 if q == "preview" else 0.02
    cy.use_denoising = True; cy.denoiser = "OPENIMAGEDENOISE"
    cy.max_bounces = 4; cy.diffuse_bounces = 1; cy.glossy_bounces = 2; cy.transmission_bounces = 2; cy.transparent_max_bounces = 8
    cy.volume_bounces = 0; cy.caustics_reflective = False; cy.caustics_refractive = False
    cy.volume_step_rate = 4.0 if q == "preview" else 2.0; cy.volume_max_steps = 64
    r.use_motion_blur = True; r.motion_blur_shutter = 0.5
    r.use_persistent_data = True; r.film_transparent = False
    try: cy.texture_limit_render = "1024" if q == "preview" else "OFF"
    except Exception: pass
    r.image_settings.file_format = "JPEG" if q == "preview" else "PNG"; r.image_settings.quality = 92
    sc.view_settings.view_transform = "AgX"


render_settings("preview")
os.makedirs(os.path.join(VD, "build", "donington"), exist_ok=True)
json.dump({"cam": CAMLOG, "box": list(BOX), "map": {"z": Z_MAP, "alt": MAP_ALT, "len": MAP_LEN, "labels": "post"}}, open(os.path.join(VD, "build", "camlog.json"), "w"))
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(VD, "build", "donington", "scene.blend"))
log("saved scene.blend")

if SHEET:
    os.makedirs(SHEET, exist_ok=True)
    sc.render.resolution_x, sc.render.resolution_y = 360, 640; sc.cycles.samples = 10
    picks = [("hook", 40), ("split", 150), ("est_air", 230), ("est_crowd", 320), ("chase_entry", 430), ("box_wait", 520),
             ("box_pass", 568), ("pit_side", 650), ("rise", 760), ("map", 900), ("map", 1060), ("map", 1150),
             ("descend", 1235), ("track_side", 1300), ("hero_slow", 1400), ("drone", 1480), ("limit", 1580), ("return", 1650)]
    want = os.environ.get("SHEET_PICKS")
    if want: picks = [p_ for p_ in picks if p_[0] in want.split(",")]
    if os.environ.get("SHEET_FRAMES"):          # e.g. "box_pass:560,box_pass:586"
        picks = [(a, int(b)) for a, b in (x.split(":") for x in os.environ["SHEET_FRAMES"].split(","))]
    for nm, f in picks:
        sc.frame_set(f); sc.render.filepath = os.path.join(SHEET, f"{f:05d}_{nm}.jpg")
        t = time.time(); bpy.ops.render.render(write_still=True); log("sheet", nm, f, round(time.time() - t, 1), "s")
