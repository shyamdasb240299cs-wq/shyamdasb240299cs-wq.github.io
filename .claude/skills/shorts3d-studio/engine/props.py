"""Props: procedural realistic props, asset import (Poly Haven/.blend/.glb/.fbx/.obj), sizing, surface snapping,
grip planning, bite marks. Every prop is ONE mesh object (joined) so contacts/BVH/attachments are exact."""
import bpy, bmesh, math, os, json, urllib.request
from mathutils import Vector, Matrix, Quaternion
from mathutils.bvhtree import BVHTree
from . import core

CACHE = os.environ.get("S3D_CACHE", os.path.expanduser("~/.shorts3d"))


# ------------------------------------------------------------------ materials
def mat(name, color=(0.8, 0.8, 0.8, 1), rough=0.5, metal=0.0, sss=0.0, coat=0.0, emit=None, transmission=0.0, alpha=1.0):
    m = bpy.data.materials.get(name)
    if m:
        return m
    m = bpy.data.materials.new(name); m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    core.set_input(b, "Base Color", color); core.set_input(b, "Roughness", rough); core.set_input(b, "Metallic", metal)
    if sss: core.set_input(b, "Subsurface Weight", sss); core.set_input(b, "Subsurface Radius", (1, .5, .25)); core.set_input(b, "Subsurface Scale", 0.01)
    if coat: core.set_input(b, "Coat Weight", coat)
    if transmission: core.set_input(b, "Transmission Weight", transmission)
    if emit: core.set_input(b, "Emission Color", emit); core.set_input(b, "Emission Strength", 3.0)
    if alpha < 1:
        core.set_input(b, "Alpha", alpha)
    return m


def _nodes(m):
    return m.node_tree.nodes, m.node_tree.links, m.node_tree.nodes["Principled BSDF"]


def mat_wood(name="wood", base=(0.32, 0.18, 0.09, 1)):
    m = bpy.data.materials.get(name)
    if m: return m
    m = mat(name, base, 0.45, coat=0.15); n, L, b = _nodes(m)
    tc = n.new("ShaderNodeTexCoord"); mp = n.new("ShaderNodeMapping"); mp.inputs["Scale"].default_value = (1, 8, 1)
    w = n.new("ShaderNodeTexWave"); w.wave_type = "RINGS"; w.inputs["Scale"].default_value = 2.5
    w.inputs["Distortion"].default_value = 6; w.inputs["Detail"].default_value = 3
    cr = n.new("ShaderNodeValToRGB"); cr.color_ramp.elements[0].color = tuple(c * 0.55 for c in base[:3]) + (1,)
    cr.color_ramp.elements[1].color = tuple(min(1, c * 1.35) for c in base[:3]) + (1,)
    L.new(tc.outputs["Object"], mp.inputs["Vector"]); L.new(mp.outputs["Vector"], w.inputs["Vector"])
    L.new(w.outputs["Fac"], cr.inputs["Fac"]); L.new(cr.outputs["Color"], b.inputs["Base Color"])
    return m


def mat_bread(name="bread"):
    """Crumb (porous, light) on faces pointing up/down, crust (brown, shiny-ish) on sides."""
    m = bpy.data.materials.get(name)
    if m: return m
    m = mat(name, (0.85, 0.7, 0.45, 1), 0.85, sss=0.15); n, L, b = _nodes(m)
    geo = n.new("ShaderNodeNewGeometry"); sep = n.new("ShaderNodeSeparateXYZ"); ab = n.new("ShaderNodeMath"); ab.operation = "ABSOLUTE"
    tc = n.new("ShaderNodeTexCoord"); vor = n.new("ShaderNodeTexVoronoi"); vor.inputs["Scale"].default_value = 180
    nz = n.new("ShaderNodeTexNoise"); nz.inputs["Scale"].default_value = 60
    crumb = n.new("ShaderNodeValToRGB"); crumb.color_ramp.elements[0].color = (0.55, 0.38, 0.18, 1); crumb.color_ramp.elements[1].color = (0.93, 0.82, 0.6, 1)
    crumb.color_ramp.elements[0].position = 0.02; crumb.color_ramp.elements[1].position = 0.18
    crust = n.new("ShaderNodeRGB"); crust.outputs[0].default_value = (0.38, 0.17, 0.05, 1)
    mix = n.new("ShaderNodeMix"); mix.data_type = "RGBA"
    ramp = n.new("ShaderNodeMapRange"); ramp.inputs["From Min"].default_value = 0.35; ramp.inputs["From Max"].default_value = 0.75
    bump = n.new("ShaderNodeBump"); bump.inputs["Strength"].default_value = 0.6; bump.inputs["Distance"].default_value = 0.002
    L.new(geo.outputs["Normal"], sep.inputs[0]); L.new(sep.outputs["Z"], ab.inputs[0]); L.new(ab.outputs[0], ramp.inputs["Value"])
    L.new(tc.outputs["Object"], vor.inputs["Vector"]); L.new(vor.outputs["Distance"], crumb.inputs["Fac"])
    L.new(ramp.outputs["Result"], mix.inputs["Factor"]); L.new(crust.outputs[0], mix.inputs["A"]); L.new(crumb.outputs["Color"], mix.inputs["B"])
    L.new(mix.outputs["Result"], b.inputs["Base Color"]); L.new(vor.outputs["Distance"], bump.inputs["Height"])
    L.new(bump.outputs["Normal"], b.inputs["Normal"])
    return m


# ------------------------------------------------------------------ procedural builders
def _obj_from_bm(name, bm, material=None, coll=None, smooth=True):
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)  # outward normals: contact tests rely on them
    me = bpy.data.meshes.new(name); bm.to_mesh(me); bm.free()
    if smooth:
        me.polygons.foreach_set("use_smooth", [True] * len(me.polygons))
    ob = bpy.data.objects.new(name, me)
    (coll or bpy.context.scene.collection).objects.link(ob)
    if material: me.materials.append(material)
    return ob


def _bevel(ob, w, seg=3):
    m = ob.modifiers.new("bevel", "BEVEL"); m.width = w; m.segments = seg; m.limit_method = "ANGLE"


def _apply_mods(ob):
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(ob.evaluated_get(dg))
    ob.modifiers.clear(); old = ob.data; ob.data = me; bpy.data.meshes.remove(old)


def proc_box(name, dx, dy, dz, material, coll=None, bevel=0.004):
    bm = bmesh.new(); bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts: v.co = Vector((v.co.x * dx, v.co.y * dy, v.co.z * dz + dz / 2))
    ob = _obj_from_bm(name, bm, material, coll, smooth=False)
    if bevel: _bevel(ob, bevel); _apply_mods(ob)
    return ob


def proc_cyl(name, r, h, material, coll=None, seg=48, bevel=0.002):
    bm = bmesh.new(); bmesh.ops.create_cone(bm, cap_ends=True, segments=seg, radius1=r, radius2=r, depth=h)
    bmesh.ops.translate(bm, verts=bm.verts, vec=(0, 0, h / 2))
    ob = _obj_from_bm(name, bm, material, coll)
    if bevel: _bevel(ob, bevel); _apply_mods(ob)
    return ob


def proc_lathe(name, profile, material, coll=None, seg=64):
    """profile: list of (r, z) points, revolved around Z."""
    bm = bmesh.new()
    rings = []
    for i in range(seg):
        a = 2 * math.pi * i / seg; ca, sa = math.cos(a), math.sin(a)
        rings.append([bm.verts.new((r * ca, r * sa, z)) for r, z in profile])
    for i in range(seg):
        A, B = rings[i], rings[(i + 1) % seg]
        for j in range(len(profile) - 1):
            bm.faces.new((A[j], B[j], B[j + 1], A[j + 1]))
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
    return _obj_from_bm(name, bm, material, coll)


def proc_table(name, coll=None, w=1.2, d=0.75, h=0.76, material=None):
    m = material or mat_wood()
    top = proc_box(name, w, d, 0.04, m, coll, 0.006); top.location.z = h - 0.04
    _apply_loc(top)
    for sx in (-1, 1):
        for sy in (-1, 1):
            leg = proc_box(name + "_leg", 0.05, 0.05, h - 0.04, m, coll, 0.004)
            leg.location = (sx * (w / 2 - 0.07), sy * (d / 2 - 0.07), 0); _apply_loc(leg)
            _join(top, leg)
    top["s3d_surface"] = True
    return top


def _apply_loc(ob):
    me = ob.data; me.transform(Matrix.Translation(ob.location)); ob.location = (0, 0, 0)


def _join(a, b):
    bm = bmesh.new(); bm.from_mesh(a.data); bm2 = b.data
    off = len(bm.verts)
    bm.from_mesh(bm2); bm.to_mesh(a.data); bm.free()
    for mt in b.data.materials:
        if mt and mt.name not in [x.name for x in a.data.materials if x]:
            a.data.materials.append(mt)
    bpy.data.objects.remove(b)


def proc_plate(name, coll=None, d=0.26):
    r = d / 2
    prof = [(0.0, 0.0), (r * 0.55, 0.0), (r * 0.6, 0.004), (r * 0.62, 0.008), (r * 0.9, 0.016), (r, 0.022),
            (r * 0.99, 0.025), (r * 0.88, 0.02), (r * 0.6, 0.011), (r * 0.58, 0.009), (0.0, 0.009)]
    ob = proc_lathe(name, prof, mat("ceramic_white", (0.92, 0.92, 0.9, 1), 0.12, coat=0.5), coll)
    ob["s3d_surface"] = True
    return ob


def proc_bread_slice(name, coll=None, w=0.11, h=0.115, t=0.016):
    """Classic sandwich-bread silhouette (square base, domed top), extruded and bevelled."""
    pts = []
    for i in range(12):  # dome
        a = math.pi * i / 11
        pts.append((-math.cos(a) * w * 0.56, h * 0.62 + math.sin(a) * h * 0.38))
    pts = [(-w / 2, 0.0)] + pts + [(w / 2, 0.0)]
    bm = bmesh.new()
    lo = [bm.verts.new((x, y - h / 2, 0)) for x, y in pts]
    hi = [bm.verts.new((x, y - h / 2, t)) for x, y in pts]
    n = len(pts)
    bm.faces.new(lo[::-1]); bm.faces.new(hi)
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((lo[i], lo[j], hi[j], hi[i]))
    ob = _obj_from_bm(name, bm, mat_bread(), coll, smooth=False)
    _bevel(ob, 0.003, 3); _apply_mods(ob)
    ob.data.polygons.foreach_set("use_smooth", [True] * len(ob.data.polygons))
    return ob


def curve_tube(name, points, radius, coll=None, material=None, res=4):
    """Smooth tube along points (bezier, auto handles) converted to a mesh object."""
    cu = bpy.data.curves.new(name + "_c", "CURVE"); cu.dimensions = "3D"
    cu.bevel_depth = radius; cu.bevel_resolution = res; cu.use_fill_caps = True
    sp = cu.splines.new("BEZIER"); sp.bezier_points.add(len(points) - 1)
    for bp, p in zip(sp.bezier_points, points):
        bp.co = p; bp.handle_left_type = bp.handle_right_type = "AUTO"
    tmp = bpy.data.objects.new(name + "_tmp", cu); bpy.context.scene.collection.objects.link(tmp)
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(tmp.evaluated_get(dg)); bpy.data.objects.remove(tmp); bpy.data.curves.remove(cu)
    ob = bpy.data.objects.new(name, me); (coll or bpy.context.scene.collection).objects.link(ob)
    if material: me.materials.append(material)
    me.polygons.foreach_set("use_smooth", [True] * len(me.polygons))
    # remember the centre-line (for flow particles that travel along wires/pipes)
    from mathutils.geometry import interpolate_bezier
    pts = [Vector(p) for p in points]
    line = []
    for i in range(len(pts) - 1):
        a_, b_ = pts[i], pts[i + 1]
        h1 = a_ + (b_ - (pts[i - 1] if i > 0 else a_)) / 6; h2 = b_ - ((pts[i + 2] if i + 2 < len(pts) else b_) - a_) / 6
        line += interpolate_bezier(a_, h1, h2, b_, 10)[:-1]
    line.append(pts[-1])
    ob["s3d_path"] = [c for p in line for c in p]
    return ob


def proc_mug(name, coll=None):
    prof = [(0.0, 0.002), (0.038, 0.0), (0.041, 0.004), (0.042, 0.095), (0.044, 0.1), (0.04, 0.1), (0.037, 0.095),
            (0.036, 0.01), (0.0, 0.01)]
    m = mat("ceramic_mug", core.hexcol("#e8eef2"), 0.2, coat=0.6)
    ob = proc_lathe(name, prof, m, coll)
    h = curve_tube(name + "_handle", [(0.038, 0, 0.082), (0.07, 0, 0.06), (0.038, 0, 0.026)], 0.0065, coll, m)
    _join(ob, h)
    return ob


def proc_phone(name, coll=None):
    ob = proc_box(name, 0.071, 0.147, 0.008, mat("phone_body", (0.05, 0.05, 0.06, 1), 0.25, metal=0.6), coll, 0.0035)
    return ob


def proc_apple(name, coll=None, r=0.04):
    bm = bmesh.new(); bmesh.ops.create_uvsphere(bm, u_segments=32, v_segments=24, radius=r)
    for v in bm.verts:
        z = v.co.z / r
        v.co.z *= 0.92
        k = 1 + 0.08 * (1 - abs(z)) - (0.25 * max(0, z - 0.85) / 0.15 if z > 0.85 else 0)
        v.co.x *= k; v.co.y *= k
        if abs(z) > 0.9: v.co.z -= math.copysign(0.006, z)
    bmesh.ops.translate(bm, verts=bm.verts, vec=(0, 0, r * 0.92))
    return _obj_from_bm(name, bm, mat("apple_red", (0.55, 0.04, 0.03, 1), 0.3, coat=0.3, sss=0.05), coll)


def proc_coin(name, coll=None):
    return proc_cyl(name, 0.0121, 0.00175, mat("coin", (0.8, 0.6, 0.35, 1), 0.25, metal=1.0), coll, 48, 0.0003)


PROCS = {"table": proc_table, "plate": proc_plate, "bread_slice": proc_bread_slice, "mug": proc_mug,
         "phone": proc_phone, "apple": proc_apple, "coin": proc_coin,
         "box": lambda n, c=None: proc_box(n, 0.2, 0.15, 0.12, mat("cardboard", (0.55, 0.4, 0.25, 1), 0.8), c)}


# ------------------------------------------------------------------ import
def _join_objects(objs, name):
    meshes = [o for o in objs if o.type == "MESH"]
    if not meshes:
        raise RuntimeError("asset has no meshes")
    dg = bpy.context.evaluated_depsgraph_get()
    bm = bmesh.new(); mats = []
    for o in meshes:
        me = bpy.data.meshes.new_from_object(o.evaluated_get(dg))
        me.transform(o.matrix_world)
        base = len(mats)
        for mt in me.materials:
            mats.append(mt)
        for p in me.polygons:
            p.material_index += base
        bm.from_mesh(me); bpy.data.meshes.remove(me)
    me = bpy.data.meshes.new(name); bm.to_mesh(me); bm.free()
    for mt in mats: me.materials.append(mt)
    ob = bpy.data.objects.new(name, me)
    for o in objs:
        bpy.data.objects.remove(o)
    return ob


def import_file(path, name, coll):
    before = set(bpy.data.objects)
    ext = os.path.splitext(path)[1].lower()
    if ext == ".blend":
        with bpy.data.libraries.load(path, link=False) as (src, dst):
            dst.objects = list(src.objects)
        objs = [o for o in dst.objects if o]
    else:
        if ext in (".glb", ".gltf"): bpy.ops.import_scene.gltf(filepath=path)
        elif ext == ".fbx": bpy.ops.import_scene.fbx(filepath=path)
        elif ext == ".obj": bpy.ops.wm.obj_import(filepath=path)
        else: raise RuntimeError(f"unsupported asset {path}")
        objs = [o for o in bpy.data.objects if o not in before]
        for o in objs:
            for c in list(o.users_collection): c.objects.unlink(o)
    ob = _join_objects(objs, name)
    coll.objects.link(ob)
    return ob


def polyhaven(asset_id, res="1k"):
    """Download a Poly Haven (CC0) model as .blend + textures into the cache; return path."""
    d = os.path.join(CACHE, "polyhaven", asset_id, res); os.makedirs(d, exist_ok=True)
    blend = os.path.join(d, f"{asset_id}_{res}.blend")
    if os.path.exists(blend):
        return blend
    files = json.load(urllib.request.urlopen(f"https://api.polyhaven.com/files/{asset_id}", timeout=60))
    entry = files["blend"][res]["blend"]
    urllib.request.urlretrieve(entry["url"], blend)
    for rel, inc in entry.get("include", {}).items():
        p = os.path.join(d, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
        urllib.request.urlretrieve(inc["url"], p)
    return blend


def normalize_size(ob, size_cm=None):
    """Scale so the largest dimension = size_cm; set origin to bottom-center (rests on z=0)."""
    me = ob.data
    xs = [v.co for v in me.vertices]
    mn = Vector((min(v.x for v in xs), min(v.y for v in xs), min(v.z for v in xs)))
    mx = Vector((max(v.x for v in xs), max(v.y for v in xs), max(v.z for v in xs)))
    c = Vector(((mn.x + mx.x) / 2, (mn.y + mx.y) / 2, mn.z))
    me.transform(Matrix.Translation(-c))
    k = 1.0
    if size_cm:
        k = (size_cm / 100.0) / max((mx - mn)[:])
        me.transform(Matrix.Scale(k, 4))
    if "s3d_path" in ob:
        fl = list(ob["s3d_path"])
        ob["s3d_path"] = [((fl[i + j] - c[j]) * k) for i in range(0, len(fl), 3) for j in range(3)]
    me.update()


def make(name, src, coll, size_cm=None, params=None):
    """src: proc:<kind> | poly:<id>[@2k] | file:<path> | lib:<name> (library dir). params -> procedural builders."""
    kind, _, arg = src.partition(":")
    if kind == "proc":
        if arg in PROCS:
            ob = PROCS[arg](name, coll)
        else:
            from . import mech
            if arg not in mech.BUILDERS:
                raise RuntimeError(f"unknown proc prop '{arg}'. known: {sorted(PROCS) + sorted(mech.BUILDERS)}")
            ob = mech.BUILDERS[arg](name, coll, **(params or {}))
    elif kind == "poly":
        aid, _, res = arg.partition("@")
        ob = import_file(polyhaven(aid, res or "1k"), name, coll)
    elif kind == "lib":
        p = os.path.join(CACHE, "library", "props", arg)
        cand = [p + e for e in (".blend", ".glb", ".fbx", ".obj") if os.path.exists(p + e)]
        if not cand: raise RuntimeError(f"library prop {arg} not found in {p}.*")
        ob = import_file(cand[0], name, coll)
    else:
        ob = import_file(arg if kind == "file" else src, name, coll)
    normalize_size(ob, size_cm)
    ob["s3d_prop"] = True
    return ob


# ------------------------------------------------------------------ placement
def bvh(ob, evaluated=False):
    return core.bvh_world(ob, evaluated=evaluated)


def top_z(surface, x, y, default=None):
    t = bvh(surface)
    hit = t.ray_cast(Vector((x, y, 50.0)), Vector((0, 0, -1)))
    return hit[0].z if hit[0] is not None else (default if default is not None else core.world_bbox(surface)[1].z)


def place_on(ob, surface, xy=None, yaw=0.0, lift=0.0):
    """Rest ob on top of surface at world xy: lowest vertex touches the surface (no floating / sinking)."""
    mn, mx = core.world_bbox(surface)
    if xy is None:
        xy = ((mn.x + mx.x) / 2, (mn.y + mx.y) / 2)
    ob.rotation_euler = (0, 0, yaw)
    ob.location = (xy[0], xy[1], 0)
    bpy.context.view_layer.update()
    # sample several points of the object's footprint so wide objects rest on the highest support
    bmn, bmx = core.world_bbox(ob)
    t = bvh(surface); best = None
    for fx in (0.2, 0.5, 0.8):
        for fy in (0.2, 0.5, 0.8):
            p = Vector((bmn.x + (bmx.x - bmn.x) * fx, bmn.y + (bmx.y - bmn.y) * fy, 50.0))
            h = t.ray_cast(p, Vector((0, 0, -1)))
            if h[0] is not None:
                best = h[0].z if best is None else max(best, h[0].z)
    z = best if best is not None else mx.z
    ob.location.z = z - (bmn.z - ob.location.z) + lift + 0.0005
    bpy.context.view_layer.update()
    return ob


# ------------------------------------------------------------------ grips
def grip(ob, from_point, kind="auto", side=None):
    """Plan a hand grip on ob for a hand coming from from_point (world).
    Returns dict(type, palm_point, palm_n (into object), fingers (dir), approach, width)."""
    mn, mx = core.world_bbox(ob)
    dims = mx - mn
    c = (mn + mx) / 2
    hmax = max(dims.x, dims.y)
    t = bvh(ob)
    to_obj = (c - Vector(from_point)); to_obj.z = 0
    to_obj = to_obj.normalized() if to_obj.length > 1e-6 else Vector((0, 1, 0))
    if kind == "auto":
        if dims.z < 0.025 and hmax > 0.05: kind = "pinch_edge"
        elif max(dims) < 0.045: kind = "pinch"
        elif dims.z > 1.3 * hmax: kind = "side"
        else: kind = "top"
    side_dir = Vector((-to_obj.y, to_obj.x, 0)) * (-1 if side == "L" else 1)  # fingers wrap toward the body midline
    medial = Quaternion((0, 0, 1), math.radians(12) * (-1 if side == "L" else 1))  # hands angle slightly inward
    if kind == "top":
        palm_pt = Vector((c.x, c.y, mx.z)); palm_n = Vector((0, 0, -1)); fingers = medial @ to_obj
        approach = Vector((0, 0, -1)); width = min(dims.x, dims.y)
    elif kind == "side":
        hit = t.ray_cast(Vector((c.x, c.y, c.z)) - to_obj * 1.0, to_obj)
        palm_pt = hit[0] if hit[0] else c - to_obj * (hmax / 2)
        palm_n = to_obj.copy(); fingers = Vector((0, 0, -1)).cross(to_obj).cross(to_obj).normalized() * -1
        fingers = side_dir.copy(); approach = to_obj.copy(); width = hmax
    elif kind == "pinch_edge":  # flat item: palm hovers above the near edge, fingertips come down onto it
        hit = t.ray_cast(Vector((c.x, c.y, mx.z - 0.002)) - to_obj * 1.0, to_obj)
        edge = hit[0] if hit[0] else c - to_obj * (hmax / 2)
        palm_pt = Vector((edge.x, edge.y, mx.z + 0.03)) + to_obj * 0.03
        palm_n = (Vector((0, 0, -1)) + to_obj * 0.15).normalized(); fingers = (to_obj + Vector((0, 0, -0.25))).normalized()
        approach = (Vector((0, 0, -1)) + to_obj * 0.4).normalized(); width = dims.z
    else:  # pinch (small object, from above)
        palm_pt = Vector((c.x, c.y, mx.z + 0.035)); palm_n = Vector((0, 0, -1)); fingers = medial @ to_obj
        approach = Vector((0, 0, -1)); width = max(dims)
    return {"type": kind, "palm_point": palm_pt, "palm_n": palm_n.normalized(), "fingers": fingers.normalized(),
            "approach": approach.normalized(), "width": width, "center": c, "dims": dims}


# ------------------------------------------------------------------ bites
def bite_copy(ob, point_world, frame, coll, radius=(0.022, 0.03, 0.016), idx=1):
    """Make a bitten copy of ob (boolean with a jaw-shaped ellipsoid) visible from `frame`."""
    cut = bpy.data.objects.new(f"{ob.name}_bitecut{idx}", bpy.data.meshes.new("bc"))
    bm = bmesh.new(); bmesh.ops.create_uvsphere(bm, u_segments=24, v_segments=12, radius=1.0)
    bm.to_mesh(cut.data); bm.free()
    coll.objects.link(cut)
    inv = ob.matrix_world.inverted()
    p = inv @ Vector(point_world)
    cut.matrix_world = ob.matrix_world @ Matrix.Translation(p) @ Matrix.Diagonal((*radius, 1)).to_4x4()
    new = ob.copy(); new.data = ob.data.copy(); new.name = f"{ob.name}_bite{idx}"
    coll.objects.link(new)
    mod = new.modifiers.new("bite", "BOOLEAN"); mod.operation = "DIFFERENCE"; mod.object = cut
    if hasattr(mod, "solver"): mod.solver = "EXACT" if "EXACT" in [i.identifier for i in mod.bl_rna.properties["solver"].enum_items] else mod.solver
    bpy.context.view_layer.update()
    _apply_mods(new)
    bpy.data.objects.remove(cut)
    new.animation_data_clear()
    return new
