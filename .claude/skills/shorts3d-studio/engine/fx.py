"""Explainer FX: x-ray, cutaway holes, 3D arrows, labels, glow, flow particles, lightning, pop-in, glass,
exploded views, crumbs (ballistic), all time-ranged in frames."""
import bpy, bmesh, math, random
from mathutils import Vector, Matrix, Quaternion
from . import core, props as P


def _emit_mat(name, color, strength=4.0, alpha=1.0):
    m = bpy.data.materials.get(name)
    if m: return m
    m = bpy.data.materials.new(name); m.use_nodes = True
    n = m.node_tree.nodes; b = n["Principled BSDF"]
    c = core.hexcol(color) if isinstance(color, str) else color
    core.set_input(b, "Base Color", c); core.set_input(b, "Emission Color", c); core.set_input(b, "Emission Strength", strength)
    core.set_input(b, "Roughness", 0.3)
    if alpha < 1:
        core.set_input(b, "Alpha", alpha)
        if hasattr(m, "surface_render_method"): m.surface_render_method = "BLENDED"
    return m


def _vis(ob, f0, f1, frames):
    """Visible only in [f0, f1]."""
    for fr, hid in ((frames[0], True), (f0 - 1, True), (f0, False), (f1, False), (f1 + 1, True)):
        if fr < frames[0]: continue
        ob.hide_render = hid; ob.keyframe_insert("hide_render", frame=fr)
        ob.hide_viewport = hid; ob.keyframe_insert("hide_viewport", frame=fr)


# ------------------------------------------------------------------ x-ray
def xray(objs, f0, f1, color="#3EC6E0", fade=6, inner=(), frames=None):
    """Skin/clothes turn into a glowing translucent shell between f0..f1; `inner` objects appear inside."""
    for ob in objs:
        for slot in ob.material_slots:
            m = slot.material
            if not m: continue
            m = m.copy(); slot.material = m
            nt = m.node_tree; n = nt.nodes; L = nt.links
            out = next(x for x in n if x.type == "OUTPUT_MATERIAL")
            src = out.inputs["Surface"].links[0].from_socket
            lw = n.new("ShaderNodeLayerWeight"); lw.inputs["Blend"].default_value = 0.35
            em = n.new("ShaderNodeEmission"); em.inputs["Color"].default_value = core.hexcol(color); em.inputs["Strength"].default_value = 2.5
            tr = n.new("ShaderNodeBsdfTransparent")
            rim = n.new("ShaderNodeMixShader"); L.new(lw.outputs["Facing"], rim.inputs[0])
            L.new(tr.outputs[0], rim.inputs[1]); L.new(em.outputs[0], rim.inputs[2])
            mx = n.new("ShaderNodeMixShader"); mx.name = "s3d_xray"
            L.new(src, mx.inputs[1]); L.new(rim.outputs[0], mx.inputs[2]); L.new(mx.outputs[0], out.inputs["Surface"])
            if hasattr(m, "surface_render_method"): m.surface_render_method = "BLENDED"
            fac = mx.inputs[0]
            for fr, v in ((frames[0], 0.0), (f0, 0.0), (f0 + fade, 1.0), (f1 - fade, 1.0), (f1, 0.0)):
                fac.default_value = v; fac.keyframe_insert("default_value", frame=fr)
    for o in inner:
        _vis(o, f0, f1, frames)


# ------------------------------------------------------------------ cutaway hole (shader-based: robust on any mesh)
def cutaway(targets, center, normal, radius, f0, f1, frames, bone_parent=None, rim_color="#3EC6E0", inner=(), fade=5):
    """Round window through targets (e.g. shirt + body) facing `normal`: surfaces inside the radius on the
    front side turn transparent; inner (back-facing) surfaces render as a dark body cavity; glowing rim."""
    n = Vector(normal).normalized()
    q = n.to_track_quat("Z", "Y")
    cut = bpy.data.objects.new("s3d_cutter", None); bpy.context.scene.collection.objects.link(cut)
    cut.empty_display_type = "CIRCLE"; cut.empty_display_size = radius
    cut.matrix_world = Matrix.Translation(center) @ q.to_matrix().to_4x4()
    ring = bpy.data.objects.new("cut_rim", bpy.data.meshes.new("rim"))
    bm = bmesh.new(); bmesh.ops.create_circle(bm, segments=64, radius=radius); bm.to_mesh(ring.data); bm.free()
    bpy.context.scene.collection.objects.link(ring)
    ring.matrix_world = Matrix.Translation(center + n * 0.03) @ q.to_matrix().to_4x4()
    wf = ring.modifiers.new("w", "WIREFRAME"); wf.thickness = 0.006
    ring.data.materials.append(_emit_mat("rim_" + rim_color, rim_color, 6))
    if bone_parent:  # follow the chest bone exactly (bone parents are measured from the bone TAIL)
        rig, bn = bone_parent
        pb = rig.pose.bones[bn]
        P_ = rig.matrix_world @ pb.matrix @ Matrix.Translation((0, pb.length, 0))
        for o in (cut, ring):
            mw = o.matrix_world.copy()
            o.parent = rig; o.parent_type = "BONE"; o.parent_bone = bn
            o.matrix_parent_inverse = P_.inverted()
            o.matrix_basis = mw
    for t in targets:
        for slot in t.material_slots:
            m = slot.material
            if not m: continue
            m = m.copy(); slot.material = m
            nt = m.node_tree; nd = nt.nodes; L = nt.links
            out = next(x for x in nd if x.type == "OUTPUT_MATERIAL")
            src = out.inputs["Surface"].links[0].from_socket
            tc = nd.new("ShaderNodeTexCoord"); tc.object = cut
            sep = nd.new("ShaderNodeSeparateXYZ"); L.new(tc.outputs["Object"], sep.inputs[0])
            comb = nd.new("ShaderNodeCombineXYZ"); L.new(sep.outputs["X"], comb.inputs["X"]); L.new(sep.outputs["Y"], comb.inputs["Y"])
            ln = nd.new("ShaderNodeVectorMath"); ln.operation = "LENGTH"; L.new(comb.outputs[0], ln.inputs[0])
            inside = nd.new("ShaderNodeMath"); inside.operation = "LESS_THAN"; inside.inputs[1].default_value = radius
            L.new(ln.outputs["Value"], inside.inputs[0])
            front = nd.new("ShaderNodeMath"); front.operation = "GREATER_THAN"; front.inputs[1].default_value = -0.03
            L.new(sep.outputs["Z"], front.inputs[0])
            hole = nd.new("ShaderNodeMath"); hole.operation = "MULTIPLY"; L.new(inside.outputs[0], hole.inputs[0]); L.new(front.outputs[0], hole.inputs[1])
            on = nd.new("ShaderNodeValue"); on.name = "s3d_cut_on"
            for fr, v in ((frames[0], 0.0), (f0, 0.0), (f0 + fade, 1.0), (f1 - fade, 1.0), (f1, 0.0)):
                on.outputs[0].default_value = v; on.outputs[0].keyframe_insert("default_value", frame=max(frames[0], fr))
            fac = nd.new("ShaderNodeMath"); fac.operation = "MULTIPLY"; L.new(hole.outputs[0], fac.inputs[0]); L.new(on.outputs[0], fac.inputs[1])
            geo = nd.new("ShaderNodeNewGeometry")
            cav = nd.new("ShaderNodeBsdfPrincipled"); core.set_input(cav, "Base Color", core.hexcol("#5A1218")); core.set_input(cav, "Roughness", 0.45)
            core.set_input(cav, "Subsurface Weight", 0.3)
            bf = nd.new("ShaderNodeMath"); bf.operation = "MULTIPLY"; L.new(geo.outputs["Backfacing"], bf.inputs[0]); L.new(on.outputs[0], bf.inputs[1])
            back = nd.new("ShaderNodeMixShader"); L.new(bf.outputs[0], back.inputs[0]); L.new(src, back.inputs[1]); L.new(cav.outputs[0], back.inputs[2])
            tr = nd.new("ShaderNodeBsdfTransparent")
            mx = nd.new("ShaderNodeMixShader"); L.new(fac.outputs[0], mx.inputs[0]); L.new(back.outputs[0], mx.inputs[1]); L.new(tr.outputs[0], mx.inputs[2])
            L.new(mx.outputs[0], out.inputs["Surface"])
            if hasattr(m, "surface_render_method"): m.surface_render_method = "DITHERED"
            m.use_backface_culling = False
    _vis(ring, f0, f1, frames)
    for o in inner:
        _vis(o, f0, f1, frames)
    return cut, ring


# ------------------------------------------------------------------ arrows + labels
def arrow(name, p0, p1, f0, f1, frames, color="#3EC6E0", radius=0.01, bend=0.15, coll=None):
    """Curved 3D arrow that draws itself on (f0 -> f0+grow) and stays until f1."""
    p0, p1 = Vector(p0), Vector(p1)
    mid = (p0 + p1) / 2 + Vector((0, 0, (p1 - p0).length * bend))
    cu = bpy.data.curves.new(name, "CURVE"); cu.dimensions = "3D"; cu.bevel_depth = radius; cu.bevel_resolution = 4
    sp = cu.splines.new("BEZIER"); sp.bezier_points.add(2)
    for bp, p in zip(sp.bezier_points, (p0, mid, p1)):
        bp.co = p; bp.handle_left_type = bp.handle_right_type = "AUTO"
    ob = bpy.data.objects.new(name, cu); (coll or bpy.context.scene.collection).objects.link(ob)
    m = _emit_mat("arrow_" + color, color, 3.0); cu.materials.append(m)
    head = bpy.data.objects.new(name + "_head", bpy.data.meshes.new(name + "_h"))
    bm = bmesh.new(); bmesh.ops.create_cone(bm, cap_ends=True, segments=24, radius1=radius * 3.2, radius2=0, depth=radius * 7)
    bmesh.ops.translate(bm, verts=bm.verts, vec=(0, 0, radius * 3.5)); bm.to_mesh(head.data); bm.free()
    head.data.materials.append(m); (coll or bpy.context.scene.collection).objects.link(head)
    d = (p1 - mid).normalized(); head.matrix_world = Matrix.Translation(p1 - d * radius * 3) @ d.to_track_quat("Z", "Y").to_matrix().to_4x4()
    grow = min(12, max(4, (f1 - f0) // 3))
    for fr, v in ((frames[0], 0.0), (f0, 0.0), (f0 + grow, 1.0)):
        cu.bevel_factor_end = v; cu.keyframe_insert("bevel_factor_end", frame=fr)
    for fr, s in ((frames[0], 0.0), (f0 + grow - 1, 0.0), (f0 + grow + 3, 1.15), (f0 + grow + 6, 1.0)):
        head.scale = (s, s, s); head.keyframe_insert("scale", frame=fr)
    _vis(ob, f0, f1, frames); _vis(head, f0, f1, frames)
    return ob


def label3d(name, text, at, f0, f1, frames, color="#FFFFFF", size=0.06, cam=None, coll=None):
    cu = bpy.data.curves.new(name, "FONT"); cu.body = text; cu.size = size; cu.extrude = size * 0.08
    cu.bevel_depth = size * 0.02; cu.align_x = "CENTER"; cu.align_y = "CENTER"
    ob = bpy.data.objects.new(name, cu); (coll or bpy.context.scene.collection).objects.link(ob)
    ob.location = at; cu.materials.append(_emit_mat("label_" + color, color, 1.5))
    if cam:
        c = ob.constraints.new("TRACK_TO"); c.target = cam; c.track_axis = "TRACK_Z"; c.up_axis = "UP_Y"
    pop(ob, f0, frames); _vis(ob, f0, f1, frames)
    return ob


def pop(ob, f0, frames, dur=8):
    """Scale-in with a little overshoot (classic explainer appearance)."""
    s0 = ob.scale.copy()
    for fr, k in ((frames[0], 0.0), (f0, 0.0), (f0 + int(dur * 0.6), 1.12), (f0 + dur, 1.0)):
        ob.scale = s0 * k; ob.keyframe_insert("scale", frame=max(frames[0], fr))
    ob.scale = s0


# ------------------------------------------------------------------ glow / glass / flow / lightning
def glow(ob, f0, f1, frames, color="#FF4D4D", strength=6.0, pulse_hz=1.5, fps=30):
    for slot in ob.material_slots:
        m = slot.material
        if not m: continue
        m = m.copy(); slot.material = m
        b = m.node_tree.nodes.get("Principled BSDF")
        if not b: continue
        core.set_input(b, "Emission Color", core.hexcol(color))
        s = b.inputs["Emission Strength"]
        for fr in range(f0, f1 + 1, 2):
            t = (fr - f0) / fps
            env = core.smooth((fr - f0) / 6) * core.smooth((f1 - fr) / 6)
            s.default_value = strength * env * (0.6 + 0.4 * math.sin(t * pulse_hz * 2 * math.pi)); s.keyframe_insert("default_value", frame=fr)
        s.default_value = 0; s.keyframe_insert("default_value", frame=frames[0]); s.keyframe_insert("default_value", frame=f1 + 2)


def glass(ob, tint=(0.9, 0.97, 1.0, 1), rough=0.03):
    m = P.mat(f"glass_{ob.name}", tint, rough, transmission=1.0)
    ob.data.materials.clear(); ob.data.materials.append(m)


def flow(name, points, f0, f1, frames, color="#FF3B3B", count=24, radius=0.006, speed=0.35, fps=30, coll=None):
    """Glowing particles streaming along a path (blood, air, current, data)."""
    pts = [Vector(p) for p in points]
    seglen = [(pts[i + 1] - pts[i]).length for i in range(len(pts) - 1)]
    total = sum(seglen) or 1.0

    def at(s):
        s %= total
        for i, L in enumerate(seglen):
            if s <= L: return pts[i].lerp(pts[i + 1], s / max(L, 1e-9))
            s -= L
        return pts[-1]
    m = _emit_mat("flow_" + color, color, 5.0)
    kw = core.KeyWriter(); obs = []
    for k in range(count):
        o = bpy.data.objects.new(f"{name}_{k}", bpy.data.meshes.new(f"{name}_{k}"))
        bm = bmesh.new(); bmesh.ops.create_icosphere(bm, subdivisions=2, radius=radius); bm.to_mesh(o.data); bm.free()
        o.data.materials.append(m); (coll or bpy.context.scene.collection).objects.link(o); obs.append(o)
        for fr in range(f0, f1 + 1):
            kw.put(o, "location", fr, at(k / count * total + (fr - f0) / fps * speed))
        _vis(o, f0, f1, frames)
    kw.write()
    return obs


def lightning(name, p0, p1, f0, f1, frames, color="#9FE8FF", jag=0.03, coll=None):
    m = _emit_mat("bolt_" + color, color, 12.0)
    rng = random.Random(hash(name) % 9999)
    p0, p1 = Vector(p0), Vector(p1)
    for i, fr in enumerate(range(f0, f1 + 1, 2)):
        pts = [p0]
        for k in range(1, 9):
            u = k / 9
            pts.append(p0.lerp(p1, u) + Vector((rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1))) * jag)
        pts.append(p1)
        o = P.curve_tube(f"{name}_{i}", pts, 0.0025, coll, m, res=1)
        _vis(o, fr, fr + 1, frames)


# ------------------------------------------------------------------ exploded view + crumbs
def exploded(parts, center, f0, f1, frames, dist=0.12):
    c = Vector(center)
    for i, ob in enumerate(parts):
        mn, mx = core.world_bbox(ob); d = ((mn + mx) / 2 - c)
        d = d.normalized() if d.length > 1e-6 else Vector((0, 0, 1))
        base = ob.location.copy()
        st = f0 + i * 2
        for fr, k in ((frames[0], 0), (st, 0), (st + 12, 1), (f1 - 12, 1), (f1, 0)):
            ob.location = base + d * dist * core.smooth(k); ob.keyframe_insert("location", frame=max(frames[0], fr))
        ob.location = base


def crumbs(name, at, f0, frames, count=14, material=None, floor_z=0.0, fps=30, coll=None, seed=3):
    """Tiny pieces that fall with gravity and bounce on the surface below (bite/crunch/break moments)."""
    rng = random.Random(seed); g = 9.81
    m = material or P.mat_bread()
    kw = core.KeyWriter()
    for k in range(count):
        o = bpy.data.objects.new(f"{name}_{k}", bpy.data.meshes.new(f"{name}_{k}"))
        bm = bmesh.new(); bmesh.ops.create_icosphere(bm, subdivisions=1, radius=rng.uniform(0.0015, 0.004))
        for v in bm.verts: v.co *= rng.uniform(0.7, 1.3)
        bm.to_mesh(o.data); bm.free(); o.data.materials.append(m)
        (coll or bpy.context.scene.collection).objects.link(o)
        p = Vector(at); v = Vector((rng.uniform(-.35, .35), rng.uniform(-.35, .35), rng.uniform(-.05, .35)))
        o.rotation_mode = "XYZ"
        for fr in range(f0, frames[-1] + 1):
            kw.put(o, "location", fr, p.copy())
            kw.put(o, "rotation_euler", fr, (fr * 0.3 * (k % 3 + 1), fr * 0.2, 0))
            dt = 1 / fps; v.z -= g * dt; p += v * dt
            if p.z < floor_z + 0.002:
                p.z = floor_z + 0.002; v.z = -v.z * 0.3; v.x *= 0.6; v.y *= 0.6
        _vis(o, f0, frames[-1], frames)
    kw.write()
