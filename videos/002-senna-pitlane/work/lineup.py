import bpy, sys, os
VD = sys.argv[sys.argv.index("--") + 1]; OUT = sys.argv[-1]
SKILL = os.path.abspath(os.path.join(VD, "..", "..", ".claude", "skills", "shorts3d-studio"))
sys.path.insert(0, os.path.join(VD, "work")); sys.path.insert(0, SKILL)
import crowd
from engine import build as EB
from mathutils import Vector
bpy.ops.wm.read_factory_settings(use_empty=True); sc = bpy.context.scene
HOME = os.path.expanduser("~/.shorts3d")
paths = {"chars": os.path.join(HOME, "library", "characters"), "mh_data": "", "mh_assets": "", "cache": HOME, "clips": ""}
keys = crowd.actor_keys()
for i, k in enumerate(keys):
    rig = EB.load_character(k, {"char": k}, paths, {})
    for o in rig.users_collection[0].objects:
        if o.type == "MESH":
            for m in list(o.modifiers):
                if m.type == "SUBSURF": o.modifiers.remove(m)
            if o.data.shape_keys: o.shape_key_clear()
    rig.location = ((i % 16) * 0.9, 0, -(i // 16) * 2.4); rig.rotation_euler = (0, 0, 0)
    t = bpy.data.curves.new(k, "FONT"); t.body = k[4:]; t.size = 0.18; to = bpy.data.objects.new(k + "_t", t); sc.collection.objects.link(to)
    to.location = ((i % 16) * 0.9 - 0.2, -0.4, 2.0 - (i // 16) * 2.4); to.rotation_euler = (1.5708, 0, 0)
for im in bpy.data.images:
    if im.filepath.endswith(".tga") and os.path.exists(im.filepath[:-4] + "_1k.png"):
        im.filepath = im.filepath[:-4] + "_1k.png"; im.reload()
w = bpy.data.worlds.new("w"); sc.world = w; w.use_nodes = True; w.node_tree.nodes["Background"].inputs[0].default_value = (0.8, 0.82, 0.85, 1)
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam")); sc.collection.objects.link(cam); sc.camera = cam
cam.data.type = "ORTHO"; cam.data.ortho_scale = 15.0; cam.location = (6.75, -20, 0.7); cam.rotation_euler = (1.5708, 0, 0)
sc.render.engine = "BLENDER_WORKBENCH"; sc.display.shading.light = "STUDIO"; sc.display.shading.color_type = "TEXTURE"; sc.render.resolution_x, sc.render.resolution_y = 1800, 1150
sc.render.filepath = OUT; bpy.ops.render.render(write_still=True)
