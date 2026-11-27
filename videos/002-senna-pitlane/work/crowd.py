"""Realistic audience: Microsoft Rocketbox people (MIT) through the shorts3d engine (fixed skeletons, PBR, real mocap).
Each avatar becomes one animated 'actor' with its own randomized simple gestures (idle, breathing, looking around,
talking, clapping...). Actors are instanced on Poisson-disc points (minimum spacing -> nobody overlaps), facing the
track, standing exactly on the terrain / terrace surface. Sources live 300 m under the ground (never seen)."""
import bpy, os, math, random, glob
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

HOME = os.path.expanduser("~/.shorts3d")
UNDER = -300.0
GESTURES = [("look_around", 3.5), ("scratch_head", 2.6), ("talk", 3.0), ("agree", 2.2), ("breathe", 4.0),
            ("clap", 2.4), ("talk_relaxed", 3.0), ("disagree", 2.0)]


def actor_keys():
    lib = os.path.join(HOME, "library", "characters")
    return sorted(os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(lib, "aud_*.blend")))


def load_actors(sc, keys, fps, frames, seed=1, cheer_times=(), lite=True):
    """-> parent collection AUD_ACTORS with one child collection per actor (sorted names), animations baked."""
    from engine import build as EB, rigkit, motion, core
    paths = {"chars": os.path.join(HOME, "library", "characters"), "mh_data": os.path.join(HOME, "mh", "data"),
             "mh_assets": os.path.join(HOME, "mh", "assets"), "cache": HOME, "clips": os.path.join(HOME, "clips")}
    parent = bpy.data.collections.new("AUD_ACTORS"); sc.collection.children.link(parent)
    rnd = random.Random(seed); perfs = []
    T = (frames[-1] + 1) / fps
    for i, key in enumerate(keys):
        rig = EB.load_character(f"a{i:02d}", {"char": key}, paths, {})
        coll = rig.users_collection[0]
        if lite:   # preview on CPU: no subdivision, no face morph data, 1K textures (the GPU final keeps them all)
            for o in coll.objects:
                if o.type != "MESH": continue
                for m in list(o.modifiers):
                    if m.type == "SUBSURF": o.modifiers.remove(m)
                if o.data.shape_keys: o.shape_key_clear()
                for mat in o.data.materials:
                    if not mat or not mat.node_tree: continue
                    for n in mat.node_tree.nodes:
                        if n.type == "TEX_IMAGE" and n.image and n.image.filepath.endswith(".tga"):
                            lo = n.image.filepath[:-4] + "_1k.png"
                            if os.path.exists(lo) and not n.image.get("lite"):
                                n.image.filepath = lo; n.image.reload(); n.image["lite"] = 1
        sc.collection.children.unlink(coll); parent.children.link(coll)
        coll.name = f"AUD_{i:02d}_{key}"
        coll.instance_offset = (0, 0, UNDER)
        rig.location = (0, 0, UNDER); bpy.context.view_layer.update()
        ch = rigkit.Character(f"a{i:02d}", rig); ch.setup_controls()
        pf = motion.Performer(ch, [0.0, 0.0], 0.0, fps, seed=100 + i, scene_props={})
        pf.clips_dir = paths["clips"]
        pf.add_idle(rnd.choice(["idle", "idle", "breathe"]), rnd.uniform(0.6, 0.85))
        t = rnd.uniform(0.0, 3.0)
        while t < T - 1.0:                                   # sparse, simple gestures at random times
            name, dur = rnd.choice(GESTURES)
            pf.do("clip", {"name": name, "at_s": t, "dur": dur * rnd.uniform(0.85, 1.2), "amt": rnd.uniform(0.55, 0.9)})
            if rnd.random() < 0.5:                           # glance somewhere (track, neighbour, sky)
                tgt = [rnd.uniform(-8, 8), rnd.uniform(-14, -4), rnd.uniform(0.8, 2.4) + UNDER]
                pf.do("look_at", {"target": tgt, "at_s": t + rnd.uniform(0.2, dur)})
            t += dur + rnd.uniform(2.5, 7.5)
        for ct in cheer_times:                               # some react when the cars blast past
            if rnd.random() < 0.35:
                pf.do("clip", {"name": rnd.choice(["clap", "cheer"]), "at_s": ct + rnd.uniform(-0.3, 0.4), "dur": 2.2, "amt": 0.8})
        perfs.append(pf)
    kw = core.KeyWriter()
    for pf in perfs: pf.bake(kw, frames)
    kw.write()
    return parent


def poisson(cands_fn, r, n_try, rnd, ok_fn):
    """dart throwing with a hash grid: points never closer than r."""
    cell = r / math.sqrt(2); grid = {}; pts = []
    for _ in range(n_try):
        p = cands_fn(rnd)
        if p is None or not ok_fn(p): continue
        gx, gy = int(p[0] // cell), int(p[1] // cell); bad = False
        for dx in (-2, -1, 0, 1, 2):
            for dy in (-2, -1, 0, 1, 2):
                q = grid.get((gx + dx, gy + dy))
                if q is not None and (q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2 < r * r: bad = True; break
            if bad: break
        if bad: continue
        grid[(gx, gy)] = p; pts.append(p)
    return pts


class Exclusions:
    def __init__(self, W, extra_lines=(), tree_pts=()):
        self.roads = [(r["P"][:, :2], r["w"]) for r in W.roads.values()]
        allp = np.vstack([p for p, _ in self.roads]); self.road_pts = allp
        self.road_w = np.concatenate([np.full(len(p), w) for p, w in self.roads])
        lines = []
        for w in W.ways:
            if w["kind"] in ("barrier=fence", "barrier=wall", "barrier=guard_rail", "barrier=tyres"):
                lines.append(W.resample(w["pts"], 0.5))
        for L in extra_lines: lines.append(np.asarray(L)[:, :2])
        self.line_pts = np.vstack(lines) if lines else np.zeros((0, 2))
        self.bldg = [np.array(w["pts"][:-1]) for w in W.ways if w["kind"].startswith("building") and w["closed"]]
        self.trees = np.asarray(tree_pts)[:, :2] if len(tree_pts) else np.zeros((0, 2))

    def ok(self, x, y, road_clear=7.0, line_clear=0.8, tree_clear=1.8):
        d = np.hypot(self.road_pts[:, 0] - x, self.road_pts[:, 1] - y) - self.road_w / 2
        if d.min() < road_clear: return False
        if len(self.line_pts) and np.min(np.hypot(self.line_pts[:, 0] - x, self.line_pts[:, 1] - y)) < line_clear: return False
        if len(self.trees) and np.min(np.hypot(self.trees[:, 0] - x, self.trees[:, 1] - y)) < tree_clear: return False
        for P in self.bldg:
            mn, mx = P.min(0) - 1, P.max(0) + 1
            if not (mn[0] <= x <= mx[0] and mn[1] <= y <= mx[1]): continue
            inside = False; n = len(P)
            for i in range(n):
                x1, y1 = P[i]; x2, y2 = P[(i + 1) % n]
                if ((y1 > y) != (y2 > y)) and (x < (x2 - x1) * (y - y1) / (y2 - y1 + 1e-12) + x1): inside = not inside
            if inside: return False
        return True


def ground_bvh(objs):
    dg = bpy.context.evaluated_depsgraph_get()
    return [BVHTree.FromObject(o, dg) for o in objs]


def ground_z(bvhs, x, y, z_hint=2000.0):
    best = None
    for b in bvhs:
        hit = b.ray_cast(Vector((x, y, z_hint)), Vector((0, 0, -1)))
        if hit[0] is not None and (best is None or hit[0].z > best):
            best = hit[0].z
    return best


def instancer(name, pts, yaws, ids, actors_coll, coll):
    me = bpy.data.meshes.new(name); me.from_pydata([tuple(p) for p in pts], [], []); me.update()
    o = bpy.data.objects.new(name, me); coll.objects.link(o)
    a = me.attributes.new("yaw", "FLOAT", "POINT"); a.data.foreach_set("value", list(map(float, yaws)))
    b = me.attributes.new("actor", "INT", "POINT"); b.data.foreach_set("value", list(map(int, ids)))
    ng = bpy.data.node_groups.new(f"{name}_gn", "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    N, L = ng.nodes, ng.links
    gi = N.new("NodeGroupInput"); go = N.new("NodeGroupOutput")
    m2p = N.new("GeometryNodeMeshToPoints"); L.new(gi.outputs[0], m2p.inputs[0])
    ci = N.new("GeometryNodeCollectionInfo"); ci.inputs[0].default_value = actors_coll
    ci.inputs["Separate Children"].default_value = True; ci.inputs["Reset Children"].default_value = False
    ci.transform_space = "ORIGINAL"
    iop = N.new("GeometryNodeInstanceOnPoints"); iop.inputs["Pick Instance"].default_value = True
    L.new(m2p.outputs[0], iop.inputs["Points"]); L.new(ci.outputs[0], iop.inputs["Instance"])
    na = N.new("GeometryNodeInputNamedAttribute"); na.data_type = "INT"; na.inputs[0].default_value = "actor"
    L.new(na.outputs["Attribute"], iop.inputs["Instance Index"])
    ny = N.new("GeometryNodeInputNamedAttribute"); ny.data_type = "FLOAT"; ny.inputs[0].default_value = "yaw"
    comb = N.new("ShaderNodeCombineXYZ"); L.new(ny.outputs["Attribute"], comb.inputs[2]); L.new(comb.outputs[0], iop.inputs["Rotation"])
    L.new(iop.outputs[0], go.inputs[0])
    md = o.modifiers.new("aud", "NODES"); md.node_group = ng
    return o


def assign_actors(pts, n_actors, rnd, near=2.2):
    """random actor per point, never the same actor as a neighbour within `near` m (no visible twins side by side)."""
    ids = []
    P = np.asarray(pts)[:, :2]
    for i, p in enumerate(P):
        taken = {ids[j] for j in range(i) if (P[j, 0] - p[0]) ** 2 + (P[j, 1] - p[1]) ** 2 < near * near}
        choices = [k for k in range(n_actors) if k not in taken] or list(range(n_actors))
        ids.append(rnd.choice(choices))
    return ids


def yaw_facing(u):
    """instance yaw so the actor's forward (-Y) points along 2D direction u"""
    return math.atan2(u[0], -u[1])


def build_audience(W, sc, fps, frames, zones, quality="preview", cheer_times=(), seed=7, spacing=0.9, density=0.55):
    """zones: [(name, road_key, side, d0, d1, s0, s1)] banks beside roads; plus the grandstand rows (W.stand_rows)."""
    keys = actor_keys()
    parent = load_actors(sc, keys, fps, frames, seed, cheer_times, lite=(quality == "preview"))
    stand_poly = None
    if getattr(W, "stand_lines", None):
        a, b = W.stand_lines; stand_poly = np.vstack([a, b[::-1]])
    ex = Exclusions(W, extra_lines=list(getattr(W, "stand_lines", [])), tree_pts=getattr(W, "tree_points", []))
    if stand_poly is not None: ex.bldg.append(stand_poly)
    bvhs = ground_bvh([bpy.data.objects["terrain"]])
    rnd = random.Random(seed); C = bpy.data.collections.new("AUDIENCE"); sc.collection.children.link(C)
    total = 0
    # grandstand terraces: people along each tread (gaps, never closer than 0.78 m)
    pts, yaws = [], []
    for row, yaw in getattr(W, "stand_rows", []):
        d = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(row[:, :2], axis=0), axis=1))])
        x = rnd.uniform(0, 1.0)
        while x < d[-1]:
            if rnd.random() < 0.78:
                p = np.array([np.interp(x, d, row[:, j]) for j in range(3)]); yv = float(np.interp(x, d, yaw))
                if hasattr(ex, "avoid_fn") and not ex.avoid_fn(p[0], p[1]):      # keep the camera's spot free
                    x += rnd.uniform(0.78, 1.35); continue
                pts.append(tuple(p)); yaws.append(yv + rnd.uniform(-0.2, 0.2))
            x += rnd.uniform(0.78, 1.35)
    if pts:
        ids = assign_actors(pts, len(keys), rnd); instancer("aud_stand", pts, yaws, ids, parent, C); total += len(pts)
    # banks behind the barriers/fences
    for name, key, side, d0, d1, s0, s1 in zones:
        r = W.roads[key]; P = r["P"][:, :2]
        t = np.gradient(P, axis=0); t /= np.linalg.norm(t, axis=1, keepdims=True) + 1e-9; n = np.stack([-t[:, 1], t[:, 0]], 1)
        s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1))])
        def cand(rr):
            ss = rr.uniform(s0, s1); i = int(np.searchsorted(s, ss)); i = min(max(i, 0), len(P) - 1)
            d = rr.uniform(d0, d1); return (P[i, 0] + n[i, 0] * d * side, P[i, 1] + n[i, 1] * d * side, i)
        area = (s1 - s0) * (d1 - d0); n_try = int(area / (spacing * spacing) * 3.0)
        raw = poisson(lambda rr: cand(rr), spacing, n_try, rnd, lambda p: ex.ok(p[0], p[1]))
        keep = [p for p in raw if rnd.random() < density]
        zp, zy = [], []
        for x, y, i in keep:
            z = ground_z(bvhs, x, y)
            if z is None: continue
            # slope guard: skip steep spots
            zx = ground_z(bvhs, x + 0.5, y); zyy = ground_z(bvhs, x, y + 0.5)
            if zx is None or zyy is None or max(abs(zx - z), abs(zyy - z)) > 0.22: continue
            u = (-n[i, 0] * side, -n[i, 1] * side)
            zp.append((x, y, z)); zy.append(yaw_facing(u) + rnd.uniform(-0.45, 0.45))
        if zp:
            ids = assign_actors(zp, len(keys), rnd); instancer(f"aud_{name}", zp, zy, ids, parent, C); total += len(zp)
    print("audience", total, "people from", len(keys), "actors")
    return C
