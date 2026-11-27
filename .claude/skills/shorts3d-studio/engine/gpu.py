"""Detect the render GPU and benchmark it (run inside Blender by `s3d.py gpu`).

blender -b --factory-startup -P engine/gpu.py -- <out.json> [bench]
Writes {"backend", "devices", "bench": {"cycles_spf", "eevee_spf"}} - per-frame seconds extrapolated to a 1080x1920
character shot (scene complexity factor included). s3d.py turns this + VRAM into a quality tier.
"""
import bpy, json, sys, time, math

ORDER = ("OPTIX", "CUDA", "HIP", "METAL", "ONEAPI")


def detect():
    out = {"backend": "NONE", "devices": []}
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
    except Exception:
        return out
    for be in ORDER:
        try:
            prefs.compute_device_type = be
        except TypeError:
            continue
        prefs.get_devices()
        devs = [d for d in prefs.devices if d.type == be]
        if devs:
            out["backend"] = be
            out["devices"] = [d.name for d in devs]
            break
    cpu = [d.name for d in prefs.devices if d.type == "CPU"]
    out["cpu"] = cpu[0] if cpu else ""
    return out


def use_gpu(backend):
    """Enable every device of the chosen backend for Cycles (CPU off: it slows hybrid renders on laptops)."""
    if backend in ("", "NONE", None):
        return False
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        prefs.compute_device_type = backend
        prefs.get_devices()
        for d in prefs.devices:
            d.use = d.type == backend
        bpy.context.scene.cycles.device = "GPU"
        return True
    except Exception:
        return False


def _bench_scene():
    sc = bpy.context.scene
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bpy.ops.mesh.primitive_plane_add(size=6)
    for i in range(7):  # glossy / sss / rough spheres ~ a character shot's shading cost
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.25, location=((i - 3) * 0.55, 0, 0.3), segments=96, ring_count=48)
        m = bpy.data.materials.new(f"m{i}")
        if not m.node_tree:
            m.use_nodes = True
        b = m.node_tree.nodes["Principled BSDF"]
        b.inputs["Roughness"].default_value = 0.15 + 0.12 * i
        b.inputs["Subsurface Weight"].default_value = 0.3 if i % 2 else 0.0
        bpy.context.object.data.materials.append(m)
    for loc, e in (((2, -3, 4), 800), ((-3, -2, 2), 300)):
        bpy.ops.object.light_add(type="AREA", location=loc); bpy.context.object.data.energy = e; bpy.context.object.data.size = 2
        bpy.context.object.rotation_euler = (0.9, 0, math.atan2(loc[0], -loc[1]))
    bpy.ops.object.camera_add(location=(0, -5, 1.2), rotation=(math.radians(84), 0, 0)); sc.camera = bpy.context.object
    w = bpy.data.worlds.new("w"); sc.world = w
    sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = 540, 960, 100
    sc.render.filepath = bpy.app.tempdir + "s3d_bench.png"


def bench(backend):
    _bench_scene(); sc = bpy.context.scene; res = {}
    sc.render.engine = "CYCLES"
    gpu = use_gpu(backend)
    c = sc.cycles; c.samples = 64; c.use_adaptive_sampling = True; c.adaptive_threshold = 0.03; c.use_denoising = True
    sc.render.use_persistent_data = True
    bpy.ops.render.render(write_still=False)  # warm-up (kernel compile / BVH)
    t = time.time(); bpy.ops.render.render(write_still=False); dt = time.time() - t
    res["cycles_spf"] = round(dt * 4 * 1.5 * 2.2, 2)  # x4 pixels, x1.5 samples (96), x2.2 character/hair/SSS scenes
    res["cycles_device"] = "GPU" if gpu else "CPU"
    for eng in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"):
        try:
            sc.render.engine = eng; break
        except TypeError:
            continue
    sc.eevee.taa_render_samples = 32
    bpy.ops.render.render(write_still=False)
    t = time.time(); bpy.ops.render.render(write_still=False); dt = time.time() - t
    res["eevee_spf"] = round(dt * 4 * 2.0 * 2.0, 2)  # x4 pixels, x2 samples (64), x2 scene complexity
    return res


if __name__ == "__main__":
    a = sys.argv[sys.argv.index("--") + 1:]
    info = detect()
    if len(a) > 1 and a[1] == "bench":
        info["bench"] = bench(info["backend"])
    json.dump(info, open(a[0], "w"), indent=1)
    print("[s3d] gpu", json.dumps(info))
