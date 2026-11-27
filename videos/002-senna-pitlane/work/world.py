"""Donington Park (1993 GP layout) from OpenStreetMap + Environment Agency 1 m LIDAR.
Local frame: X east, Y north, Z up (metres, relative to the DEM at the origin). Import inside Blender:
    import world; W = world.World(video_dir); W.build_all()
Everything is real scale: road widths, heights from the laser terrain, barriers/fences/buildings from the map."""
import bpy, bmesh, json, math, os, glob, random
import numpy as np
from mathutils import Vector, Matrix, noise
from mathutils.geometry import tessellate_polygon

TEX = os.path.expanduser("~/.shorts3d/ph_tex")
PH = os.path.expanduser("~/.shorts3d/polyhaven")
HDRI = os.path.expanduser("~/.shorts3d/polyhaven_hdri/overcast_soil_puresky_2k.hdr")

# road widths (m) - Donington GP circuit ~10-11 m, 1993 pit lane incl. working lane ~14 m
WIDTH = {"raceway": 10.5, "pitlane": 12.0, "old_entry": 8.5, "service": 5.0}
TRACK_WAYS = ["Melbourne Loop (down)", "Melbourne Hairpin", "Melbourne Loop (up)", "Goddards", "Wheatcroft Straight",
              "Redgate", "Hollywood", "Craner Curves", "Old Hairpin", "Starkey's Bridge", "McLeans", "Coppice",
              "Fogarty's", "Roberts"]


def coll(name, parent=None):
    c = bpy.data.collections.get(name) or bpy.data.collections.new(name)
    if c.name not in [x.name for x in (parent or bpy.context.scene.collection).children]:
        (parent or bpy.context.scene.collection).children.link(c)
    return c


def mesh_obj(name, verts, faces, c, uvs=None, mat=None, smooth=True, uv_name="UVMap", extra_uv=None):
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(v) for v in verts], [], [tuple(f) for f in faces])
    if uvs is not None:
        uv = me.uv_layers.new(name=uv_name); flat = []
        for poly in me.polygons:
            for li in poly.loop_indices:
                flat.append(uvs[me.loops[li].vertex_index])
        uv.data.foreach_set("uv", np.array(flat, np.float32).ravel())
    if extra_uv is not None:
        uv2 = me.uv_layers.new(name="UV2"); flat = []
        for poly in me.polygons:
            for li in poly.loop_indices:
                flat.append(extra_uv[me.loops[li].vertex_index])
        uv2.data.foreach_set("uv", np.array(flat, np.float32).ravel())
    if smooth:
        me.polygons.foreach_set("use_smooth", [True] * len(me.polygons))
    me.update()
    o = bpy.data.objects.new(name, me); c.objects.link(o)
    if mat: me.materials.append(mat)
    return o


# ------------------------------------------------------------------ materials
def _img(path, nonc=False):
    im = bpy.data.images.load(path, check_existing=True)
    if nonc:
        im.colorspace_settings.name = "Non-Color"
    return im


def _tex_set(nt, folder, vec, scale):
    N, L = nt.nodes, nt.links
    f = lambda tag: (glob.glob(os.path.join(TEX, folder, f"*_{tag}_*")) or [None])[0]
    mp = N.new("ShaderNodeMapping"); mp.inputs["Scale"].default_value = (1 / scale, 1 / scale, 1 / scale); L.new(vec, mp.inputs[0])
    d = N.new("ShaderNodeTexImage"); d.image = _img(f("diff")); L.new(mp.outputs[0], d.inputs[0])
    r = N.new("ShaderNodeTexImage"); r.image = _img(f("rough"), True); L.new(mp.outputs[0], r.inputs[0])
    n = N.new("ShaderNodeTexImage"); n.image = _img(f("nor_gl"), True); L.new(mp.outputs[0], n.inputs[0])
    nm = N.new("ShaderNodeNormalMap"); L.new(n.outputs[0], nm.inputs["Color"])
    return d.outputs[0], r.outputs[0], nm.outputs[0], mp


def new_mat(name):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.use_nodes = True; nt = m.node_tree; nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial"); b = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(b.outputs[0], out.inputs[0])
    return m, nt, b


def math_node(nt, op, a, b=None, clamp=False):
    n = nt.nodes.new("ShaderNodeMath"); n.operation = op; n.use_clamp = clamp
    for i, v in enumerate((a, b)):
        if v is None: continue
        if isinstance(v, (int, float)): n.inputs[i].default_value = v
        else: nt.links.new(v, n.inputs[i])
    return n.outputs[0]


def mix_rgb(nt, fac, a, b, blend="MIX"):
    n = nt.nodes.new("ShaderNodeMix"); n.data_type = "RGBA"; n.blend_type = blend
    for sock, v in ((0, fac), (6, a), (7, b)):
        if isinstance(v, (int, float)): n.inputs[sock].default_value = v
        elif isinstance(v, tuple): n.inputs[sock].default_value = v
        else: nt.links.new(v, n.inputs[sock])
    return n.outputs[2]


def rain_ripples(nt, scale=6.0):
    """Animated rain-drop ripple normal (4D voronoi, W = scene time)."""
    N, L = nt.nodes, nt.links
    tc = N.new("ShaderNodeTexCoord")
    v = N.new("ShaderNodeTexVoronoi"); v.voronoi_dimensions = "4D"; v.feature = "F1"; v.inputs["Scale"].default_value = scale
    L.new(tc.outputs["Object"], v.inputs["Vector"])
    drv = v.inputs["W"].driver_add("default_value"); drv.driver.expression = "frame/9.0"
    ring = math_node(nt, "SINE", math_node(nt, "MULTIPLY", v.outputs["Distance"], 40.0))
    fade = math_node(nt, "SUBTRACT", 1.0, math_node(nt, "MULTIPLY", v.outputs["Distance"], 2.2), clamp=True)
    h = math_node(nt, "MULTIPLY", ring, fade)
    bump = N.new("ShaderNodeBump"); bump.inputs["Strength"].default_value = 0.08; bump.inputs["Distance"].default_value = 0.01
    L.new(h, bump.inputs["Height"])
    return bump


def asphalt_mat(name="asphalt_wet", lines=True, width=10.5, kerb=False):
    """Wet track asphalt: dark, puddles (mirror), rubbered racing line, white edge lines, ripples from rain."""
    m, nt, b = new_mat(name); N, L = nt.nodes, nt.links
    uv = N.new("ShaderNodeUVMap"); uv.uv_map = "UVMap"
    col, rough, nrm, mp = _tex_set(nt, "asphalt_track", uv.outputs[0], 4.0)
    sep = N.new("ShaderNodeSeparateXYZ"); L.new(uv.outputs[0], sep.inputs[0])
    ax = math_node(nt, "ABSOLUTE", sep.outputs[0])
    # wet darkening + puddle mask (low frequency noise, more puddles near the edges/camber)
    pn = N.new("ShaderNodeTexNoise"); pn.inputs["Scale"].default_value = 0.09; pn.inputs["Detail"].default_value = 3
    tc = N.new("ShaderNodeTexCoord"); L.new(tc.outputs["Object"], pn.inputs["Vector"])
    pr = N.new("ShaderNodeMapRange"); pr.inputs["From Min"].default_value = 0.52; pr.inputs["From Max"].default_value = 0.6
    L.new(pn.outputs["Fac"], pr.inputs["Value"]); puddle = pr.outputs[0]
    wet = mix_rgb(nt, 0.55, col, (0.0, 0.0, 0.0, 1), "MULTIPLY")
    # racing line: darker rubber band roughly in the middle third
    rl = math_node(nt, "SUBTRACT", 1.0, math_node(nt, "MULTIPLY", ax, 1.0 / (width * 0.28)), clamp=True)
    c1 = mix_rgb(nt, math_node(nt, "MULTIPLY", rl, 0.35), wet, (0.01, 0.01, 0.011, 1))
    out_col = c1
    if lines:
        e0 = width / 2 - 0.45
        a = math_node(nt, "GREATER_THAN", ax, e0); bb = math_node(nt, "LESS_THAN", ax, e0 + 0.18)
        line = math_node(nt, "MULTIPLY", a, bb)
        out_col = mix_rgb(nt, line, c1, (0.62, 0.62, 0.6, 1))
    L.new(mix_rgb(nt, math_node(nt, "MULTIPLY", puddle, 0.6), out_col, (0.004, 0.004, 0.005, 1)), b.inputs["Base Color"])
    # roughness: wet film ~0.12, puddles mirror 0.02, dry-ish texture variation
    r1 = math_node(nt, "MULTIPLY", rough, 0.28)
    r2 = mix_rgb(nt, puddle, r1, (0.015, 0.015, 0.015, 1))
    L.new(r2, b.inputs["Roughness"])
    rip = rain_ripples(nt); L.new(nrm, rip.inputs["Normal"]); L.new(rip.outputs[0], b.inputs["Normal"])
    b.inputs["Specular IOR Level"].default_value = 0.6
    return m


def kerb_mat():
    m, nt, b = new_mat("kerb"); N, L = nt.nodes, nt.links
    uv = N.new("ShaderNodeUVMap")
    sep = N.new("ShaderNodeSeparateXYZ"); L.new(uv.outputs[0], sep.inputs[0])
    stripe = math_node(nt, "GREATER_THAN", math_node(nt, "FRACT", math_node(nt, "MULTIPLY", sep.outputs[1], 0.5)), 0.5)
    c = mix_rgb(nt, stripe, (0.55, 0.03, 0.02, 1), (0.75, 0.75, 0.72, 1))
    dirt = N.new("ShaderNodeTexNoise"); dirt.inputs["Scale"].default_value = 3.0
    L.new(uv.outputs[0], dirt.inputs["Vector"])
    L.new(mix_rgb(nt, math_node(nt, "MULTIPLY", dirt.outputs["Fac"], 0.5), c, (0.05, 0.05, 0.05, 1)), b.inputs["Base Color"])
    b.inputs["Roughness"].default_value = 0.12
    return m


def grass_mat(mask_img=None, mask_rect=None):
    """Wet April grass: green verge with texture detail, worn brown patches, gravel traps from a land-cover mask."""
    m, nt, b = new_mat("grass_wet"); N, L = nt.nodes, nt.links
    tc = N.new("ShaderNodeTexCoord")
    gcol, grough, gnrm, _ = _tex_set(nt, "leafy_grass", tc.outputs["Object"], 2.2)
    scol, srough, snrm, _ = _tex_set(nt, "sparse_grass", tc.outputs["Object"], 5.0)
    lum = N.new("ShaderNodeRGBToBW"); L.new(gcol, lum.inputs[0])
    var = N.new("ShaderNodeTexNoise"); var.inputs["Scale"].default_value = 0.08; var.inputs["Detail"].default_value = 4
    L.new(tc.outputs["Object"], var.inputs["Vector"])
    green = mix_rgb(nt, var.outputs["Fac"], (0.028, 0.055, 0.012, 1), (0.06, 0.085, 0.022, 1))
    det = math_node(nt, "ADD", math_node(nt, "MULTIPLY", lum.outputs[0], 1.6), 0.35)
    col = mix_rgb(nt, 1.0, green, det, "MULTIPLY")
    big = N.new("ShaderNodeTexNoise"); big.inputs["Scale"].default_value = 0.025; big.inputs["Detail"].default_value = 3
    L.new(tc.outputs["Object"], big.inputs["Vector"])
    br = N.new("ShaderNodeMapRange"); br.inputs["From Min"].default_value = 0.56; br.inputs["From Max"].default_value = 0.68
    L.new(big.outputs["Fac"], br.inputs["Value"])
    worn = mix_rgb(nt, 0.6, scol, (0.0, 0.0, 0.0, 1), "MULTIPLY")
    col = mix_rgb(nt, math_node(nt, "MULTIPLY", br.outputs[0], 0.7), col, worn)
    rough = mix_rgb(nt, 0.6, grough, (0.38, 0.38, 0.38, 1))
    if mask_img:
        sep = N.new("ShaderNodeSeparateXYZ"); L.new(tc.outputs["Object"], sep.inputs[0])
        x0, y0, w, h = mask_rect
        u = math_node(nt, "DIVIDE", math_node(nt, "SUBTRACT", sep.outputs[0], x0), w)
        v = math_node(nt, "DIVIDE", math_node(nt, "SUBTRACT", sep.outputs[1], y0), h)
        cv = N.new("ShaderNodeCombineXYZ"); L.new(u, cv.inputs[0]); L.new(v, cv.inputs[1])
        mt = N.new("ShaderNodeTexImage"); mt.image = _img(mask_img, True); mt.extension = "CLIP"; L.new(cv.outputs[0], mt.inputs[0])
        ms = N.new("ShaderNodeSeparateColor"); L.new(mt.outputs[0], ms.inputs[0])
        vcol, vrough, vnrm, _ = _tex_set(nt, "gravel_stones", tc.outputs["Object"], 1.6)
        gv = mix_rgb(nt, 0.45, vcol, (0.0, 0.0, 0.0, 1), "MULTIPLY")
        col = mix_rgb(nt, ms.outputs[0], col, gv)
        rough = mix_rgb(nt, ms.outputs[0], rough, math_node(nt, "MULTIPLY", vrough, 0.6))
        nm = N.new("ShaderNodeMix"); nm.data_type = "VECTOR"; L.new(ms.outputs[0], nm.inputs[0]); L.new(gnrm, nm.inputs[4]); L.new(vnrm, nm.inputs[5])
        L.new(nm.outputs[1], b.inputs["Normal"])
    else:
        L.new(gnrm, b.inputs["Normal"])
    L.new(col, b.inputs["Base Color"]); L.new(rough, b.inputs["Roughness"])
    b.inputs["Specular IOR Level"].default_value = 0.55
    return m


def tex_mat(name, folder, scale, tint=None, rough_mul=1.0, proj="object"):
    m, nt, b = new_mat(name); N, L = nt.nodes, nt.links
    tc = N.new("ShaderNodeTexCoord"); src = tc.outputs["Object"] if proj == "object" else N.new("ShaderNodeUVMap").outputs[0]
    col, rough, nrm, _ = _tex_set(nt, folder, src, scale)
    if tint: col = mix_rgb(nt, 1.0, col, tint, "MULTIPLY")
    L.new(col, b.inputs["Base Color"]); L.new(math_node(nt, "MULTIPLY", rough, rough_mul), b.inputs["Roughness"]); L.new(nrm, b.inputs["Normal"])
    return m


def flat_mat(name, rgb, rough=0.5, metal=0.0, emit=None, emit_s=0.0, alpha=None):
    m, nt, b = new_mat(name)
    b.inputs["Base Color"].default_value = (*rgb, 1); b.inputs["Roughness"].default_value = rough; b.inputs["Metallic"].default_value = metal
    if emit:
        b.inputs["Emission Color"].default_value = (*emit, 1); b.inputs["Emission Strength"].default_value = emit_s
    if alpha is not None:
        b.inputs["Alpha"].default_value = alpha
    return m


# ------------------------------------------------------------------ world
class World:
    def __init__(self, vd):
        self.vd = vd
        g = json.load(open(os.path.join(vd, "data", "geo.json")))
        self.geo = g; self.ways = g["ways"]
        d = g["dem"]; self.dem = np.load(os.path.join(vd, "data", "dem_local.npy")); self.dx0, self.dy0 = d["x0"], d["y0"]
        self.C = coll("WORLD")
        self.roads = {}      # name -> dict(pts (N,3), width, kind)

    # --- terrain height (bilinear, metres)
    def h(self, x, y):
        X = np.asarray(x, np.float64) - self.dx0; Y = np.asarray(y, np.float64) - self.dy0
        H, W = self.dem.shape
        X = np.clip(X, 0, W - 1.001); Y = np.clip(Y, 0, H - 1.001)
        x0 = np.floor(X).astype(int); y0 = np.floor(Y).astype(int); fx = X - x0; fy = Y - y0
        D = self.dem
        return (D[y0, x0] * (1 - fx) * (1 - fy) + D[y0, x0 + 1] * fx * (1 - fy) + D[y0 + 1, x0] * (1 - fx) * fy + D[y0 + 1, x0 + 1] * fx * fy)

    def way(self, name=None, wid=None, kind=None):
        for w in self.ways:
            if (name and w["name"] == name) or (wid and w["id"] == wid):
                return w
        return None

    # --- polyline utilities
    @staticmethod
    def resample(P, step=1.0):
        P = np.asarray(P, np.float64)
        seg = np.linalg.norm(np.diff(P, axis=0), axis=1); s = np.concatenate([[0], np.cumsum(seg)])
        n = max(2, int(s[-1] / step) + 1); t = np.linspace(0, s[-1], n)
        return np.stack([np.interp(t, s, P[:, i]) for i in range(P.shape[1])], 1)

    @staticmethod
    def smooth_line(P, sigma=2.0, keep_ends=True):
        P = np.asarray(P, np.float64); n = len(P)
        if n < 5: return P
        r = int(3 * sigma); k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2); k /= k.sum()
        pad = np.concatenate([np.repeat(P[:1], r, 0), P, np.repeat(P[-1:], r, 0)])
        out = np.stack([np.convolve(pad[:, i], k, "valid") for i in range(P.shape[1])], 1)
        if keep_ends: out[0], out[-1] = P[0], P[-1]
        return out

    def profile(self, P2, width):
        """Road height along a 2D centreline: median over the road width, smoothed (real road = smooth surface)."""
        P2 = np.asarray(P2); t = np.gradient(P2, axis=0); t /= np.linalg.norm(t, axis=1, keepdims=True) + 1e-9
        nrm = np.stack([-t[:, 1], t[:, 0]], 1)
        samples = [self.h(P2[:, 0] + nrm[:, 0] * o, P2[:, 1] + nrm[:, 1] * o) for o in np.linspace(-width / 2, width / 2, 7)]
        z = np.median(np.stack(samples), 0)
        return self.smooth_line(z[:, None], sigma=6.0, keep_ends=False)[:, 0]

    # --- roads
    def add_road(self, key, pts2, width, kind, z_bias=0.0):
        P = self.smooth_line(self.resample(pts2, 1.0), 1.5)
        z = self.profile(P, width) + z_bias
        self.roads[key] = {"P": np.column_stack([P, z]), "w": width, "kind": kind}
        return self.roads[key]

    def road_mesh(self, key, mat, uv_len=True, cross=5):
        r = self.roads[key]; P = r["P"]; w = r["w"]
        t = np.gradient(P[:, :2], axis=0); t /= np.linalg.norm(t, axis=1, keepdims=True) + 1e-9
        nrm = np.stack([-t[:, 1], t[:, 0]], 1)
        s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(P[:, :2], axis=0), axis=1))])
        offs = np.linspace(-w / 2, w / 2, cross)
        V, UV = [], []
        for i in range(len(P)):
            for o in offs:
                x, y = P[i, 0] + nrm[i, 0] * o, P[i, 1] + nrm[i, 1] * o
                # gentle camber toward the edges + blend with the terrain cross slope
                zt = float(self.h(x, y)); zc = P[i, 2] - 0.06 * (abs(o) / (w / 2)) ** 2
                V.append((x, y, 0.75 * zc + 0.25 * min(zt, zc + 0.15) + 0.04)); UV.append((o, s[i]))
        F = []
        for i in range(len(P) - 1):
            for j in range(cross - 1):
                a = i * cross + j; F.append((a, a + 1, a + 1 + cross, a + cross))
        o = mesh_obj(f"road_{key}", V, F, self.C, uvs=UV, mat=mat)
        r["obj"] = o; r["s"] = s; r["nrm"] = nrm
        return o

    def build_roads(self):
        A = asphalt_mat("asphalt_track", lines=True, width=WIDTH["raceway"])
        PL = asphalt_mat("asphalt_pitlane", lines=False, width=WIDTH["pitlane"])
        for nm in TRACK_WAYS:
            w = self.way(name=nm)
            if w: self.add_road(nm, w["pts"], WIDTH["raceway"], "raceway"); self.road_mesh(nm, A)
        for w in self.ways:   # the unnamed GP connectors (the straights)
            if w["kind"] == "highway=raceway" and not w["name"] and w["tags"].get("raceway") != "pitlane":
                self.add_road(w["id"], w["pts"], WIDTH["raceway"], "raceway"); self.road_mesh(w["id"], A)
        e = self.way(name="Old GP pit entry"); self.add_road("old_entry", e["pts"], WIDTH["old_entry"], "pitlane", z_bias=-0.035)
        self.road_mesh("old_entry", PL)
        pl = self.way(name="Pit Lane")
        j = pl["nodes"].index(e["nodes"][-1])        # 1993: pit lane starts where the old entry joins
        self.add_road("pit_lane", pl["pts"][j:], WIDTH["pitlane"], "pitlane", z_bias=-0.035); self.road_mesh("pit_lane", PL)
        return A, PL

    def paths(self):
        """Centrelines for driving (3D, road surface height) - Senna via the 1993 pit lane, the others via Goddards."""
        R = self.roads
        def cat(*keys):
            out = []
            for k in keys:
                P = R[k]["P"]
                if out and np.linalg.norm(out[-1][-1, :2] - P[0, :2]) > np.linalg.norm(out[-1][-1, :2] - P[-1, :2]):
                    P = P[::-1]
                out.append(P if not out else P[1:])
            return np.vstack(out)
        straight = next(k for k, r in R.items() if k not in TRACK_WAYS and r["kind"] == "raceway"
                        and np.linalg.norm(r["P"][-1, :2] - R["Fogarty's"]["P"][0, :2]) < 3) if "Fogarty's" in R else None
        return cat, straight


# ------------------------------------------------------------------ terrain
def _axis(lo, hi, ilo, ihi, fine, coarse):
    a = np.arange(lo, ilo, coarse); b = np.arange(ilo, ihi, fine); c = np.arange(ihi, hi + 0.01, coarse)
    return np.concatenate([a, b, c])


def build_terrain(W, inner=(-460, 470, -270, 310), fine=2.0, coarse=12.0):
    from mathutils.kdtree import KDTree
    xs = _axis(-1090, 1690, inner[0], inner[1], fine, coarse); ys = _axis(-1140, 1240, inner[2], inner[3], fine, coarse)
    X, Y = np.meshgrid(xs, ys); Z = W.h(X, Y)
    # carve under the road ribbons (terrain never pokes through the asphalt)
    pts = []; info = []
    for k, r in W.roads.items():
        for p in r["P"]:
            pts.append(p); info.append(r["w"])
    kd = KDTree(len(pts))
    for i, p in enumerate(pts): kd.insert((p[0], p[1], 0.0), i)
    kd.balance()
    inner_mask = (X > inner[0] - 50) & (X < inner[1] + 50) & (Y > inner[2] - 50) & (Y < inner[3] + 50)
    iy, ix = np.nonzero(inner_mask)
    for a, b in zip(iy, ix):
        co, idx, dist = kd.find((X[a, b], Y[a, b], 0.0))
        w = info[idx]
        if dist < w / 2 + 0.8:
            zr = pts[idx][2] + 0.04
            Z[a, b] = min(Z[a, b], zr - (0.35 if dist < w / 2 - 1.0 else 0.07))
    ny, nx = X.shape
    V = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()])
    idx = np.arange(nx * ny).reshape(ny, nx)
    F = np.column_stack([idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel(), idx[1:, 1:].ravel(), idx[1:, :-1].ravel()])
    me = bpy.data.meshes.new("terrain")
    me.vertices.add(len(V)); me.vertices.foreach_set("co", V.astype(np.float32).ravel())
    me.loops.add(len(F) * 4); me.loops.foreach_set("vertex_index", F.astype(np.int32).ravel())
    me.polygons.add(len(F)); me.polygons.foreach_set("loop_start", np.arange(0, len(F) * 4, 4, dtype=np.int32))
    me.polygons.foreach_set("loop_total", np.full(len(F), 4, np.int32))
    me.update(calc_edges=True); me.polygons.foreach_set("use_smooth", [True] * len(F))
    o = bpy.data.objects.new("terrain", me); W.C.objects.link(o)
    o.data.materials.append(grass_mat(os.path.join(W.vd, "data", "landcover.png"), (-700.0, -450.0, 1400.0, 900.0)))
    return o


def setup_world(scene, strength=1.0):
    w = bpy.data.worlds.new("overcast"); scene.world = w; w.use_nodes = True
    nt = w.node_tree; N, L = nt.nodes, nt.links; N.clear()
    out = N.new("ShaderNodeOutputWorld"); bg = N.new("ShaderNodeBackground"); env = N.new("ShaderNodeTexEnvironment")
    env.image = bpy.data.images.load(HDRI)
    # desaturate + cool the sky slightly (cold wet April afternoon)
    hsv = N.new("ShaderNodeHueSaturation"); hsv.inputs["Saturation"].default_value = 0.55; hsv.inputs["Value"].default_value = 0.95
    L.new(env.outputs[0], hsv.inputs["Color"]); L.new(hsv.outputs[0], bg.inputs[0]); bg.inputs[1].default_value = strength
    L.new(bg.outputs[0], out.inputs[0])
    vs = scene.view_settings; vs.view_transform = "AgX"
    try: vs.look = "AgX - Medium High Contrast"
    except Exception: pass
    vs.exposure = 0.15
    return w


def setup_mist(scene, start=70.0, depth=900.0, color=(0.56, 0.6, 0.64)):
    vl = scene.view_layers[0]; vl.use_pass_mist = True
    scene.world.mist_settings.start = start; scene.world.mist_settings.depth = depth; scene.world.mist_settings.falloff = "QUADRATIC"
    scene.use_nodes = True
    try:
        nt = scene.compositing_node_group
    except AttributeError:
        nt = None
    if nt is None:
        nt = bpy.data.node_groups.new("comp", "CompositorNodeTree")
        try: scene.compositing_node_group = nt
        except Exception: pass
    N, L = nt.nodes, nt.links
    for n in list(N): N.remove(n)
    rl = N.new("CompositorNodeRLayers")
    mix = N.new("CompositorNodeMixRGB") if hasattr(bpy.types, "CompositorNodeMixRGB") else N.new("ShaderNodeMix")
    try:
        L.new(rl.outputs["Mist"], mix.inputs[0]); L.new(rl.outputs["Image"], mix.inputs[1]); mix.inputs[2].default_value = (*color, 1)
        outsock = mix.outputs[0]
    except Exception:
        mix.data_type = "RGBA"; L.new(rl.outputs["Mist"], mix.inputs[0]); L.new(rl.outputs["Image"], mix.inputs[6]); mix.inputs[7].default_value = (*color, 1)
        outsock = mix.outputs[2]
    try:
        comp = N.new("CompositorNodeComposite"); L.new(outsock, comp.inputs[0])
    except Exception:
        go = N.new("NodeGroupOutput")
        if not nt.interface.items_tree:
            nt.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
        L.new(outsock, go.inputs[0])
    return nt


# ------------------------------------------------------------------ kerbs, barriers, fences
def _frame(P2):
    t = np.gradient(P2, axis=0); t /= np.linalg.norm(t, axis=1, keepdims=True) + 1e-9
    return t, np.stack([-t[:, 1], t[:, 0]], 1)


def _curv(P2):
    t, _ = _frame(P2); a = np.unwrap(np.arctan2(t[:, 1], t[:, 0]))
    s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(P2, axis=0), axis=1))])
    k = np.gradient(a, s + np.arange(len(s)) * 1e-6)
    return W_smooth(k, 3.0)


def W_smooth(x, sigma):
    r = int(3 * sigma); k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2); k /= k.sum()
    return np.convolve(np.pad(x, r, mode="edge"), k, "valid")


def build_kerbs(W):
    km = kerb_mat(); V, F, UV = [], [], []
    for key, r in W.roads.items():
        if r["kind"] != "raceway" and key != "old_entry": continue
        P = r["P"]; w = r["w"]; t, nrm = _frame(P[:, :2]); k = _curv(P[:, :2])
        s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(P[:, :2], axis=0), axis=1))])
        for side in (1, -1):
            on = (k * side > 1 / 95.0) | (np.abs(k) > 1 / 32.0)       # inside of corners; both sides of hairpins
            on = np.convolve(on.astype(float), np.ones(9), "same") > 0
            i = 0
            while i < len(P):
                if not on[i]: i += 1; continue
                j = i
                while j < len(P) and on[j]: j += 1
                if s[min(j, len(P) - 1)] - s[i] > 6:
                    base = len(V)
                    for q in range(i, j):
                        for o, dz in ((w / 2 - 0.25, 0.0), (w / 2 + 0.55, 0.045), (w / 2 + 1.05, 0.0)):
                            x, y = P[q, 0] + nrm[q, 0] * o * side, P[q, 1] + nrm[q, 1] * o * side
                            V.append((x, y, P[q, 2] + 0.05 + dz)); UV.append((o, s[q]))
                    for q in range(j - i - 1):
                        for c in range(2):
                            a = base + q * 3 + c; F.append((a, a + 1, a + 4, a + 3) if side > 0 else (a, a + 3, a + 4, a + 1))
                i = j
    return mesh_obj("kerbs", V, F, W.C, uvs=UV, mat=km)


def steel_mat():
    m, nt, b = new_mat("armco_steel")
    b.inputs["Base Color"].default_value = (0.42, 0.43, 0.44, 1); b.inputs["Metallic"].default_value = 1.0
    b.inputs["Roughness"].default_value = 0.28
    n = nt.nodes.new("ShaderNodeTexNoise"); n.inputs["Scale"].default_value = 3.0
    mr = nt.nodes.new("ShaderNodeMapRange"); mr.inputs["To Min"].default_value = 0.18; mr.inputs["To Max"].default_value = 0.5
    nt.links.new(n.outputs["Fac"], mr.inputs["Value"]); nt.links.new(mr.outputs[0], b.inputs["Roughness"])
    return m


def _line_pts(W, pts, step):
    P = W.resample(pts, step); z = W.h(P[:, 0], P[:, 1]); return np.column_stack([P, z])


def build_barriers(W, radius=700.0):
    Cb = coll("BARRIERS", W.C)
    steel = steel_mat()
    conc = tex_mat("concrete_wall", "concrete_floor_worn_001", 2.0, tint=(0.75, 0.75, 0.74, 1), rough_mul=0.6)
    near = lambda pts: min(np.hypot(p[0], p[1]) for p in pts) < radius
    rail_V, rail_F, post_V, post_F, wall_V, wall_F, tyre_pts = [], [], [], [], [], [], []
    for w in W.ways:
        kind = w["kind"]
        if not near(w["pts"]): continue
        if kind == "barrier=guard_rail":
            P = _line_pts(W, w["pts"], 1.0); t, nrm = _frame(P[:, :2])
            for zlo, zhi in ((0.42, 0.74), (0.76, 1.08)):            # double W-beam armco
                base = len(rail_V)
                for i in range(len(P)):
                    for dz, off in ((0, 0), (0.08, 0.05), (0.16, 0), (0.24, 0.05), (zhi - zlo, 0)):
                        rail_V.append((P[i, 0] + nrm[i, 0] * off, P[i, 1] + nrm[i, 1] * off, P[i, 2] + zlo + dz))
                for i in range(len(P) - 1):
                    for c in range(4):
                        a = base + i * 5 + c; rail_F.append((a, a + 5, a + 6, a + 1))
            for i in range(0, len(P), 2):
                bx = len(post_V); x, y, z = P[i]; tx, ty = t[i]; nx, ny = nrm[i]
                for (u, v, h) in [(-0.05, -0.12, 0), (0.05, -0.12, 0), (0.05, -0.02, 0), (-0.05, -0.02, 0),
                                  (-0.05, -0.12, 1.05), (0.05, -0.12, 1.05), (0.05, -0.02, 1.05), (-0.05, -0.02, 1.05)]:
                    post_V.append((x + tx * u + nx * v, y + ty * u + ny * v, z + h))
                post_F += [(bx, bx + 1, bx + 2, bx + 3), (bx + 4, bx + 7, bx + 6, bx + 5), (bx, bx + 4, bx + 5, bx + 1),
                           (bx + 1, bx + 5, bx + 6, bx + 2), (bx + 2, bx + 6, bx + 7, bx + 3), (bx + 3, bx + 7, bx + 4, bx)]
        elif kind == "barrier=wall":
            P = _line_pts(W, w["pts"], 1.0); t, nrm = _frame(P[:, :2]); base = len(wall_V)
            for i in range(len(P)):
                for (o, h) in ((-0.18, 0.0), (-0.18, 1.05), (0.18, 1.05), (0.18, 0.0)):
                    wall_V.append((P[i, 0] + nrm[i, 0] * o, P[i, 1] + nrm[i, 1] * o, P[i, 2] + h - 0.1))
            for i in range(len(P) - 1):
                for c in range(3):
                    a = base + i * 4 + c; wall_F.append((a, a + 4, a + 5, a + 1))
        elif kind == "barrier=tyres":
            P = _line_pts(W, w["pts"], 0.62); t, nrm = _frame(P[:, :2])
            for i in range(len(P)):
                for row in (0, 1):
                    for lvl in range(4):
                        tyre_pts.append((P[i, 0] + nrm[i, 0] * row * 0.6, P[i, 1] + nrm[i, 1] * row * 0.6, P[i, 2] + 0.13 + lvl * 0.25))
    mesh_obj("armco_rails", rail_V, rail_F, Cb, mat=steel, smooth=False)
    mesh_obj("armco_posts", post_V, post_F, Cb, mat=steel, smooth=False)
    mesh_obj("walls", wall_V, wall_F, Cb, mat=conc, smooth=False)
    # tyre walls: instanced tyres (black rubber, wet)
    tyre = make_tyre(Cb)
    pts_obj = mesh_obj("tyre_wall_pts", tyre_pts, [], Cb, smooth=False)
    instance_on_points(pts_obj, tyre, rot_random=True)
    return Cb


def make_tyre(C):
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=14, radius1=0.31, radius2=0.31, depth=0.23)
    inner = bmesh.ops.create_cone(bm, cap_ends=False, segments=14, radius1=0.17, radius2=0.17, depth=0.231)
    me = bpy.data.meshes.new("tyre_inst"); bm.to_mesh(me); bm.free()
    o = bpy.data.objects.new("tyre_inst", me); C.objects.link(o); o.hide_render = True; o.hide_viewport = True
    m = flat_mat("tyre_rubber_wet", (0.012, 0.012, 0.013), rough=0.32); me.materials.append(m)
    return o


def instance_on_points(pts_obj, inst, rot_random=False, scale=1.0, align_z=None):
    """Geometry-nodes instancing (cheap: one shared mesh)."""
    md = pts_obj.modifiers.new("inst", "NODES")
    ng = bpy.data.node_groups.new(f"inst_{inst.name}", "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    N, L = ng.nodes, ng.links
    gi = N.new("NodeGroupInput"); go = N.new("NodeGroupOutput")
    m2p = N.new("GeometryNodeMeshToPoints"); L.new(gi.outputs[0], m2p.inputs[0])
    oi = N.new("GeometryNodeObjectInfo"); oi.inputs[0].default_value = inst; oi.transform_space = "ORIGINAL"
    iop = N.new("GeometryNodeInstanceOnPoints"); L.new(m2p.outputs[0], iop.inputs["Points"]); L.new(oi.outputs["Geometry"], iop.inputs["Instance"])
    if rot_random:
        rv = N.new("FunctionNodeRandomValue"); rv.data_type = "FLOAT_VECTOR"
        rv.inputs["Min"].default_value = (0, 0, 0); rv.inputs["Max"].default_value = (0.05, 0.05, 6.28)
        L.new(rv.outputs["Value"], iop.inputs["Rotation"])
    iop.inputs["Scale"].default_value = (scale, scale, scale)
    L.new(iop.outputs[0], go.inputs[0]); md.node_group = ng
    return md


def build_fences(W, radius=650.0):
    """Catch fences (OSM barrier=fence near the track): steel posts + wire mesh (alpha)."""
    Cf = coll("FENCES", W.C)
    wire = bpy.data.materials.new("catch_fence_wire"); wire.use_nodes = True; nt = wire.node_tree; N, L = nt.nodes, nt.links
    b = N.get("Principled BSDF"); b.inputs["Base Color"].default_value = (0.5, 0.52, 0.53, 1); b.inputs["Metallic"].default_value = 1.0
    b.inputs["Roughness"].default_value = 0.35
    uv = N.new("ShaderNodeUVMap"); sep = N.new("ShaderNodeSeparateXYZ"); L.new(uv.outputs[0], sep.inputs[0])
    d1 = math_node(nt, "ADD", sep.outputs[0], sep.outputs[1]); d2 = math_node(nt, "SUBTRACT", sep.outputs[0], sep.outputs[1])
    g1 = math_node(nt, "LESS_THAN", math_node(nt, "PINGPONG", math_node(nt, "MULTIPLY", d1, 12.0), 0.5), 0.06)
    g2 = math_node(nt, "LESS_THAN", math_node(nt, "PINGPONG", math_node(nt, "MULTIPLY", d2, 12.0), 0.5), 0.06)
    L.new(math_node(nt, "MAXIMUM", g1, g2), b.inputs["Alpha"])
    try: wire.surface_render_method = "DITHERED"
    except Exception: pass
    steel = bpy.data.materials.get("armco_steel") or steel_mat()
    V, F, UV, PV, PF = [], [], [], [], []
    for w in W.ways:
        if w["kind"] != "barrier=fence" or min(np.hypot(p[0], p[1]) for p in w["pts"]) > radius: continue
        P = _line_pts(W, w["pts"], 1.5); s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(P[:, :2], axis=0), axis=1))])
        base = len(V)
        for i in range(len(P)):
            V += [(P[i, 0], P[i, 1], P[i, 2] + 0.1), (P[i, 0], P[i, 1], P[i, 2] + 3.4)]; UV += [(s[i], 0.1), (s[i], 3.4)]
        for i in range(len(P) - 1):
            a = base + i * 2; F.append((a, a + 2, a + 3, a + 1))
        for i in range(0, len(P), 2):
            x, y, z = P[i]; bx = len(PV)
            for (u, v, h) in [(-0.04, -0.04, 0), (0.04, -0.04, 0), (0.04, 0.04, 0), (-0.04, 0.04, 0),
                              (-0.04, -0.04, 3.6), (0.04, -0.04, 3.6), (0.04, 0.04, 3.6), (-0.04, 0.04, 3.6)]:
                PV.append((x + u, y + v, z + h))
            PF += [(bx, bx + 4, bx + 5, bx + 1), (bx + 1, bx + 5, bx + 6, bx + 2), (bx + 2, bx + 6, bx + 7, bx + 3), (bx + 3, bx + 7, bx + 4, bx)]
    mesh_obj("catch_fence_mesh", V, F, Cf, uvs=UV, mat=wire, smooth=False)
    mesh_obj("catch_fence_posts", PV, PF, Cf, mat=steel, smooth=False)
    return Cf


# ------------------------------------------------------------------ buildings + pit garages
MODERN = ("Hospitality", "Suites", "750 Motor Club", "Race Control", "Media Centre", "Auctions")


def _extrude(poly, z0, h, V, F):
    n = len(poly); base = len(V)
    for x, y in poly: V.append((x, y, z0))
    for x, y in poly: V.append((x, y, z0 + h))
    for i in range(n):
        j = (i + 1) % n; F.append((base + i, base + j, base + n + j, base + n + i))
    for tri in tessellate_polygon([[Vector((x, y, 0)) for x, y in poly]]):
        F.append(tuple(base + n + t for t in tri[::-1]))


def build_buildings(W, radius=600.0):
    Cb = coll("BUILDINGS", W.C)
    wall = tex_mat("building_wall", "painted_plaster_wall", 3.0, tint=(0.78, 0.78, 0.76, 1), rough_mul=0.7)
    V, F = [], []
    for w in W.ways:
        if not w["kind"].startswith("building") or not w["closed"]: continue
        if any(k in (w["name"] or "") for k in MODERN) or w["name"] == "Pit Garages 1-38": continue
        P = np.array(w["pts"][:-1])
        if np.hypot(*P.mean(0)) > radius: continue
        lv = float(w["tags"].get("building:levels", 1) or 1)
        z0 = float(W.h(P[:, 0], P[:, 1]).min()) - 0.3
        _extrude([tuple(p) for p in P], z0, 3.4 * lv + 0.6, V, F)
    mesh_obj("buildings", V, F, Cb, mat=wall, smooth=False)
    return Cb


def build_pit_garages(W, mclaren_bay=21):
    """1993-style pit building from the OSM footprint: 38 open garage bays facing the pit lane, roof fascia,
    lit interiors. Returns bay centres (world, on the apron) so crews/props can be placed."""
    Cp = coll("PITS", W.C)
    g = next(w for w in W.ways if w["name"] == "Pit Garages 1-38")
    P = np.array(g["pts"][:-1])
    # facade = the long edge nearest the pit lane centreline
    lane = W.roads["pit_lane"]["P"][:, :2]
    best = None
    for i in range(len(P)):
        a, b = P[i], P[(i + 1) % len(P)]
        L = np.linalg.norm(b - a)
        if L < 100: continue
        mid = (a + b) / 2; d = np.min(np.linalg.norm(lane - mid, axis=1))
        if best is None or d < best[0]: best = (d, a, b)
    _, A, B = best
    ux = (B - A) / np.linalg.norm(B - A); n_out = np.array([-ux[1], ux[0]])
    if np.min(np.linalg.norm(lane - ((A + B) / 2 + n_out * 5), axis=1)) > np.min(np.linalg.norm(lane - ((A + B) / 2 - n_out * 5), axis=1)):
        n_out = -n_out
    depth = 11.5; Lf = np.linalg.norm(B - A); nb = 38; bw = Lf / nb
    zf = lambda p: float(W.h(p[0], p[1]))
    z0 = min(zf(A), zf(B)) - 0.2
    conc = bpy.data.materials.get("concrete_wall") or tex_mat("concrete_wall", "concrete_floor_worn_001", 2.0)
    white = tex_mat("pit_facade", "painted_plaster_wall", 2.5, tint=(0.92, 0.92, 0.9, 1), rough_mul=0.6)
    dark = flat_mat("garage_interior", (0.035, 0.036, 0.04), rough=0.7)
    door = tex_mat("roller_door", "metal_plate", 1.2, tint=(0.62, 0.64, 0.66, 1), rough_mul=0.7)
    light = flat_mat("garage_strip_light", (1, 1, 1), emit=(0.95, 0.97, 1.0), emit_s=6.0)
    fascia_cols = [(0.08, 0.12, 0.35), (0.55, 0.05, 0.04), (0.1, 0.1, 0.1), (0.85, 0.85, 0.82), (0.05, 0.25, 0.12), (0.7, 0.55, 0.05)]
    fas = [flat_mat(f"fascia_{i}", c, rough=0.45) for i, c in enumerate(fascia_cols)]
    V, F, M = [], [], []
    def quad(p0, p1, z_a, z_b, mi, inset=0.0):
        base = len(V); o = n_out * inset
        for (x, y), z in (((p0 + o), z_a), ((p1 + o), z_a), ((p1 + o), z_b), ((p0 + o), z_b)):
            V.append((x, y, z0 + z))
        F.append((base, base + 1, base + 2, base + 3)); M.append(mi)
    mats = [white, dark, door, light, conc] + fas
    H_open, H_total = 3.3, 5.6
    bays = []
    for i in range(nb):
        p0 = A + ux * (i * bw); p1 = A + ux * ((i + 1) * bw); c = 0.45
        quad(p0, p0 + ux * c, 0, H_total, 0); quad(p1 - ux * c, p1, 0, H_total, 0)              # pillars
        quad(p0 + ux * c, p1 - ux * c, H_open, H_open + 0.9, 2, inset=-0.15)                  # rolled-up door
        quad(p0, p1, H_open + 0.9, H_total, 5 + (i * 7) % len(fas))                             # team fascia band
        quad(p0 + ux * c, p1 - ux * c, 0, H_open, 1, inset=-depth + 0.6)                      # interior back wall
        for side in (c, bw - c):                                                              # interior side walls
            q = A + ux * (i * bw + side); base = len(V)
            for (pp, z) in ((q, 0), (q - n_out * (depth - 0.6), 0), (q - n_out * (depth - 0.6), H_open), (q, H_open)):
                V.append((pp[0], pp[1], z0 + z))
            F.append((base, base + 1, base + 2, base + 3)); M.append(1)
        base = len(V)                                                                         # strip light (ceiling)
        for pp in ((p0 + p1) / 2 - ux * 1.2 - n_out * 2.0, (p0 + p1) / 2 + ux * 1.2 - n_out * 2.0,
                   (p0 + p1) / 2 + ux * 1.2 - n_out * 2.3, (p0 + p1) / 2 - ux * 1.2 - n_out * 2.3):
            V.append((pp[0], pp[1], z0 + H_open - 0.05))
        F.append((base, base + 1, base + 2, base + 3)); M.append(3)
        bays.append(((p0 + p1) / 2, ux, n_out, z0))
    # roof + back block
    back = [tuple(A - n_out * depth), tuple(B - n_out * depth), tuple(B), tuple(A)]
    base = len(V)
    for x, y in back: V.append((x, y, z0 + H_total))
    F.append((base, base + 1, base + 2, base + 3)); M.append(4)
    me_o = mesh_obj("pit_garages", V, F, Cp, mat=None, smooth=False)
    for m in mats: me_o.data.materials.append(m)
    me_o.data.polygons.foreach_set("material_index", M)
    # apron floor in front of the garages (concrete) and garage floors
    apron = []
    for i in range(0, nb + 1):
        p = A + ux * (i * bw); apron.append(p)
    V2, F2 = [], []
    for i, p in enumerate(apron):
        for o in (-depth + 0.6, 4.0):
            q = p + n_out * o; V2.append((q[0], q[1], z0 + 0.03))
    for i in range(len(apron) - 1):
        a = i * 2; F2.append((a, a + 2, a + 3, a + 1))
    mesh_obj("pit_floor", V2, F2, Cp, mat=tex_mat("pit_floor_conc", "concrete_floor_worn_001", 2.5, tint=(0.55, 0.55, 0.55, 1), rough_mul=0.35), smooth=False)
    W.bays = bays; W.pit_axis = (A, B, ux, n_out, z0)
    return Cp, bays


# ------------------------------------------------------------------ trees (light procedural canopy trees)
def _tree_mesh(name, kind, C, sub=2):
    bm = bmesh.new(); rnd = random.Random(hash(name) & 0xffff)
    h = 9.0 if kind == "oak" else 14.0
    bmesh.ops.create_cone(bm, cap_ends=False, segments=6, radius1=0.32, radius2=0.12, depth=h * 0.6,
                          matrix=Matrix.Translation((0, 0, h * 0.3)))
    clumps = 7 if kind == "oak" else 5
    for k in range(clumps):
        r = rnd.uniform(1.8, 3.0) if kind == "oak" else rnd.uniform(1.4, 2.2)
        c = Vector((rnd.uniform(-2.2, 2.2), rnd.uniform(-2.2, 2.2), h * rnd.uniform(0.55, 0.95)))
        if kind == "conifer":
            c = Vector((rnd.uniform(-0.6, 0.6), rnd.uniform(-0.6, 0.6), h * (0.35 + 0.12 * k)))
            r = 2.6 - 0.35 * k
        g = bmesh.ops.create_icosphere(bm, subdivisions=sub, radius=r, matrix=Matrix.Translation(c))
        for v in g["verts"]:
            d = noise.noise(v.co * 0.9) * 0.45 + noise.noise(v.co * 2.7) * 0.18
            v.co += (v.co - c).normalized() * d * r
    me = bpy.data.meshes.new(name); bm.to_mesh(me); bm.free()
    o = bpy.data.objects.new(name, me); C.objects.link(o)
    return o


def foliage_mat():
    m, nt, b = new_mat("foliage_spring"); N, L = nt.nodes, nt.links
    tc = N.new("ShaderNodeTexCoord"); ob = N.new("ShaderNodeObjectInfo")
    v = N.new("ShaderNodeTexVoronoi"); v.inputs["Scale"].default_value = 9.0; L.new(tc.outputs["Object"], v.inputs["Vector"])
    nz = N.new("ShaderNodeTexNoise"); nz.inputs["Scale"].default_value = 2.5; L.new(tc.outputs["Object"], nz.inputs["Vector"])
    base = mix_rgb(nt, ob.outputs["Random"], (0.035, 0.055, 0.02, 1), (0.07, 0.075, 0.03, 1))
    col = mix_rgb(nt, math_node(nt, "MULTIPLY", nz.outputs["Fac"], 0.8), base, (0.02, 0.025, 0.012, 1))
    L.new(col, b.inputs["Base Color"]); b.inputs["Roughness"].default_value = 0.6
    bump = N.new("ShaderNodeBump"); bump.inputs["Strength"].default_value = 0.6; L.new(v.outputs["Distance"], bump.inputs["Height"])
    L.new(bump.outputs[0], b.inputs["Normal"])
    b.inputs["Subsurface Weight"].default_value = 0.05
    trunk = flat_mat("bark_wet", (0.03, 0.025, 0.02), rough=0.5)
    return m, trunk


def _poisson_in_poly(poly, spacing, rnd, limit=4000):
    from matplotlib.path import Path as MPath   # may not exist in Blender python
    raise ImportError


def _points_in_poly(poly, spacing, rnd):
    P = np.array(poly); mn, mx = P.min(0), P.max(0)
    xs = np.arange(mn[0], mx[0], spacing); ys = np.arange(mn[1], mx[1], spacing)
    out = []
    for x in xs:
        for y in ys:
            px, py = x + rnd.uniform(-0.45, 0.45) * spacing, y + rnd.uniform(-0.45, 0.45) * spacing
            # ray casting point-in-polygon
            inside = False; n = len(P)
            for i in range(n):
                x1, y1 = P[i]; x2, y2 = P[(i + 1) % n]
                if ((y1 > py) != (y2 > py)) and (px < (x2 - x1) * (py - y1) / (y2 - y1 + 1e-12) + x1):
                    inside = not inside
            if inside: out.append((px, py))
    return out


def build_trees(W, radius=1000.0):
    Ct = coll("TREES", W.C)
    proto_c = coll("TREE_PROTOS", Ct); proto_c.hide_render = False
    fol, bark = foliage_mat()
    protos = []
    for i, kind in enumerate(["oak", "oak", "oak", "conifer", "oak", "oak", "conifer"]):
        o = _tree_mesh(f"tree_proto_{i}", kind, proto_c, sub=2 if i < 4 else 1); o.data.materials.append(bark); o.data.materials.append(fol)
        # trunk faces = first cone (material 0), the clumps material 1
        nfaces_trunk = 6
        idx = [0 if k < nfaces_trunk else 1 for k in range(len(o.data.polygons))]
        o.data.polygons.foreach_set("material_index", idx)
        o.hide_render = True; o.hide_viewport = True; protos.append(o)
    rnd = random.Random(7); pts = {i: [] for i in range(len(protos))}
    roads = np.vstack([r["P"][:, :2] for r in W.roads.values()])
    for w in W.ways:
        if w["kind"] not in ("natural=wood", "natural=tree_row"): continue
        if min(np.hypot(p[0], p[1]) for p in w["pts"]) > radius: continue
        if w["kind"] == "natural=tree_row":
            cand = [tuple(p) for p in W.resample(w["pts"], 7.0)]
        else:
            far = min(np.hypot(p[0], p[1]) for p in w["pts"]) > 380
            cand = _points_in_poly(w["pts"][:-1] if w["closed"] else w["pts"], 11.0 if far else 7.5, rnd)
        for (x, y) in cand:
            if np.min(np.hypot(roads[:, 0] - x, roads[:, 1] - y)) < 14: continue
            far = np.hypot(x, y) > 380
            k = rnd.choice([4, 4, 5, 6]) if far else rnd.choice([0, 0, 1, 1, 2, 3]); pts[k].append((x, y, float(W.h(x, y)) - 0.2))
    n = 0
    for k, P in pts.items():
        if not P: continue
        po = mesh_obj(f"trees_{k}", P, [], Ct, smooth=False)
        md = instance_on_points(po, protos[k], rot_random=True, scale=1.0)
        n += len(P)
    print("trees", n)
    return Ct


# ------------------------------------------------------------------ crowd (rain-day spectators)
JACKETS = [(0.02, 0.03, 0.1), (0.35, 0.02, 0.02), (0.6, 0.45, 0.02), (0.03, 0.12, 0.05), (0.08, 0.08, 0.08),
           (0.25, 0.25, 0.27), (0.5, 0.5, 0.5), (0.02, 0.15, 0.35), (0.45, 0.15, 0.02), (0.7, 0.7, 0.68)]
UMBRELLAS = [(0.02, 0.02, 0.02), (0.03, 0.05, 0.22), (0.5, 0.03, 0.03), (0.75, 0.75, 0.72), (0.05, 0.25, 0.1), (0.7, 0.55, 0.05)]


def _ramp_mat(name, cols, rough, by="Random", sat_jitter=False):
    m, nt, b = new_mat(name); N, L = nt.nodes, nt.links
    oi = N.new("ShaderNodeObjectInfo"); ramp = N.new("ShaderNodeValToRGB"); ramp.color_ramp.interpolation = "CONSTANT"
    el = ramp.color_ramp.elements
    while len(el) < len(cols): el.new(0.5)
    for i, c in enumerate(cols):
        el[i].position = i / len(cols); el[i].color = (*c, 1)
    L.new(oi.outputs[by], ramp.inputs[0]); L.new(ramp.outputs[0], b.inputs["Base Color"]); b.inputs["Roughness"].default_value = rough
    return m


def _person(name, C, umbrella=False, hood=False, seated=False):
    bm = bmesh.new(); parts = []
    def cyl(r1, r2, h, at, mat, seg=8):
        g = bmesh.ops.create_cone(bm, cap_ends=True, segments=seg, radius1=r1, radius2=r2, depth=h, matrix=Matrix.Translation(at))
        parts.append((g["verts"], mat))
    def sph(r, at, mat, sub=1):
        g = bmesh.ops.create_icosphere(bm, subdivisions=sub, radius=r, matrix=Matrix.Translation(at)); parts.append((g["verts"], mat))
    leg_h = 0.45 if seated else 0.85; z0 = 0.0
    cyl(0.085, 0.065, leg_h, Vector((-0.1, 0, leg_h / 2)), 1); cyl(0.085, 0.065, leg_h, Vector((0.1, 0, leg_h / 2)), 1)
    cyl(0.2, 0.23, 0.62, Vector((0, 0, leg_h + 0.31)), 0, seg=10)
    cyl(0.06, 0.05, 0.55, Vector((-0.27, 0.02, leg_h + 0.3)), 0); cyl(0.06, 0.05, 0.55, Vector((0.27, 0.02, leg_h + 0.3)), 0)
    sph(0.105, Vector((0, 0, leg_h + 0.75)), 2)
    if hood: sph(0.125, Vector((0, 0.025, leg_h + 0.77)), 0)
    if umbrella:
        cyl(0.012, 0.012, 1.0, Vector((0.12, -0.12, leg_h + 0.9)), 3, seg=4)
        g = bmesh.ops.create_cone(bm, cap_ends=False, segments=10, radius1=0.55, radius2=0.03, depth=0.3,
                                  matrix=Matrix.Translation((0.12, -0.12, leg_h + 1.32)))
        parts.append((g["verts"], 3))
    me = bpy.data.meshes.new(name); bm.to_mesh(me)
    vid2mat = {}
    for verts, mi in parts:
        for v in verts: vid2mat[v.index] = mi
    bm.free()
    mi_list = []
    for p in me.polygons:
        mi_list.append(vid2mat.get(p.vertices[0], 0))
    o = bpy.data.objects.new(name, me); C.objects.link(o)
    for m in (bpy.data.materials["crowd_jacket"], bpy.data.materials["crowd_trousers"], bpy.data.materials["crowd_skin"],
              bpy.data.materials["crowd_umbrella"]):
        me.materials.append(m)
    me.polygons.foreach_set("material_index", mi_list)
    return o


def crowd_protos(C):
    _ramp_mat("crowd_jacket", JACKETS, 0.25)          # wet nylon rain jackets
    _ramp_mat("crowd_trousers", [(0.02, 0.025, 0.05), (0.03, 0.03, 0.03), (0.08, 0.07, 0.06), (0.04, 0.06, 0.12)], 0.6)
    _ramp_mat("crowd_skin", [(0.55, 0.38, 0.3), (0.45, 0.3, 0.22), (0.62, 0.45, 0.36), (0.3, 0.2, 0.14)], 0.5)
    _ramp_mat("crowd_umbrella", UMBRELLAS, 0.2)
    pc = coll("CROWD_PROTOS", C)
    protos = [_person("person_umb", pc, umbrella=True), _person("person_hood", pc, hood=True),
              _person("person_plain", pc), _person("person_umb2", pc, umbrella=True, hood=True)]
    for o in protos: o.hide_render = True; o.hide_viewport = True
    return pc, protos


def crowd_instancer(pts_obj, proto_coll, sway=True):
    """Instances people from a collection (random pick), facing the per-point 'yaw' attribute, with a gentle idle sway."""
    md = pts_obj.modifiers.new("crowd", "NODES")
    ng = bpy.data.node_groups.new("crowd_gn", "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    N, L = ng.nodes, ng.links
    gi = N.new("NodeGroupInput"); go = N.new("NodeGroupOutput")
    m2p = N.new("GeometryNodeMeshToPoints"); L.new(gi.outputs[0], m2p.inputs[0])
    ci = N.new("GeometryNodeCollectionInfo"); ci.inputs[0].default_value = proto_coll
    ci.inputs["Separate Children"].default_value = True; ci.inputs["Reset Children"].default_value = True
    iop = N.new("GeometryNodeInstanceOnPoints"); iop.inputs["Pick Instance"].default_value = True
    L.new(m2p.outputs[0], iop.inputs["Points"]); L.new(ci.outputs[0], iop.inputs["Instance"])
    ridx = N.new("FunctionNodeRandomValue"); ridx.data_type = "INT"; ridx.inputs["Min"].default_value = 0; ridx.inputs["Max"].default_value = 3
    L.new(ridx.outputs["Value"], iop.inputs["Instance Index"])
    yaw = N.new("GeometryNodeInputNamedAttribute"); yaw.data_type = "FLOAT"; yaw.inputs[0].default_value = "yaw"
    comb = N.new("ShaderNodeCombineXYZ")
    if sway:
        t = N.new("GeometryNodeInputSceneTime"); ph = N.new("FunctionNodeRandomValue"); ph.data_type = "FLOAT"
        ph.inputs["Min"].default_value = 0; ph.inputs["Max"].default_value = 30
        add = N.new("ShaderNodeMath"); add.operation = "ADD"; L.new(t.outputs["Seconds"], add.inputs[0]); L.new(ph.outputs["Value"], add.inputs[1])
        sn = N.new("ShaderNodeMath"); sn.operation = "SINE"; mul = N.new("ShaderNodeMath"); mul.operation = "MULTIPLY"
        sc = N.new("ShaderNodeMath"); sc.operation = "MULTIPLY"; sc.inputs[1].default_value = 0.7
        L.new(add.outputs[0], sc.inputs[0]); L.new(sc.outputs[0], sn.inputs[0]); L.new(sn.outputs[0], mul.inputs[0]); mul.inputs[1].default_value = 0.18
        y2 = N.new("ShaderNodeMath"); y2.operation = "ADD"; L.new(yaw.outputs["Attribute"], y2.inputs[0]); L.new(mul.outputs[0], y2.inputs[1])
        L.new(y2.outputs[0], comb.inputs[2])
    else:
        L.new(yaw.outputs["Attribute"], comb.inputs[2])
    L.new(comb.outputs[0], iop.inputs["Rotation"])
    rs = N.new("FunctionNodeRandomValue"); rs.data_type = "FLOAT"; rs.inputs["Min"].default_value = 0.92; rs.inputs["Max"].default_value = 1.08
    L.new(rs.outputs["Value"], iop.inputs["Scale"])
    L.new(iop.outputs[0], go.inputs[0]); md.node_group = ng
    return md


def build_crowd(W, zones, density=0.5, seed=3):
    """zones: [(name, road_key, side(+1 left/-1 right of travel), d_near, d_far, s_from, s_to)] - spectators on the
    bank beside a road, facing the track, never on a road. One object per zone (so the renderer can cull them)."""
    Cc = coll("CROWD", W.C)
    pc = bpy.data.collections.get("CROWD_PROTOS")
    if pc is None:
        pc, protos = crowd_protos(Cc)
    rnd = random.Random(seed)
    allroads = np.vstack([r["P"][:, :2] for r in W.roads.values()])
    total = 0
    for name, key, side, d0, d1, s0, s1 in zones:
        r = W.roads[key]; C = r["P"][:, :2]; t, nrm = _frame(C)
        s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(C, axis=0), axis=1))])
        P, YAW = [], []
        for i in range(len(C)):
            if not (s0 <= s[i] <= s1): continue
            for d in np.arange(d0, d1, 0.95):
                if rnd.random() > density: continue
                off = d + rnd.uniform(-0.3, 0.3); along = rnd.uniform(-0.45, 0.45)
                x = C[i, 0] + nrm[i, 0] * off * side + t[i, 0] * along; y = C[i, 1] + nrm[i, 1] * off * side + t[i, 1] * along
                if np.min(np.hypot(allroads[:, 0] - x, allroads[:, 1] - y)) < 9.0: continue
                P.append((x, y, float(W.h(x, y)))); YAW.append(math.atan2(-nrm[i, 1] * side, -nrm[i, 0] * side) - math.pi / 2 + rnd.uniform(-0.5, 0.5))
        if not P: continue
        po = mesh_obj(f"crowd_{name}", P, [], Cc, smooth=False)
        a = po.data.attributes.new("yaw", "FLOAT", "POINT"); a.data.foreach_set("value", YAW)
        crowd_instancer(po, pc); total += len(P)
    print("crowd", total)
    return Cc


def build_grandstand(W, key="Wheatcroft Straight", s0=170.0, s1=300.0, d0=24.0, tiers=10):
    """Covered spectator stand on the outside of the pit straight (stepped concrete, steel roof, crowd on the steps)."""
    Cg = coll("GRANDSTAND", W.C)
    r = W.roads[key]; C = r["P"][:, :2]; t, nrm = _frame(C)
    s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(C, axis=0), axis=1))])
    side = 1 if np.mean(C[:, 1]) < 60 else -1
    # pick the side away from the pits (pits are south of the straight -> stand on the north = +normal if that points north)
    i0, i1 = np.searchsorted(s, s0), np.searchsorted(s, s1)
    sign = 1 if nrm[(i0 + i1) // 2, 1] > 0 else -1
    conc = bpy.data.materials.get("concrete_wall"); roofm = flat_mat("stand_roof", (0.55, 0.56, 0.58), rough=0.3, metal=0.8)
    V, F, rows = [], [], []
    zbase = float(np.median(W.h(C[i0:i1, 0] + nrm[i0:i1, 0] * d0 * sign, C[i0:i1, 1] + nrm[i0:i1, 1] * d0 * sign)))
    for k in range(tiers):
        dk = d0 + k * 0.85; zk = zbase + 0.5 + k * 0.42
        base = len(V)
        for i in range(i0, i1, 3):
            for dd, zz in ((dk, zk), (dk + 0.85, zk)):
                V.append((C[i, 0] + nrm[i, 0] * dd * sign, C[i, 1] + nrm[i, 1] * dd * sign, zz))
        n = len(range(i0, i1, 3))
        for q in range(n - 1):
            a = base + q * 2; F.append((a, a + 2, a + 3, a + 1))
        rows.append((dk + 0.42, zk))
    mesh_obj("stand_tiers", V, F, Cg, mat=conc, smooth=False)
    # roof plane above
    V2, F2 = [], []
    dk_far = d0 + tiers * 0.85 + 1.0; zr = zbase + 0.5 + tiers * 0.42 + 3.2
    for i in range(i0, i1, 3):
        for dd, zz in ((d0 - 1.5, zr - 0.6), (dk_far, zr)):
            V2.append((C[i, 0] + nrm[i, 0] * dd * sign, C[i, 1] + nrm[i, 1] * dd * sign, zz))
    n = len(range(i0, i1, 3))
    for q in range(n - 1):
        a = q * 2; F2.append((a, a + 1, a + 3, a + 2))
    mesh_obj("stand_roof", V2, F2, Cg, mat=roofm, smooth=False)
    # spectators on the tiers (no umbrellas under the roof)
    rnd = random.Random(11); P, YAW = [], []
    for dk, zk in rows:
        for i in range(i0, i1):
            if rnd.random() < 0.82:
                x = C[i, 0] + nrm[i, 0] * dk * sign + rnd.uniform(-0.3, 0.3); y = C[i, 1] + nrm[i, 1] * dk * sign + rnd.uniform(-0.3, 0.3)
                P.append((x, y, zk)); YAW.append(math.atan2(-nrm[i, 1] * sign, -nrm[i, 0] * sign) - math.pi / 2 + rnd.uniform(-0.3, 0.3))
    pc = bpy.data.collections.get("CROWD_PROTOS")
    po = mesh_obj("stand_crowd", P, [], Cg, smooth=False)
    a = po.data.attributes.new("yaw", "FLOAT", "POINT"); a.data.foreach_set("value", YAW)
    crowd_instancer(po, pc)
    return Cg


def build_boards(W, wall_ids=("841515317",), height=1.0):
    """Generic trackside hoardings on the spectator walls (colour panels, no brands)."""
    Cb = bpy.data.collections.get("BARRIERS") or coll("BARRIERS", W.C)
    cols = [(0.03, 0.06, 0.25), (0.75, 0.65, 0.05), (0.6, 0.04, 0.03), (0.85, 0.85, 0.82), (0.03, 0.3, 0.12), (0.05, 0.05, 0.06)]
    mats = [flat_mat(f"board_{i}", c, rough=0.35) for i, c in enumerate(cols)]
    V, F, M = [], [], []
    for w in W.ways:
        if w["id"] not in wall_ids: continue
        P = _line_pts(W, w["pts"], 1.0); s = 0.0
        for i in range(0, len(P) - 4, 4):
            a, b = P[i], P[i + 4]; base = len(V)
            for (p, z) in ((a, 1.0), (b, 1.0), (b, 1.0 + height), (a, 1.0 + height)):
                V.append((p[0], p[1], p[2] + z - 0.1))
            F.append((base, base + 1, base + 2, base + 3)); M.append((i // 4) % len(mats))
    o = mesh_obj("hoardings", V, F, Cb, smooth=False)
    for m in mats: o.data.materials.append(m)
    o.data.polygons.foreach_set("material_index", M)
    return o
