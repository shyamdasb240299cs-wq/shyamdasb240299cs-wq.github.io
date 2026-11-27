"""Prepare the two cars + driver once -> assets/cars_raw.blend (meshes only, real scale, canonical orientation:
forward -Y, left +X, up Z, ground z=0 at the tyre bottoms, wheelbase centre at the origin). Rigging happens in
the scene build (vehicle._rig) so the empties are always fresh.
  blender -b -P work/assets_prep.py -- <uploads_dir> <video_dir>"""
import bpy, bmesh, sys, os, math, re
import numpy as np
from mathutils import Vector, Matrix

UP, VD = sys.argv[sys.argv.index("--") + 1:][:2]
F_MP48 = os.path.join(UP, "0df91b84-mclaren_mp48_1993_senna_clean.glb")
F_FW15 = os.path.join(UP, "2380f956-fw15c.glb")
F_FW16 = os.path.join(UP, "da1c42d2-williams_fw16__ayrton_senna.glb")
CLEAN = os.path.join(VD, "assets", "mp48_base_clean.png")

bpy.ops.wm.read_factory_settings(use_empty=True)


def imp(path):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    bpy.context.view_layer.update()
    meshes = [o for o in new if o.type == "MESH"]
    for o in meshes:                       # bake world transform into the mesh, drop the node hierarchy
        mw = o.matrix_world.copy(); o.parent = None; o.data.transform(mw); o.matrix_world = Matrix.Identity(4)
    for o in new:
        if o.type != "MESH":
            bpy.data.objects.remove(o, do_unlink=True)
    return meshes


def coll(name):
    c = bpy.data.collections.new(name); bpy.context.scene.collection.children.link(c); return c


def move(objs, c):
    for o in objs:
        for u in list(o.users_collection): u.objects.unlink(o)
        c.objects.link(o)


def verts(o):
    a = np.empty(len(o.data.vertices) * 3); o.data.vertices.foreach_get("co", a); return a.reshape(-1, 3)


def transform_all(objs, M):
    for o in objs: o.data.transform(M)


def bbox(objs):
    P = np.vstack([verts(o) for o in objs]); return P.min(0), P.max(0)


# ------------------------------------------------------------------ McLaren MP4/8 (Senna)
mp = imp(F_MP48)
body = max(mp, key=lambda o: len(o.data.vertices)); glass = [o for o in mp if o is not body]
# cleaned base colour (no sponsor logos)
img = bpy.data.images.load(CLEAN)
for m in body.data.materials:
    for n in m.node_tree.nodes:
        if n.type == "TEX_IMAGE" and n.outputs[0].links and any(l.to_socket.name == "Base Color" for l in n.outputs[0].links):
            n.image = img
# separate the wheels: loose parts lying wholly inside each wheel cylinder (model space: forward -X, up Z)
WHEELS = {"FL": (-1.53, -1.09, -0.016), "FR": (-1.53, 1.07, -0.016), "RL": (2.13, -1.07, -0.019), "RR": (2.13, 1.01, -0.019)}
HALFW = {"F": 0.205, "R": 0.275}; RAD = 0.43
bm = bmesh.new(); bm.from_mesh(body.data); bm.verts.ensure_lookup_table()
co = np.array([v.co[:] for v in bm.verts]); lab = -np.ones(len(co), int); k = 0
for v in bm.verts:
    if lab[v.index] >= 0: continue
    st = [v]
    while st:
        x = st.pop()
        if lab[x.index] >= 0: continue
        lab[x.index] = k; st += [e.other_vert(x) for e in x.link_edges if lab[e.other_vert(x).index] < 0]
    k += 1
assign = {}
for key, (cx, cy, cz) in WHEELS.items():
    inside = (np.hypot(co[:, 0] - cx, co[:, 2] - cz) <= RAD) & (np.abs(co[:, 1] - cy) <= HALFW[key[0]] + 0.02)
    tot = np.bincount(lab, minlength=k); ins = np.bincount(lab, weights=inside.astype(float), minlength=k)
    for p in np.nonzero((ins == tot) & (tot > 0))[0]:
        assign[p] = key
bm.free()
wheel_objs = []
for key in WHEELS:
    parts = [p for p, kk in assign.items() if kk == key]
    sel = np.isin(lab, parts)
    bpy.ops.object.select_all(action="DESELECT"); bpy.context.view_layer.objects.active = body; body.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT"); bpy.ops.mesh.select_all(action="DESELECT"); bpy.ops.object.mode_set(mode="OBJECT")
    # vertex indices are unchanged until the first separate, so recompute selection by position each time
    P = verts(body); cx, cy, cz = WHEELS[key]
    insel = (np.hypot(P[:, 0] - cx, P[:, 2] - cz) <= RAD) & (np.abs(P[:, 1] - cy) <= HALFW[key[0]] + 0.02)
    # only whole loose parts: rebuild labels on the current mesh
    bm = bmesh.new(); bm.from_mesh(body.data); bm.verts.ensure_lookup_table()
    lab2 = -np.ones(len(P), int); kk = 0
    for v in bm.verts:
        if lab2[v.index] >= 0: continue
        st = [v]
        while st:
            x = st.pop()
            if lab2[x.index] >= 0: continue
            lab2[x.index] = kk; st += [e.other_vert(x) for e in x.link_edges if lab2[e.other_vert(x).index] < 0]
        kk += 1
    bm.free()
    tot = np.bincount(lab2, minlength=kk); ins = np.bincount(lab2, weights=insel.astype(float), minlength=kk)
    whole = np.nonzero((ins == tot) & (tot > 0))[0]
    selv = np.isin(lab2, whole)
    body.data.vertices.foreach_set("select", selv.tolist())
    bpy.ops.object.mode_set(mode="EDIT"); bpy.ops.mesh.separate(type="SELECTED"); bpy.ops.object.mode_set(mode="OBJECT")
    new = [o for o in bpy.context.selected_objects if o is not body][0]
    new.name = f"MP48_TIRE_{key}"; wheel_objs.append(new)
    print(f"wheel {key}: {int(selv.sum())} verts, {len(whole)} parts")
body.name = "MP48_body"
for g in glass: g.name = "MP48_glass"
cars_mp = [body] + glass + wheel_objs
# model -> canonical (forward -Y, left +X, up Z): model forward is -X -> rotate +90 deg about Z (x->y)
Rz = Matrix.Rotation(math.radians(90), 4, "Z")   # (-1,0,0) -> (0,-1,0)
transform_all(cars_mp, Rz)
mn, mx = bbox(cars_mp)
s = 2.0 / (mx[0] - mn[0])                          # 1993 max width 2000 mm (wheel outer faces)
W = bbox(wheel_objs)
zmin = W[0][2]; cxy = [(sum(bbox([o])[0][i] + bbox([o])[1][i] for o in wheel_objs) / (2 * len(wheel_objs))) for i in (0, 1)]
T = Matrix.Scale(s, 4) @ Matrix.Translation((-cxy[0], -cxy[1], -zmin))
transform_all(cars_mp, T)
mn, mx = bbox(cars_mp); print("MP4/8 scale", round(s, 4), "size", np.round(mx - mn, 3))
c_mp = coll("MP48_RAW"); move(cars_mp, c_mp)

# ------------------------------------------------------------------ driver figure from the FW16 model
fw16 = imp(F_FW16)
drv = [o for o in fw16 if any(m and m.name.startswith("Senna_") for m in o.data.materials)]
for o in fw16:
    if o not in drv: bpy.data.objects.remove(o, do_unlink=True)
for o in drv:
    o.name = "DRV_" + o.data.materials[0].name.replace("_PBS_SG", "")
mn, mx = bbox(drv); print("driver bbox (FW16 space)", np.round(mn, 3), np.round(mx, 3))
c_drv = coll("DRIVER_RAW"); move(drv, c_drv)

# ------------------------------------------------------------------ rival: FW15C geometry, generic livery
fw = imp(F_FW15)
mn, mx = bbox(fw); print("FW15C raw size", np.round(mx - mn, 4))
L = max(mx - mn)
s2 = 4.3 / L if L < 1.0 or L > 8 else 1.0
transform_all(fw, Matrix.Scale(s2, 4))
mn, mx = bbox(fw); size = mx - mn
long_ax = int(np.argmax(size[:2]))
print("FW15C scaled size", np.round(size, 3), "long axis", "xy"[long_ax])
for o in fw:
    o.name = "RIV_" + o.name
c_fw = coll("RIVAL_RAW"); move(fw, c_fw)
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(VD, "assets", "cars_raw.blend"))
print("saved cars_raw.blend")

# ------------------------------------------------------------------ pass 2: drivers into cockpits, rival livery
def load_img(name):
    return bpy.data.images.load(os.path.join(VD, "assets", name))


def place_driver(prefix, dy, dz, coll_name, suit_img=None, helmet_img=None):
    objs = []
    for o in [o for o in bpy.data.objects if o.name.startswith("DRV_")]:
        c = o.copy(); c.data = o.data.copy(); c.name = prefix + o.name[4:]
        c.data.transform(Matrix.Translation((0, dy, dz)))
        for i, m in enumerate(c.data.materials):
            if m is None: continue
            m2 = m.copy(); m2.name = prefix + m.name; c.data.materials[i] = m2
            target = suit_img if "Body" in m.name else (helmet_img if "Helmet" in m.name else None)
            if target:
                for n in m2.node_tree.nodes:
                    if n.type == "TEX_IMAGE" and n.outputs[0].links and any(l.to_socket.name == "Base Color" for l in n.outputs[0].links):
                        n.image = target
        bpy.data.collections[coll_name].objects.link(c); objs.append(c)
    return objs


place_driver("MP48_drv_", -0.12, -0.03, "MP48_RAW", suit_img=load_img("suit_mclaren.png"))
# FW15C: already canonical (forward -Y, left +X); centre the wheelbase and put the tyres on z=0
riv = [o for o in bpy.data.collections["RIVAL_RAW"].objects]
transform_all(riv, Matrix.Translation((0, 0.4695, 0.005)))
place_driver("RIV_drv_", -0.11, -0.04, "RIVAL_RAW", helmet_img=load_img("helmet_rival.png"))
# generic rival livery: graphite metallic body, orange accents on nose / wing end plates, plain rubber tyres
def bsdf(m):
    return next((n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
for o in riv:
    for i, m in enumerate(o.data.materials):
        if m is None: continue
        nm = m.name.upper()
        if nm == "BODY":
            m.name = "RIV_paint"; b = bsdf(m)
            for l in list(b.inputs["Base Color"].links): m.node_tree.links.remove(l)
            b.inputs["Base Color"].default_value = (0.045, 0.047, 0.052, 1); b.inputs["Metallic"].default_value = 0.55
            b.inputs["Roughness"].default_value = 0.3
            if "Coat Weight" in b.inputs: b.inputs["Coat Weight"].default_value = 0.8
        elif nm in ("TIRE_TREAD",):
            b = bsdf(m)
            for l in list(b.inputs["Base Color"].links): m.node_tree.links.remove(l)
            b.inputs["Base Color"].default_value = (0.018, 0.018, 0.019, 1); b.inputs["Roughness"].default_value = 0.7
accent = bpy.data.materials.new("RIV_accent"); accent.use_nodes = True
b = bsdf(accent); b.inputs["Base Color"].default_value = (0.85, 0.22, 0.02, 1); b.inputs["Roughness"].default_value = 0.35
for o in riv:
    if re.search(r"NOSE|FRONT_WING|ENDPLATE|DERIVE|FLAP", o.name.upper()) and o.data.materials and o.data.materials[0] and o.data.materials[0].name == "RIV_paint":
        if "NOSE" in o.name.upper(): o.data.materials[0] = accent
# wheel objects need 'tire'/'rim'/'wheel' in their names for the rig; brake discs ride on the steer pivot
for o in riv:
    if re.search(r"tire_tread", o.name.lower()):
        o.name = "RIV_TIRE_" + re.search(r"_(LF|RF|LR|RR)_", o.name).group(1)
# remove the FW16 source driver
for o in [o for o in bpy.data.objects if o.name.startswith("DRV_")]:
    bpy.data.objects.remove(o, do_unlink=True)
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(VD, "assets", "cars_raw.blend"))
print("saved cars_raw.blend (pass 2)")
