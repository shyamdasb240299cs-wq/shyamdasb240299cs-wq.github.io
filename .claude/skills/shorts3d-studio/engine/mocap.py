"""Real human motion (mocap clips) layered onto the procedural performance.

A clip (FBX of any humanoid; default source: Microsoft Rocketbox, MIT, ~300 clips) is converted ONCE into a small
skeleton-independent JSON cache. Per frame it stores, in the character frame (facing -Y):
  q    torso / clavicle / hand / finger rotations as deltas from a reference bind pose (P_clip @ R_ref^-1)
  sp   spine rotations (any number of spine bones; re-sampled onto the actor's chain)
  ua/fa upper-arm and forearm DIRECTIONS (rest-pose independent -> arms solved by IK with the actor's own lengths)
  hip  pelvis offset / pelvis rest height (weight shifts; feet stay planted, so no sliding)
The performer blends clips in with crossfades; hands that reach, hold or gesture ignore the clip (contacts exact).

blender -b -P engine/mocap.py -- convert <clip.fbx> <out.json> [ref_rest.json]
blender -b -P engine/mocap.py -- rest <avatar.fbx> <out.json>        (reference bind pose of a clip family)
"""
import bpy, json, math, os, sys
from mathutils import Vector, Quaternion, Matrix

_LIB = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "library", "clips", "clips.json")))
CLIPS, ALIAS = _LIB["clips"], _LIB["alias"]   # curated Rocketbox clips (MIT): name -> what it reads as; short aliases
FINGERS = ("thumb", "index", "middle", "ring", "pinky")
KEYS = ["hips", "neck", "head"] + [f"{p}.{s}" for s in "LR" for p in ("shoulder", "hand")] + \
       [f"{f}{n}.{s}" for s in "LR" for f in FINGERS for n in (1, 2, 3)]


def _q(M):
    return M.to_3x3().normalized().to_quaternion()


def _facing(arm, b, rigkit):
    """Yaw that turns the skeleton to face -Y (from toe direction in the current pose)."""
    vs = []
    for s in "LR":
        ft, to = b.get(f"foot.{s}"), b.get(f"toe.{s}")
        if ft and to:
            v = arm.matrix_world @ arm.pose.bones[to].head - arm.matrix_world @ arm.pose.bones[ft].head
            v.z = 0; vs.append(v)
    if not vs or sum(vs, Vector()).length < 1e-6:
        return 0.0
    f = sum(vs, Vector()).normalized()
    return math.atan2(f.x, -f.y)


def _import(path, anim):
    before = set(bpy.data.objects)
    ext = os.path.splitext(path)[1].lower()
    if ext == ".bvh":
        bpy.ops.import_anim.bvh(filepath=path, axis_forward="-Z", axis_up="Y")
    else:
        bpy.ops.import_scene.fbx(filepath=path, use_anim=anim, ignore_leaf_bones=False)
    new = [o for o in bpy.data.objects if o not in before]
    arm = next((o for o in new if o.type == "ARMATURE"), None)
    if arm is None:
        raise RuntimeError(f"no armature in {path}")
    return arm, new


def _cleanup(objs):
    acts = {o.animation_data.action for o in objs if o.animation_data and o.animation_data.action}
    for o in objs:
        bpy.data.objects.remove(o, do_unlink=True)
    for a in acts:
        bpy.data.actions.remove(a)


def rest_pose(path, out):
    """Reference bind pose (world rotations per canonical bone) of a clip family, e.g. a Rocketbox avatar."""
    from engine import rigkit
    arm, objs = _import(path, False)
    b, spine = rigkit.map_bones(arm.data.bones, arm.name)
    for pb in arm.pose.bones:
        pb.matrix_basis = Matrix()
    bpy.context.view_layer.update()
    R = Quaternion((0, 0, 1), -_facing(arm, b, rigkit))
    Mw = arm.matrix_world
    rest = {k: [round(x, 5) for x in R @ _q(Mw @ arm.data.bones[b[k]].matrix_local)] for k in KEYS if b.get(k)}
    rest["spine"] = [[round(x, 5) for x in R @ _q(Mw @ arm.data.bones[n].matrix_local)] for n in spine]
    json.dump(rest, open(out, "w"))
    _cleanup(objs)
    print(f"[s3d] reference rest -> {out}")


def convert(path, out, ref=None):
    """FBX/BVH clip -> JSON cache (see module doc)."""
    from engine import rigkit
    sc = bpy.context.scene
    sc.render.fps = 30
    arm, objs = _import(path, True)
    b, spine = rigkit.map_bones(arm.data.bones, arm.name)
    act = arm.animation_data.action if arm.animation_data else None
    if not act:
        raise RuntimeError(f"{path}: no animation")
    f0, f1 = (int(round(x)) for x in act.frame_range)
    Mw = arm.matrix_world
    refd = json.load(open(ref)) if ref and os.path.exists(ref) else None
    keys = [k for k in KEYS if b.get(k)]
    sc.frame_set(f0)
    R = Quaternion((0, 0, 1), -_facing(arm, b, rigkit)); RM = R.to_matrix().to_4x4()
    if refd:
        Rr = {k: Quaternion(refd[k]) for k in keys if k in refd}
        Rs = [Quaternion(q) for q in refd.get("spine", [])]
    else:  # the clip's own bind pose is the reference (Mixamo/most FBX clips store one)
        Rr = {k: R @ _q(Mw @ arm.data.bones[b[k]].matrix_local) for k in keys}
        Rs = [R @ _q(Mw @ arm.data.bones[n].matrix_local) for n in spine]
    if len(Rs) != len(spine):
        Rs = [R @ _q(Mw @ arm.data.bones[n].matrix_local) for n in spine]
    keys = [k for k in keys if k in Rr]
    hip_h = (Mw @ arm.data.bones[b["hips"]].head_local).z or 1.0
    P = lambda bn: R @ _q(Mw @ arm.pose.bones[bn].matrix)
    J = lambda bn: RM @ (Mw @ arm.pose.bones[bn].head)
    rnd = lambda v, n=4: [round(x, n) for x in v]
    F = []
    hip0 = None
    for f in range(f0, f1 + 1):
        sc.frame_set(f)
        q = [rnd(P(b[k]) @ Rr[k].inverted()) for k in keys]
        sp = [rnd(P(n) @ Rs[i].inverted()) for i, n in enumerate(spine)]
        arms = []
        for s in "LR":
            sh, el, wr = J(b[f"upperarm.{s}"]), J(b[f"forearm.{s}"]), J(b[f"hand.{s}"])
            arms += rnd((el - sh).normalized(), 3) + rnd((wr - el).normalized(), 3)
        hp = J(b["hips"])
        hip0 = hip0 or hp.copy()
        F.append({"q": q, "sp": sp, "a": arms, "h": rnd((hp - hip0) / hip_h)})
    data = {"src": os.path.basename(path), "fps": sc.render.fps, "keys": keys, "frames": F,
            "note": "Microsoft Rocketbox (MIT) or user clip; converted by shorts3d mocap.py"}
    json.dump(data, open(out, "w"), separators=(",", ":"))
    _cleanup(objs)
    print(f"[s3d] clip {os.path.basename(path)}: {len(F)} frames, {len(keys)} bones -> {out}")


class Clip:
    def __init__(self, path):
        d = json.load(open(path))
        self.name = os.path.splitext(os.path.basename(path))[0]
        self.fps, self.keys, self.F = d["fps"], d["keys"], d["frames"]
        self.n = len(self.F); self.dur = self.n / self.fps

    def sample(self, u):
        """u seconds into the clip (clamped) -> {'q': {key: Quat}, 'sp': [Quat], 'ua'/'fa': {s: Vec}, 'hip': Vec}"""
        x = max(0.0, min(self.n - 1.0, u * self.fps)); i = int(x); a = x - i; j = min(i + 1, self.n - 1)
        A, B = self.F[i], self.F[j]
        sl = lambda p, q: Quaternion(p).slerp(Quaternion(q), a)
        lv = lambda p, q: Vector(p).lerp(Vector(q), a)
        out = {"q": {k: sl(A["q"][n], B["q"][n]) for n, k in enumerate(self.keys)},
               "sp": [sl(p, q) for p, q in zip(A["sp"], B["sp"])], "hip": lv(A["h"], B["h"])}
        out["ua"] = {"L": lv(A["a"][0:3], B["a"][0:3]).normalized(), "R": lv(A["a"][6:9], B["a"][6:9]).normalized()}
        out["fa"] = {"L": lv(A["a"][3:6], B["a"][3:6]).normalized(), "R": lv(A["a"][9:12], B["a"][9:12]).normalized()}
        return out


def blend(S, T, a):
    """Blend two samples (a = weight of T)."""
    if S is None:
        return T
    out = {"q": {k: (S["q"][k].slerp(T["q"][k], a) if k in T["q"] else S["q"][k]) for k in S["q"]},
           "hip": S["hip"].lerp(T["hip"], a)}
    for k, v in T["q"].items():
        out["q"].setdefault(k, v)
    out["sp"] = [p.slerp(q, a) for p, q in zip(S["sp"], T["sp"])] if len(S["sp"]) == len(T["sp"]) else (T["sp"] if a > 0.5 else S["sp"])
    for part in ("ua", "fa"):
        out[part] = {s: S[part][s].lerp(T[part][s], a).normalized() for s in "LR"}
    out["parts"] = set(S.get("parts", ())) | set(T.get("parts", ()))
    return out


def spine_delta(sp, i, n):
    """Delta for the actor's spine bone i of n, re-sampled from the clip's spine (any bone count)."""
    if not sp:
        return Quaternion()
    x = (i + 1) / n * len(sp) - 1
    if x <= 0:
        return Quaternion().slerp(sp[0], (i + 1) / n * len(sp))
    k = min(int(x), len(sp) - 2) if len(sp) > 1 else 0
    if len(sp) == 1:
        return sp[0]
    return sp[k].slerp(sp[k + 1], min(1.0, x - k))


def find(name, clips_dir, log=print):
    """Clip name/alias/path -> Clip (converting the FBX on first use)."""
    nm = ALIAS.get(name, name)
    cands = [nm] if os.path.isabs(nm) else [os.path.join(clips_dir, nm + ".json"), os.path.join(clips_dir, nm + ".max.fbx"),
                                             os.path.join(clips_dir, nm + ".fbx"), os.path.join(clips_dir, nm + ".bvh"), nm]
    for c in cands:
        if c.endswith(".json") and os.path.exists(c):
            return Clip(c)
    for c in cands:
        if os.path.exists(c) and c.lower().endswith((".fbx", ".bvh")):
            js = os.path.join(clips_dir, os.path.basename(c).split(".")[0] + ".json")
            ref = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "library", "clips", "biped_rest.json")
            convert(c, js, ref if os.path.basename(c).startswith(("m_", "f_")) else None)
            return Clip(js)
    log(f"clip '{name}' not found in {clips_dir} (run: s3d.py setup --clips)")
    return None


if __name__ == "__main__":
    a = sys.argv[sys.argv.index("--") + 1:]
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from engine import mocap
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    if a[0] == "rest":
        mocap.rest_pose(a[1], a[2])
    else:
        mocap.convert(a[1], a[2], a[3] if len(a) > 3 else None)
