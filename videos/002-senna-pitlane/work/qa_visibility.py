"""Subject-visibility QA (run after a build): share of sample points on each car that the camera really sees, per shot.
    blender -b build/donington/scene.blend -P work/qa_visibility.py"""
import bpy, json, math, os
from mathutils import Vector
sc = bpy.context.scene
VD = os.path.abspath(os.path.join(os.path.dirname(bpy.data.filepath), "..", ".."))
for o in bpy.data.collections["MAP"].all_objects: o.hide_viewport = True
for o in bpy.data.objects:
    if o.name.startswith("rain") or "spray" in o.name or o.name.startswith("limit"): o.hide_viewport = True
def desc(o):
    s = {o.name}
    for c in o.children: s |= desc(c)
    return s
CARS = {k: (bpy.data.objects[f"{k}_root"], desc(bpy.data.objects[f"{k}_root"])) for k in ("senna", "rival")}
cuts = json.load(open(os.path.join(VD, "build", "cuts.json")))["cuts"]
PTS = [(x, y, z) for x in (-0.6, 0.6) for y in (-1.8, 0.0, 1.9) for z in (0.25, 0.75)]
res = {}
for name, f0, f1 in cuts:
    if name in ("map", "limit"): continue
    for f in range(f0, f1 + 1, 6):
        sc.frame_set(f); dg = bpy.context.evaluated_depsgraph_get(); cam = sc.camera
        cl = cam.matrix_world.translation.copy(); R = cam.matrix_world.to_3x3(); lens = cam.data.lens
        tv = 18.0 / lens; th = tv * 9 / 16
        for k, (root, names) in CARS.items():
            M = root.matrix_world; seen = inf = 0
            for p in PTS:
                w = M @ Vector(p); rel = R.transposed() @ (w - cl)
                if rel.z >= -0.5 or abs(rel.x / -rel.z) > th or abs(rel.y / -rel.z) > tv: continue
                inf += 1; d = w - cl
                hit, loc, n, i, ob, _ = sc.ray_cast(dg, cl, d.normalized(), distance=d.length + 0.5)
                if not hit or ob.name in names or (ob.parent and ob.parent.name in names): seen += 1
                elif (loc - w).length < 0.35: seen += 1
            res.setdefault(name, []).append((f, k, inf, seen))
for name, rows in res.items():
    line = []
    for k in ("senna", "rival"):
        r = [(f, i, s) for f, kk, i, s in rows if kk == k]
        inview = [x for x in r if x[1] > 0]
        bad = [f for f, i, s in inview if s / i < 0.5]
        line.append(f"{k}: in-view {len(inview)}/{len(r)} occluded>50% at {bad[:8]}{'…' if len(bad) > 8 else ''}")
    print(f"[visqa] {name:12s} " + " | ".join(line))
