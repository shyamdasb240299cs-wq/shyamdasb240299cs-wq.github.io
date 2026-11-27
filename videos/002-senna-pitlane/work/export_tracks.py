"""Dump per-frame camera + car positions (evaluated from the baked animation curves, no scene evaluation) for the
sound design: blender -b scene.blend -P export_tracks.py -- <out.json>"""
import bpy, sys, json
out = sys.argv[sys.argv.index("--") + 1]
sc = bpy.context.scene


def curves(ob):
    ad = ob.animation_data; act = ad.action if ad else None
    cs = {}
    if not act: return cs
    fcs = list(getattr(act, "fcurves", []) or [])
    for L in getattr(act, "layers", []):
        for st in L.strips:
            for cb in getattr(st, "channelbags", []):
                fcs += list(cb.fcurves)
    for fc in fcs:
        cs[(fc.data_path, fc.array_index)] = fc
    return cs


def loc_track(name, n):
    ob = bpy.data.objects[name]; cs = curves(ob)
    return [[cs[("location", i)].evaluate(f) if ("location", i) in cs else ob.location[i] for i in range(3)] for f in range(n)]


n = sc.frame_end + 1
data = {"fps": sc.render.fps, "n": n, "cam": loc_track("CAM", n), "senna": loc_track("senna_root", n), "rival": loc_track("rival_root", n),
        "lens": [], "cut": []}
cam = bpy.data.objects["CAM"].data; cl = curves(cam)
data["lens"] = [cl[("lens", 0)].evaluate(f) if ("lens", 0) in cl else cam.lens for f in range(n)]
json.dump(data, open(out, "w"))
print("tracks exported", n)
