"""Make ANY free character asset engine-ready (.fbx/.glb/.gltf/.blend/.obj).

  normalise   metres, Z-up, faces -Y, feet on the floor, centred, transforms applied, rest pose
  materials   rebuild PBR from the textures next to the file (colour/normal/specular/roughness/opacity),
              skin subsurface, alpha-clipped hair/lash cards
  skeleton    any humanoid rig is mapped by rigkit (Mixamo, Bip01/Rocketbox, UE, CC, Daz, VRM, Rigify, MPFB)
  face        ARKit/viseme shape keys in any naming are detected by rigkit
  library     saved as CHAR_<name> in ~/.shorts3d/library/characters/<name>.blend

blender -b -P engine/assetchar.py -- <asset_file> <name> <library_dir> [textures_dir]
"""
import bpy, os, re, sys, glob, math
from mathutils import Vector, Matrix, Quaternion

ROLES = [("opacity", ("opacity", "alpha", "mask", "transparen")), ("normal", ("normal", "nrm", "_nor", "_n.")),
         ("rough", ("rough",)), ("spec", ("specular", "spec", "_s.")),
         ("color", ("color", "colour", "diffuse", "albedo", "basecolor", "base_color", "diff", "_col", "_d."))]
IMG_EXT = (".png", ".jpg", ".jpeg", ".tga", ".tif", ".tiff", ".exr", ".bmp", ".webp")


def log(*a):
    print("[s3d]", *a)


def import_any(path):
    before = set(bpy.data.objects)
    ext = os.path.splitext(path)[1].lower()
    if ext == ".blend":
        with bpy.data.libraries.load(path, link=False) as (src, dst):
            dst.objects = list(src.objects)
        for o in dst.objects:
            if o and o.name not in bpy.context.scene.collection.objects:
                bpy.context.scene.collection.objects.link(o)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path, use_anim=False, ignore_leaf_bones=False)
    elif ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".obj":
        bpy.ops.wm.obj_import(filepath=path)
    else:
        raise RuntimeError(f"unsupported character file {path}")
    return [o for o in bpy.data.objects if o not in before]


def _eval_bbox(meshes):
    dg = bpy.context.evaluated_depsgraph_get()
    mn = Vector((1e9, 1e9, 1e9)); mx = -mn
    for o in meshes:
        ev = o.evaluated_get(dg); me = ev.to_mesh()
        M = o.matrix_world
        for v in list(me.vertices)[:: max(1, len(me.vertices) // 4000)]:
            p = M @ v.co
            mn = Vector(map(min, mn, p)); mx = Vector(map(max, mx, p))
        ev.to_mesh_clear()
    return mn, mx


def _apply_all(objs, loc=True):
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.hide_set(False); o.select_set(True)
    bpy.context.view_layer.objects.active = next((o for o in objs if o.type == "ARMATURE"), objs[0])
    bpy.ops.object.transform_apply(location=loc, rotation=True, scale=True)


def normalize(objs):
    arm = next((o for o in objs if o.type == "ARMATURE"), None)
    meshes = [o for o in objs if o.type == "MESH"]
    for o in objs:
        o.animation_data_clear()
        if o.type == "MESH" and o.data.shape_keys:
            o.data.shape_keys.animation_data_clear()
    if arm:
        for pb in arm.pose.bones:
            pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0); pb.rotation_euler = (0, 0, 0); pb.scale = (1, 1, 1)
    _apply_all(objs, loc=False)
    bpy.context.view_layer.update()
    mn, mx = _eval_bbox(meshes)
    h = mx.z - mn.z
    # units: centimetre exports are 100x too big, some are 100x too small
    k = 1.0
    if h > 4.0: k = 1.75 / h if h > 400 else 0.01
    elif h < 0.5: k = 1.75 / max(h, 1e-6)
    roots = [o for o in objs if o.parent is None or o.parent not in objs]
    for o in roots:
        o.matrix_world = Matrix.Scale(k, 4) @ o.matrix_world
    bpy.context.view_layer.update()
    # facing: toes point forward (skeleton) or nose/feet protrusion (mesh); turn the character to face -Y
    fwd = None
    if arm:
        from . import rigkit
        try:
            names = {b.name for b in arm.data.bones}
            vecs = []
            for side in "LR":
                ft = next((rigkit._fmt(p, side) for p in rigkit.LIMB["foot"] if rigkit._fmt(p, side) in names), None)
                to = next((rigkit._fmt(p, side) for p in rigkit.LIMB["toe"] if rigkit._fmt(p, side) in names), None)
                if ft and to:
                    v = arm.matrix_world @ arm.data.bones[to].head_local - arm.matrix_world @ arm.data.bones[ft].head_local
                    v.z = 0; vecs.append(v)
            if vecs:
                fwd = sum(vecs, Vector()).normalized()
        except Exception as e:
            log("facing from skeleton failed:", e)
    if fwd is None:
        mn, mx = _eval_bbox(meshes)
        fwd = Vector((0, -1, 0))
    yaw = math.atan2(fwd.x, -fwd.y)  # angle that maps -Y onto fwd
    R = Matrix.Rotation(-yaw, 4, "Z")
    for o in roots:
        o.matrix_world = R @ o.matrix_world
    bpy.context.view_layer.update()
    mn, mx = _eval_bbox(meshes)
    T = Matrix.Translation((-(mn.x + mx.x) / 2, -(mn.y + mx.y) / 2, -mn.z))
    for o in roots:
        o.matrix_world = T @ o.matrix_world
    bpy.context.view_layer.update()
    _apply_all(objs, loc=True)
    mn, mx = _eval_bbox(meshes)
    log(f"normalised: height {mx.z - mn.z:.2f} m, scale x{k:g}, turned {math.degrees(yaw):.0f} deg")
    return arm, meshes, mx.z - mn.z


def fix_skeleton(arm, h):
    """FBX/glTF bones have meaningless tails (Blender IK uses the tail as the hand/foot point -> bent, 'sitting' legs and
    folded elbows). Point every bone at its next joint, give limbs consistent rolls, and hang legs from the pelvis and
    arms from the chest (3ds Max biped parents thighs to the spine and clavicles to the neck). Rest shape is unchanged."""
    from . import rigkit
    if any(b.name.startswith(("ORG-", "MCH-")) for b in arm.data.bones):
        log("rigify control rig detected: skeleton left as is"); return
    b, spine = rigkit.map_bones(arm.data.bones, arm.name)
    k = h / 1.75
    bpy.ops.object.select_all(action="DESELECT")
    arm.hide_set(False); arm.select_set(True); bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm.data.edit_bones
    E = lambda key: eb.get(b.get(key) or "")
    # palm normal per hand from the knuckle line (mirror-correct): fingers x knuckles
    palm = {}
    for s_, g in (("L", 1), ("R", -1)):
        hn, i1, p1, m1 = E(f"hand.{s_}"), E(f"index1.{s_}"), E(f"pinky1.{s_}"), E(f"middle1.{s_}")
        if hn and i1 and p1 and m1:
            along = (m1.head - hn.head).normalized(); across = (i1.head - p1.head).normalized()
            palm[s_] = (along.cross(across) * g).normalized()
        else:
            palm[s_] = Vector((0, 0, -1))
    def aim(bone, tail, roll_to):
        if bone and (tail - bone.head).length > 0.004:
            bone.use_connect = False; bone.tail = tail
            if roll_to is not None:
                bone.align_roll(roll_to)
    fwd, up = Vector((0, -1, 0)), Vector((0, 0, 1))
    torso = spine + [x for x in (b.get("neck"), b.get("head")) if x]
    if spine and E("hips"):
        aim(E("hips"), eb[spine[0]].head, fwd)
    for i in range(len(torso) - 1):
        aim(eb[torso[i]], eb[torso[i + 1]].head, fwd)
    if E("head"):
        aim(E("head"), E("head").head + up * 0.2 * k, fwd)
    for s_ in "LR":
        pn = palm[s_]
        for a_, c_, r in (("shoulder", "upperarm", fwd), ("upperarm", "forearm", pn), ("forearm", "hand", pn),
                          ("hand", "middle1", pn), ("thigh", "shin", fwd), ("shin", "foot", fwd), ("foot", "toe", up)):
            if E(f"{a_}.{s_}") and E(f"{c_}.{s_}"):
                aim(E(f"{a_}.{s_}"), E(f"{c_}.{s_}").head, r)
        for f in rigkit.FINGERS:
            for n in (1, 2):
                if E(f"{f}{n}.{s_}") and E(f"{f}{n + 1}.{s_}"):
                    aim(E(f"{f}{n}.{s_}"), E(f"{f}{n + 1}.{s_}").head, pn)
            l3, l2 = E(f"{f}3.{s_}"), E(f"{f}2.{s_}")
            if l3 and l2:
                aim(l3, l3.head + (l2.tail - l2.head) * 0.85, pn)
        if E(f"toe.{s_}"):
            aim(E(f"toe.{s_}"), E(f"toe.{s_}").head + fwd * 0.05 * k, up)
        if E(f"eye.{s_}"):
            aim(E(f"eye.{s_}"), E(f"eye.{s_}").head + fwd * 0.02 * k, up)
        th, cl = E(f"thigh.{s_}"), E(f"shoulder.{s_}")
        if th and E("hips") and th.parent and th.parent.name != b["hips"]:
            th.parent = E("hips")
        if cl and spine and cl.parent and cl.parent.name in (b.get("neck"), b.get("head")):
            cl.parent = eb[spine[-1]]
    bpy.ops.object.mode_set(mode="OBJECT")
    log("skeleton: bone tails aimed at next joints, limb rolls aligned, legs on pelvis, arms on chest")


def _find_textures(dirs):
    files = []
    for d in dirs:
        if d and os.path.isdir(d):
            for root, _, fs in os.walk(d):
                if root.count(os.sep) - d.count(os.sep) > 2:
                    continue
                files += [os.path.join(root, f) for f in fs if f.lower().endswith(IMG_EXT)]
    return files


def _role(fname):
    f = os.path.basename(fname).lower()
    for role, keys in ROLES:
        if any(k in f for k in keys):
            return role
    return None


def _tex_for(material, existing, files):
    """Pick textures per role for a material: name match first, then textures already referenced by its nodes."""
    base = re.sub(r"\.\d+$", "", material.name).lower()
    picks = {}
    for f in files:
        b = os.path.basename(f).lower()
        if b.startswith(base) or base in b:
            r = _role(b)
            if r and r not in picks:
                picks[r] = f
    for n in existing:
        img = getattr(n, "image", None)
        if img:
            p = bpy.path.abspath(img.filepath)
            cand = p if os.path.exists(p) else next((f for f in files if os.path.basename(f).lower() == os.path.basename(p).lower()), None)
            if cand:
                r = _role(cand) or "color"
                picks.setdefault(r, cand)
    return picks


def fix_materials(meshes, search_dirs):
    files = _find_textures(search_dirs)
    done = set()
    for o in meshes:
        for slot in o.material_slots:
            m = slot.material
            if not m or m.name in done:
                continue
            done.add(m.name)
            m.use_nodes = True
            nt = m.node_tree
            picks = _tex_for(m, [n for n in nt.nodes if n.type == "TEX_IMAGE"], files)
            if not picks:
                continue
            nt.nodes.clear()
            out = nt.nodes.new("ShaderNodeOutputMaterial"); b = nt.nodes.new("ShaderNodeBsdfPrincipled")
            nt.links.new(b.outputs[0], out.inputs["Surface"])
            name = m.name.lower()
            hairy = any(k in name for k in ("hair", "opacity", "lash", "brow", "alpha", "card")) or "opacity" in picks and "color" not in picks
            skin = any(k in name for k in ("body", "head", "skin", "face", "arm", "leg", "hand")) and not hairy

            def img(path, color=True):
                t = nt.nodes.new("ShaderNodeTexImage"); t.image = bpy.data.images.load(path, check_existing=True)
                if not color:
                    t.image.colorspace_settings.name = "Non-Color"
                return t
            col = picks.get("color") or (picks.get("opacity") if hairy else None)
            if col:
                tc = img(col)
                if skin:
                    hs = nt.nodes.new("ShaderNodeHueSaturation"); hs.inputs["Saturation"].default_value = 1.06
                    nt.links.new(tc.outputs["Color"], hs.inputs["Color"]); nt.links.new(hs.outputs["Color"], b.inputs["Base Color"])
                else:
                    nt.links.new(tc.outputs["Color"], b.inputs["Base Color"])
            if picks.get("normal"):
                tn = img(picks["normal"], False); nm = nt.nodes.new("ShaderNodeNormalMap"); nm.inputs["Strength"].default_value = 0.9
                nt.links.new(tn.outputs["Color"], nm.inputs["Color"]); nt.links.new(nm.outputs["Normal"], b.inputs["Normal"])
            if hairy:
                b.inputs["Roughness"].default_value = 0.62
                if "Specular IOR Level" in b.inputs: b.inputs["Specular IOR Level"].default_value = 0.25
            elif picks.get("rough"):
                tr = img(picks["rough"], False); nt.links.new(tr.outputs["Color"], b.inputs["Roughness"])
            elif picks.get("spec"):  # specular map -> roughness (inverted, softened)
                ts = img(picks["spec"], False); inv = nt.nodes.new("ShaderNodeMapRange")
                inv.inputs["To Min"].default_value = 0.75; inv.inputs["To Max"].default_value = 0.3
                nt.links.new(ts.outputs["Color"], inv.inputs["Value"]); nt.links.new(inv.outputs["Result"], b.inputs["Roughness"])
            else:
                b.inputs["Roughness"].default_value = 0.5
            if hairy or picks.get("opacity"):
                op = img(picks.get("opacity") or col)
                gt = nt.nodes.new("ShaderNodeMath"); gt.operation = "GREATER_THAN"; gt.inputs[1].default_value = 0.35
                src = op.outputs["Alpha"] if op.image.channels == 4 else op.outputs["Color"]
                nt.links.new(src, gt.inputs[0]); nt.links.new(gt.outputs[0], b.inputs["Alpha"])
                if hasattr(m, "surface_render_method"):
                    m.surface_render_method = "DITHERED"
            if skin:
                b.inputs["Subsurface Weight"].default_value = 0.12
                b.inputs["Subsurface Radius"].default_value = (1.0, 0.35, 0.18)
                b.inputs["Subsurface Scale"].default_value = 0.01
            m["s3d_kind"] = "hair" if hairy else ("skin" if skin else "cloth")
    log(f"materials rebuilt from {len(files)} texture files")


def tag_parts(meshes):
    def face_keys(o):
        sk = o.data.shape_keys
        return len(sk.key_blocks) if sk else 0
    body = max(meshes, key=lambda o: (face_keys(o), len(o.data.vertices)))
    for o in meshes:
        n = o.name.lower()
        if o is body:
            o["s3d_part"] = "body"
        elif any(k in n for k in ("hair", "brow", "lash", "beard", "opacity")) or any(
                sl.material and sl.material.get("s3d_kind") == "hair" for sl in o.material_slots):
            o["s3d_part"] = "hair"
        elif any(k in n for k in ("eye", "teeth", "tongue", "cornea")):
            o["s3d_part"] = "eyes"
        else:
            o["s3d_part"] = "clothes"
        sk = o.data.shape_keys
        if sk:
            for kb in sk.key_blocks:
                kb.value = 0.0
    return body


def face_gain(ch, h):
    """Some asset faces have weak expression shapes. Measure the smile and scale expressions so a full smile moves
    the mouth corner ~11 mm (MakeHuman-like); stored on the rig, applied by the performer (not to jaw/blinks/visemes)."""
    best = 0.0
    for skd, kn in ch.face.get("mouthSmileLeft", []) + ch.face.get("mouthSmileRight", []):
        kb, ref = skd.key_blocks[kn], skd.reference_key
        n = len(ref.data); stride = max(1, n // 20000)
        best = max(best, max(((kb.data[i].co - ref.data[i].co).length for i in range(0, n, stride)), default=0.0))
    if best <= 1e-5:
        return 1.0
    g = max(1.0, min(1.6, 0.011 * h / 1.75 / best))
    log(f"face: smile moves {best * 1000:.1f} mm -> expression gain x{g:.2f}")
    return g


def make_library_character(path, name, lib_dir, tex_dir=None):
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    objs = import_any(path)
    arm, meshes, h = normalize(objs)
    if arm:
        fix_skeleton(arm, h)
    d = os.path.dirname(os.path.abspath(path))
    fix_materials(meshes, [tex_dir, d, os.path.join(d, "..", "Textures"), os.path.join(d, "textures"), os.path.join(d, "..", "textures"), os.path.join(d, "..")])
    body = tag_parts(meshes)
    for o in meshes:
        if not any(m.type == "SUBSURF" for m in o.modifiers) and len(o.data.vertices) < 30000 and o.get("s3d_part") in ("body", "clothes"):
            sd = o.modifiers.new("Subdiv", "SUBSURF"); sd.levels = 0; sd.render_levels = 1
    coll = bpy.data.collections.new(f"CHAR_{name}")
    bpy.context.scene.collection.children.link(coll)
    for o in objs:
        for c in list(o.users_collection):
            c.objects.unlink(o)
        coll.objects.link(o)
    if arm:
        arm["s3d_rig"] = "asset"; arm["s3d_height"] = float(h); arm["s3d_char"] = name; arm["s3d_src"] = os.path.basename(path)
        arm.name = f"{name}_rig"
        from . import rigkit
        ch = rigkit.Character(name, arm)  # validates mapping + face keys
        arm["s3d_face_gain"] = face_gain(ch, h)
        log(f"skeleton mapped ({len(arm.data.bones)} bones), face keys: {len(ch.face)} ARKit/viseme channels"
            + ("" if ch.has_face else "  (no face rig: expressions will be skipped)"))
    else:
        log("no skeleton found: run auto-rig (not supported for this file yet) or use a rigged asset")
    os.makedirs(lib_dir, exist_ok=True)
    out = os.path.join(lib_dir, f"{name}.blend")
    bpy.data.libraries.write(out, {coll, *coll.objects}, fake_user=True, path_remap="ABSOLUTE")
    log(f"saved character '{name}' -> {out}")
    return out


if __name__ == "__main__":
    a = sys.argv[sys.argv.index("--") + 1:]
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from engine import assetchar  # noqa (package-relative imports inside)
    assetchar.make_library_character(a[0], a[1], a[2], a[3] if len(a) > 3 else None)
