"""Render frames of build/donington/scene.blend (resumable).
    blender -b build/donington/scene.blend -P work/render.py -- [--q preview|final] [--from F] [--to F] [--step K]
Preview: Cycles CPU 432x768, 8 spp adaptive + OIDN, 1K textures, no motion blur, spray only when it reads. Final (GPU machine): 1080x1920, 160 spp."""
import bpy, sys, os, time, json
args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
opt = lambda k, d: args[args.index(k) + 1] if k in args else d
Q = opt("--q", "preview"); F0 = int(opt("--from", 0)); STEP = int(opt("--step", 1))
sc = bpy.context.scene; F1 = int(opt("--to", sc.frame_end))
VD = os.path.abspath(os.path.join(os.path.dirname(bpy.data.filepath), "..", ".."))
out = os.path.join(VD, "build", "donington", "frames"); os.makedirs(out, exist_ok=True)
r = sc.render; cy = sc.cycles
r.engine = "CYCLES"
if Q == "final":
    prefs = bpy.context.preferences.addons["cycles"].preferences
    for be in ("OPTIX", "CUDA", "HIP", "METAL", "ONEAPI"):
        try:
            prefs.compute_device_type = be; prefs.get_devices()
            if any(d.type == be for d in prefs.devices):
                for d in prefs.devices: d.use = d.type == be
                cy.device = "GPU"; break
        except Exception:
            pass
    r.resolution_x, r.resolution_y = 1080, 1920; cy.samples = 160; cy.adaptive_threshold = 0.02
    cy.texture_limit_render = "OFF"; r.image_settings.file_format = "PNG"; ext = "png"
else:
    cy.device = "CPU"; r.resolution_x, r.resolution_y = 432, 768; cy.samples = 8; cy.adaptive_threshold = 0.05
    cy.texture_limit_render = "1024"; r.image_settings.file_format = "JPEG"; r.image_settings.quality = 93; ext = "jpg"
r.resolution_percentage = 100; cy.use_denoising = True; r.use_persistent_data = True; r.use_motion_blur = Q == "final"
SPRAY = []
if Q != "final":   # CPU preview: cheaper spray noise, no puddle ripples (invisible at 432 px), spray only where it reads
    cy.denoising_prefilter = "FAST"
    for m in bpy.data.materials:
        nt = m.node_tree if m.use_nodes else None
        if not nt: continue
        if m.name.endswith("_spray_vol"):
            for n in nt.nodes:
                if n.type == "TEX_NOISE": n.inputs["Detail"].default_value = 2.0
        if any(n.type == "TEX_VORONOI" and n.voronoi_dimensions == "4D" for n in nt.nodes):
            for bmp in [n for n in nt.nodes if n.type == "BUMP"]:
                src = bmp.inputs["Normal"].links[0].from_socket if bmp.inputs["Normal"].links else None
                for l in list(bmp.outputs[0].links):
                    if src: nt.links.new(src, l.to_socket)
                    else: nt.links.remove(l)
    for o in bpy.data.objects:
        if o.name.endswith("_spray"):
            amt = bpy.data.materials[o.name + "_vol"].node_tree.nodes["spray_amt"]
            SPRAY.append((o, amt))


def spray_reads():
    """Is each spray plume in front of the lens and big enough on screen to read (>~6 % of the frame)?"""
    from mathutils import Vector
    import math
    cam = sc.camera; cl = cam.matrix_world.translation; fwd = cam.matrix_world.to_quaternion() @ Vector((0, 0, -1))
    lens = cam.data.lens; half = math.atan(18.0 / lens); out_ = []
    for o, amt in SPRAY:
        c = o.matrix_world @ Vector((0.0, 8.0, 1.2)); d = (c - cl).length
        ang = fwd.angle(c - cl) if d > 1e-3 else 0.0
        out_.append(amt.outputs[0].default_value > 0.05 and d < lens * 1.3 and ang < min(half + math.atan2(9.0, d), 1.55))
    return out_


def spray_plan():
    """Decide per shot (never mid-shot, so nothing pops); the loop's last and first shots share one decision."""
    cuts = json.load(open(os.path.join(VD, "build", "cuts.json")))["cuts"]; want = {}
    for name, f0, f1 in cuts:
        for f in range(f0, f1 + 1, 3):
            sc.frame_set(f)
            want.setdefault(name, set()).update(i for i, v in enumerate(spray_reads()) if v)
    u = want.get("hook", set()) | want.get("return", set()); want["hook"] = want["return"] = u
    plan = {f: want.get(name, set()) for name, f0, f1 in cuts for f in range(f0, f1 + 1)}
    print("[render] spray per shot:", {k: sorted(SPRAY[i][0].name for i in v) for k, v in want.items() if v}, flush=True)
    return plan


PLAN = spray_plan() if SPRAY else {}
open(os.path.join(out, ".quality"), "w").write(Q)
done, t_start = 0, time.time()
frames = [f for f in range(F0, F1 + 1, STEP) if not os.path.exists(os.path.join(out, f"{f:05d}.{ext}"))]
for f in frames:
    t = time.time(); sc.frame_set(f); r.filepath = os.path.join(out, f"{f:05d}.{ext}")
    for i, (o, _a) in enumerate(SPRAY): o.hide_render = i not in PLAN.get(f, set())
    bpy.ops.render.render(write_still=True); done += 1
    spf = (time.time() - t_start) / done
    json.dump({"q": Q, "done": done, "total": len(frames), "spf": round(spf, 1), "eta_min": round(spf * (len(frames) - done) / 60, 1)},
              open(os.path.join(out, "progress.json"), "w"))
    print(f"[render] {f} {time.time() - t:.1f}s  ({done}/{len(frames)}, eta {spf * (len(frames) - done) / 60:.0f} min)", flush=True)
