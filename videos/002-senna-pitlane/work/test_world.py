import bpy, sys, os, time
VD = sys.argv[sys.argv.index("--") + 1]; OUT = sys.argv[-1]; sys.path.insert(0, os.path.join(VD, "work"))
import world
from mathutils import Vector
bpy.ops.wm.read_factory_settings(use_empty=True)
sc = bpy.context.scene; T = time.time()
W = world.World(VD); W.build_roads(); world.build_terrain(W); world.build_kerbs(W)
world.build_barriers(W); world.build_fences(W); world.build_buildings(W); world.build_pit_garages(W)
world.build_trees(W)
world.build_crowd(W, [("Wheatcroft Straight", -1, 22, 34, 0, 165), ("Wheatcroft Straight", -1, 22, 34, 305, 480),
                      ("Goddards", -1, 30, 44, 0, 95), ("Melbourne Loop (down)", 1, 15, 26, 0, 360)])
world.build_grandstand(W); world.build_boards(W)
world.setup_world(sc); world.setup_mist(sc)
print("world built in", round(time.time() - T, 1), "s")
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam")); sc.collection.objects.link(cam); sc.camera = cam
cam.data.clip_start = 0.3; cam.data.clip_end = 4000
sc.render.engine = "BLENDER_EEVEE"; sc.eevee.taa_render_samples = 12
sc.render.resolution_x, sc.render.resolution_y = 540, 960
views = [((-120, -95, 32), (70, -30, 0), 24), ((62, 13, 1.3), (-40, 33, 1.0), 20), ((150, -60, 3), (220, -20, 1), 28)]
for i, (loc, tgt, lens) in enumerate(views):
    cam.data.lens = lens
    L = Vector((loc[0], loc[1], float(W.h(loc[0], loc[1])) + loc[2])); G = Vector((tgt[0], tgt[1], float(W.h(tgt[0], tgt[1])) + tgt[2]))
    cam.location = L; cam.rotation_euler = (G - L).to_track_quat('-Z', 'Y').to_euler()
    sc.render.filepath = os.path.join(OUT, f"world_{i}.png"); t = time.time(); bpy.ops.render.render(write_still=True); print("render", i, round(time.time() - t, 1), "s")
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT, "world_test.blend"))
