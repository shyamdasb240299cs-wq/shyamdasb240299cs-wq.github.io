"""Cameras: shot-size presets, 9:16 auto-framing (caption-safe), moves, DOF, handheld, clip avoidance.

A camera is solved AFTER the performance is baked, so it frames the real motion (smoothed)."""
import bpy, math
from mathutils import Vector, Quaternion
from . import core

# size: subject height that fills the frame (m); lens mm; f-stop; default elevation (deg); aim part
SIZES = {  # ys = where the subject point sits on screen (0 bottom .. 1 top); captions live at ~0.2
    "ecu":    dict(h=0.16, lens=85, f=2.0, el=0, part="face", ys=0.55),
    "cu":     dict(h=0.36, lens=65, f=2.8, el=3, part="face", ys=0.6),
    "mcu":    dict(h=0.62, lens=50, f=3.5, el=5, part="upper", ys=0.6),
    "medium": dict(h=1.1, lens=35, f=5.0, el=7, part="chest", ys=0.6),
    "wide":   dict(h=2.5, lens=26, f=8.0, el=10, part="body", ys=0.55),
    "xwide":  dict(h=4.5, lens=24, f=11, el=14, part="body", ys=0.55),
    "insert": dict(h=0.24, lens=55, f=2.8, el=38, part="object", ys=0.55),
    "macro":  dict(h=0.1, lens=100, f=2.4, el=30, part="object", ys=0.55),
}
ANGLES = {"front": 0, "front_left": -35, "front_right": 35, "left": -90, "right": 90, "back_left": -145,
          "back_right": 145, "back": 180}
CAPTION_SAFE = 0.36  # keep subject centre in the upper part; captions live at ~78% of height


def _part_point(char, part, rig_eval=True):
    """World point on the character for framing (uses evaluated bones)."""
    rig = char.rig; pb = rig.pose.bones; M = rig.matrix_world
    if part == "face":
        hd = char.bone("head"); ph = pb[hd.name]
        Mh = M @ ph.matrix @ hd.matrix_local.inverted()
        return Mh @ ((char.eye_mid + char.mouth) / 2)
    if part == "eyes":
        hd = char.bone("head"); ph = pb[hd.name]
        return M @ ph.matrix @ hd.matrix_local.inverted() @ char.eye_mid
    if part == "mouth":
        hd = char.bone("head"); ph = pb[hd.name]
        return M @ ph.matrix @ hd.matrix_local.inverted() @ char.mouth
    if part in ("hand_R", "hand_L"):
        return M @ pb[char.b[f"hand.{part[-1]}"]].head
    if part == "upper":
        return _part_point(char, "face").lerp(_part_point(char, "chest"), 0.35)
    if part == "chest":  # mid-sternum (heart/lungs level)
        b = char.spine[-1] if char.spine else char.b["hips"]
        return (M @ pb[b].head).lerp(M @ pb[b].tail, 0.35)
    if part == "waist":
        return (M @ pb[char.b["hips"]].head) + Vector((0, 0, 0.15))
    return (M @ pb[char.b["hips"]].head)  # body


def _yaw_of(char):
    q = char.rig.matrix_world.to_quaternion()
    f = q @ char.fwd
    return math.atan2(f.x, -f.y)


def solve_camera(name, spec, frames, chars, objs, fps):
    """spec: {size, target, angle, el, lens, move, amount, dist, fstop, handheld, focus, roll, from, to}
    target: 'actor' | 'actor.face|eyes|mouth|hand_R|hand_L|chest' | '<prop>' | [x,y,z]."""
    size = SIZES.get(spec.get("size", "medium"), SIZES["medium"])
    lens = float(spec.get("lens", size["lens"]))
    cam_data = bpy.data.cameras.new(name)
    cam = bpy.data.objects.new(name, cam_data)
    bpy.context.scene.collection.objects.link(cam)
    cam_data.lens = lens; cam_data.sensor_fit = "AUTO"; cam_data.sensor_width = 36
    cam_data.clip_start = 0.01; cam_data.clip_end = 200
    tgt = spec.get("target", "actor")
    char, part, ob = None, None, None
    if isinstance(tgt, str):
        base, _, part = tgt.partition(".")
        if base in chars:
            char = chars[base]; part = part or size["part"]
            if part == "object":
                part = "hand_R"
        else:
            ob = objs.get(base) or bpy.data.objects.get(base)
    sc = bpy.context.scene

    def target_at(f):
        if char:
            sc.frame_set(f)
            return _part_point(char, part)
        if ob:
            sc.frame_set(f)
            mn, mx = core.world_bbox(ob); return (mn + mx) / 2
        return Vector(tgt)
    # sample + smooth the target path (cameras never jitter)
    pts = [target_at(f) for f in frames]
    k = max(1, int(fps * 0.25))
    sm = []
    for i in range(len(pts)):
        a, b = max(0, i - k), min(len(pts), i + k + 1)
        sm.append(sum(pts[a:b], Vector()) / (b - a))
    span = max((p - sm[0]).length for p in sm) if sm else 0.0
    if spec.get("lock", span < 0.12):  # subject barely moves -> rock-steady locked frame
        mid = sum(sm, Vector()) / len(sm)
        sm = [mid.copy() for _ in sm]
    # direction around the subject
    yaw0 = _yaw_of(char) if char else 0.0
    az = math.radians(spec.get("az", ANGLES.get(spec.get("angle", "front_left"), 0)))
    el = math.radians(spec.get("el", size["el"]))
    if spec.get("angle") == "top":
        el = math.radians(80)
    if spec.get("angle") == "low":
        el = math.radians(-18)
    vfov = 2 * math.atan(18.0 / lens)
    dist = float(spec.get("dist", 0)) or (size["h"] / (2 * math.tan(vfov / 2)))
    if ob is not None and "dist" not in spec:
        mn, mx = core.world_bbox(ob)
        h = max((mx - mn).length * 1.4, size["h"] * 0.5)
        dist = h / (2 * math.tan(vfov / 2))
    move = spec.get("move", "static"); amt = float(spec.get("amount", 1.0))
    n = len(frames)
    noise = core.Noise1D(hash(name) % 1000)
    kw = core.KeyWriter()
    cam.rotation_mode = "QUATERNION"
    blocked_scale = 1.0
    for i, f in enumerate(frames):
        u = i / max(1, n - 1)
        e = core.smooth(u)
        d = dist; a = az; elev = el; L = lens; up_off = 0.0
        if move == "push_in": d = dist * (1.0 - 0.18 * amt * e)
        elif move == "pull_out": d = dist * (0.85 + 0.18 * amt * e)
        elif move == "orbit": a = az + math.radians(25 * amt) * (e - 0.5)
        elif move == "crane_up": elev = el + math.radians(18 * amt) * e; up_off = 0.25 * amt * e
        elif move == "crane_down": elev = el + math.radians(18 * amt) * (1 - e)
        elif move == "dolly_zoom":
            L = lens * (1 + 0.6 * amt * e); d = dist * (L / lens)
        elif move == "snap_zoom":
            k2 = core.ease_out(min(1, u * 5)); d = dist * (1 - 0.35 * amt * k2)
        p = sm[i]
        dirv = Quaternion((0, 0, 1), yaw0 + a) @ Vector((0, -1, 0))
        dirv = (dirv * math.cos(elev) + Vector((0, 0, math.sin(elev)))).normalized()
        if spec.get("angle") == "pov" and char:
            sc.frame_set(f); eye = _part_point(char, "eyes")
            fw = Quaternion((0, 0, 1), _yaw_of(char)) @ char.fwd
            loc = eye + fw * 0.07
            lk = spec.get("look")
            if isinstance(lk, str) and (objs.get(lk) or bpy.data.objects.get(lk)):
                mn, mx = core.world_bbox(objs.get(lk) or bpy.data.objects.get(lk)); aim = (mn + mx) / 2
            elif isinstance(lk, (list, tuple)):
                aim = Vector(lk)
            else:
                aim = loc + fw * 0.6 + Vector((0, 0, -0.45))
        else:
            # place the subject at screen height ys (captions sit low in 9:16)
            loc = p + dirv * d + Vector((0, 0, up_off))
            fh = 2 * d * math.tan(vfov / 2)
            fwdc = (p - loc).normalized(); upc = (Vector((0, 0, 1)) - fwdc * fwdc.z).normalized()
            aim = p - upc * (float(spec.get("ys", size.get("ys", 0.58))) - 0.5) * fh
        if spec.get("handheld", move == "handheld"):
            hh = float(spec.get("shake", 1.0))
            loc = loc + Vector((noise.fbm(f * 0.04) * 0.012, noise.fbm(f * 0.04 + 50) * 0.008, noise.fbm(f * 0.04 + 90) * 0.01)) * hh * max(0.4, d)
        q = (aim - loc).to_track_quat("-Z", "Y")
        if spec.get("roll"):
            q = q @ Quaternion((0, 0, 1), math.radians(spec["roll"]))
        kw.put(cam, "location", f, loc); kw.put(cam, "rotation_quaternion", f, q)
        kw.put(cam_data, "lens", f, L)
        kw.put(cam_data, "dof.focus_distance", f, (p - loc).length)
    kw.write()
    cam_data.dof.use_dof = spec.get("dof", True)
    cam_data.dof.aperture_fstop = float(spec.get("fstop", size["f"]))
    cam_data.dof.aperture_blades = 7
    _avoid_clipping(cam, frames, sm, chars)
    return cam


def _avoid_clipping(cam, frames, targets, chars):
    """If scenery blocks the view (wall/table between camera and subject), log it for QA."""
    sc = bpy.context.scene
    dg = bpy.context.evaluated_depsgraph_get()
    hits = 0
    for i in range(0, len(frames), max(1, len(frames) // 6)):
        f = frames[i]; sc.frame_set(f)
        o = cam.matrix_world.translation; t = targets[i]
        d = t - o
        res, loc, nrm, idx, ob, mat = sc.ray_cast(dg, o, d.normalized(), distance=max(0.0, d.length - 0.25))
        if res and ob and not ob.get("s3d_part") and not ob.get("s3d_prop") and ob.type == "MESH":
            hits += 1
    if hits:
        core.log(f"camera {cam.name}: view blocked in {hits} samples (move angle/az or use 'lock')")
