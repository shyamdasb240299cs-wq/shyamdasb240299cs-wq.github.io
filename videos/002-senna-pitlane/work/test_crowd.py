import bpy, sys, os, time, random, math
VD = sys.argv[sys.argv.index("--") + 1]; OUT = sys.argv[-1]
SKILL = os.path.abspath(os.path.join(VD, "..", "..", ".claude", "skills", "shorts3d-studio"))
sys.path.insert(0, os.path.join(VD, "work")); sys.path.insert(0, SKILL)
import crowd
from mathutils import Vector
bpy.ops.wm.read_factory_settings(use_empty=True); sc = bpy.context.scene; sc.render.fps = 30
t = time.time()
keys = crowd.actor_keys()[::5]
par = crowd.load_actors(sc, keys, 30, list(range(0, 61)), seed=3)
print("actors", len(keys), round(time.time() - t, 1), "s")
rnd = random.Random(2)
pts = crowd.poisson(lambda r: (r.uniform(-6, 6), r.uniform(-4, 4)), 0.85, 400, rnd, lambda p: True)
pts3 = [(x, y, 0.0) for x, y in pts]
ids = crowd.assign_actors(pts3, len(keys), rnd)
yaws = [math.radians(180 + rnd.uniform(-25, 25)) for _ in pts]
C = bpy.data.collections.new("AUD"); sc.collection.children.link(C)
crowd.instancer("aud_test", pts3, yaws, ids, par, C)
print("people", len(pts))
me = bpy.data.meshes.new("g"); me.from_pydata([(-30, -30, 0), (30, -30, 0), (30, 30, 0), (-30, 30, 0)], [], [(0, 1, 2, 3)])
g = bpy.data.objects.new("g", me); sc.collection.objects.link(g)
w = bpy.data.worlds.new("w"); sc.world = w; w.use_nodes = True; w.node_tree.nodes["Background"].inputs[0].default_value = (0.6, 0.65, 0.7, 1)
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam")); sc.collection.objects.link(cam); sc.camera = cam
cam.location = (0, 9.5, 1.7); cam.rotation_euler = (Vector((0, 0, 1.2)) - cam.location).to_track_quat('-Z', 'Y').to_euler(); cam.data.lens = 32
sc.render.engine = "CYCLES"; sc.cycles.samples = 16; sc.cycles.use_denoising = True; sc.render.resolution_x, sc.render.resolution_y = 432, 768
for f in (5, 50):
    sc.frame_set(f); sc.render.filepath = os.path.join(OUT, f"crowd_{f}.png"); bpy.ops.render.render(write_still=True)
print("done", round(time.time() - t, 1))
