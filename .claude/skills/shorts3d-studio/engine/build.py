"""Blender entry point: build ONE scene from a resolved plan, bake the performance, QA, render.

blender -b --factory-startup -P engine/build.py -- <plan.json> <scene> <out_dir> <quality> [shots=S01,S02]
quality: sheet (3 stills/shot) | blocking (workbench, all frames) | preview (EEVEE 50%) | final (EEVEE) | cycles
"""
import bpy, sys, os, json, time, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mathutils import Vector
from engine import core, rigkit, motion, props as P, sets, camera, fx, qa, mhchar  # noqa: E402

T0 = time.time()


def reset():
    for c in list(bpy.data.collections):
        bpy.data.collections.remove(c)
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)


def load_character(key, cs, paths, brand):
    name = cs.get("char", "hero")
    lib = paths["chars"]; os.makedirs(lib, exist_ok=True)
    f = os.path.join(lib, name + ".blend")
    preset = os.path.join(lib, name + ".json")
    if not os.path.exists(f):
        spec = json.load(open(preset)) if os.path.exists(preset) else {}
        spec.update({k: cs[k] for k in ("macro", "outfit") if k in cs})
        coll, rig, body = mhchar.build_character(name, paths["mh_data"], paths["mh_assets"],
                                                 macro=spec.get("macro"), outfit=spec.get("outfit"), log=core.log)
        bpy.data.libraries.write(f, {coll, *coll.objects}, fake_user=True)
        core.log(f"character '{name}' built and cached -> {f}")
        bpy.data.collections.remove(coll)
        for o in list(rig.children) + [rig]:
            if o.name in bpy.data.objects: bpy.data.objects.remove(o)
    with bpy.data.libraries.load(f, link=False) as (src, dst):
        dst.collections = [c for c in src.collections if c.startswith("CHAR_")]
    coll = dst.collections[0]
    bpy.context.scene.collection.children.link(coll)
    rig = next(o for o in coll.objects if o.type == "ARMATURE")
    rig.name = f"{key}_rig"
    return rig


def resolve_point(spec, objs, chars):
    """'prop' | 'actor.part' | [x,y,z] -> world Vector (evaluated at current frame)."""
    if isinstance(spec, (list, tuple)):
        return Vector(spec)
    base, _, part = spec.partition(".")
    if base in chars:
        return camera._part_point(chars[base], part or "chest")
    ob = objs.get(base) or bpy.data.objects.get(base)
    mn, mx = core.world_bbox(ob)
    return (mn + mx) / 2


def prop_verb(ob, verb, a, fps, objs):
    f0 = round(float(a.get("at_s", 0)) * fps); dur = round(float(a.get("dur", 0.6)) * fps)
    if verb == "pop":
        fx.pop(ob, f0, [0], dur=max(4, dur // 2 or 8))
    elif verb in ("move", "slide"):
        to = Vector(a["to"]) if isinstance(a["to"], (list, tuple)) else resolve_point(a["to"], objs, {})
        ob.keyframe_insert("location", frame=f0); ob.location = to; ob.keyframe_insert("location", frame=f0 + dur)
    elif verb == "spin":
        ob.rotation_mode = "XYZ"; ob.keyframe_insert("rotation_euler", frame=f0)
        ob.rotation_euler.z += math.radians(float(a.get("deg", 360))); ob.keyframe_insert("rotation_euler", frame=f0 + dur)
    elif verb in ("hide", "show"):
        for fr, hid in ((0, verb == "show"), (f0 - 1, verb == "show"), (f0, verb == "hide")):
            ob.hide_render = hid; ob.keyframe_insert("hide_render", frame=max(0, fr))
    elif verb == "fall":  # ballistic drop to the support below, small bounce
        g = 9.81; p = ob.location.copy(); v = 0.0
        dg = bpy.context.evaluated_depsgraph_get(); sc = bpy.context.scene
        mn, _ = core.world_bbox(ob); lift = p.z - mn.z
        res, loc, *_ = sc.ray_cast(dg, Vector((p.x, p.y, mn.z - 0.001)), Vector((0, 0, -1)), distance=50)
        floor = (loc.z if res else 0.0) + lift
        fr = f0; ob.keyframe_insert("location", frame=fr); bounced = 0
        while fr < f0 + fps * 3:
            fr += 1; v -= g / fps; p.z += v / fps
            if p.z <= floor:
                p.z = floor; v = -v * 0.25; bounced += 1
            ob.location = p; ob.keyframe_insert("location", frame=fr)
            if bounced >= 3 and abs(v) < 0.05: break
    elif verb == "shake":
        base = ob.location.copy()
        for i in range(0, dur, 2):
            ob.location = base + Vector(((-1) ** i * 0.004, 0, 0)); ob.keyframe_insert("location", frame=f0 + i)
        ob.location = base; ob.keyframe_insert("location", frame=f0 + dur)


def apply_fx(item, frames, objs, chars, brand, cams):
    t = item["type"]; f0, f1 = int(item["f0"]), int(item["f1"])
    col = item.get("color", brand.get("color", "#3EC6E0"))
    tgt = lambda k: [o for o in (chars[item[k]].meshes if item.get(k) in chars else [objs.get(item.get(k))]) if o]
    inner = [objs[n] for n in item.get("inner", []) if n in objs]
    if t == "xray":
        fx.xray(tgt("target"), f0, f1, col, inner=inner, frames=frames)
    elif t == "cutaway":
        ch = chars.get(item["target"])
        bpy.context.scene.frame_set(f0)
        c = resolve_point(item.get("at", item["target"] + ".chest"), objs, chars)
        nrm = (ch.rig.matrix_world.to_quaternion() @ ch.fwd) if ch else Vector(item.get("normal", (0, -1, 0)))
        targets = [o for o in ch.meshes if o.get("s3d_part") in ("body", "clothes")] if ch else tgt("target")
        fx.cutaway(targets, c, nrm, float(item.get("radius", 0.09)), f0, f1, frames,
                   bone_parent=(ch.rig, ch.spine[-1]) if ch and ch.spine else None, rim_color=col, inner=inner)
    elif t == "arrow":
        bpy.context.scene.frame_set(f0)
        fx.arrow(item.get("name", f"arrow_{f0}"), resolve_point(item["from"], objs, chars), resolve_point(item["to"], objs, chars),
                 f0, f1, frames, col, float(item.get("radius", 0.01)), float(item.get("bend", 0.15)))
    elif t == "label":
        bpy.context.scene.frame_set(f0)
        fx.label3d(item.get("name", f"label_{f0}"), item["text"], resolve_point(item["at"], objs, chars) + Vector(item.get("offset", (0, 0, 0.08))),
                   f0, f1, frames, item.get("color", "#FFFFFF"), float(item.get("size", 0.06)), cams.get(item.get("shot")))
    elif t == "glow":
        fx.glow(objs[item["target"]], f0, f1, frames, col, float(item.get("strength", 6)))
    elif t == "glass":
        fx.glass(objs[item["target"]])
    elif t == "flow":
        pts = [resolve_point(p, objs, chars) for p in item["path"]]
        fx.flow(item.get("name", f"flow_{f0}"), pts, f0, f1, frames, col, int(item.get("count", 24)))
    elif t == "lightning":
        fx.lightning(item.get("name", f"bolt_{f0}"), resolve_point(item["from"], objs, chars), resolve_point(item["to"], objs, chars), f0, f1, frames, col)
    elif t == "exploded":
        parts = [objs[n] for n in item["parts"]]
        fx.exploded(parts, resolve_point(item.get("center", item["parts"][0]), objs, chars), f0, f1, frames, float(item.get("dist", 0.12)))
    elif t == "crumbs":
        bpy.context.scene.frame_set(f0)
        fx.crumbs(item.get("name", f"crumbs_{f0}"), resolve_point(item["at"], objs, chars), f0, frames,
                  int(item.get("count", 14)), floor_z=float(item.get("floor_z", 0.0)))
    else:
        core.log(f"unknown fx {t}")


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    plan = json.load(open(argv[0])); scene_name, out, quality = argv[1], argv[2], argv[3]
    opts = dict(a.split("=", 1) for a in argv[4:] if "=" in a)
    os.makedirs(out, exist_ok=True)
    S = plan["scenes"][scene_name]; fps = plan["fps"]; brand = plan.get("brand", {}); paths = plan["paths"]
    shots = [s for s in plan["shots"] if s["scene"] == scene_name]
    if opts.get("shots"):
        want = set(opts["shots"].split(",")); shots = [s for s in shots if s["id"] in want]
    reset()
    sc = bpy.context.scene; sc.render.fps = fps
    coll = core.ensure_coll("SET")
    st = S.get("set", {})
    objs = dict(sets.SETS[st.get("preset", "studio")](coll, color=st.get("color", brand.get("bg", "#3EC6E0")), ground=st.get("ground", "grass")))
    wd = st.get("world", {})
    sets.world(wd.get("kind", "sky" if st.get("preset") == "outdoor" else "color"), wd.get("color", st.get("sky", "#BDEBF5")),
               float(wd.get("strength", 0.35)), wd.get("hdri"))
    logo = brand.get("logo")
    for name, ps in S.get("props", {}).items():
        ob = P.make(name, ps["src"], coll, ps.get("size_cm"), ps.get("params"))
        if ps.get("surface"): ob["s3d_surface"] = True
        if ps.get("obstacle"): ob["s3d_obstacle"] = True
        if ps.get("free"): ob["s3d_free"] = True
        yaw = math.radians(float(ps.get("yaw", 0)))
        if "on" in ps:
            P.place_on(ob, objs[ps["on"]], ps.get("at"), yaw)
        else:
            at = ps.get("at", [0, 0]); ob.location = (at[0], at[1], at[2] if len(at) > 2 else 0); ob.rotation_euler.z = yaw
        if ps.get("color"):
            for sl in ob.material_slots:
                if sl.material:
                    sl.material = sl.material.copy(); b = sl.material.node_tree.nodes.get("Principled BSDF")
                    if b and not b.inputs["Base Color"].links: b.inputs["Base Color"].default_value = core.hexcol(ps["color"])
        if ps.get("label") and logo:
            sets.prop_label(ob, logo, ps["label"], float(ps.get("label_size", 0.05)))
        objs[name] = ob
    bpy.context.view_layer.update()
    # cast
    chars, perfs = {}, {}
    for key, cs in S.get("cast", {}).items():
        rig = load_character(key, cs, paths, brand)
        at = cs.get("at", [0, -1.5]); rig.location = (at[0], at[1], 0)
        face = cs.get("face", 0)
        if isinstance(face, str):
            p = resolve_point(face, objs, chars); yaw = math.atan2(p.x - at[0], -(p.y - at[1]))
        else:
            yaw = math.radians(float(face))
        rig.rotation_euler = (0, 0, yaw)
        s_ = float(cs.get("scale", 1.0)); rig.scale = (s_, s_, s_)
        bpy.context.view_layer.update()
        ch = rigkit.Character(key, rig); ch.setup_controls(); chars[key] = ch
        if brand.get("shirt", True):
            clothes = [o for o in ch.meshes if o.get("s3d_part") == "clothes"]
            chest = rig.matrix_world @ (ch.arm_head("upperarm.L").lerp(ch.arm_head("upperarm.R"), 0.5) + Vector((0, 0, -0.13)))
            fwd = rig.matrix_world.to_quaternion() @ ch.fwd
            sets.shirt_decal(clothes, logo, chest, fwd, size=float(brand.get("shirt_logo_size", 0.12)),
                             tint=cs.get("shirt", brand.get("shirt_color")))
        perfs[key] = motion.Performer(ch, at, yaw, fps, seed=len(perfs) + 1, scene_props=objs)
    # performance script (prop verbs too)
    for it in S.get("script", []):
        who = it["who"]; a = dict(it.get("args", {}))
        if who in perfs:
            r = perfs[who].do(it["verb"], a)
            if r: core.log(f"{who}.{it['verb']} {r[0]:.2f}-{r[1]:.2f}s")
        elif who in objs:
            prop_verb(objs[who], it["verb"], a, fps, objs)
    end_f = max([s["f1"] for s in plan["shots"] if s["scene"] == scene_name] + [int(max([p.cursor for p in perfs.values()] or [0]) * fps)])
    frames = list(range(0, end_f + 2))
    sc.frame_start, sc.frame_end = 0, frames[-1]
    kw = core.KeyWriter()
    for p in perfs.values(): p.bake(kw, frames)
    kw.write()
    for p in perfs.values(): p.solve_contacts(kw, frames)
    kw.write()
    for p in perfs.values(): p.bake_attachments(kw, frames, coll)
    core.log(f"performance baked ({len(frames)} frames) in {time.time() - T0:.1f}s")
    # cameras (one per shot) + camera markers
    cams = {}
    for sh in shots:
        fr = list(range(sh["f0"], sh["f1"] + 1))
        cam = camera.solve_camera(f"CAM_{sh['id']}", sh.get("cam", {}), fr, chars, objs, fps)
        cams[sh["id"]] = cam
        m = sc.timeline_markers.new(sh["id"], frame=sh["f0"]); m.camera = cam
        sh["cam_name"] = cam.name
        tg = sh.get("cam", {}).get("target", "actor")
        base, _, part = tg.partition(".") if isinstance(tg, str) else ("", "", "")
        if base in chars:
            sh["_target_fn"] = (lambda c=chars[base], pt=(part or "face"): camera._part_point(c, pt if pt != "object" else "hand_R"))
        elif base in objs:
            sh["_target_fn"] = (lambda o=objs[base]: (lambda mn_mx: (mn_mx[0] + mn_mx[1]) / 2)(core.world_bbox(o)))
    if shots:
        sc.camera = cams[shots[0]["id"]]
    for it in S.get("fx", []):
        apply_fx(it, frames, objs, chars, brand, cams)
    for sh in shots:
        for it in sh.get("fx", []):
            it = dict(it); it.setdefault("f0", sh["f0"]); it.setdefault("f1", sh["f1"]); it.setdefault("shot", sh["id"])
            apply_fx(it, frames, objs, chars, brand, cams)
    # lights centred on the action
    pts = [ch.rig.matrix_world.translation for ch in chars.values()] or [Vector((0, 0, 0))]
    center = sum(pts, Vector()) / len(pts) + Vector((0, 0, 1.2 if chars else 0.5))
    yaw0 = next(iter(perfs.values())).root_yaw.at(shots[0]["f0"] / fps) if perfs and shots else 0.0
    sets.light_rig(st.get("light", "soft"), center, yaw0, coll, float(st.get("light_scale", 1.0)))
    # timed events for automatic foley (only inside this scene's rendered shots)
    ranges = [(sh["f0"], sh["f1"]) for sh in shots]
    inside = lambda t: any(a <= t * fps <= b for a, b in ranges)
    evs = []
    for p in perfs.values():
        for st in p.steps:
            evs.append({"type": "step", "t": st[2]})
        for e in p.events:
            if e["type"] == "grip": evs.append({"type": "grab", "t": e["t_close"]})
            elif e["type"] == "bite": evs.append({"type": "bite", "t": e["t_contact"] + 0.06})
            elif e["type"] == "detach": evs.append({"type": "release", "t": e["t"]})
    for it in S.get("fx", []) + [x for sh in shots for x in sh.get("fx", [])]:
        if it.get("type") in ("arrow", "label") and "f0" in it: evs.append({"type": "pop" if it["type"] == "label" else "arrow", "t": it["f0"] / fps})
    for it in S.get("script", []):
        if it["verb"] == "pop": evs.append({"type": "pop", "t": float(it["args"].get("at_s", 0))})
    json.dump(sorted([e for e in evs if inside(e["t"])], key=lambda e: e["t"]), open(os.path.join(out, "events.json"), "w"))
    # QA
    issues = qa.run(list(perfs.values()), {k: v for k, v in objs.items() if v.get("s3d_prop")}, shots, frames)
    json.dump({"scene": scene_name, "issues": issues, "log": core.LOG[-60:]}, open(os.path.join(out, f"qa_{scene_name}.json"), "w"), indent=1)
    for sh in shots:
        sh.pop("_target_fn", None)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out, f"{scene_name}.blend"))
    # screen-space anchors for 2D overlays (assembly draws crisp arrows/labels there)
    anchors = {}
    for sh in shots:
        for ov in sh.get("overlays", []):
            if "anchor" not in ov: continue
            cam = cams[sh["id"]]; track = []
            for f in range(sh["f0"], sh["f1"] + 1, 2):
                sc.frame_set(f)
                from bpy_extras.object_utils import world_to_camera_view
                v = world_to_camera_view(sc, cam, resolve_point(ov["anchor"], objs, chars))
                track.append([f, round(v.x, 4), round(1 - v.y, 4)])
            anchors.setdefault(sh["id"], {})[ov["anchor"]] = track
    json.dump(anchors, open(os.path.join(out, f"anchors_{scene_name}.json"), "w"))
    # render
    q = {"sheet": "preview", "blocking": "blocking", "draft": "draft", "preview": "preview", "final": "final", "cycles": "cycles"}[quality]
    sets.render(q, tuple(plan.get("res", (1080, 1920))), fps)
    if quality == "sheet":
        sc.render.resolution_percentage = int(opts.get("pct", 40))
        for sh in shots:
            for k, f in enumerate((sh["f0"] + 2, (sh["f0"] + sh["f1"]) // 2, sh["f1"] - 1)):
                sc.frame_set(f); sc.camera = cams[sh["id"]]
                sc.render.filepath = os.path.join(out, "sheet", f"{sh['f0']:05d}_{sh['id']}_{k}.jpg")
                bpy.ops.render.render(write_still=True)
    else:
        fdir = os.path.join(out, "frames"); os.makedirs(fdir, exist_ok=True)
        step = int(opts.get("step", 1))
        sc.frame_step = step
        for sh in shots:
            sc.frame_start, sc.frame_end = sh["f0"], sh["f1"]
            sc.render.filepath = os.path.join(fdir, "#####")
            bpy.ops.render.render(animation=True)
    print(f"[s3d] DONE scene={scene_name} shots={len(shots)} frames={len(frames)} secs={time.time() - T0:.0f}")
    for i in issues:
        print("[s3d] QA", i)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback; traceback.print_exc()
        print("[s3d] FAILED", e)
        sys.exit(1)
