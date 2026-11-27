"""Vehicles: import any free car model (.glb/.gltf/.fbx/.blend), strip badges/logos, rebuild realistic materials,
rig it (wheels spin + steer + suspension, body pitch/roll, steering wheel, pedals, gauge needles, lights) and drive it
along a 3D path with physically derived motion.

    car = vehicle.load("mustang.glb", paint="#0B2A6B", remove=("badge", "supercharger", "engine_glossblack"))
    vehicle.drive(car, path, [(f0, s0, v0), (f1, s1, v1), ...], fps=30)   # arc length s (m) and speed (m/s) keys
    vehicle.xray(car, [(f, 0), (f+12, 1)])                                 # front-to-back x-ray wipe

Model conventions handled: any forward axis (detected from wheel layout + headlights), metres, nested glTF/FBX
hierarchies (flattened), wheels as separate meshes (clustered by position). Lessons: reference/lessons.md.
"""
import bpy, math, re
import numpy as np
from mathutils import Vector, Matrix, Quaternion
from . import core

# ------------------------------------------------------------------ materials
ROLES = [  # (regex on material name, role) - first match wins
    (r"white|emblem|logo|badge", "badge"), (r"tinted|glass|window|windshield|windscreen", "glass"),
    (r"rear.?light|tail|brake.?light", "tail"), (r"^light|head.?light|lamp|emit", "head"),
    (r"lambert|\brim|alloy|wheel", "rim"), (r"orange|\bamber|indicator|signal", "amber"), (r"mirror|chrome", "chrome"),
    (r"tire|tyre|rubber", "tire"), (r"brake|disc|rotor|caliper", "brake"), (r"plate|steel|metal|exhaust", "metal"),
    (r"leather|dash|interior|trim_int", "leather"), (r"seat|fabric|cloth", "seat"), (r"roll|cage", "cage"),
    (r"carpaint|paint|body", "paint"), (r"black|gloss|plastic|trim", "black")]


def _bsdf(mat):
    mat.use_nodes = True
    nt = mat.node_tree; nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial"); out.location = (600, 0)
    b = nt.nodes.new("ShaderNodeBsdfPrincipled"); b.location = (200, 0)
    nt.links.new(b.outputs[0], out.inputs[0])
    return nt, b, out


def _set(b, **kw):
    names = {"base": "Base Color", "metal": "Metallic", "rough": "Roughness", "coat": "Coat Weight",
             "coat_rough": "Coat Roughness", "trans": "Transmission Weight", "ior": "IOR", "alpha": "Alpha",
             "emit": "Emission Color", "emit_s": "Emission Strength", "spec": "Specular IOR Level", "aniso": "Anisotropic",
             "sss": "Subsurface Weight", "sheen": "Sheen Weight"}
    for k, v in kw.items():
        if v is not None:
            core.set_input(b, names[k], v)


def _noise_bump(nt, b, scale=400.0, strength=0.03, kind="noise", coat=False):
    tc = nt.nodes.new("ShaderNodeTexCoord")
    if kind == "voronoi":
        t = nt.nodes.new("ShaderNodeTexVoronoi"); t.inputs["Scale"].default_value = scale
        val = t.outputs["Distance"]
    else:
        t = nt.nodes.new("ShaderNodeTexNoise"); t.inputs["Scale"].default_value = scale
        t.inputs["Detail"].default_value = 6.0; val = t.outputs["Fac"]
    nt.links.new(tc.outputs["Object"], t.inputs["Vector"])
    bump = nt.nodes.new("ShaderNodeBump"); bump.inputs["Strength"].default_value = strength
    nt.links.new(val, bump.inputs["Height"]); nt.links.new(bump.outputs[0], b.inputs["Normal"])
    if coat and "Coat Normal" in b.inputs:
        pass  # clear coat stays mirror-smooth over the flakes (that is what makes paint look like paint)


def build_material(mat, role, paint="#0B2A6B"):
    nt, b, out = _bsdf(mat)
    try:
        mat.surface_render_method = "DITHERED"
    except Exception:
        pass
    if role == "paint":
        _set(b, base=core.hexcol(paint), metal=0.55, rough=0.34, coat=1.0, coat_rough=0.025)
        _noise_bump(nt, b, scale=2600.0, strength=0.06, kind="voronoi", coat=True)      # metallic flakes
    elif role == "black":
        _set(b, base=(0.008, 0.008, 0.009, 1), metal=0.0, rough=0.22, coat=0.6, coat_rough=0.05)
    elif role == "glass":
        _set(b, base=(0.55, 0.58, 0.6, 1), metal=0.0, rough=0.0, trans=1.0, ior=1.52)
    elif role == "tail":
        _set(b, base=(0.6, 0.02, 0.02, 1), rough=0.08, trans=0.6, ior=1.5, emit=(1.0, 0.03, 0.02, 1), emit_s=2.5)
    elif role == "head":
        _set(b, base=(1, 1, 1, 1), rough=0.1, emit=(1.0, 0.92, 0.82, 1), emit_s=12.0)
    elif role == "amber":
        _set(b, base=(0.9, 0.35, 0.02, 1), rough=0.1, trans=0.5, emit=(1.0, 0.4, 0.02, 1), emit_s=0.6)
    elif role == "chrome":
        _set(b, base=(0.92, 0.92, 0.93, 1), metal=1.0, rough=0.03)
    elif role == "tire":
        _set(b, base=(0.018, 0.018, 0.019, 1), rough=0.78, spec=0.35)
        _noise_bump(nt, b, scale=900.0, strength=0.08)
    elif role == "brake":
        _set(b, base=(0.42, 0.42, 0.43, 1), metal=1.0, rough=0.38)
    elif role == "rim":
        _set(b, base=(0.62, 0.63, 0.65, 1), metal=1.0, rough=0.22)
    elif role == "metal":
        _set(b, base=(0.28, 0.28, 0.29, 1), metal=1.0, rough=0.45)
    elif role == "leather":
        _set(b, base=(0.022, 0.02, 0.019, 1), rough=0.52, sheen=0.2)
        _noise_bump(nt, b, scale=1800.0, strength=0.05)
    elif role == "seat":
        _set(b, base=(0.03, 0.03, 0.032, 1), rough=0.6, sheen=0.4)
    elif role == "cage":
        _set(b, base=(0.05, 0.05, 0.055, 1), metal=0.8, rough=0.35)
    else:  # badge (should be deleted) or unknown
        _set(b, base=(0.02, 0.02, 0.02, 1), rough=0.3)
    mat["s3d_role"] = role
    return mat


def role_of(name):
    n = name.lower()
    for pat, role in ROLES:
        if re.search(pat, n):
            return role
    return "black"


# ------------------------------------------------------------------ import + flatten
def _import(path):
    before = set(bpy.data.objects)
    p = str(path).lower()
    if p.endswith((".glb", ".gltf")):
        bpy.ops.import_scene.gltf(filepath=str(path))
    elif p.endswith(".fbx"):
        bpy.ops.import_scene.fbx(filepath=str(path))
    elif p.endswith(".blend"):
        with bpy.data.libraries.load(str(path)) as (src, dst):
            dst.objects = src.objects
        for o in dst.objects:
            bpy.context.scene.collection.objects.link(o)
    else:
        raise ValueError(f"unsupported car file: {path}")
    return [o for o in bpy.data.objects if o not in before]


class Car:
    def __init__(self, name):
        self.name = name; self.root = None; self.body = None
        self.wheels = {}          # key FL FR RL RR -> dict(hub, steer, spin, r, center)
        self.parts = {}           # steer_wheel, gas, brake_pedal, shifter -> pivot empties
        self.meshes = []; self.mats = {}; self.length = 4.5; self.wheelbase = 2.7; self.track = 1.6
        self.needles = {}; self.lights = []


def _axis_pca(pts):
    P = np.array(pts); c = P.mean(0); u, s, vt = np.linalg.svd(P - c, full_matrices=False)
    return Vector(c), [Vector(v) for v in vt], s


def load(path, name="car", paint="#0B2A6B", remove=("badge", "emblem", "logo", "icosphere"), coll=None,
         hide=()):
    """Import + flatten + clean + rig. `remove`: substrings of object or material names to delete (badges/logos).
    `hide`: substrings of objects to hide (e.g. the stock engine when you show your own)."""
    coll = coll or core.ensure_coll(f"CAR_{name}")
    new = _import(path)
    meshes = [o for o in new if o.type == "MESH"]
    dg = bpy.context.evaluated_depsgraph_get()
    # bake world transforms, drop the original hierarchy/armature (glTF nodes, FBX rigs)
    for o in meshes:
        mw = o.matrix_world.copy()
        for m in list(o.modifiers):
            if m.type == "ARMATURE":
                o.modifiers.remove(m)
        o.parent = None; o.matrix_world = mw
    for o in new:
        if o.type != "MESH":
            bpy.data.objects.remove(o, do_unlink=True)
    car = Car(name)
    kill = [o for o in meshes if any(k in o.name.lower() for k in remove)
            or any(k in m.name.lower() for m in o.data.materials if m for k in remove)
            or role_of(o.data.materials[0].name if o.data.materials and o.data.materials[0] else "") == "badge"]
    for o in kill:
        meshes.remove(o); bpy.data.objects.remove(o, do_unlink=True)
    for o in meshes:
        for c in list(o.users_collection):
            c.objects.unlink(o)
        coll.objects.link(o)
        o.data.transform(o.matrix_world); o.matrix_world = Matrix.Identity(4)   # mesh in world space
        if any(k in o.name.lower() for k in hide):
            o.hide_render = True; o.hide_viewport = True; o["s3d_hidden"] = True
    car.meshes = [o for o in meshes if not o.get("s3d_hidden")]
    # materials -> realistic roles
    for m in {m for o in meshes for m in o.data.materials if m}:
        car.mats[m.name] = build_material(m, role_of(m.name), paint)
    _rig(car, coll)
    return car


def _co(o):
    """Vertex coordinates (mesh space) as an (N, 3) numpy array (bpy collections cannot be step-sliced)."""
    a = np.empty(len(o.data.vertices) * 3, np.float64); o.data.vertices.foreach_get("co", a)
    return a.reshape(-1, 3)


def strip_region(car, center, half, pattern=None):
    """Delete loose mesh parts lying entirely inside an axis-aligned box (canonical car space): grille logos etc."""
    import bmesh
    c, h = np.array(center), np.array(half); n_del = 0
    for o in car.meshes:
        if pattern and not re.search(pattern, o.name.lower()):
            continue
        P = _co(o)
        inside = np.all(np.abs(P - c) <= h, axis=1)
        if not inside.any():
            continue
        bm = bmesh.new(); bm.from_mesh(o.data); bm.verts.ensure_lookup_table(); seen, kill = set(), []
        for v in bm.verts:
            if v.index in seen or not inside[v.index]:
                continue
            stack, part = [v], []
            while stack:
                x = stack.pop()
                if x.index in seen: continue
                seen.add(x.index); part.append(x)
                stack += [e.other_vert(x) for e in x.link_edges if e.other_vert(x).index not in seen]
            if all(inside[x.index] for x in part):
                kill += part
        if kill:
            bmesh.ops.delete(bm, geom=kill, context="VERTS"); bm.to_mesh(o.data); n_del += len(kill)
        bm.free()
    return n_del


def _verts_world(o):
    M = np.array(o.matrix_world); P = _co(o)
    return P @ M[:3, :3].T + M[:3, 3]


def _rig(car, coll):
    meshes = car.meshes
    is_wheel = lambda o: re.search(r"wheel|tire|tyre|rim", o.name.lower()) and not re.search(r"brake|steer", o.name.lower())
    wheel_meshes = [o for o in meshes if is_wheel(o)]
    tires = [o for o in wheel_meshes if any(m and role_of(m.name) == "tire" for m in o.data.materials)] or wheel_meshes
    # cluster tires into 4 corners by position
    cents = []
    for t in tires:
        V = _verts_world(t)
        cents.append(((V.min(0) + V.max(0)) / 2, V))
    allc = np.array([c for c, _ in cents]); mid = (allc.min(0) + allc.max(0)) / 2
    long_ax = 1 if np.ptp(allc[:, 1]) > np.ptp(allc[:, 0]) else 0          # wheelbase axis (x or y)
    lat_ax = 1 - long_ax
    zmin = min(V[:, 2].min() for _, V in cents)
    # forward direction: headlights side (emissive 'head' parts) else the side with the longer overhang
    heads = [o for o in meshes if any(m and m.get("s3d_role") == "head" for m in o.data.materials)]
    if heads:
        hc = np.mean([_verts_world(o).mean(0) for o in heads], 0)
        fwd_sign = 1 if hc[long_ax] > mid[long_ax] else -1
    else:
        fwd_sign = -1
    groups = {}
    for (c, V), t in zip(cents, tires):
        front = (c[long_ax] - mid[long_ax]) * fwd_sign > 0
        # model's right side = forward x up
        fwd = np.zeros(3); fwd[long_ax] = fwd_sign; right = np.cross(fwd, [0, 0, 1])
        is_right = np.dot(c - mid, right) > 0
        groups.setdefault(("F" if front else "R") + ("R" if is_right else "L"), []).append((c, V, t))
    # model frame -> canonical car frame (forward -Y, left +X, up Z, origin = wheelbase centre on the ground)
    fwd = Vector((0, 0, 0)); fwd[long_ax] = fwd_sign
    up = Vector((0, 0, 1)); right = fwd.cross(up)
    R = Matrix((-right, -fwd, up)).transposed()      # columns: X=left, Y=back, Z=up  (model -> canonical inverse)
    origin = Vector((mid[0], mid[1], zmin))
    to_canon = (Matrix.Translation(origin) @ R.to_4x4()).inverted()
    for o in meshes:
        o.data.transform(to_canon)
    root = core.empty(f"{car.name}_root", coll=coll, size=1.0, kind="ARROWS"); car.root = root
    centers = {}
    for k, lst in groups.items():
        V = np.vstack([v for _, v, _ in lst]); Mc = np.array(to_canon); Vc = V @ Mc[:3, :3].T + Mc[:3, 3]
        c = (Vc.min(0) + Vc.max(0)) / 2; r = (Vc[:, 2].max() - Vc[:, 2].min()) / 2
        centers[k] = (Vector(c), r)
    car.wheelbase = abs(centers["FL"][0].y - centers["RL"][0].y) if "FL" in centers and "RL" in centers else 2.7
    car.track = abs(centers["FL"][0].x - centers["FR"][0].x) if "FL" in centers and "FR" in centers else 1.6
    ys = np.concatenate([_co(o)[:, 1] for o in meshes])
    car.length = float(ys.max() - ys.min()); car.front_y = float(ys.min()); car.rear_y = float(ys.max())
    body = core.empty(f"{car.name}_body", loc=(0, 0, 0.5), coll=coll, size=0.4); body.parent = root
    car.body = body
    wheel_set = set(o for o in wheel_meshes)
    for k, (c, r) in centers.items():
        hub = core.empty(f"{car.name}_hub_{k}", loc=c, coll=coll, size=0.2); hub.parent = root
        steer = core.empty(f"{car.name}_steer_{k}", coll=coll, size=0.15); steer.parent = hub
        spin = core.empty(f"{car.name}_spin_{k}", coll=coll, size=0.25, kind="CIRCLE"); spin.parent = steer
        spin.rotation_mode = "XYZ"; steer.rotation_mode = "XYZ"
        car.wheels[k] = {"hub": hub, "steer": steer, "spin": spin, "r": r, "center": c, "z0": c.z}
    # attach meshes: wheels -> spin of nearest corner; brakes -> steer (front) / hub (rear); rest -> body
    for o in meshes:
        cen = Vector(tuple(_co(o).mean(0)))
        k = min(car.wheels, key=lambda kk: (car.wheels[kk]["center"] - cen).length) if car.wheels else None
        near = k and (car.wheels[k]["center"] - cen).length < car.wheels[k]["r"] * 1.1
        if o in wheel_set and near:
            _parent_keep(o, car.wheels[k]["spin"])
        elif near and re.search(r"brake|caliper|disc|rotor", o.name.lower()):
            _parent_keep(o, car.wheels[k]["steer"])
        else:
            _parent_keep(o, body)
    _rig_interior(car, coll)
    root["s3d_car"] = car.name
    return car


def _parent_keep(o, parent):
    """Parent keeping the world transform (matrix_world of fresh empties is stale until a view-layer update)."""
    bpy.context.view_layer.update()
    mw = o.matrix_world.copy()
    o.parent = parent
    o.matrix_parent_inverse = parent.matrix_world.inverted()
    o.matrix_basis = Matrix.Identity(4) if o.type == "MESH" else o.matrix_basis
    bpy.context.view_layer.update()
    if (o.matrix_world.translation - mw.translation).length > 1e-4:
        o.matrix_world = mw


def _pivot(car, name, loc, parent, coll, size=0.05, mode="XYZ"):
    e = core.empty(f"{car.name}_{name}", loc=loc, coll=coll, size=size); e.rotation_mode = mode
    _parent_keep(e, parent)
    return e


def _find(car, pat, exclude=None):
    for o in car.meshes:
        n = o.name.lower()
        if re.search(pat, n) and not (exclude and re.search(exclude, n)):
            return o
    return None


def _rig_interior(car, coll):
    bpy.context.view_layer.update()
    sw = _find(car, r"steer(?!ing.?box)|steering.?wheel", exclude=r"box|rack|column_lower")
    if sw:
        c, axes, s = _axis_pca(_co(sw))
        ax = axes[2] if axes[2].y > 0 else -axes[2]          # column axis points back toward the driver
        # rim centre: centre of the bounding box of points projected on the wheel plane
        piv = _pivot(car, "steerwheel", c, car.body, coll, 0.12, "QUATERNION"); piv["axis"] = tuple(ax)
        _parent_keep(sw, piv); car.parts["steer_wheel"] = piv
    for key, pat in (("gas", r"pedal.?gas|gas.?pedal|accel|throttle.?pedal"), ("brake_pedal", r"pedal.?brake|brake.?pedal")):
        p = _find(car, pat)
        if p:
            P = _co(p); xs, ys, zs = P[:, 0], P[:, 1], P[:, 2]
            hinge = Vector(((min(xs) + max(xs)) / 2, max(ys) - 0.02, max(zs) + 0.12))   # hanging pedal pivot above
            piv = _pivot(car, key, hinge, car.body, coll)
            _parent_keep(p, piv); car.parts[key] = piv
    sh = _find(car, r"shift|gear.?lever|gearstick")
    if sh:
        c = Vector(tuple(_co(sh).mean(0)))
        piv = _pivot(car, "shifter", c - Vector((0, 0, 0.12)), car.body, coll)
        _parent_keep(sh, piv); car.parts["shifter"] = piv
    g = _find(car, r"gauge|cluster|instrument")
    if g:
        _gauges(car, g, coll)


def _dial_image(name, kind, size=512):
    """Instrument dial face (tach 0-8 x1000 rpm with red zone, speedo 0-160 mph), drawn with numpy, no files."""
    img = bpy.data.images.get(name) or bpy.data.images.new(name, size, size, alpha=True)
    yy, xx = np.mgrid[0:size, 0:size]; cx = cy = size / 2; r = np.hypot(xx - cx, yy - cy) / (size / 2)
    ang = np.degrees(np.arctan2(xx - cx, cy - yy))                      # 0 = up, +clockwise
    px = np.zeros((size, size, 4), np.float32); px[..., 3] = (r <= 1.0)
    face = (r <= 1.0)
    px[face, :3] = 0.012
    ring = (r > 0.93) & (r <= 0.97); px[ring, :3] = 0.25
    maxv, step, red = (8, 1, 6.5) if kind == "tach" else (160, 20, None)
    for i in range(0, int(maxv / (step / 5)) + 1):
        v = i * step / 5; a = -135 + 270 * v / maxv
        major = abs(v / step - round(v / step)) < 1e-6
        band = (r > (0.74 if major else 0.8)) & (r < 0.9) & (np.abs(((ang - a) + 180) % 360 - 180) < (1.4 if major else 0.7))
        col = (0.9, 0.12, 0.08) if red and v >= red else (0.85, 0.85, 0.85)
        px[band, :3] = col
    if red:
        a0 = -135 + 270 * red / maxv
        arc = (r > 0.9) & (r < 0.93) & (ang > a0) & (ang < 135); px[arc, :3] = (0.9, 0.1, 0.06)
    hub = r < 0.08; px[hub, :3] = 0.3
    img.pixels.foreach_set(px[::-1].ravel()); img.pack()
    return img


def _gauges(car, cluster, coll):
    """Emissive dial faces + needles placed on the instrument cluster, facing the driver."""
    V = _co(cluster)
    lo, hi = V.min(0), V.max(0); c = Vector(tuple((lo + hi) / 2))
    eye = Vector((c.x, c.y + 0.75, c.z + 0.18))                    # driver's eye: behind (+Y) and above
    n = (eye - c).normalized()
    yv = (Vector((0, 0, 1)) - n * n.z).normalized(); xv = yv.cross(n)  # dial frame: x = driver's right, y = up, z = n
    frame = Matrix((xv, yv, n)).transposed()
    w = hi[0] - lo[0]; r = min(0.068, w / 4.6)
    for i, kind in enumerate(("tach", "speedo")):
        p = c + xv * ((-1 if i == 0 else 1) * w * 0.24) + n * 0.012      # tach on the driver's left
        bpy.ops.mesh.primitive_circle_add(vertices=48, radius=r, fill_type="TRIFAN")
        dial = bpy.context.object; dial.name = f"{car.name}_dial_{kind}"
        for cc in list(dial.users_collection): cc.objects.unlink(dial)
        coll.objects.link(dial)
        dial.matrix_world = Matrix.Translation(p) @ frame.to_4x4()
        dial.data.uv_layers.new(name="UVMap")
        for poly in dial.data.polygons:
            for li in poly.loop_indices:
                co = dial.data.vertices[dial.data.loops[li].vertex_index].co
                dial.data.uv_layers[0].data[li].uv = (co.x / (2 * r) + 0.5, co.y / (2 * r) + 0.5)
        m = bpy.data.materials.new(f"{car.name}_dial_{kind}"); nt, b, out = _bsdf(m)
        tex = nt.nodes.new("ShaderNodeTexImage"); tex.image = _dial_image(f"dial_{kind}", kind)
        em = nt.nodes.new("ShaderNodeEmission"); em.inputs["Strength"].default_value = 3.0
        nt.links.new(tex.outputs["Color"], em.inputs["Color"]); nt.links.new(em.outputs[0], out.inputs[0])
        dial.data.materials.append(m)
        _parent_keep(dial, car.body)
        # needle: thin bar from hub upward (dial-local +Y = 0 rpm direction after rotation offset)
        bpy.ops.mesh.primitive_cube_add(size=1)
        nd = bpy.context.object; nd.name = f"{car.name}_needle_{kind}"
        for cc in list(nd.users_collection): cc.objects.unlink(nd)
        coll.objects.link(nd)
        nd.data.transform(Matrix.Diagonal((0.0035, r * 0.85, 0.002, 1)) @ Matrix.Translation((0, 0.5, 0)))
        nm = bpy.data.materials.new(f"{car.name}_needle"); nnt, nb, nout = _bsdf(nm)
        _set(nb, base=(1, 0.25, 0.02, 1), emit=(1, 0.25, 0.02, 1), emit_s=6.0); nd.data.materials.append(nm)
        piv = core.empty(f"{car.name}_needlepiv_{kind}", coll=coll, size=0.02)
        piv.matrix_world = Matrix.Translation(p + n * 0.004) @ frame.to_4x4()
        piv.rotation_mode = "XYZ"
        nd.parent = piv; nd.matrix_parent_inverse = Matrix.Identity(4); nd.location = (0, 0, 0)
        _parent_keep(piv, car.body)
        car.needles[kind] = piv


def set_needle(car, kind, frac, frame):
    """frac 0..1 of the dial sweep (-135..+135 deg)."""
    piv = car.needles.get(kind)
    if not piv:
        return
    base = Matrix.Identity(3)
    piv.rotation_mode = "QUATERNION"
    if "q0" not in piv:
        piv["q0"] = tuple(piv.matrix_basis.to_quaternion())
    q0 = Quaternion(piv["q0"])
    a = math.radians(135 - 270 * core.clamp(frac))
    piv.rotation_quaternion = q0 @ Quaternion((0, 0, 1), a)
    piv.keyframe_insert("rotation_quaternion", frame=frame)


# ------------------------------------------------------------------ path + driving
class Path:
    """Polyline centreline with arc-length parameter; sample(s) -> point, tangent, curvature (signed, +left)."""
    def __init__(self, pts):
        P = [Vector(p) for p in pts]; self.P = P
        self.S = [0.0]
        for a, b in zip(P, P[1:]):
            self.S.append(self.S[-1] + (b - a).length)
        self.L = self.S[-1]

    def _idx(self, s):
        s = core.clamp(s, 0.0, self.L - 1e-6)
        lo, hi = 0, len(self.S) - 1
        while hi - lo > 1:
            m = (lo + hi) // 2
            if self.S[m] <= s: lo = m
            else: hi = m
        return lo, (s - self.S[lo]) / max(1e-9, self.S[lo + 1] - self.S[lo])

    def point(self, s):
        if s < 0:      # straight extension before the start
            return self.P[0] + (self.P[0] - self.P[1]).normalized() * (-s)
        if s > self.L:
            return self.P[-1] + (self.P[-1] - self.P[-2]).normalized() * (s - self.L)
        i, u = self._idx(s); return self.P[i].lerp(self.P[i + 1], u)

    def tangent(self, s, h=1.5):
        return (self.point(s + h) - self.point(s - h)).normalized()

    def curvature(self, s, h=4.0):
        t0, t1 = self.tangent(s - h), self.tangent(s + h)
        a0, a1 = math.atan2(t0.y, t0.x), math.atan2(t1.y, t1.x)
        d = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
        return d / (2 * h)


def frame_matrix(path, s, lateral=0.0, height=0.0):
    """World matrix of a car-frame (X left, Y back, Z up) at arc length s; lateral +left offset in metres."""
    t = path.tangent(s); up = Vector((0, 0, 1))
    right = t.cross(up).normalized(); up2 = right.cross(t).normalized()
    p = path.point(s) - right * lateral + up2 * height
    M = Matrix((-right, -t, up2)).transposed().to_4x4(); M.translation = p
    return M


def _spring(xs, dt, f=1.6, zeta=0.55):
    """Second-order follower: body pitch/roll lag + overshoot like real suspension."""
    w = 2 * math.pi * f; y, v, out = xs[0], 0.0, []
    for x in xs:
        a = w * w * (x - y) - 2 * zeta * w * v; v += a * dt; y += v * dt; out.append(y)
    return out


def speed_profile(speeds, s0, f0, f1, fps=30):
    """speeds [(frame, m/s)] eased between keys -> per-frame (s, v); s integrated from s0 (always consistent)."""
    speeds = sorted(speeds); dt = 1.0 / fps; S, Vv = [], []; s = s0
    def v_at(f):
        if f <= speeds[0][0]: return speeds[0][1]
        for (fa, a), (fb, b) in zip(speeds, speeds[1:]):
            if fa <= f <= fb: return a + (b - a) * core.smooth((f - fa) / max(1, fb - fa))
        return speeds[-1][1]
    for f in range(f0, f1 + 1):
        v = v_at(f); S.append(s); Vv.append(v); s += v * dt
    return S, Vv


def drive(car, path, speeds, s0=0.0, frames=None, fps=30, lateral=0.0, pitch_k=0.011, roll_k=0.012, ratio=14.0,
          idle_rpm=900, gears=(0, 3.2, 2.1, 1.5, 1.15, 0.92), final=3.55, wheelspin=None, throttle=None, seed=1,
          max_tilt=0.07, spin_start=None, noise_offset=0):
    """Keyframe the whole car for every frame in `frames` (f0, f1) (default: first..last speed key).
    speeds: [(frame, m/s)] eased between keys; distance integrated from s0 along the path.
    wheelspin: [(f0, f1, extra_rad_per_s)] rear tyre slip (launch). throttle: [(frame, 0..1)] pedal/rpm hint.
    spin_start: {wheel: angle} to continue wheel rotation from another drive (seamless loops); noise_offset shifts the
    road-texture noise clock (use -NF on the payoff drive of a looping video so its last frame flows into frame 0).
    Returns frames, s, v and the wheel angles on the first/last frame ("spin_first", "spin_last")."""
    f_first, f_last = frames or (sorted(speeds)[0][0], sorted(speeds)[-1][0])
    frames = list(range(f_first, f_last + 1)); dt = 1.0 / fps
    S, Vv = speed_profile(speeds, s0, f_first, f_last, fps)
    A = [0.0] + [(b - a) / dt for a, b in zip(Vv, Vv[1:])]
    A = _spring(A, dt, f=3.0, zeta=0.9)
    lat = [v * v * path.curvature(s) for s, v in zip(S, Vv)]
    cl = lambda x: max(-max_tilt, min(max_tilt, x))
    pitch = _spring([cl(-pitch_k * a) for a in A], dt); roll = _spring([cl(-roll_k * a) for a in lat], dt)
    noise = core.Noise1D(seed)
    spin_acc = {k: (spin_start or {}).get(k, 0.0) for k in car.wheels}
    spin_first = None
    thr_keys = sorted(throttle or [])
    def thr(f):
        if not thr_keys: return 0.6
        for (fa, a), (fb, b) in zip(thr_keys, thr_keys[1:]):
            if fa <= f <= fb: return a + (b - a) * core.smooth((f - fa) / max(1, fb - fa))
        return thr_keys[0][1] if f < thr_keys[0][0] else thr_keys[-1][1]
    for i, f in enumerate(frames):
        s, v = S[i], Vv[i]
        M = frame_matrix(path, s, lateral)
        car.root.matrix_world = M; car.root.keyframe_insert("location", frame=f); car.root.rotation_mode = "QUATERNION"
        car.root.rotation_quaternion = M.to_quaternion(); car.root.keyframe_insert("rotation_quaternion", frame=f)
        fn = f + noise_offset
        hz = 0.004 * (noise(fn / fps * 3.1) if noise else 0.0)    # road texture through the springs
        car.body.rotation_mode = "XYZ"
        car.body.rotation_euler = (pitch[i] + 0.0015 * (noise(fn / fps * 2.3 + 7) if noise else 0), roll[i], 0)
        car.body.location = (0, 0, 0.5 + hz)
        car.body.keyframe_insert("rotation_euler", frame=f); car.body.keyframe_insert("location", frame=f)
        k_ = path.curvature(s + car.wheelbase / 2)
        delta = math.atan(car.wheelbase * k_)
        for k, w in car.wheels.items():
            if k[0] == "F":
                inner = (k[1] == "L") == (delta > 0)
                d = math.atan(car.wheelbase / max(0.5, abs(1 / (abs(k_) + 1e-6)) + (-1 if inner else 1) * car.track / 2)) if abs(k_) > 1e-5 else 0.0
                w["steer"].rotation_euler = (0, 0, math.copysign(d, delta)); w["steer"].keyframe_insert("rotation_euler", frame=f)
            extra = 0.0
            if wheelspin and k[0] == "R":
                for f0, f1, rate in wheelspin:
                    if f0 <= f <= f1:
                        extra = rate * math.sin(math.pi * (f - f0) / max(1, f1 - f0)) * dt
            spin_acc[k] += v * dt / w["r"] + extra
            w["spin"].rotation_euler = (spin_acc[k], 0, 0); w["spin"].keyframe_insert("rotation_euler", frame=f)
        if spin_first is None:
            spin_first = dict(spin_acc)
            # suspension: body pitch/roll compress the corners (wheels stay on the road, hub travels in the arch)
        sw = car.parts.get("steer_wheel")
        if sw:
            ax = Vector(sw["axis"]); sw.rotation_quaternion = Quaternion(ax, delta * ratio); sw.keyframe_insert("rotation_quaternion", frame=f)
        th = thr(f)
        if "gas" in car.parts:
            car.parts["gas"].rotation_euler = (-0.32 * th, 0, 0); car.parts["gas"].keyframe_insert("rotation_euler", frame=f)
        # rpm from road speed and gear (shift at 6200)
        wheel_rpm = v / max(0.2, list(car.wheels.values())[0]["r"]) * 60 / (2 * math.pi)
        rpm, g = idle_rpm, 1
        for g in range(1, len(gears)):
            rpm = wheel_rpm * gears[g] * final
            if rpm < 6200: break
        rpm = max(idle_rpm, rpm) + (extra / dt * 60 / (2 * math.pi) * gears[g] * final if wheelspin else 0)
        set_needle(car, "tach", rpm / 8000.0, f)
        set_needle(car, "speedo", v * 2.23694 / 160.0, f)
    for ob in [car.root, car.body] + [w[k] for w in car.wheels.values() for k in ("steer", "spin")] + list(car.parts.values()) + list(car.needles.values()):
        if ob.animation_data and ob.animation_data.action:
            _linear(ob.animation_data.action)
    return {"frames": frames, "s": S, "v": Vv, "spin_first": spin_first, "spin_last": dict(spin_acc),
            "radius": {k: w["r"] for k, w in car.wheels.items()}}


def _linear(action):
    """Per-frame keys: make interpolation linear (Blender 4.4+ layered actions and older)."""
    curves = []
    if hasattr(action, "fcurves"):
        try:
            curves = list(action.fcurves)
        except Exception:
            curves = []
    if not curves and hasattr(action, "layers"):
        for L in action.layers:
            for st in L.strips:
                for cb in getattr(st, "channelbags", []):
                    curves += list(cb.fcurves)
    for fc in curves:
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"


def park(car, path, s, frame, lateral=0.0):
    M = frame_matrix(path, s, lateral)
    car.root.matrix_world = M; car.root.rotation_mode = "QUATERNION"; car.root.rotation_quaternion = M.to_quaternion()
    car.root.keyframe_insert("location", frame=frame); car.root.keyframe_insert("rotation_quaternion", frame=frame)


# ------------------------------------------------------------------ x-ray
def xray_group(car, color=(0.55, 0.85, 1.0)):
    """Node group shared by the body materials: factor 0 solid .. 1 x-ray, wiping front to back with a glowing edge.
    Animate with xray_keys(car, [(frame, value)])."""
    ng = bpy.data.node_groups.get(f"S3D_XRay_{car.name}")
    if ng:
        return ng
    ng = bpy.data.node_groups.new(f"S3D_XRay_{car.name}", "ShaderNodeTree")
    ng.interface.new_socket("Shader", in_out="INPUT", socket_type="NodeSocketShader")
    ng.interface.new_socket("Shader", in_out="OUTPUT", socket_type="NodeSocketShader")
    N = ng.nodes; Ln = ng.links
    gi = N.new("NodeGroupInput"); go = N.new("NodeGroupOutput")
    tc = N.new("ShaderNodeTexCoord"); tc.object = car.root
    sep = N.new("ShaderNodeSeparateXYZ"); Ln.new(tc.outputs["Object"], sep.inputs[0])
    val = N.new("ShaderNodeValue"); val.name = "wipe"; val.label = "wipe"; val.outputs[0].default_value = 0.0
    # p: 0 at the front bumper .. 1 at the rear; factor = clamp((wipe*1.35 - p) / 0.25)
    mp = N.new("ShaderNodeMapRange"); mp.inputs["From Min"].default_value = car.front_y; mp.inputs["From Max"].default_value = car.rear_y
    Ln.new(sep.outputs["Y"], mp.inputs["Value"])
    mul = N.new("ShaderNodeMath"); mul.operation = "MULTIPLY"; mul.inputs[1].default_value = 1.35; Ln.new(val.outputs[0], mul.inputs[0])
    sub = N.new("ShaderNodeMath"); sub.operation = "SUBTRACT"; Ln.new(mul.outputs[0], sub.inputs[0]); Ln.new(mp.outputs[0], sub.inputs[1])
    fac = N.new("ShaderNodeMapRange"); fac.inputs["From Min"].default_value = 0.0; fac.inputs["From Max"].default_value = 0.25
    Ln.new(sub.outputs[0], fac.inputs["Value"])
    # edge glow 4f(1-f)
    one = N.new("ShaderNodeMath"); one.operation = "SUBTRACT"; one.inputs[0].default_value = 1.0; Ln.new(fac.outputs[0], one.inputs[1])
    edge = N.new("ShaderNodeMath"); edge.operation = "MULTIPLY"; Ln.new(fac.outputs[0], edge.inputs[0]); Ln.new(one.outputs[0], edge.inputs[1])
    lw = N.new("ShaderNodeLayerWeight"); lw.inputs["Blend"].default_value = 0.35
    pw = N.new("ShaderNodeMath"); pw.operation = "POWER"; pw.inputs[1].default_value = 1.6; Ln.new(lw.outputs["Facing"], pw.inputs[0])
    rim = N.new("ShaderNodeMath"); rim.operation = "MULTIPLY_ADD"; rim.inputs[1].default_value = 2.2; rim.inputs[2].default_value = 0.08
    Ln.new(pw.outputs[0], rim.inputs[0])
    # the glowing wipe edge fades in/out while the wipe leaves/reaches the front bumper (no bright band snapping off)
    ends = N.new("ShaderNodeMapRange"); ends.interpolation_type = "SMOOTHSTEP"; ends.clamp = True
    ends.inputs["From Min"].default_value = 0.0; ends.inputs["From Max"].default_value = 0.3; Ln.new(val.outputs[0], ends.inputs["Value"])
    edge2 = N.new("ShaderNodeMath"); edge2.operation = "MULTIPLY"; Ln.new(edge.outputs[0], edge2.inputs[0]); Ln.new(ends.outputs[0], edge2.inputs[1])
    eg = N.new("ShaderNodeMath"); eg.operation = "MULTIPLY_ADD"; eg.inputs[1].default_value = 30.0
    Ln.new(edge2.outputs[0], eg.inputs[0]); Ln.new(rim.outputs[0], eg.inputs[2])
    em = N.new("ShaderNodeEmission"); em.inputs["Color"].default_value = (*color, 1); Ln.new(eg.outputs[0], em.inputs["Strength"])
    tr = N.new("ShaderNodeBsdfTransparent")
    alpha = N.new("ShaderNodeMath"); alpha.operation = "MULTIPLY_ADD"; alpha.inputs[1].default_value = 0.45; alpha.inputs[2].default_value = 0.03
    Ln.new(pw.outputs[0], alpha.inputs[0])
    xr = N.new("ShaderNodeMixShader"); Ln.new(alpha.outputs[0], xr.inputs[0]); Ln.new(tr.outputs[0], xr.inputs[1]); Ln.new(em.outputs[0], xr.inputs[2])
    mix = N.new("ShaderNodeMixShader"); Ln.new(fac.outputs["Result"], mix.inputs[0]); Ln.new(gi.outputs[0], mix.inputs[1]); Ln.new(xr.outputs[0], mix.inputs[2])
    # the glowing wipe edge also shows on the still-solid side
    add = N.new("ShaderNodeAddShader"); em2 = N.new("ShaderNodeEmission"); em2.inputs["Color"].default_value = (*color, 1)
    e2 = N.new("ShaderNodeMath"); e2.operation = "MULTIPLY"; e2.inputs[1].default_value = 25.0; Ln.new(edge.outputs[0], e2.inputs[0])
    Ln.new(e2.outputs[0], em2.inputs["Strength"]); Ln.new(mix.outputs[0], add.inputs[0]); Ln.new(em2.outputs[0], add.inputs[1])
    Ln.new(add.outputs[0], go.inputs[0])
    return ng


XRAY_ROLES = ("paint", "black", "glass", "tail", "head", "amber", "chrome", "leather", "seat", "cage", "rim", "tire", "brake", "metal")


def xray_materials(car, roles=XRAY_ROLES, extra=()):
    ng = xray_group(car)
    mats = [m for m in car.mats.values() if m.get("s3d_role") in roles] + list(extra)
    for m in mats:
        nt = m.node_tree
        out = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")
        if not out.inputs[0].links or out.inputs[0].links[0].from_node.type == "GROUP":
            continue
        src = out.inputs[0].links[0].from_socket
        g = nt.nodes.new("ShaderNodeGroup"); g.node_tree = ng; g.location = (out.location.x - 200, out.location.y - 200)
        nt.links.new(src, g.inputs[0]); nt.links.new(g.outputs[0], out.inputs[0])
    return ng


def xray_keys(car, keys):
    ng = xray_group(car); v = ng.nodes["wipe"]
    for f, val in keys:
        v.outputs[0].default_value = val; v.outputs[0].keyframe_insert("default_value", frame=f)


def lights(car, on=True, coll=None, energy=900.0):
    """Real spot lights at the headlights (they light the road at dusk) + emissive lenses already in the materials."""
    coll = coll or car.root.users_collection[0]
    heads = [o for o in car.meshes if any(m and m.get("s3d_role") == "head" for m in o.data.materials)]
    for o in heads:
        c = Vector(tuple(_co(o).mean(0)))
        L = bpy.data.lights.new(f"{car.name}_headlamp", "SPOT"); L.energy = energy; L.spot_size = math.radians(55)
        L.spot_blend = 0.6; L.color = (1.0, 0.93, 0.82)
        try: L.shadow_soft_size = 0.05
        except Exception: pass
        ob = bpy.data.objects.new(L.name, L); coll.objects.link(ob)
        ob.location = c + Vector((0, -0.08, 0)); ob.rotation_euler = (math.radians(-84), 0, 0)   # aim forward (-Y), slightly down
        _parent_keep(ob, car.body)
        car.lights.append(ob)
    return car.lights
