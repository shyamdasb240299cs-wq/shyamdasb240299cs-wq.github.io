"""Sets, lighting, world, render quality, and BRAND application (logo decal on shirts/props, brand colours)."""
import bpy, bmesh, math, os
from mathutils import Vector, Matrix, Quaternion
from . import core, props as P


# ------------------------------------------------------------------ environments
def _cove(name, color, coll, w=16.0, depth=7.0, height=6.0, r=1.6):
    """Seamless cyclorama: floor curving into a back wall (no visible horizon line)."""
    prof = [(-depth, 0.0)]
    for i in range(13):
        a = math.pi / 2 * i / 12
        prof.append((r - r * math.cos(a) + 0.0, r - r * math.cos(math.pi / 2 - a)))
    prof = [(-depth, 0.0), (0.0, 0.0)] + [(r * math.sin(math.pi / 2 * i / 12), r - r * math.cos(math.pi / 2 * i / 12)) for i in range(1, 13)] + [(r, height)]
    bm = bmesh.new()
    left = [bm.verts.new((-w / 2, y, z)) for y, z in prof]
    right = [bm.verts.new((w / 2, y, z)) for y, z in prof]
    for i in range(len(prof) - 1):
        bm.faces.new((left[i], right[i], right[i + 1], left[i + 1]))
    ob = P._obj_from_bm(name, bm, P.mat(f"cyc_{color}", core.hexcol(color), 0.55), coll)
    ob["s3d_env"] = True
    return ob


def studio(coll, color="#3EC6E0", wall_at=2.2, **_):
    cyc = _cove("cyc", color, coll)
    cyc.location.y = wall_at
    return {"floor": cyc}


def kitchen(coll, color="#BFE9F2", **_):
    floor = P.proc_box("floor", 10, 10, 0.02, P.mat_wood("floor_wood", (0.42, 0.27, 0.15, 1)), coll, 0)
    floor.location.z = -0.02
    wall = P.proc_box("wall", 10, 0.1, 3.0, P.mat("wall_paint", core.hexcol(color), 0.85), coll, 0)
    wall.location.y = 1.6
    counter = P.proc_box("counter", 3.0, 0.62, 0.9, P.mat("cabinet", (0.92, 0.92, 0.9, 1), 0.4), coll, 0.004)
    counter.location = (0.0, 1.25, 0)
    top = P.proc_box("countertop", 3.05, 0.66, 0.04, P.mat("stone", (0.2, 0.21, 0.23, 1), 0.25, coat=0.3), coll, 0.003)
    top.location = (0.0, 1.24, 0.9)
    top["s3d_surface"] = True; counter["s3d_obstacle"] = True
    up = P.proc_box("cabinets_up", 3.0, 0.35, 0.75, P.mat("cabinet", (0.92, 0.92, 0.9, 1), 0.4), coll, 0.004)
    up.location = (0.0, 1.38, 1.55)
    win = P.proc_box("window", 1.2, 0.02, 0.9, P.mat("window_glow", (1, 1, 1, 1), 0.1, emit=(0.9, 0.95, 1.0, 1)), coll, 0)
    win.location = (-2.6, 1.54, 1.3)
    for o in (floor, wall, counter, up, win): o["s3d_env"] = True
    return {"floor": floor, "counter": top}


def room(coll, color="#D9EEF5", **_):
    floor = P.proc_box("floor", 10, 10, 0.02, P.mat_wood("floor_wood", (0.45, 0.3, 0.18, 1)), coll, 0); floor.location.z = -0.02
    w1 = P.proc_box("wall_back", 10, 0.1, 3.0, P.mat("wall_paint", core.hexcol(color), 0.85), coll, 0); w1.location.y = 2.2
    w2 = P.proc_box("wall_side", 0.1, 10, 3.0, P.mat("wall_paint", core.hexcol(color), 0.85), coll, 0); w2.location.x = -2.6
    for o in (floor, w1, w2): o["s3d_env"] = True
    return {"floor": floor}


def outdoor(coll, ground="grass", **_):
    col = {"grass": (0.13, 0.3, 0.07, 1), "asphalt": (0.08, 0.08, 0.09, 1), "sand": (0.75, 0.62, 0.42, 1)}.get(ground, (0.2, 0.3, 0.1, 1))
    m = P.mat(f"ground_{ground}", col, 0.9)
    nt = m.node_tree; n = nt.nodes; b = n["Principled BSDF"]
    nz = n.new("ShaderNodeTexNoise"); nz.inputs["Scale"].default_value = 40
    bp = n.new("ShaderNodeBump"); bp.inputs["Strength"].default_value = 0.4
    nt.links.new(nz.outputs["Fac"], bp.inputs["Height"]); nt.links.new(bp.outputs["Normal"], b.inputs["Normal"])
    g = P.proc_box("ground", 80, 80, 0.02, m, coll, 0); g.location.z = -0.02; g["s3d_env"] = True
    return {"floor": g}


def void(coll, **_):
    return {}


SETS = {"studio": studio, "kitchen": kitchen, "room": room, "outdoor": outdoor, "void": void}


# ------------------------------------------------------------------ world + lights
def world(kind="color", color="#9FDCEB", strength=0.35, hdri=None):
    sc = bpy.context.scene
    w = bpy.data.worlds.new("s3d_world"); sc.world = w
    w.use_nodes = True
    nt = w.node_tree; n = nt.nodes; bg = n["Background"]
    bg.inputs["Strength"].default_value = strength
    if hdri:
        path = hdri if os.path.exists(hdri) else polyhaven_hdri(hdri)
        env = n.new("ShaderNodeTexEnvironment"); env.image = bpy.data.images.load(path, check_existing=True)
        nt.links.new(env.outputs["Color"], bg.inputs["Color"])
    elif kind == "sky":
        sky = n.new("ShaderNodeTexSky")
        try:
            sky.sky_type = "NISHITA"; sky.sun_elevation = math.radians(35); sky.sun_rotation = math.radians(60)
        except Exception:
            pass
        nt.links.new(sky.outputs["Color"], bg.inputs["Color"])
        bg.inputs["Strength"].default_value = 0.35
    else:
        bg.inputs["Color"].default_value = core.hexcol(color)
    return w


def polyhaven_hdri(asset_id, res="2k"):
    import json, urllib.request
    d = os.path.join(P.CACHE, "polyhaven_hdri"); os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{asset_id}_{res}.hdr")
    if not os.path.exists(p):
        files = json.load(urllib.request.urlopen(f"https://api.polyhaven.com/files/{asset_id}", timeout=60))
        urllib.request.urlretrieve(files["hdri"][res]["hdr"]["url"], p)
    return p


def _area(name, loc, target, energy, size, color=(1, 1, 1), coll=None):
    l = bpy.data.lights.new(name, "AREA"); l.energy = energy; l.size = size; l.color = color
    try:
        l.shape = "DISK"
    except Exception:
        pass
    o = bpy.data.objects.new(name, l); (coll or bpy.context.scene.collection).objects.link(o)
    o.location = loc
    o.rotation_mode = "QUATERNION"; o.rotation_quaternion = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y")
    return o


RIGS = {  # (az deg rel. to subject facing, elevation deg, distance m, energy W, size m, colour)
    "soft": [("key", -42, 32, 2.8, 750, 1.3, (1, .95, .9)), ("fill", 50, 12, 3.2, 140, 3.0, (.85, .92, 1)),
             ("rim", 155, 38, 2.4, 650, 1.0, (1, .98, .95)), ("top", 0, 85, 3.5, 90, 3.0, (1, 1, 1))],
    "drama": [("key", -55, 30, 2.6, 900, 1.2, (1, .93, .85)), ("fill", 60, 10, 3.5, 60, 3.0, (.8, .88, 1)),
              ("rim", 150, 35, 2.2, 900, 1.0, (.9, .97, 1))],
    "bright": [("key", -30, 30, 3.0, 800, 3.0, (1, .98, .95)), ("fill", 35, 20, 3.0, 450, 3.0, (.95, .97, 1)),
               ("rim", 180, 45, 2.6, 400, 2.0, (1, 1, 1))],
    "night": [("key", -50, 25, 2.5, 220, 0.6, (1, .75, .45)), ("fill", 60, 10, 3.0, 70, 3.0, (.4, .55, 1)),
              ("rim", 160, 30, 2.5, 380, 1.0, (.55, .7, 1))],
    "xray": [("rim_l", -120, 20, 2.2, 700, 1.0, (.3, .9, 1)), ("rim_r", 120, 20, 2.2, 700, 1.0, (.3, .9, 1)),
             ("fill", 0, 10, 3.0, 80, 3.0, (.7, .85, 1))],
}


def light_rig(kind, center, facing_yaw=0.0, coll=None, scale=1.0):
    out = []
    for nm, az, el, d, e, s, c in RIGS.get(kind, RIGS["soft"]):
        a = facing_yaw + math.radians(az); el_r = math.radians(el)
        dirv = Quaternion((0, 0, 1), a) @ Vector((0, -1, 0))
        loc = Vector(center) + (dirv * math.cos(el_r) + Vector((0, 0, math.sin(el_r)))) * d * scale
        out.append(_area(f"L_{nm}", loc, center, e * scale * scale, s * scale, c, coll))
    return out


# ------------------------------------------------------------------ render quality
def render(quality="preview", res=(1080, 1920), fps=30, engine=None):
    sc = bpy.context.scene; r = sc.render
    r.fps = fps
    r.resolution_x, r.resolution_y = res
    r.resolution_percentage = {"preview": 50, "draft": 35, "blocking": 50, "final": 100}.get(quality, 100)
    eng = engine or ("CYCLES" if quality == "cycles" else core.eevee_id())
    if quality == "blocking":
        eng = "BLENDER_WORKBENCH"
    r.engine = eng
    vs = sc.view_settings
    vs.view_transform = "AgX"
    looks = {i.identifier for i in vs.bl_rna.properties["look"].enum_items}
    for lk in ("AgX - Punchy", "Punchy", "AgX - Medium High Contrast", "Medium High Contrast"):
        try:
            vs.look = lk; break
        except TypeError:
            continue
    vs.exposure = -0.15
    if eng == "BLENDER_WORKBENCH":
        sh = sc.display.shading; sh.light = "STUDIO"; sh.color_type = "TEXTURE"; sh.show_shadows = True
        sc.display.render_aa = "FXAA"
    elif eng == "CYCLES":
        c = sc.cycles; c.samples = 96 if quality != "cycles_preview" else 24
        c.use_adaptive_sampling = True; c.adaptive_threshold = 0.02; c.use_denoising = True
        c.max_bounces = 8; c.caustics_reflective = c.caustics_refractive = False
        try:
            prefs = bpy.context.preferences.addons["cycles"].preferences
            prefs.get_devices()
            for dev_type in ("OPTIX", "CUDA", "HIP", "METAL", "ONEAPI"):
                if any(d.type == dev_type for d in prefs.devices):
                    prefs.compute_device_type = dev_type
                    for d in prefs.devices: d.use = True
                    c.device = "GPU"; break
        except Exception:
            pass
        r.use_motion_blur = quality != "preview"
    else:
        ee = sc.eevee
        ee.taa_render_samples = {"preview": 12, "draft": 8, "final": 64}.get(quality, 32)
        for attr, val in (("use_raytracing", quality == "final"), ("use_shadows", True), ("shadow_ray_count", 2),
                          ("use_gtao", True), ("fast_gi_method", "GLOBAL_ILLUMINATION")):
            if hasattr(ee, attr):
                try: setattr(ee, attr, val)
                except Exception: pass
        if hasattr(r, "use_motion_blur"):
            r.use_motion_blur = quality == "final"
    r.image_settings.file_format = "PNG" if quality == "final" else "JPEG"
    if quality != "final":
        r.image_settings.quality = 90
    r.film_transparent = False
    return sc


# ------------------------------------------------------------------ brand
def _uv_at_hit(ob, poly_index, hit_local):
    """UV + local UV axes (du/dworld) at a hit point on a polygon (triangulated)."""
    me = ob.data; me.calc_loop_triangles()
    uvl = me.uv_layers.active
    best = None
    for tri in me.loop_triangles:
        if tri.polygon_index != poly_index:
            continue
        vs = [me.vertices[i].co for i in tri.vertices]
        from mathutils.geometry import barycentric_transform, intersect_point_tri
        if intersect_point_tri(hit_local, *vs) or best is None:
            uvs = [uvl.data[li].uv.to_3d() for li in tri.loops]
            uv = barycentric_transform(hit_local, *vs, *uvs)
            best = (uv, vs, uvs)
    return best


def shirt_decal(clothes_obs, image_path, chest_world, fwd_world, up_world=(0, 0, 1), size=0.12, tint=None):
    """Print a logo on the shirt in UV space (sticks to the cloth when the body moves)."""
    if not image_path or not os.path.exists(image_path):
        core.log("brand: no logo image, skipping decal"); return None
    dg = bpy.context.evaluated_depsgraph_get()
    start = Vector(chest_world) + Vector(fwd_world) * 0.6
    res, loc, nrm, idx, ob, _ = bpy.context.scene.ray_cast(dg, start, -Vector(fwd_world))
    if not res or ob not in clothes_obs:
        core.log("brand: chest ray missed the shirt"); return None
    inv = ob.matrix_world.inverted()
    hit = inv @ loc
    info = _uv_at_hit(ob, idx, hit)
    if not info:
        return None
    uv, vs, uvs = info
    # UV scale (uv units per metre) and the UV direction that corresponds to world-up
    e1, e2 = (ob.matrix_world.to_3x3() @ (vs[1] - vs[0])), (ob.matrix_world.to_3x3() @ (vs[2] - vs[0]))
    t1, t2 = (uvs[1] - uvs[0]).to_2d(), (uvs[2] - uvs[0]).to_2d()
    G = Matrix(((e1.dot(e1), e1.dot(e2)), (e1.dot(e2), e2.dot(e2))))
    try:
        Gi = G.inverted()
    except ValueError:
        return None
    up = Vector(up_world); b = Vector((up.dot(e1), up.dot(e2)))
    ab = Gi @ b  # up ~ a*e1 + b*e2 (projected)
    uv_up = t1 * ab[0] + t2 * ab[1]
    up_proj = e1 * ab[0] + e2 * ab[1]
    scale_uv = uv_up.length / max(1e-6, up_proj.length)  # uv per metre
    ang = math.atan2(uv_up.x, uv_up.y)
    img = bpy.data.images.load(image_path, check_existing=True)
    for slot in ob.material_slots:
        m = slot.material
        if not m or m.get("s3d_decal"):
            continue
        m = m.copy(); slot.material = m; m["s3d_decal"] = True
        nt = m.node_tree; n = nt.nodes; L = nt.links
        bsdf = n.get("Principled BSDF")
        base_link = bsdf.inputs["Base Color"].links[0].from_socket if bsdf.inputs["Base Color"].links else None
        uvn = n.new("ShaderNodeUVMap")
        mp = n.new("ShaderNodeMapping"); mp.vector_type = "POINT"
        # uv -> decal space: translate to centre, rotate so logo-up = body-up, scale to size, recentre
        sz = size * scale_uv
        sub = n.new("ShaderNodeVectorMath"); sub.operation = "SUBTRACT"; sub.inputs[1].default_value = (uv.x, uv.y, 0)
        rot = n.new("ShaderNodeVectorRotate"); rot.rotation_type = "Z_AXIS"; rot.inputs["Angle"].default_value = ang
        sc_ = n.new("ShaderNodeVectorMath"); sc_.operation = "SCALE"; sc_.inputs["Scale"].default_value = 1.0 / max(1e-6, sz)
        add = n.new("ShaderNodeVectorMath"); add.operation = "ADD"; add.inputs[1].default_value = (0.5, 0.5, 0)
        tex = n.new("ShaderNodeTexImage"); tex.image = img; tex.extension = "CLIP"
        L.new(uvn.outputs["UV"], sub.inputs[0]); L.new(sub.outputs[0], rot.inputs["Vector"])
        L.new(rot.outputs["Vector"], sc_.inputs[0]); L.new(sc_.outputs[0], add.inputs[0]); L.new(add.outputs[0], tex.inputs["Vector"])
        mix = n.new("ShaderNodeMix"); mix.data_type = "RGBA"
        if base_link:
            src = base_link
            if tint:  # recolour white/grey fabric to the brand colour, keep saturated parts (jeans)
                hsv = n.new("ShaderNodeSeparateColor"); hsv.mode = "HSV"
                L.new(base_link, hsv.inputs["Color"])
                mr = n.new("ShaderNodeMapRange"); mr.inputs["From Min"].default_value = 0.18; mr.inputs["From Max"].default_value = 0.05
                L.new(hsv.outputs[1], mr.inputs["Value"])
                tc = n.new("ShaderNodeMix"); tc.data_type = "RGBA"; tc.blend_type = "MULTIPLY"
                L.new(mr.outputs["Result"], tc.inputs["Factor"]); L.new(base_link, tc.inputs["A"])
                tc.inputs["B"].default_value = core.hexcol(tint)
                src = tc.outputs["Result"]
            L.new(src, mix.inputs["A"])
        else:
            mix.inputs["A"].default_value = bsdf.inputs["Base Color"].default_value
        L.new(tex.outputs["Color"], mix.inputs["B"]); L.new(tex.outputs["Alpha"], mix.inputs["Factor"])
        L.new(mix.outputs["Result"], bsdf.inputs["Base Color"])
    core.log(f"brand: logo printed on {ob.name}")
    return ob


def prop_label(ob, image_path, face="front", size=0.05, offset=(0, 0, 0)):
    """Stick a logo on a rigid prop (object-space projection on the chosen face)."""
    if not image_path or not os.path.exists(image_path):
        return
    img = bpy.data.images.load(image_path, check_existing=True)
    mn = Vector(ob.bound_box[0]); mx = Vector(ob.bound_box[6]); c = (mn + mx) / 2 + Vector(offset)
    axis = {"front": ("X", "Z", -1), "top": ("X", "Y", 2), "side": ("Y", "Z", 0)}[face]
    for slot in ob.material_slots:
        m = slot.material
        if not m: continue
        m = m.copy(); slot.material = m
        nt = m.node_tree; n = nt.nodes; L = nt.links; b = n.get("Principled BSDF")
        tc = n.new("ShaderNodeTexCoord"); sep = n.new("ShaderNodeSeparateXYZ"); comb = n.new("ShaderNodeCombineXYZ")
        L.new(tc.outputs["Object"], sep.inputs[0])
        def mapped(ax, centre):
            mth = n.new("ShaderNodeMath"); mth.operation = "MULTIPLY_ADD"
            mth.inputs[1].default_value = 1.0 / size; mth.inputs[2].default_value = 0.5 - centre / size
            L.new(sep.outputs[ax], mth.inputs[0]); return mth.outputs[0]
        L.new(mapped(axis[0], getattr(c, axis[0].lower())), comb.inputs[0])
        L.new(mapped(axis[1], getattr(c, axis[1].lower())), comb.inputs[1])
        tex = n.new("ShaderNodeTexImage"); tex.image = img; tex.extension = "CLIP"
        L.new(comb.outputs[0], tex.inputs["Vector"])
        mix = n.new("ShaderNodeMix"); mix.data_type = "RGBA"
        if b.inputs["Base Color"].links:
            L.new(b.inputs["Base Color"].links[0].from_socket, mix.inputs["A"])
        else:
            mix.inputs["A"].default_value = b.inputs["Base Color"].default_value
        L.new(tex.outputs["Color"], mix.inputs["B"]); L.new(tex.outputs["Alpha"], mix.inputs["Factor"])
        L.new(mix.outputs["Result"], b.inputs["Base Color"])
