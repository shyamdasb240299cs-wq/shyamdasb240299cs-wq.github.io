"""Render frames of build/donington/scene.blend (resumable).
    blender -b build/donington/scene.blend -P work/render.py -- [--q preview|final] [--from F] [--to F] [--step K]
Preview: Cycles CPU 432x768, 12 spp adaptive + OIDN, 1K textures. Final (GPU machine): 1080x1920, 160 spp."""
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
    cy.device = "CPU"; r.resolution_x, r.resolution_y = 432, 768; cy.samples = 12; cy.adaptive_threshold = 0.05
    cy.texture_limit_render = "1024"; r.image_settings.file_format = "JPEG"; r.image_settings.quality = 93; ext = "jpg"
r.resolution_percentage = 100; cy.use_denoising = True; r.use_persistent_data = True; r.use_motion_blur = True
open(os.path.join(out, ".quality"), "w").write(Q)
done, t_start = 0, time.time()
frames = [f for f in range(F0, F1 + 1, STEP) if not os.path.exists(os.path.join(out, f"{f:05d}.{ext}"))]
for f in frames:
    t = time.time(); sc.frame_set(f); r.filepath = os.path.join(out, f"{f:05d}.{ext}")
    bpy.ops.render.render(write_still=True); done += 1
    spf = (time.time() - t_start) / done
    json.dump({"q": Q, "done": done, "total": len(frames), "spf": round(spf, 1), "eta_min": round(spf * (len(frames) - done) / 60, 1)},
              open(os.path.join(out, "progress.json"), "w"))
    print(f"[render] {f} {time.time() - t:.1f}s  ({done}/{len(frames)}, eta {spf * (len(frames) - done) / 60:.0f} min)", flush=True)
