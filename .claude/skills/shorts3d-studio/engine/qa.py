"""Automatic quality checks (cheap, text output). Every issue names frames + a concrete fix hint."""
import bpy, math
from mathutils import Vector
from bpy_extras.object_utils import world_to_camera_view
from . import core


def _ranges(frames):
    out = []
    for f in sorted(frames):
        if out and f - out[-1][1] <= 3:
            out[-1][1] = f
        else:
            out.append([f, f])
    return ", ".join(f"f{a}" if a == b else f"f{a}-{b}" for a, b in out[:4])


def run(performers, props, shots, frames, step=3):
    sc = bpy.context.scene
    issues = []
    sample = frames[::step]
    held = {}  # prop -> set(frames) when attached
    for perf in performers:
        for ev in perf.events:
            if ev["type"] == "attach":
                det = next((e for e in perf.events if e["type"] == "detach" and e["prop"] == ev["prop"] and e["t"] > ev["t"]), None)
                a = round(ev["t"] * perf.fps); b = round(det["t"] * perf.fps) if det else frames[-1]
                held.setdefault(ev["prop"], set()).update(range(a, b + 1))
    furniture = [o for o in props.values() if (o.get("s3d_surface") or o.get("s3d_obstacle")) and core.world_bbox(o)[1].z > 0.3]
    fbvh = {o.name: core.bvh_world(o, evaluated=False) for o in furniture}
    pbvh = {}
    bad = {"reach": [], "skate": [], "under": [], "finger": [], "body": [], "float": [], "frame": []}
    last_foot = {}
    for f in sample:
        sc.frame_set(f)
        for perf in performers:
            ch = perf.ch; rig = ch.rig; pb = rig.pose.bones; M = rig.matrix_world
            for s in "LR":
                hand = M @ pb[ch.b[f"hand.{s}"]].head
                tgt = ch.ctrl[f"hand.{s}"].matrix_world.translation
                if (hand - tgt).length > 0.025:
                    bad["reach"].append((f, perf, s, (hand - tgt).length))
                fp = M @ pb[ch.b[f"foot.{s}"]].head
                if fp.z < 0.035:
                    bad["under"].append((f, perf, s))
                key = (perf.ch.name, s)
                planted = all(not (st[1] <= f / perf.fps <= st[2]) for st in perf.steps if st[0] == s)
                if planted and key in last_foot and (Vector((fp.x, fp.y)) - last_foot[key]).length > 0.012 * step:
                    bad["skate"].append((f, perf, s))
                last_foot[key] = Vector((fp.x, fp.y))
                # fingers inside a held prop
                for prop, fr in held.items():
                    if f in fr and prop in props and perf.holding_at(prop, f) == s:
                        if prop not in pbvh or pbvh[prop][0] != f:
                            pbvh[prop] = (f, core.bvh_world(props[prop], evaluated=False))
                        tree = pbvh[prop][1]
                        for fname, pts in ch.fingertip_points(s).items():
                            for p in pts[3:]:
                                loc, nrm, _, dist = tree.find_nearest(p)
                                if loc is not None and (p - loc).dot(nrm) < 0 and 0.004 < dist < 0.03:
                                    bad["finger"].append((f, perf, s, dist, prop)); break
            # body vs furniture (wrists/elbows/knees/hips are the usual offenders)
            for o in furniture:
                tree = fbvh[o.name]
                for bn in ("hand.L", "hand.R", "forearm.L", "forearm.R", "shin.L", "shin.R", "thigh.L", "thigh.R"):
                    b = ch.b.get(bn)
                    if not b: continue
                    p = M @ pb[b].head
                    loc, nrm, _, dist = tree.find_nearest(p)
                    if loc is not None and (p - loc).dot(nrm) < 0 and dist > 0.02:
                        bad["body"].append((f, perf, bn, dist, o.name))
    # props resting: not held -> must touch support (no floating, no sinking)
    for name, ob in props.items():
        if ob.get("s3d_surface") or name.endswith("__bitten") or ob.get("s3d_free"):
            continue
        for f in (frames[0], frames[-1]):
            if f in held.get(name, ()):
                continue
            sc.frame_set(f)
            mn, mx = core.world_bbox(ob)
            dg = bpy.context.evaluated_depsgraph_get()
            o = Vector(((mn.x + mx.x) / 2, (mn.y + mx.y) / 2, mx.z + 0.3))
            res, hit = False, None
            for _ in range(8):  # walk down through the prop itself to the first support surface
                res, loc, n, i, hit, _m = sc.ray_cast(dg, o, Vector((0, 0, -1)), distance=5.0)
                if not res or (hit is not ob and not hit.name.startswith(name + "_bite")):
                    break
                o = loc - Vector((0, 0, 1e-4))
            if res and hit is not ob:
                gap = mn.z - loc.z
                if abs(gap) > 0.004 and mn.z > 0.01:
                    bad["float"].append((f, name, gap))
    # framing: subject inside safe area of each shot camera
    for sh in shots:
        cam = bpy.data.objects.get(sh["cam_name"])
        tgt = sh.get("_target_fn")
        if not cam or not tgt: continue
        for f in (sh["f0"], (sh["f0"] + sh["f1"]) // 2, sh["f1"]):
            sc.frame_set(f)
            p = tgt()
            if p is None: continue
            v = world_to_camera_view(sc, cam, p)
            if v.z <= 0 or not (0.05 < v.x < 0.95) or not (0.3 < v.y < 0.97):
                bad["frame"].append((f, sh["id"], (round(v.x, 2), round(v.y, 2))))
    # summarise
    def per(key, fmt, hint):
        if bad[key]:
            issues.append(f"{fmt} @ {_ranges([b[0] for b in bad[key]])}  -> {hint}")
    per("reach", f"hand can't reach target (max {max([b[3] for b in bad['reach']] or [0])*100:.0f} cm)", "walk_to closer (stop:reach) or lower the object / add crouch")
    per("finger", "finger inside held prop", "grip:auto -> try grip: top|side|pinch, or enlarge prop size_cm")
    per("body", f"body inside furniture ({', '.join(sorted({b[2] for b in bad['body']}))})", "move actor back (cast.at) or reach from another side")
    per("skate", "foot sliding while planted", "slow walk (speed 0.8) or shorten path")
    per("under", "foot below floor", "check floor height / set z")
    if bad["float"]:
        issues.append("prop not resting on support: " + ", ".join(f"{n} {g*1000:+.0f}mm" for _, n, g in bad["float"][:4]) + "  -> use on:<surface> placement")
    if bad["frame"]:
        issues.append("subject outside caption-safe frame: " + ", ".join(f"{s}@f{f} {v}" for f, s, v in bad["frame"][:4]) + "  -> change size/angle or target")
    return issues
