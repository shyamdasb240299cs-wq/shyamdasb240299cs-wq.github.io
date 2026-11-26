"""Outdoor driving sets: a real-scale road along any 3D centreline, conforming terrain (rock cut on one side, valley
on the other), guard rail, lane markings, scattered trees/rocks, distant mountain ridges and aerial perspective.

    path = road.centreline(ctrl_pts)                      # Catmull-Rom -> vehicle.Path (arc length in metres)
    R = road.Road(path, width=7.0, cut_side=+1)           # +1: mountain on the LEFT of travel, valley on the right
    road.build(R, coll, tex=dict(asphalt=.., gravel=.., ground=.., rock=..), nature="nature.blend")

Textures: Poly Haven folders (CC0) with *_diff/_nor_gl/_rough|_arm maps. Everything else is procedural.
"""
import bpy, math, random, os, glob
import numpy as np
from mathutils import Vector, Matrix, kdtree
from . import core
from .vehicle import Path


def centreline(ctrl, step=0.5):
    """Catmull-Rom through control points (x, y, z) -> dense Path."""
    P = [Vector(p) for p in ctrl]; P = [P[0] * 2 - P[1]] + P + [P[-1] * 2 - P[-2]]
    out = []
    for i in range(1, len(P) - 2):
        p0, p1, p2, p3 = P[i - 1], P[i], P[i + 1], P[i + 2]
        n = max(2, int((p2 - p1).length / step))
        for k in range(n):
            t = k / n
            out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t + (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3))
    out.append(P[-2])
    return Path(out)


class Road:
    def __init__(self, path, width=7.0, shoulder=1.2, cut_side=1, cut_height=(6.0, 13.0), valley_drop=55.0, seed=3):
        self.path, self.width, self.shoulder, self.cut_side = path, width, shoulder, cut_side
        self.cut_height, self.valley_drop, self.seed = cut_height, valley_drop, seed
        self.noise = core.Noise1D(seed)
        # dense samples for nearest-point queries
        self.ss = np.arange(0, path.L, 0.5)
        self.pts = np.array([tuple(path.point(s)) for s in self.ss])
        self.kd = kdtree.KDTree(len(self.pts))
        for i, p in enumerate(self.pts):
            self.kd.insert((p[0], p[1], 0.0), i)
        self.kd.balance()
        T = np.gradient(self.pts, axis=0); T /= np.linalg.norm(T, axis=1)[:, None] + 1e-9
        self.left = np.stack([-T[:, 1], T[:, 0], np.zeros(len(T))], 1)
        self.left /= np.linalg.norm(self.left, axis=1)[:, None] + 1e-9
        # far-field spine: straight line through the ends (continuous everywhere, unlike nearest-point queries)
        a, b = self.pts[0, :2], self.pts[-1, :2]; self.sp0 = a; self.spd = (b - a) / np.linalg.norm(b - a)
        self.spl = np.array([-self.spd[1], self.spd[0]])
        u = (self.pts[:, :2] - a) @ self.spd; order = np.argsort(u); self.spu, self.spz = u[order], self.pts[order, 2]
        self.spoff = float(np.median((self.pts[:, :2] - a) @ self.spl))

    def edge(self):
        return self.width / 2 + self.shoulder

    def local(self, x, y):
        """World (x, y) -> (s, signed lateral d (+left), road height)."""
        co, i, dist = self.kd.find((x, y, 0.0))
        p = self.pts[i]; d = (x - p[0]) * self.left[i, 0] + (y - p[1]) * self.left[i, 1]
        return self.ss[i], d, p[2]

    def height(self, x, y, blend=(70.0, 170.0)):
        s, d, zr = self.local(x, y)
        a = abs(d) - self.edge()
        if a < blend[0]:
            return self._profile(s, d, zr)
        u = (np.array((x, y)) - self.sp0) @ self.spd; v = float((np.array((x, y)) - self.sp0) @ self.spl) - self.spoff
        zf = self._profile(u, v, float(np.interp(u, self.spu, self.spz))) + self.mountain(u, v)
        if a > blend[1]:
            return zf
        w = core.smooth((a - blend[0]) / (blend[1] - blend[0]))
        return self._profile(s, d, zr) * (1 - w) + zf * w

    def mountain(self, u, v):
        """Far field. Valley side: forested valley floor rising gently toward the horizon (the HDRI's real mountains
        stay visible above it). Cut side and past both ends: ridges, kept moderate so the backdrop still reads."""
        r_end = max(0.0, self.spu[0] - u, u - self.spu[-1])
        valley = v * self.cut_side < 0 and r_end < 200.0
        n = self.noise
        if valley:
            k = core.smooth((abs(v) - 500.0) / 3500.0)
            return k * (self.valley_drop * 0.85 - 10.0) + 8.0 * n.fbm(u / 300.0 + v / 410.0 + 7, 3) * core.smooth(abs(v) / 800.0)
        r = max(abs(v), r_end * 1.2)
        if r < 420.0:
            return 0.0
        k = core.smooth((r - 420.0) / 1400.0)
        ridge = 1.0 - abs(n.fbm(u / 700.0 + (13 if v > 0 else 71), 4) * 0.8 + n(v / 900.0 + 5) * 0.4)
        return k * (180.0 + 420.0 * max(0.0, ridge) ** 2.0)

    def _profile(self, s, d, zr):
        e = self.edge(); side = 1 if d * self.cut_side > 0 else -1; a = abs(d) - e
        n = self.noise
        if a <= 0.0:
            return zr - 0.06 - 0.02 * max(0.0, a + 0.6)
        if side > 0:   # mountain: ditch, steep rock cut, then forested slope
            H = self.cut_height[0] + (self.cut_height[1] - self.cut_height[0]) * (0.5 + 0.5 * n(s / 37.0))
            if a < 0.8:
                return zr - 0.25 * math.sin(math.pi * a / 0.8)
            cut = min(H, (a - 0.8) * 2.4 + 0.6 * n.fbm(s / 6.0 + a / 3.0))
            above = max(0.0, a - 0.8 - H / 2.4) * (0.45 + 0.15 * n(s / 60.0 + 3)) + 3.0 * n.fbm(s / 25.0 + a / 18.0 + 11)
            return zr + cut + above
        # valley: berm for the guard rail, fill slope, then the valley floor with rolling hills
        if a < 1.6:
            return zr - 0.08
        drop = (a - 1.6) * 0.75
        floor = self.valley_drop * (0.85 + 0.25 * n(s / 90.0 + 5))
        k = min(drop, floor * (1 - math.exp(-drop / floor)))
        return zr - 0.08 - k + 4.0 * n.fbm(s / 40.0 + a / 30.0 + 21) * min(1.0, a / 20.0)


# ------------------------------------------------------------------ materials
def _img(path, colorspace="sRGB"):
    im = bpy.data.images.load(path, check_existing=True)
    if colorspace != "sRGB":
        try: im.colorspace_settings.name = "Non-Color"
        except Exception: pass
    return im


def _maps(folder):
    f = lambda pat: (glob.glob(os.path.join(folder, "**", pat), recursive=True) or [None])[0]
    return {"diff": f("*_diff_*"), "nor": f("*_nor_gl_*"), "rough": f("*_rough_*"), "arm": f("*_arm_*")}


def _pbr_nodes(nt, maps, vec, b, tint=None, rough_mul=1.0, nstrength=1.0):
    N = nt.nodes; L = nt.links
    d = N.new("ShaderNodeTexImage"); d.image = _img(maps["diff"]); L.new(vec, d.inputs[0])
    col = d.outputs[0]
    if tint:
        m = N.new("ShaderNodeMix"); m.data_type = "RGBA"; m.blend_type = "MULTIPLY"; m.inputs[0].default_value = 1.0
        L.new(col, m.inputs[6]); m.inputs[7].default_value = tint; col = m.outputs[2]
    rough = None
    if maps.get("rough"):
        r = N.new("ShaderNodeTexImage"); r.image = _img(maps["rough"], "Non"); L.new(vec, r.inputs[0]); rough = r.outputs[0]
    elif maps.get("arm"):
        r = N.new("ShaderNodeTexImage"); r.image = _img(maps["arm"], "Non"); L.new(vec, r.inputs[0])
        sp = N.new("ShaderNodeSeparateColor"); L.new(r.outputs[0], sp.inputs[0]); rough = sp.outputs[1]
    if rough is not None and rough_mul != 1.0:
        mm = N.new("ShaderNodeMath"); mm.operation = "MULTIPLY"; mm.inputs[1].default_value = rough_mul; L.new(rough, mm.inputs[0]); rough = mm.outputs[0]
    nrm = None
    if maps.get("nor"):
        n = N.new("ShaderNodeTexImage"); n.image = _img(maps["nor"], "Non"); L.new(vec, n.inputs[0])
        nm = N.new("ShaderNodeNormalMap"); nm.inputs["Strength"].default_value = nstrength; L.new(n.outputs[0], nm.inputs["Color"]); nrm = nm.outputs[0]
    return col, rough, nrm


def aerial_group(color=(0.5, 0.4, 0.38), dist=3200.0, maxf=0.7):
    """Shader node group: mixes a surface toward haze colour with camera distance (atmospheric perspective)."""
    ng = bpy.data.node_groups.get("S3D_Aerial")
    if ng:
        return ng
    ng = bpy.data.node_groups.new("S3D_Aerial", "ShaderNodeTree")
    ng.interface.new_socket("Shader", in_out="INPUT", socket_type="NodeSocketShader")
    ng.interface.new_socket("Shader", in_out="OUTPUT", socket_type="NodeSocketShader")
    N, L = ng.nodes, ng.links
    gi, go = N.new("NodeGroupInput"), N.new("NodeGroupOutput")
    cam = N.new("ShaderNodeCameraData")
    k = N.new("ShaderNodeMath"); k.operation = "DIVIDE"; k.inputs[1].default_value = dist; L.new(cam.outputs["View Distance"], k.inputs[0])
    e = N.new("ShaderNodeMath"); e.operation = "EXPONENT"; neg = N.new("ShaderNodeMath"); neg.operation = "MULTIPLY"; neg.inputs[1].default_value = -1.0
    L.new(k.outputs[0], neg.inputs[0]); L.new(neg.outputs[0], e.inputs[0])
    f = N.new("ShaderNodeMath"); f.operation = "MULTIPLY_ADD"; f.inputs[1].default_value = -maxf; f.inputs[2].default_value = maxf
    L.new(e.outputs[0], f.inputs[0])                                   # f = maxf * (1 - exp(-d/dist))
    haze = N.new("ShaderNodeEmission"); haze.name = "haze"; haze.inputs["Color"].default_value = (*color, 1); haze.inputs["Strength"].default_value = 1.0
    mix = N.new("ShaderNodeMixShader"); L.new(f.outputs[0], mix.inputs[0]); L.new(gi.outputs[0], mix.inputs[1]); L.new(haze.outputs[0], mix.inputs[2])
    L.new(mix.outputs[0], go.inputs[0])
    return ng


def add_aerial(mat):
    nt = mat.node_tree; out = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")
    if not out.inputs[0].links:
        return
    src = out.inputs[0].links[0].from_socket
    if src.node.type == "GROUP" and src.node.node_tree and src.node.node_tree.name == "S3D_Aerial":
        return
    g = nt.nodes.new("ShaderNodeGroup"); g.node_tree = aerial_group()
    nt.links.new(src, g.inputs[0]); nt.links.new(g.outputs[0], out.inputs[0])


def _new_mat(name):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.use_nodes = True; nt = m.node_tree; nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial"); b = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(b.outputs[0], out.inputs[0])
    return m, nt, b


def asphalt_material(tex, width=7.0):
    """UV: x = metres across (0 = centre), y = metres along. Double yellow centre line, white edge lines, wear."""
    m, nt, b = _new_mat("road_asphalt"); N, L = nt.nodes, nt.links
    uv = N.new("ShaderNodeUVMap"); mp = N.new("ShaderNodeMapping"); mp.inputs["Scale"].default_value = (1 / 3.0, 1 / 3.0, 1)
    L.new(uv.outputs[0], mp.inputs[0])
    col, rough, nrm = _pbr_nodes(nt, _maps(tex["asphalt"]), mp.outputs[0], b, tint=(0.78, 0.76, 0.74, 1))
    sep = N.new("ShaderNodeSeparateXYZ"); L.new(uv.outputs[0], sep.inputs[0])
    absx = N.new("ShaderNodeMath"); absx.operation = "ABSOLUTE"; L.new(sep.outputs[0], absx.inputs[0])
    def band(lo, hi):
        a = N.new("ShaderNodeMath"); a.operation = "GREATER_THAN"; a.inputs[1].default_value = lo; L.new(absx.outputs[0], a.inputs[0])
        c = N.new("ShaderNodeMath"); c.operation = "LESS_THAN"; c.inputs[1].default_value = hi; L.new(absx.outputs[0], c.inputs[0])
        m_ = N.new("ShaderNodeMath"); m_.operation = "MULTIPLY"; L.new(a.outputs[0], m_.inputs[0]); L.new(c.outputs[0], m_.inputs[1])
        return m_.outputs[0]
    yellow = band(0.06, 0.17)
    e0 = width / 2 - 0.32; white = band(e0, e0 + 0.15)
    wear = N.new("ShaderNodeTexNoise"); wear.inputs["Scale"].default_value = 2.5; wear.inputs["Detail"].default_value = 8
    L.new(mp.outputs[0], wear.inputs[0])
    wr = N.new("ShaderNodeMapRange"); wr.inputs["From Min"].default_value = 0.35; wr.inputs["From Max"].default_value = 0.6
    L.new(wear.outputs["Fac"], wr.inputs["Value"])
    def paint(mask, rgb, mix_in):
        mm = N.new("ShaderNodeMath"); mm.operation = "MULTIPLY"; L.new(mask, mm.inputs[0]); L.new(wr.outputs[0], mm.inputs[1])
        mx = N.new("ShaderNodeMix"); mx.data_type = "RGBA"; L.new(mm.outputs[0], mx.inputs[0]); L.new(mix_in, mx.inputs[6])
        mx.inputs[7].default_value = rgb
        return mx.outputs[2], mm.outputs[0]
    c1, my = paint(yellow, (0.62, 0.42, 0.04, 1), col)
    c2, mw = paint(white, (0.72, 0.72, 0.7, 1), c1)
    L.new(c2, b.inputs["Base Color"])
    if rough is not None:
        L.new(rough, b.inputs["Roughness"])
    if nrm is not None:
        L.new(nrm, b.inputs["Normal"])
    return m


def tex_material(name, folder, scale=2.0, tint=None, proj="uv", rough_mul=1.0, nstrength=1.0):
    m, nt, b = _new_mat(name); N, L = nt.nodes, nt.links
    if proj == "uv":
        src = N.new("ShaderNodeUVMap").outputs[0]
    else:
        tc = N.new("ShaderNodeTexCoord"); src = tc.outputs["Object"]
    mp = N.new("ShaderNodeMapping"); mp.inputs["Scale"].default_value = (1 / scale, 1 / scale, 1 / scale); L.new(src, mp.inputs[0])
    col, rough, nrm = _pbr_nodes(nt, _maps(folder), mp.outputs[0], b, tint=tint, rough_mul=rough_mul, nstrength=nstrength)
    L.new(col, b.inputs["Base Color"])
    if rough is not None: L.new(rough, b.inputs["Roughness"])
    if nrm is not None: L.new(nrm, b.inputs["Normal"])
    return m


def terrain_material(tex):
    """Forest ground on gentle slopes, rock on steep faces (slope from the geometry normal), box-projected."""
    m, nt, b = _new_mat("terrain"); N, L = nt.nodes, nt.links
    tc = N.new("ShaderNodeTexCoord")
    def layer(folder, scale, tint):
        mp = N.new("ShaderNodeMapping"); mp.inputs["Scale"].default_value = (1 / scale,) * 3; L.new(tc.outputs["Object"], mp.inputs[0])
        mz = _maps(folder); outs = {}
        for key, cs in (("diff", "sRGB"), ("nor", "Non"), ("rough", "Non"), ("arm", "Non")):
            if mz.get(key):
                t = N.new("ShaderNodeTexImage"); t.image = _img(mz[key], cs); t.projection = "BOX"; t.projection_blend = 0.25
                L.new(mp.outputs[0], t.inputs[0]); outs[key] = t.outputs[0]
        if tint:
            mm = N.new("ShaderNodeMix"); mm.data_type = "RGBA"; mm.blend_type = "MULTIPLY"; mm.inputs[0].default_value = 1
            L.new(outs["diff"], mm.inputs[6]); mm.inputs[7].default_value = tint; outs["diff"] = mm.outputs[2]
        if "rough" not in outs and "arm" in outs:
            sp = N.new("ShaderNodeSeparateColor"); L.new(outs["arm"], sp.inputs[0]); outs["rough"] = sp.outputs[1]
        return outs
    g = layer(tex["ground"], 2.5, (1.0, 1.0, 1.0, 1)); r = layer(tex["rock"], 4.0, (0.9, 0.86, 0.82, 1))
    # mountain meadow: olive / dry-gold grass colour field, texture kept only as detail (overlay)
    gn = N.new("ShaderNodeTexNoise"); gn.inputs["Scale"].default_value = 0.05; gn.inputs["Detail"].default_value = 6
    L.new(tc.outputs["Object"], gn.inputs[0])
    gr = N.new("ShaderNodeValToRGB"); el = gr.color_ramp.elements
    el[0].position = 0.35; el[0].color = (0.055, 0.07, 0.025, 1); el[1].position = 0.7; el[1].color = (0.24, 0.19, 0.08, 1)
    L.new(gn.outputs["Fac"], gr.inputs[0])
    ov = N.new("ShaderNodeMix"); ov.data_type = "RGBA"; ov.blend_type = "OVERLAY"; ov.inputs[0].default_value = 0.85
    L.new(gr.outputs[0], ov.inputs[6]); L.new(g["diff"], ov.inputs[7]); g["diff"] = ov.outputs[2]
    # macro variation so the far forest floor is not a flat beige: darker green-brown patches (scale ~60 m)
    mac = N.new("ShaderNodeTexNoise"); mac.inputs["Scale"].default_value = 0.016; mac.inputs["Detail"].default_value = 3
    L.new(tc.outputs["Object"], mac.inputs[0])
    mr = N.new("ShaderNodeMix"); mr.data_type = "RGBA"; mr.blend_type = "MULTIPLY"; mr.inputs[0].default_value = 1.0
    ramp = N.new("ShaderNodeValToRGB"); ramp.color_ramp.elements[0].color = (0.32, 0.36, 0.24, 1); ramp.color_ramp.elements[1].color = (1.0, 0.95, 0.85, 1)
    L.new(mac.outputs["Fac"], ramp.inputs[0]); L.new(g["diff"], mr.inputs[6]); L.new(ramp.outputs[0], mr.inputs[7]); g["diff"] = mr.outputs[2]
    geo = N.new("ShaderNodeNewGeometry"); sep = N.new("ShaderNodeSeparateXYZ"); L.new(geo.outputs["Normal"], sep.inputs[0])
    noise = N.new("ShaderNodeTexNoise"); noise.inputs["Scale"].default_value = 0.15; L.new(tc.outputs["Object"], noise.inputs[0])
    add = N.new("ShaderNodeMath"); add.operation = "MULTIPLY_ADD"; add.inputs[1].default_value = 0.25; L.new(noise.outputs["Fac"], add.inputs[0]); L.new(sep.outputs[2], add.inputs[2])
    fac = N.new("ShaderNodeMapRange"); fac.inputs["From Min"].default_value = 0.92; fac.inputs["From Max"].default_value = 0.78
    L.new(add.outputs[0], fac.inputs["Value"])                         # steep (normal.z small) -> 1 = rock
    def mix(a, bb, dt="RGBA"):
        mx = N.new("ShaderNodeMix"); mx.data_type = dt; L.new(fac.outputs[0], mx.inputs[0])
        if dt == "RGBA": L.new(a, mx.inputs[6]); L.new(bb, mx.inputs[7]); return mx.outputs[2]
        if dt == "FLOAT": L.new(a, mx.inputs[2]); L.new(bb, mx.inputs[3]); return mx.outputs[0]
        L.new(a, mx.inputs[4]); L.new(bb, mx.inputs[5]); return mx.outputs[1]
    L.new(mix(g["diff"], r["diff"]), b.inputs["Base Color"])
    L.new(mix(g["rough"], r["rough"], "FLOAT"), b.inputs["Roughness"])
    nmix = mix(g["nor"], r["nor"])
    nm = N.new("ShaderNodeNormalMap"); nm.inputs["Strength"].default_value = 1.0; L.new(nmix, nm.inputs["Color"]); L.new(nm.outputs[0], b.inputs["Normal"])
    return m


def metal_material(name="guardrail_steel"):
    m, nt, b = _new_mat(name)
    core.set_input(b, "Base Color", (0.55, 0.56, 0.57, 1)); core.set_input(b, "Metallic", 1.0); core.set_input(b, "Roughness", 0.38)
    n = nt.nodes.new("ShaderNodeTexNoise"); n.inputs["Scale"].default_value = 30; n.inputs["Detail"].default_value = 10
    r = nt.nodes.new("ShaderNodeMapRange"); r.inputs["To Min"].default_value = 0.28; r.inputs["To Max"].default_value = 0.55
    nt.links.new(n.outputs["Fac"], r.inputs["Value"]); nt.links.new(r.outputs[0], b.inputs["Roughness"])
    return m


# ------------------------------------------------------------------ geometry
def _mesh(name, verts, faces, coll, uvs=None, mat=None, smooth=True):
    me = bpy.data.meshes.new(name); me.from_pydata([tuple(v) for v in verts], [], faces); me.update()
    if uvs is not None:
        uvl = me.uv_layers.new(name="UVMap")
        for poly in me.polygons:
            for li in poly.loop_indices:
                uvl.data[li].uv = uvs[me.loops[li].vertex_index]
    if smooth:
        me.shade_smooth() if hasattr(me, "shade_smooth") else None
    if mat:
        me.materials.append(mat)
    ob = bpy.data.objects.new(name, me); coll.objects.link(ob)
    return ob


def road_mesh(R, coll, s0, s1, mat_asphalt, mat_gravel, step=1.0):
    """Asphalt (with 2 cm crown) + gravel shoulders as one ribbon, UV in metres."""
    w, sh = R.width / 2, R.shoulder
    xs = [-(w + sh), -w - 0.02, -w + 0.4, 0.0, w - 0.4, w + 0.02, w + sh]
    zs = [-0.05, -0.015, 0.0, 0.02, 0.0, -0.015, -0.05]
    verts, uvs, faces = [], [], []
    ss = np.arange(s0, s1 + step, step); n = len(xs)
    for s in ss:
        p = R.path.point(s); t = R.path.tangent(s); left = Vector((-t.y, t.x, 0)).normalized()
        for x, z in zip(xs, zs):
            verts.append(p + left * x + Vector((0, 0, z))); uvs.append((x, s))
    asph, grav = [], []
    for i in range(len(ss) - 1):
        for j in range(n - 1):
            a = i * n + j; q = (a, a + n, a + n + 1, a + 1)
            (asph if 0 < j < n - 2 else grav).append(q)
    ob = _mesh("road", verts, asph + grav, coll, uvs, None)
    ob.data.materials.append(mat_asphalt); ob.data.materials.append(mat_gravel)
    for k, poly in enumerate(ob.data.polygons):
        poly.material_index = 0 if k < len(asph) else 1
    return ob


NEAR = [0, 0.15, 0.35, 0.55, 0.8, 1.05, 1.3, 1.6, 2.0, 2.4, 2.9, 3.5, 4.2, 5.0, 6.0, 7.2, 8.6, 10.2, 12.2, 14.6, 17.5,
        21.0, 25.5, 31.0, 37.0, 44.0]


def near_strip(R, coll, s0, s1, mat=None, step=1.0):
    """Fine terrain in road coordinates (both sides, edge .. edge+44 m): smooth ditch, rock cut and fill slopes."""
    e = R.edge(); ds = [-(e + a) for a in reversed(NEAR)] + [e + a for a in NEAR]; n = len(ds)
    verts = []; ss = np.arange(s0, s1 + step, step)
    for s in ss:
        p = R.path.point(s); t = R.path.tangent(s); left = Vector((-t.y, t.x, 0)).normalized()
        for d in ds:
            q = p + left * d; verts.append((q.x, q.y, R.height(q.x, q.y)))
    faces = [(i * n + j, (i + 1) * n + j, (i + 1) * n + j + 1, i * n + j + 1)
             for i in range(len(ss) - 1) for j in range(n - 1) if not (j == n // 2 - 1)]      # no faces across the road
    return _mesh("terrain_near", verts, faces, coll, None, mat)


def terrain_mesh(R, coll, center, size=700.0, res=2.0, mat=None, corridor=42.0, s_range=None):
    """Height-field grid (world XY) conforming to the road; faces inside the near-strip corridor are left out."""
    cx, cy = center; n = int(size / res) + 1
    xs = np.linspace(cx - size / 2, cx + size / 2, n); ys = np.linspace(cy - size / 2, cy + size / 2, n)
    verts, inside = [], []
    for y in ys:
        for x in xs:
            s, d, zr = R.local(x, y)
            inside.append(abs(d) - R.edge() < corridor and (s_range is None or s_range[0] + 2 <= s <= s_range[1] - 2))
            verts.append((x, y, R.height(x, y)))
    faces = []
    for j in range(n - 1):
        for i in range(n - 1):
            q = (j * n + i, j * n + i + 1, (j + 1) * n + i + 1, (j + 1) * n + i)
            if not all(inside[k] for k in q):
                faces.append(q)
    return _mesh("terrain", verts, faces, coll, None, mat)


def guardrail(R, coll, s0, s1, side=-1, offset=0.55, mat=None, post_every=2.0):
    """W-beam steel rail + posts along the road edge on `side` (-1 = right of travel)."""
    e = R.width / 2 + offset
    prof = [(0.0, 0.50), (0.035, 0.53), (0.0, 0.58), (0.035, 0.63), (0.0, 0.66), (0.012, 0.66), (0.047, 0.63), (0.012, 0.58),
            (0.047, 0.53), (0.012, 0.50)]                       # (outward offset, height)
    verts, faces = [], []; ss = np.arange(s0, s1, 0.5); n = len(prof)
    for s in ss:
        p = R.path.point(s); t = R.path.tangent(s); left = Vector((-t.y, t.x, 0)).normalized()
        out = left * side
        for o, h in prof:
            verts.append(p + out * (e + o) + Vector((0, 0, h)))
    for i in range(len(ss) - 1):
        for j in range(n):
            a, b = i * n + j, i * n + (j + 1) % n
            faces.append((a, b, b + n, a + n))
    rail = _mesh("guardrail", verts, faces, coll, None, mat)
    # posts: small I-beam boxes
    pv, pf = [], []
    for s in np.arange(s0, s1, post_every):
        p = R.path.point(s); t = R.path.tangent(s); left = Vector((-t.y, t.x, 0)).normalized(); out = left * side
        base = p + out * (e + 0.12) + Vector((0, 0, -0.4))
        for dx, dy, dz in ((-0.05, -0.08, 0), (0.05, -0.08, 0), (0.05, 0.08, 0), (-0.05, 0.08, 0)):
            pass
        k = len(pv)
        u, v = out * 0.05, t * 0.08
        for z in (0.0, 1.05):
            for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                pv.append(base + u * a + v * b + Vector((0, 0, z)))
        pf += [(k, k + 1, k + 2, k + 3), (k + 4, k + 7, k + 6, k + 5), (k, k + 4, k + 5, k + 1), (k + 1, k + 5, k + 6, k + 2),
               (k + 2, k + 6, k + 7, k + 3), (k + 3, k + 7, k + 4, k)]
    posts = _mesh("guardrail_posts", pv, pf, coll, None, mat, smooth=False)
    return rail, posts


def outer_terrain(R, coll, center, inner=1000.0, size=9000.0, res=60.0, mat=None):
    """Coarse terrain + mountain ridges around the detailed area (overlaps under it, 2 m lower, no cracks)."""
    cx, cy = center; n = int(size / res) + 1
    xs = np.linspace(cx - size / 2, cx + size / 2, n); ys = np.linspace(cy - size / 2, cy + size / 2, n)
    verts = []
    for y in ys:
        for x in xs:
            z = R.height(x, y)
            if abs(x - cx) < inner / 2 - res and abs(y - cy) < inner / 2 - res:
                z -= 6.0
            verts.append((x, y, z))
    faces = [(j * n + i, j * n + i + 1, (j + 1) * n + i + 1, (j + 1) * n + i) for j in range(n - 1) for i in range(n - 1)]
    return _mesh("terrain_outer", verts, faces, coll, None, mat)


def mountains(coll, center, r0=1100.0, r1=6000.0, seed=5, mat=None, base_z=-60.0):
    """Distant ridges: ring height-field, peaks 250-900 m, fading into haze."""
    rnd = core.Noise1D(seed); nr, na = 40, 360
    verts, faces = [], []
    for i in range(nr):
        r = r0 + (r1 - r0) * (i / (nr - 1)) ** 1.6
        for j in range(na):
            a = 2 * math.pi * j / na
            ridge = abs(rnd.fbm(a * 9.0 + i * 0.13, 4)) ; h = (1 - ridge) ** 2.2
            z = base_z + (350 + 900 * (0.5 + 0.5 * rnd(a * 3 + 40))) * h * min(1.0, (r - r0) / 500 + 0.25)
            verts.append((center[0] + r * math.cos(a), center[1] + r * math.sin(a), z))
    for i in range(nr - 1):
        for j in range(na):
            a, b = i * na + j, i * na + (j + 1) % na
            faces.append((a, b, b + na, a + na))
    return _mesh("mountains", verts, faces, coll, None, mat)


def load_nature(path):
    """Append the trees/rocks library (collections tree_*, rock_*) once; returns {name: collection}."""
    have = {c.name: c for c in bpy.data.collections if c.name.startswith(("tree_", "rock_"))}
    if have:
        return have
    with bpy.data.libraries.load(path, link=False) as (src, dst):
        dst.collections = [c for c in src.collections if c.startswith(("tree_", "rock_"))]
    return {c.name: c for c in dst.collections}


def scatter(R, coll, lib, center, size, count, seed=7, kinds=("tree_fir_c", "tree_fir_c", "tree_sap_a", "tree_sap_b", "tree_sap_c"),
            min_road=4.5, max_slope=0.9, scale=(0.8, 1.6), side=None, near=None, rock=False, max_road=None, surfaces=()):
    """Collection instances on the terrain (outside the road corridor / steep rock), random yaw + scale."""
    rnd = random.Random(seed); made = 0; tries = 0
    holder = core.ensure_coll(coll.name + ("_rocks" if rock else "_trees"), coll)
    while made < count and tries < count * 30:
        tries += 1
        x = center[0] + (rnd.random() - 0.5) * size; y = center[1] + (rnd.random() - 0.5) * size
        s, d, zr = R.local(x, y)
        a = abs(d) - R.edge()
        if a < min_road or (side is not None and (d * R.cut_side > 0) != (side > 0)):
            continue
        if near and not (near[0] <= a <= near[1]):
            continue
        if max_road and a > max_road * (0.4 + 0.6 * rnd.random() ** 0.5):
            continue
        z = R.height(x, y); zx = R.height(x + 1.0, y) - z; zy = R.height(x, y + 1.0) - z
        if math.hypot(zx, zy) > max_slope:
            continue
        if not rock and min(R.height(x + dx, y + dy) for dx, dy in ((4, 0), (-4, 0), (0, 4), (0, -4), (2, 0), (-2, 0), (0, 2), (0, -2))) < z - 0.7:
            continue                                   # on a cut/fill rim the root ball would hang in the air
        k = rnd.choice(kinds); c = lib.get(k)
        if not c:
            continue
        e = bpy.data.objects.new(f"{k}_i{made}", None); e.instance_type = "COLLECTION"; e.instance_collection = c
        sc = rnd.uniform(*scale); e.scale = (sc, sc, sc * rnd.uniform(0.9, 1.15))
        e.rotation_euler = (rnd.uniform(-0.04, 0.04), rnd.uniform(-0.04, 0.04), rnd.uniform(0, 2 * math.pi))
        if surfaces:
            hits = [h[0].z for h in (bv.ray_cast(Vector((x, y, z + 300)), Vector((0, 0, -1))) for bv in surfaces) if h[0] is not None]
            if not hits:
                continue
            z = max(hits)
        e.location = (x, y, z - (0.45 if not rock else 0.25 * sc)); holder.objects.link(e); made += 1
    return made


def canopy_material(name="far_forest"):
    """Forest seen from far away: dark crowns with sunlit tops and gaps (procedural), used beyond the instanced trees."""
    m, nt, b = _new_mat(name); N, L = nt.nodes, nt.links
    tc = N.new("ShaderNodeTexCoord"); vo = N.new("ShaderNodeTexVoronoi"); vo.inputs["Scale"].default_value = 0.22
    L.new(tc.outputs["Object"], vo.inputs[0])
    nz = N.new("ShaderNodeTexNoise"); nz.inputs["Scale"].default_value = 0.01; nz.inputs["Detail"].default_value = 4
    L.new(tc.outputs["Object"], nz.inputs[0])
    ramp = N.new("ShaderNodeValToRGB"); el = ramp.color_ramp.elements
    el[0].position = 0.0; el[0].color = (0.035, 0.05, 0.022, 1); el[1].position = 0.9; el[1].color = (0.006, 0.01, 0.006, 1)
    L.new(vo.outputs["Distance"], ramp.inputs[0])
    mx = N.new("ShaderNodeMix"); mx.data_type = "RGBA"; mx.blend_type = "MULTIPLY"; mx.inputs[0].default_value = 1.0
    r2 = N.new("ShaderNodeMapRange"); r2.inputs["To Min"].default_value = 0.6; r2.inputs["To Max"].default_value = 1.5
    L.new(nz.outputs["Fac"], r2.inputs["Value"]); cc = N.new("ShaderNodeCombineColor")
    for i in range(3): L.new(r2.outputs[0], cc.inputs[i])
    L.new(ramp.outputs[0], mx.inputs[6]); L.new(cc.outputs[0], mx.inputs[7]); L.new(mx.outputs[2], b.inputs["Base Color"])
    core.set_input(b, "Roughness", 0.85)
    bump = N.new("ShaderNodeBump"); bump.inputs["Strength"].default_value = 0.6; bump.inputs["Distance"].default_value = 2.0
    L.new(vo.outputs["Distance"], bump.inputs["Height"]); L.new(bump.outputs[0], b.inputs["Normal"])
    return m


def build(R, coll, tex, nature=None, s0=-150, s1=450, terrain_center=None, terrain_size=900.0, terrain_res=2.5,
          trees=9000, rail=True, haze=None, seed=3):
    if haze:
        g = aerial_group(); g.nodes["haze"].inputs["Color"].default_value = (*haze, 1)
    am = asphalt_material(tex, R.width); gm = tex_material("road_gravel", tex["gravel"], scale=1.5, tint=(0.8, 0.78, 0.75, 1))
    road = road_mesh(R, coll, s0, s1, am, gm)
    tm = terrain_material(tex)
    c = terrain_center or tuple(R.path.point((s0 + s1) / 2))[:2]
    near = near_strip(R, coll, s0, s1, tm)
    ter = terrain_mesh(R, coll, c, terrain_size, terrain_res, tm, s_range=(s0, s1))
    out = {"road": road, "terrain": ter, "near": near}
    if rail:
        rm = metal_material(); out["rail"] = guardrail(R, coll, s0, s1, side=-R.cut_side, mat=rm)
    fm = canopy_material()
    out["outer"] = outer_terrain(R, coll, c, inner=terrain_size, mat=fm)
    if nature:
        from . import nature as NA
        lib = NA.Lib(nature)
        out["eco"] = NA.ecosystem(R, lib, coll, c, terrain_size, [near, ter], seed=seed, trees=trees, s_range=(s0 + 2, s1 - 2))
        NA.enhance(lib, aerial=add_aerial)
    for m in (tm, fm, am, gm):
        add_aerial(m)
    return out
