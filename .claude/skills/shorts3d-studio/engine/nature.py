"""Natural ecosystems for outdoor sets: forests with clumps and clearings, undergrowth, roadside grass, talus at rock
cuts, half-buried boulders, rock outcrops, forest-floor debris - placed on the real terrain surface (raycast), never
floating, never perched on a rim, rocks sunk into the soil with a dirt line where they meet the ground.

    lib = nature.Lib("nature.blend")                      # collections tree_* rock_* stones_* rockface_* grass_* plant_* debris_*
    nature.ecosystem(R, lib, coll, center, size, [terrain objects], seed=3)

Rules (from real mountain-road footage): trees grow vertical (not normal to the slope), big trees inside the forest,
saplings + shrubs at its edges, nothing on cut faces steeper than ~40 deg, rocks gather at the BASE of a cut (talus) and
sit 25-40 % below ground, boulders are rare and mossy in forest, grass hugs the shoulder and the guard-rail posts.
"""
import bpy, math, random, json, os
import numpy as np
from mathutils import Vector, Matrix, Euler
from mathutils.bvhtree import BVHTree
from . import core


# heavy needle-geometry trees cost ~1.3 KB of render memory per triangle: these variants (2-4 M tris each) are skipped
HEAVY = ("fir_tree_01_a", "fir_tree_01_b", "fir_sapling_medium_a", "moss_moss")


class Lib:
    def __init__(self, path, skip=HEAVY):
        info = path.replace(".blend", ".json")
        self.info = json.load(open(info)) if os.path.exists(info) else {}
        self.info = {k: v for k, v in self.info.items() if not any(x in k for x in skip)}
        have = {c.name: c for c in bpy.data.collections if c.name in self.info}
        need = [n for n in self.info if n not in have]
        if need:
            with bpy.data.libraries.load(path, link=False) as (src, dst):
                dst.collections = [c for c in src.collections if c in need]
            for c in dst.collections:
                have[c.name] = c
        self.cols = have

    def names(self, cat, contains=None, exclude=None):
        return [n for n, v in self.info.items() if v["cat"] == cat and n in self.cols
                and (not contains or any(c in n for c in contains)) and not (exclude and any(e in n for e in exclude))]

    def size(self, name):
        return self.info[name]["size"]


class Ground:
    """Raycast the real terrain meshes (BVH) -> height + normal; avoids floating / buried placements."""
    def __init__(self, objs):
        dg = bpy.context.evaluated_depsgraph_get()
        self.bvh = [BVHTree.FromObject(o, dg) for o in objs]

    def at(self, x, y, z0=2000.0):
        best = None
        for b in self.bvh:
            loc, nor, i, d = b.ray_cast(Vector((x, y, z0)), Vector((0, 0, -1)))
            if loc is not None and (best is None or loc.z > best[0].z):
                best = (loc, nor)
        return best

    def slope(self, x, y, h=1.2):
        c = self.at(x, y)
        if not c:
            return None, None
        pts = [self.at(x + dx, y + dy) for dx, dy in ((h, 0), (-h, 0), (0, h), (0, -h))]
        if any(p is None for p in pts):
            return c[0].z, 99.0
        gx = (pts[0][0].z - pts[1][0].z) / (2 * h); gy = (pts[2][0].z - pts[3][0].z) / (2 * h)
        drop = c[0].z - min(p[0].z for p in pts)
        return c[0].z, max(math.hypot(gx, gy), drop / h * 0.8)


class Spacing:
    """Poisson-disc spacing with a spatial hash; each item keeps its own radius."""
    def __init__(self, cell=2.0):
        self.cell, self.g = cell, {}

    def ok(self, x, y, r):
        c = self.cell; i0, j0 = int(math.floor(x / c)), int(math.floor(y / c)); k = int(math.ceil(4.0 / c)) + 1
        for i in range(i0 - k, i0 + k + 1):
            for j in range(j0 - k, j0 + k + 1):
                for (px, py, pr) in self.g.get((i, j), ()):
                    if (px - x) ** 2 + (py - y) ** 2 < (max(r, pr)) ** 2:
                        return False
        return True

    def add(self, x, y, r):
        self.g.setdefault((int(math.floor(x / self.cell)), int(math.floor(y / self.cell))), []).append((x, y, r))


def _inst(lib, name, coll, loc, yaw, s, tilt=(0.0, 0.0), sz=1.0):
    e = bpy.data.objects.new(f"{name}.i", None)
    e.instance_type = "COLLECTION"; e.instance_collection = lib.cols[name]
    e.location = loc; e.rotation_euler = (tilt[0], tilt[1], yaw); e.scale = (s, s, s * sz)
    coll.objects.link(e)
    return e


def _cut_top(R, s):
    n = R.noise
    H = R.cut_height[0] + (R.cut_height[1] - R.cut_height[0]) * (0.5 + 0.5 * n(s / 37.0))
    return 0.8 + H / 2.4, H


def ecosystem(R, lib, coll, center, size, terrain, seed=3, trees=9000, max_road=520.0, s_range=None, use_faces=False):
    rnd = random.Random(seed); G = Ground(terrain); sp = Spacing(2.0); n = R.noise
    out = {k: core.ensure_coll(f"{coll.name}_{k}", coll) for k in ("trees", "under", "rocks", "grass", "debris")}
    big = lib.names("tree", ("fir_tree_01",)); med = lib.names("tree", ("fir_sapling_medium",))
    small = lib.names("tree", ("fir_sapling_a", "fir_sapling_b", "fir_sapling_c", "pine_sapling_small"))
    ferns = lib.names("plant", ("fern",)); shrubs = lib.names("plant", ("shrub_02",)); bushes = lib.names("plant", ("shrub_03", "shrub_04"))
    grass = lib.names("grass", exclude=("tiny",)); moss_rocks = lib.names("rock", ("moss_set",))
    boulders = lib.names("rock", ("boulder",)); pebbles = lib.names("stones") + lib.names("rock", ("rock_07", "rock_09", "stone_01"))
    faces = lib.names("rockface"); debris = lib.names("debris")
    counts = {k: 0 for k in out}
    cx, cy = center
    def road_ok(x, y, need):
        s, d, zr = R.local(x, y)
        if s_range and not (s_range[0] <= s <= s_range[1]):
            return None
        a = abs(d) - R.edge(); cut = d * R.cut_side > 0
        if cut:
            top, H = _cut_top(R, s)
            return (s, d, a, cut) if a > top + need else None
        return (s, d, a, cut) if a > need else None
    # ---------------- forest: clumps + clearings, species by local density, Poisson spacing
    tries = 0
    while counts["trees"] < trees and tries < trees * 14:
        tries += 1
        x = cx + (rnd.random() - 0.5) * size; y = cy + (rnd.random() - 0.5) * size
        loc = road_ok(x, y, 2.2)
        if not loc:
            continue
        s, d, a, cut = loc
        if not cut and a < 24.0:         # open grassy fill slope below the rail: the low sun reaches the road and
            continue                     # the eye travels over the canopy into the valley (broad vista)
        if a > max_road * (0.55 + 0.45 * rnd.random()):
            continue
        clump = core.smooth((n.fbm(x / 48.0 + 3.1, 3) * 0.5 + n(y / 61.0 + 9.3) * 0.5 + 0.35) / 0.9)
        dens = 0.18 + 0.82 * clump
        if rnd.random() > dens:
            continue
        z, slope = G.slope(x, y)
        if z is None or slope > 0.85:
            continue
        u = rnd.random()
        if dens > 0.62:
            kind, r, sc = (rnd.choice(big), 3.3, rnd.uniform(0.9, 1.35)) if u < 0.62 else (rnd.choice(med), 2.5, rnd.uniform(0.95, 1.45))
        elif dens > 0.35:
            kind, r, sc = ((rnd.choice(big), 3.3, rnd.uniform(0.8, 1.15)) if u < 0.3 else
                           (rnd.choice(med), 2.4, rnd.uniform(0.8, 1.25)) if u < 0.75 else (rnd.choice(small), 1.2, rnd.uniform(1.6, 2.6)))
        else:
            kind, r, sc = (rnd.choice(med), 2.2, rnd.uniform(0.7, 1.1)) if u < 0.45 else (rnd.choice(small), 1.1, rnd.uniform(1.3, 2.4))
        if not sp.ok(x, y, r * (0.85 if dens > 0.6 else 1.0)):
            continue
        sp.add(x, y, r)
        _inst(lib, kind, out["trees"], (x, y, z - 0.25), rnd.uniform(0, 6.283), sc,
              (rnd.uniform(-0.035, 0.035), rnd.uniform(-0.035, 0.035)), rnd.uniform(0.92, 1.1))
        counts["trees"] += 1
        # undergrowth around the trunk / at forest edges
        k = 0 if a > 120 else (rnd.randint(0, 1) if dens > 0.5 else rnd.randint(0, 2))
        for _ in range(k):
            ang = rnd.uniform(0, 6.283); rr = rnd.uniform(r * 0.5, r * 1.3); px, py = x + math.cos(ang) * rr, y + math.sin(ang) * rr
            g = G.at(px, py)
            if not g or not road_ok(px, py, 0.8):
                continue
            pool = ferns if (dens > 0.55 and rnd.random() < 0.6) else (shrubs + bushes)
            if not pool:
                continue
            nm = rnd.choice(pool); _inst(lib, nm, out["under"], (px, py, g[0].z - 0.05), rnd.uniform(0, 6.283), rnd.uniform(0.8, 1.5))
            counts["under"] += 1
    # ---------------- roadside: grass along both shoulders, talus + pebbles at the cut base, grass on the cut top
    s0, s1 = s_range or (0.0, R.path.L)
    s = s0
    while s < s1:
        s += rnd.uniform(0.25, 0.55)
        p = R.path.point(s); t = R.path.tangent(s); left = Vector((-t.y, t.x, 0)).normalized()
        for side in (+1, -1):
            cut = side * R.cut_side > 0
            if cut:
                top, H = _cut_top(R, s)
                # grass + small plants on the lip of the cut (forest edge), never on the face itself
                if rnd.random() < 0.55 and grass:
                    a = top + rnd.uniform(0.6, 3.0); q = p + left * side * (R.edge() + a); g = G.at(q.x, q.y)
                    if g:
                        _inst(lib, rnd.choice(grass), out["grass"], (q.x, q.y, g[0].z - 0.03), rnd.uniform(0, 6.283), rnd.uniform(1.8, 3.2))
                        counts["grass"] += 1
            else:
                for _ in range(2):          # dense grass on the berm around the guard-rail posts and the fill slope
                    a = rnd.uniform(0.35, 3.2); q = p + left * side * (R.edge() + a); g = G.at(q.x, q.y)
                    if g and grass:
                        nm = rnd.choice(grass); half = max(lib.size(nm)[:2]) / 2
                        scl = min(rnd.uniform(1.5, 3.0), max(0.8, (a - 0.05) / max(0.05, half)))
                        _inst(lib, nm, out["grass"], (q.x, q.y, g[0].z - 0.03), rnd.uniform(0, 6.283), scl)
                        counts["grass"] += 1
                if rnd.random() < 0.05 and bushes:
                    a = rnd.uniform(1.2, 3.0); q = p + left * side * (R.edge() + a); g = G.at(q.x, q.y)
                    if g:
                        _inst(lib, rnd.choice(bushes), out["under"], (q.x, q.y, g[0].z - 0.05), rnd.uniform(0, 6.283), rnd.uniform(1.5, 3.0))
                        counts["under"] += 1
    # ---------------- rock outcrops set INTO the cut face (real relief), boulders in the forest, debris
    s = s0
    while faces and use_faces and s < s1:
        s += rnd.uniform(6.0, 13.0)
        top, H = _cut_top(R, s)
        p = R.path.point(s); t = R.path.tangent(s); left = Vector((-t.y, t.x, 0)).normalized() * R.cut_side
        a = 0.8 + (top - 0.8) * rnd.uniform(0.25, 0.7); q = p + left * (R.edge() + a); g = G.at(q.x, q.y)
        if not g:
            continue
        nm = rnd.choice(faces); scl = H / max(lib.size(nm)) * rnd.uniform(0.35, 0.6)
        yaw = math.atan2(-left.y, -left.x) + rnd.uniform(-0.6, 0.6)
        _inst(lib, nm, out["rocks"], (q.x, q.y, g[0].z - lib.size(nm)[2] * scl * 0.45), yaw, scl,
              (rnd.uniform(-0.3, 0.3), rnd.uniform(-0.3, 0.3)))
        counts["rocks"] += 1
    for _ in range(int(trees * 0.035)):
        x = cx + (rnd.random() - 0.5) * size * 0.7; y = cy + (rnd.random() - 0.5) * size * 0.7
        loc = road_ok(x, y, 8.0)
        if not loc or loc[2] > 300:
            continue
        z, slope = G.slope(x, y)
        if z is None or slope > 0.45 or not sp.ok(x, y, 2.2):
            continue
        pool = moss_rocks if rnd.random() < 0.7 else boulders
        if not pool:
            continue
        nm = rnd.choice(pool); scl = rnd.uniform(0.45, 1.1) if "moss" in nm else rnd.uniform(0.6, 1.3)
        sink = rnd.uniform(0.3, 0.45) * lib.size(nm)[2] * scl
        _inst(lib, nm, out["rocks"], (x, y, z - sink), rnd.uniform(0, 6.283), scl, (rnd.uniform(-0.15, 0.15), rnd.uniform(-0.15, 0.15)))
        sp.add(x, y, max(lib.size(nm)[:2]) * scl * 0.5); counts["rocks"] += 1
    for _ in range(int(trees * 0.03)):
        x = cx + (rnd.random() - 0.5) * size * 0.6; y = cy + (rnd.random() - 0.5) * size * 0.6
        loc = road_ok(x, y, 3.0)
        if not loc or loc[2] > 220 or not debris:
            continue
        z, slope = G.slope(x, y)
        if z is None or slope > 0.6:
            continue
        nm = rnd.choice(debris); _inst(lib, nm, out["debris"], (x, y, z - 0.08), rnd.uniform(0, 6.283), rnd.uniform(0.8, 1.3))
        counts["debris"] += 1
    return counts


# ------------------------------------------------------------------ materials
def _base_link(nt):
    b = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if not b:
        return None, None
    inp = b.inputs["Base Color"]
    return b, (inp.links[0].from_socket if inp.links else None)


def foliage(mat, var=0.18, translucency=0.22):
    """Per-instance colour variation (Object Info random) + backlit translucency for needles/leaves/grass."""
    nt = mat.node_tree; b, src = _base_link(nt)
    if not b or mat.get("s3d_foliage"):
        return
    N, L = nt.nodes, nt.links
    oi = N.new("ShaderNodeObjectInfo")
    hsv = N.new("ShaderNodeHueSaturation")
    m1 = N.new("ShaderNodeMapRange"); m1.inputs["To Min"].default_value = 1 - var; m1.inputs["To Max"].default_value = 1 + var * 0.6
    L.new(oi.outputs["Random"], m1.inputs["Value"]); L.new(m1.outputs[0], hsv.inputs["Value"])
    m2 = N.new("ShaderNodeMapRange"); m2.inputs["To Min"].default_value = 0.47; m2.inputs["To Max"].default_value = 0.53
    L.new(oi.outputs["Random"], m2.inputs["Value"]); L.new(m2.outputs[0], hsv.inputs["Hue"])
    if src is not None:
        L.new(src, hsv.inputs["Color"])
    else:
        hsv.inputs["Color"].default_value = b.inputs["Base Color"].default_value
    L.new(hsv.outputs[0], b.inputs["Base Color"])
    out = next(n for n in N if n.type == "OUTPUT_MATERIAL")
    if out.inputs[0].links and translucency > 0:
        s_out = out.inputs[0].links[0].from_socket
        tr = N.new("ShaderNodeBsdfTranslucent"); L.new(hsv.outputs[0], tr.inputs["Color"])
        mx = N.new("ShaderNodeMixShader"); mx.inputs[0].default_value = translucency
        L.new(s_out, mx.inputs[1]); L.new(tr.outputs[0], mx.inputs[2]); L.new(mx.outputs[0], out.inputs[0])
    mat["s3d_foliage"] = True


def rock_contact(mat, dirt=(0.075, 0.06, 0.045), line=0.42):
    """Soil at the foot of a sunk rock: below ~`line` of its height the rock turns earthy (no pasted-on look)."""
    nt = mat.node_tree; b, src = _base_link(nt)
    if not b or mat.get("s3d_contact"):
        return
    N, L = nt.nodes, nt.links
    tc = N.new("ShaderNodeTexCoord"); sep = N.new("ShaderNodeSeparateXYZ"); L.new(tc.outputs["Generated"], sep.inputs[0])
    nz = N.new("ShaderNodeTexNoise"); nz.inputs["Scale"].default_value = 9.0; L.new(tc.outputs["Object"], nz.inputs[0])
    add = N.new("ShaderNodeMath"); add.operation = "MULTIPLY_ADD"; add.inputs[1].default_value = 0.12
    L.new(nz.outputs["Fac"], add.inputs[0]); L.new(sep.outputs[2], add.inputs[2])
    mr = N.new("ShaderNodeMapRange"); mr.inputs["From Min"].default_value = line + 0.02; mr.inputs["From Max"].default_value = line - 0.1
    L.new(add.outputs[0], mr.inputs["Value"])
    mx = N.new("ShaderNodeMix"); mx.data_type = "RGBA"; L.new(mr.outputs[0], mx.inputs[0])
    if src is not None:
        L.new(src, mx.inputs[6])
    else:
        mx.inputs[6].default_value = b.inputs["Base Color"].default_value
    mx.inputs[7].default_value = (*dirt, 1); L.new(mx.outputs[2], b.inputs["Base Color"])
    mat["s3d_contact"] = True


def enhance(lib, aerial=None):
    seen = set()
    for name, c in lib.cols.items():
        cat = lib.info[name]["cat"]
        for o in c.all_objects:
            for m in (o.data.materials if o.type == "MESH" else []):
                if not m or m.name in seen:
                    continue
                seen.add(m.name); low = m.name.lower()
                if cat in ("tree", "plant", "grass") and not any(k in low for k in ("bark", "trunk", "dead")):
                    foliage(m)
                elif cat in ("rock", "stones", "rockface"):
                    rock_contact(m)
                if aerial:
                    aerial(m)
