"""Build the whole Senna pit-lane video scene -> build/donington/scene.blend
    blender -b -P work/build_scene.py -- <video_dir> [--sheet out_dir]
One render camera keyed per frame (cuts = guarded jumps, motion blur never smears across a cut), cars driven by a wet
lap simulation on the real OSM/LIDAR track, race clock remapped per shot (narration timing), map overlay in 3D."""
import bpy, sys, os, math, json, time, random
import numpy as np
from mathutils import Vector, Matrix, Quaternion, Euler

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
world.build_barriers(W); world.build_fences(W); world.build_buildings(W); world.build_pit_garages(W)
world.build_trees(W)
world.build_crowd(W, [("straight_w", "Wheatcroft Straight", -1, 22, 33, 0, 165),
                      ("straight_e", "Wheatcroft Straight", -1, 22, 33, 305, 480),
                      ("goddards", "Goddards", -1, 30, 44, 0, 95),
                      ("melb_down", "Melbourne Loop (down)", 1, 15, 25, 0, 360),
                      ("redgate", "Redgate", -1, 18, 30, 0, 190),
                      ("hollywood", "Hollywood", -1, 16, 26, 0, 200)], density=0.42)
world.build_grandstand(W); world.build_boards(W)
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
    lat1 = 1.8 * float(np.clip((s_split_sen - s1) / 60.0, 0, 1))      # left side of the straight before the split
    lat2 = -1.8 * float(np.clip((s_split_riv + 120 - s2) / 120.0, 0, 1))
    return (r1, s1, lat1), (r2, s2, lat2)


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


def states_at(cut, fv):
    name = cut[0]
    if name in ("est_air", "est_crowd"):
        t = est_tau(fv); sA = RIV.s[nearest_idx(trk_route, R["Goddards"]["P"][0, :2])] - 70.0
        return ("trk", sA - 32 + _adv(RIV, sA - 32, t), 1.2), ("trk", sA + _adv(RIV, sA, t), -1.2)
    if cut[3] is None:
        return None, None
    tau = cut[3](fv)
    r1, s1 = sen_state(tau); r2, s2 = riv_state(tau)
    lat1 = 1.8 * float(np.clip((s_split_sen - s1) / 60.0, 0, 1))
    lat2 = -1.8 * float(np.clip((s_split_riv + 120 - s2) / 120.0, 0, 1))
    return (r1, s1, lat1), (r2, s2, lat2)


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
                M = vehicle.frame_matrix(path, s, lat)
                v = sim.v_at_s(min(max(s, 0.0), sim.L)); dv = (sim.v_at_s(min(max(s + 2, 0), sim.L)) - sim.v_at_s(min(max(s - 2, 0), sim.L))) / 4.0
                acc = v * dv; k = path.curvature(s)
            q = M.to_quaternion()
            if prev_q is not None and prev_q.dot(q) < 0: q = -q
            prev_q = q
            loc = M.translation
            for i in range(3): KWR.put(root, "location", i, fv, loc[i])
            for i in range(4): KWR.put(root, "rotation_quaternion", i, fv, q[i])
            pitch = float(np.clip(-0.0011 * acc, -0.05, 0.05)); roll = float(np.clip(-0.0011 * v * v * k, -0.05, 0.05))
            KWR.put(body, "rotation_euler", 0, fv, pitch); KWR.put(body, "rotation_euler", 1, fv, roll)
            delta = math.atan(car.wheelbase * k) if st else 0.0
            for wk, w in car.wheels.items():
                KWR.put(w["spin"], "rotation_euler", 0, fv, s / w["r"])
                if wk[0] == "F": KWR.put(w["steer"], "rotation_euler", 2, fv, delta)


def car_matrix(which, cut, fv):
    st = states_at(cut, fv)[which]
    if st is None: return None
    route, s, lat = st
    return vehicle.frame_matrix(PATHS[route], s, lat)


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
HOOK_L0 = V3(-112, -102, 36.0); HOOK_T0 = V3(92, -40, 2.0)


def hook_pose(u):
    d = (HOOK_T0 - HOOK_L0).normalized()
    return HOOK_L0 + d * (u * 0.085), HOOK_T0, 30.0, Vector((0, 0, 1))


def car_pt(which, cut, fv, off=(0, 0, 0)):
    M = car_matrix(which, cut, fv)
    if M is None: return None
    return M @ Vector(off)


def cam_pose(cut, fv):
    name, f0, f1 = cut[0], cut[1], cut[2]; u = (fv - f0) / max(1, f1 - f0)
    if name == "hook":
        return hook_pose(fv)
    if name == "return":
        L, T, lens, up = hook_pose(fv - NF)
        k = 1.0 - ss(f0, f0 + 70, fv)                               # rise from low behind the cars
        cp = car_pt(0, cut, fv, (0, 9.0, 2.2)) or L; ct = car_pt(0, cut, fv, (0, -20, 0.8)) or T
        return L.lerp(cp, k), T.lerp(ct, k), 30.0 - 8 * k, up
    if name == "split":
        s_pt = car_pt(0, cut, fv, (0, 0, 0.6)); r_pt = car_pt(1, cut, fv, (0, 0, 0.6))
        tgt = s_pt.lerp(r_pt, 0.35) if (s_pt and r_pt) else (s_pt or r_pt)
        return V3(36, -90, 5.5), tgt, 30.0, Vector((0, 0, 1))
    if name == "est_air":
        a = math.radians(-58 + 14 * u); C = Vector((150, -20, 0))
        L = Vector((C.x + 260 * math.cos(a), C.y + 260 * math.sin(a), 0)); L.z = H(L.x, L.y) + 78 - 8 * u
        return L, V3(C.x, C.y, 0), 30.0, Vector((0, 0, 1))
    if name == "est_crowd":
        P = R["Wheatcroft Straight"]["P"]; i = 150; t, n = world._frame(P[:, :2])
        base = P[i, :2] - n[i] * 27.5
        L = V3(base[0], base[1], 1.85); tgt = car_pt(0, cut, fv, (0, -6, 0.5)) or V3(P[i, 0], P[i, 1], 0.5)
        return L, tgt, 34.0, Vector((0, 0, 1))
    if name == "chase_entry":
        lag = 5.0
        L = car_pt(0, cut, fv - lag, (1.7, 7.8, 1.45)); T = car_pt(0, cut, fv, (0, -6, 0.5))
        return L, T, 27.0, Vector((0, 0, 1))
    if name == "box_wait":
        L = BOX - BN * 2.6 - EAST * 1.2 + Vector((0, 0, 1.45)); T = BOX + BN * 10.0 + EAST * 60 + Vector((0, 0, 1.2))
        return L, T, 26.0, Vector((0, 0, 1))
    if name == "box_pass":
        L = BOX + BN * 14.6 + WEST * 9.0 + Vector((0, 0, 0.75)); T = BOX + BN * 6.0 + EAST * 6.0 + Vector((0, 0, 1.2))
        return L, T, 22.0, Vector((0, 0, 1))
    if name == "pit_side":
        lag = 3.0
        L = car_pt(0, cut, fv - lag, (-7.5, 0, 1.9)); T = car_pt(0, cut, fv, (0, -3.0, 0.6))
        return L, T, 30.0, Vector((0, 0, 1))
    if name == "rise":
        k = ss(0.0, 1.0, u)
        L0 = car_pt(0, cut, f0, (-7.5, 0, 1.9)) or V3(-60, 40, 10); T0_ = car_pt(0, cut, f0, (0, -3, 0.6)) or V3(-100, 50, 0)
        L1, T1, lens1, up1 = MAP_POSE
        L = L0.lerp(L1, k); L.z = L0.z + (L1.z - L0.z) * (k ** 2.2)
        q0 = look_quat(L0, T0_); q1 = look_quat(L1, T1, up1); q = q0.slerp(q1, ss(0.15, 1.0, u))
        return L, q, 30.0 + (lens1 - 30.0) * k, None
    if name == "map":
        L, T, lens, up = MAP_POSE
        return L - Vector((0, 0, 30 * u)), T, lens, up
    if name == "descend":
        k = ss(0.0, 1.0, u)
        L1 = V3(-232, 66, 4.8); T1 = V3(-298, 104, 1.2)
        L0, T0_, lens0, up0 = MAP_POSE; L0 = L0 - Vector((0, 0, 30))
        L = L0.lerp(L1, k); L.z = L1.z + (L0.z - L1.z) * ((1 - k) ** 2.2)
        q = look_quat(L0, T0_, up0).slerp(look_quat(L1, T1), ss(0.0, 0.85, u))
        return L, q, lens0 + (28.0 - lens0) * k, None
    if name == "track_side":
        lag = 3.0
        L = car_pt(0, cut, fv - lag, (9.0, 1.5, 1.7)); T = car_pt(0, cut, fv, (0, -4, 0.6))
        return L, T, 34.0, Vector((0, 0, 1))
    if name == "hero_slow":
        M_end = car_matrix(0, cut, f1)
        L = M_end @ Vector((-4.5, -30.0, 0.55)); T = car_pt(0, cut, fv, (0, -1.0, 0.45))
        return L, T, 58.0, Vector((0, 0, 1))
    if name == "drone":
        L = car_pt(0, cut, fv, (0, 18 + 14 * u, 9 + 16 * u)); T = car_pt(0, cut, fv, (0, -22, 0))
        return L, T, 28.0, Vector((0, 0, 1))
    if name == "limit":
        P = R["old_entry"]["P"]; t, n = world._frame(P[:, :2])
        L = V3(P[2, 0] - n[2, 0] * 1.5, P[2, 1] - n[2, 1] * 1.5, 1.6) + Vector((t[2, 0], t[2, 1], 0)) * (-6 + 5 * u)
        T = V3(P[22, 0], P[22, 1], 1.4)
        return L, T, 34.0, Vector((0, 0, 1))
    raise KeyError(name)


CAM = bpy.data.objects.new("CAM", bpy.data.cameras.new("CAM")); sc.collection.objects.link(CAM); sc.camera = CAM
CAM.rotation_mode = "QUATERNION"; CAM.data.clip_start = 0.08; CAM.data.clip_end = 6000.0; CAM.data.sensor_fit = "AUTO"
CAMLOG = {}


def key_camera():
    prev = None
    for fv, cut in SAMPLES:
        L, T, lens, up = cam_pose(cut, fv)
        q = T if isinstance(T, Quaternion) else look_quat(L, T, up)
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


def text_obj(name, body, at, size, mat, z):
    cu = bpy.data.curves.new(name, "FONT"); cu.body = body; cu.font = FONT; cu.size = size; cu.align_x = "CENTER"; cu.align_y = "CENTER"
    cu.extrude = 0.0; o = bpy.data.objects.new(name, cu); MAPCOL.objects.link(o)
    o.location = (at[0], at[1], z); o.rotation_euler = (0, 0, -math.pi / 2); cu.materials.append(mat)
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
    red, _ = fade_mat("map_red", (1.0, 0.1, 0.06), 9.0); gold, _ = fade_mat("map_gold", (1.0, 0.72, 0.12), 10.0)
    rc = curve_obj("route_red", red_P, zm + 1.2, 1.7, red, lateral=-1.6); gc = curve_obj("route_gold", gold_P, zm + 1.4, 1.9, gold, lateral=0.0)
    f9a, f9b = S["S09"][0] + 6, S["S09"][1] - 20; f10a, f10b = S["S10"][0] + 4, S["S10"][1] - 12
    for cobj, fa, fb in ((rc, f9a, f9b), (gc, f10a, f10b)):
        cobj.data.bevel_factor_end = 0.0; cobj.data.keyframe_insert("bevel_factor_end", frame=fa)
        cobj.data.bevel_factor_end = 1.0; cobj.data.keyframe_insert("bevel_factor_end", frame=fb)
    # markers riding the tips (ghost = red ring, Senna = head sticker)
    import bmesh
    me = bpy.data.meshes.new("ghost"); bm = bmesh.new(); bmesh.ops.create_circle(bm, cap_ends=True, segments=24, radius=6.5); bm.to_mesh(me); bm.free()
    ghost = bpy.data.objects.new("ghost_marker", me); MAPCOL.objects.link(ghost); me.materials.append(red)
    img = bpy.data.images.load(os.path.join(VD, "assets", "senna_head.png"))
    hm, _ = fade_mat("map_head", (1, 1, 1), 1.15, image=img)
    w_h = 40.0; ar = img.size[1] / img.size[0]
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
    pop_keys(ghost, S["S09"][0] + 2, None, 1.0); pop_keys(head, S["S10"][0], None, 1.0)
    # labels
    wm, _ = fade_mat("map_label", (0.95, 0.97, 1.0), 3.0); gm, _ = fade_mat("map_label_gold", (1.0, 0.78, 0.2), 6.0)
    rm, _ = fade_mat("map_label_red", (1.0, 0.25, 0.18), 6.0)
    tg = text_obj("lab_goddards", "GODDARDS", (205.0, -78.0), 12.0, wm, Z_MAP + 2.5); pop_keys(tg, S["S08"][0] + 18, None)
    te = text_obj("lab_entry", "1993 PIT ENTRY", (60.0, -98.0), 11.0, wm, Z_MAP + 2.5); pop_keys(te, S["S08"][0] + 30, None)
    t_r = text_obj("lab_red", f"{Lr:.0f} m", (150.0, 30.0), 13.0, rm, Z_MAP + 2.5); pop_keys(t_r, S["S11"][0] + 2, None)
    t_g = text_obj("lab_gold", f"{Lg:.0f} m", (20.0, -20.0), 13.0, gm, Z_MAP + 2.5); pop_keys(t_g, S["S11"][0] + 8, None)
    t_d = text_obj("lab_diff", f"-{round((Lr - Lg) / 10) * 10:.0f} m", (-60.0, -70.0), 30.0, gm, Z_MAP + 3.0); pop_keys(t_d, S["S11"][0] + 18, None)
    t_n = text_obj("lab_nolimit", "NO SPEED LIMIT", (-60.0, 70.0), 10.5, wm, Z_MAP + 2.5); pop_keys(t_n, S["S11"][0] + 30, None)
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
    floor = bpy.data.objects.new("PIT_FLOOR", None); sc.collection.objects.link(floor); floor.location = (0, 0, BZ + 0.03)
    car_dir = WEST
    # stop point the crew expected (front of the box, working lane)
    stop = BOX + BN * 7.0
    spots = [("crew02", stop + EAST * 2.4 + BN * -1.6, "tyre"), ("crew03", stop + WEST * 2.6 + BN * -1.6, "tyre"),
             ("crew04", stop + EAST * 2.4 + BN * 2.2, "tyre"), ("crew05", stop + WEST * 2.6 + BN * 2.2, "lolly"),
             ("crew06", stop + EAST * 4.6 - BN * 3.0, "chief")]
    perfs = []
    kw = core.KeyWriter()
    frames = list(range(S["S03"][0] - 10, S["S06"][1] + 2))
    for i, (key, p, role) in enumerate(spots):
        rig = EB.load_character(key, {"char": key}, paths, {})
        rig.parent = floor
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
            tgt = [cpos.x, cpos.y, cpos.z - (BZ + 0.03)]
            pf.do("look_at", {"target": tgt, "at_s": f / FPS})
        if role in ("lolly", "chief"):
            pf.do("clip", {"name": "shrug", "at_s": (S["S05"][1] - 22) / FPS, "dur": 1.6})
        perfs.append(pf)
    for pf in perfs: pf.bake(kw, frames)
    kw.write()
    for pf in perfs: pf.solve_contacts(kw, frames)
    kw.write()
    done = set()
    for img in bpy.data.images:
        if "body_color" in img.name and img.name not in done:
            recolour_overalls(img); done.add(img.name)
    return spots


def build_pit_props(spots):
    import bmesh
    Cp = world.coll("PIT_PROPS")
    rub = world.flat_mat("wet_tyre", (0.014, 0.014, 0.015), rough=0.4)
    blanket = world.flat_mat("tyre_blanket", (0.5, 0.03, 0.03), rough=0.8)
    me = bpy.data.meshes.new("wet_tyre"); bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=20, radius1=0.33, radius2=0.33, depth=0.36)
    bm.to_mesh(me); bm.free(); me.materials.append(rub)
    me2 = bpy.data.meshes.new("blanket"); bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=False, segments=20, radius1=0.345, radius2=0.345, depth=0.3); bm.to_mesh(me2); bm.free(); me2.materials.append(blanket)
    for k, (key, p, role) in enumerate(spots):
        if role != "tyre": continue
        for j in range(2):
            base = p + EAST * 0.7 + BN * 0.6 * (j - 0.5)
            t = bpy.data.objects.new(f"tyre_{k}_{j}", me); Cp.objects.link(t); t.location = (base.x, base.y, BZ + 0.03 + 0.33)
            t.rotation_euler = (math.pi / 2, 0, math.atan2(BN.y, BN.x) + 0.2 * j)
            bl = bpy.data.objects.new(f"blanket_{k}_{j}", me2); Cp.objects.link(bl); bl.location = t.location; bl.rotation_euler = t.rotation_euler
    # wheel guns + lollipop + pit board on the pit wall
    try:
        before = set(bpy.data.objects)
        bpy.ops.import_scene.gltf(filepath="/root/.claude/uploads/9d2567ac-2881-58a8-b77d-52de40e0710d/3333a52b-impact_wrench.glb")
        gun = [o for o in bpy.data.objects if o not in before and o.type == "MESH"]
        for g in gun:
            for c in list(g.users_collection): c.objects.unlink(g)
            Cp.objects.link(g)
        if gun:
            g0 = gun[0]; d = max(g0.dimensions); s_ = 0.32 / max(d, 1e-6); g0.scale = (s_, s_, s_)
            g0.parent = None; g0.location = spots[0][1] + EAST * 0.2 + BN * 0.9 + Vector((0, 0, BZ + 0.06))
    except Exception as ex:
        log("wheel gun skipped", ex)
    pole = bpy.data.meshes.new("lolly_pole"); bm = bmesh.new(); bmesh.ops.create_cone(bm, cap_ends=True, segments=8, radius1=0.02, radius2=0.02, depth=1.6)
    bm.to_mesh(pole); bm.free(); pole.materials.append(world.flat_mat("lolly_pole", (0.6, 0.6, 0.6), metal=1.0, rough=0.3))
    disc = bpy.data.meshes.new("lolly_disc"); bm = bmesh.new(); bmesh.ops.create_cone(bm, cap_ends=True, segments=24, radius1=0.24, radius2=0.24, depth=0.02)
    bm.to_mesh(disc); bm.free(); disc.materials.append(world.flat_mat("lolly_disc", (0.85, 0.08, 0.05), rough=0.4))
    lp = spots[3][1] + BN * 0.5 + EAST * 0.4
    po = bpy.data.objects.new("lolly_pole", pole); Cp.objects.link(po); po.location = (lp.x, lp.y, BZ + 0.83)
    do = bpy.data.objects.new("lolly_disc", disc); Cp.objects.link(do); do.location = (lp.x, lp.y, BZ + 1.7); do.rotation_euler = (math.pi / 2, 0, math.atan2(BN.y, BN.x))


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
    dist = G.new("ShaderNodeVectorMath"); dist.operation = "DISTANCE"; GL.new(comb.outputs[0], dist.inputs[0]); GL.new(cam.outputs["Location"], dist.inputs[1])
    near = m_("LESS_THAN", dist.outputs["Value"], 1.6)
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
    return po


def build_spray(car, key):
    """Rear spray plume (volume mist) riding the car; density follows the car speed."""
    import bmesh
    me = bpy.data.meshes.new(f"{key}_spray"); bm = bmesh.new()
    prof = [(0.0, 0.9, 0.12, 0.6), (3.0, 1.6, 0.05, 1.4), (8.0, 2.8, 0.0, 2.6), (16.0, 4.4, 0.0, 3.8)]   # (back y, half w, z0, z1)
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
    nz = N.new("ShaderNodeTexNoise"); nz.inputs["Scale"].default_value = 0.55; nz.inputs["Detail"].default_value = 4; L.new(mp.outputs[0], nz.inputs[0])
    sep = N.new("ShaderNodeSeparateXYZ"); L.new(tc.outputs["Object"], sep.inputs[0])
    fall = world.math_node(nt, "SUBTRACT", 1.0, world.math_node(nt, "DIVIDE", world.math_node(nt, "SUBTRACT", sep.outputs[1], 2.0), 16.0), clamp=True)
    d = world.math_node(nt, "MULTIPLY", world.math_node(nt, "POWER", nz.outputs["Fac"], 2.2), fall)
    amt = N.new("ShaderNodeValue"); amt.name = "spray_amt"
    L.new(world.math_node(nt, "MULTIPLY", world.math_node(nt, "MULTIPLY", d, 2.6), amt.outputs[0]), vol.inputs["Density"])
    L.new(vol.outputs[0], out.inputs["Volume"]); me.materials.append(m)
    return o, amt


SPRAY = {}
for which, key in ((0, "senna"), (1, "rival")):
    o, amt = build_spray(CARS[key], key); SPRAY[key] = amt
    for f in range(0, NF, 2):
        st = states_at(CUT_OF[f], float(f))[which]
        v = SIMS[st[0]].v_at_s(min(max(st[1], 0), SIMS[st[0]].L)) if st else 0.0
        amt.outputs[0].default_value = float(np.clip((v - 8.0) / 40.0, 0, 1)); amt.outputs[0].keyframe_insert("default_value", frame=f)
RAIN = build_rain()
KWR.write(); log("keys written, rain + spray")

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
json.dump({"cam": CAMLOG, "box": list(BOX), "map": {"z": Z_MAP, "alt": MAP_ALT, "len": MAP_LEN}}, open(os.path.join(VD, "build", "camlog.json"), "w"))
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(VD, "build", "donington", "scene.blend"))
log("saved scene.blend")

if SHEET:
    os.makedirs(SHEET, exist_ok=True)
    sc.render.resolution_x, sc.render.resolution_y = 360, 640; sc.cycles.samples = 10
    picks = [("hook", 40), ("split", 150), ("est_air", 230), ("est_crowd", 320), ("chase_entry", 430), ("box_wait", 520),
             ("box_pass", 568), ("pit_side", 650), ("rise", 760), ("map", 900), ("map", 1060), ("map", 1150),
             ("descend", 1235), ("track_side", 1300), ("hero_slow", 1400), ("drone", 1480), ("limit", 1580), ("return", 1650)]
    for nm, f in picks:
        sc.frame_set(f); sc.render.filepath = os.path.join(SHEET, f"{f:05d}_{nm}.jpg")
        t = time.time(); bpy.ops.render.render(write_still=True); log("sheet", nm, f, round(time.time() - t, 1), "s")
