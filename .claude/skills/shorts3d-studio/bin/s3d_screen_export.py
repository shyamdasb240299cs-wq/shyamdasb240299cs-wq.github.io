"""Export per-frame camera matrices + part centres from a .blend so 2D post (labels, leader lines) can stick to 3D parts.
args: <work> <out.json>
Per frame: the owning shot camera's projection (P) and view (V) matrices, the engine root + car root world matrices,
and the world bbox centre of every part an overlay may point at. Heavy procedural modifiers are switched off
(viewport only) so this runs fast beside a render."""
import bpy, json, sys, re
from mathutils import Vector

args = sys.argv[sys.argv.index("--") + 1:]
work, out = args[0], args[1]
J = json.load(open(work + "/shots.json"))
own = {}
for name, scn, f0, f1 in J["shots"]:
    for f in range(f0, f1 + 1):
        own.setdefault(f, (scn, name))           # the earlier shot owns a shared boundary frame (render order)

for ob in bpy.data.objects:                      # flows + scattered nature: not needed for positions
    for md in ob.modifiers:
        if md.type == "NODES":
            md.show_viewport = False

PAT = re.compile(r"^(T1_|TWL_|TWR_|twin_|ic|charge_pipe|downpipe_stock|filter|stand|v8_block|v8_head|v8_plenum|v8_throttle)")
parts = sorted(o.name for o in bpy.data.objects if o.type in ("MESH", "CURVE") and PAT.match(o.name))
needles = sorted(o.name for o in bpy.data.objects if re.match(r"gt_(needle|needlepiv|dial)", o.name))
roots = {"engine": "V8_root"}
car_root = "gt_root" if "gt_root" in bpy.data.objects else None
print("[export] car root:", car_root, "| parts:", len(parts), "| needles:", needles[:20])
open(work + "/object_names.txt", "w").write("\n".join(sorted(f"{o.type:8s} {o.name}" for o in bpy.data.objects)))

def M(m):
    return [round(x, 6) for row in m for x in row]

def centre(ob_e):
    bb = [ob_e.matrix_world @ Vector(c) for c in ob_e.bound_box]
    return [round(x, 5) for x in (sum(bb, Vector()) / 8.0)]

res = {"W": 1080, "H": 1920, "frames": {}, "parts": parts, "needles": needles, "car_root": car_root}
for scn in ("Road", "Studio"):
    sc = bpy.data.scenes[scn]
    frames = sorted(f for f, (s, _) in own.items() if s == scn)
    vl = sc.view_layers[0]
    names = [n for n in parts + needles if n in sc.objects]
    for f in frames:
        sc.frame_set(f)
        dg = vl.depsgraph
        cam = bpy.data.objects[own[f][1]].evaluated_get(dg)
        P = cam.calc_matrix_camera(dg, x=1080, y=1920, scale_x=1.0, scale_y=1.0)
        rec = {"scene": scn, "cam": own[f][1], "P": M(P), "V": M(cam.matrix_world.inverted()), "lens": round(cam.data.lens, 3),
               "a": {}}
        for k, n in roots.items():
            if n in sc.objects:
                rec[k] = M(bpy.data.objects[n].evaluated_get(dg).matrix_world)
        if car_root and car_root in sc.objects:
            rec["car"] = M(bpy.data.objects[car_root].evaluated_get(dg).matrix_world)
        for n in names:
            oe = bpy.data.objects[n].evaluated_get(dg)
            if oe.hide_render:
                continue
            rec["a"][n] = centre(oe)
        res["frames"][f] = rec
        if f % 100 == 0:
            print(f"[export] {scn} f{f}", flush=True)
json.dump(res, open(out, "w"))
print("[export] wrote", out, len(res["frames"]), "frames")
