import bpy, sys, os, time, random, math
VD = sys.argv[sys.argv.index("--") + 1]
SKILL = os.path.abspath(os.path.join(VD, "..", "..", ".claude", "skills", "shorts3d-studio"))
sys.path.insert(0, os.path.join(VD, "work")); sys.path.insert(0, SKILL)
import crowd, numpy as np
bpy.ops.wm.read_factory_settings(use_empty=True); sc = bpy.context.scene; sc.render.fps = 30
keys = crowd.actor_keys()[:2]
par = crowd.load_actors(sc, keys, 30, list(range(0, 121)), seed=3)
rig = [o for o in bpy.data.objects if o.type == "ARMATURE"][0]
mesh = [o for o in bpy.data.objects if o.type == "MESH" and o.parent == rig or (o.type == "MESH" and any(m.type == "ARMATURE" for m in o.modifiers))][0]
print("rig", rig.name, "anim", bool(rig.animation_data and rig.animation_data.action), "mesh", mesh.name)
for f in (5, 50, 100):
    sc.frame_set(f); dg = bpy.context.evaluated_depsgraph_get()
    ev = mesh.evaluated_get(dg); v = ev.data.vertices
    hand = max(range(len(v)), key=lambda i: v[i].co.x) if f == 5 else None
    pb = rig.pose.bones
    names = [b.name for b in pb][:3]
    print(f, "bone0 rot", [round(x, 3) for x in pb[2].rotation_quaternion], "v[1000]", [round(c, 3) for c in v[1000].co])
