"""Character adapter: discovers bones/face keys on any humanoid rig, adds IK + look controls, calibrates hands."""
import bpy, math
from mathutils import Vector, Quaternion, Matrix
from . import core

SIDES = {"L": ("Left", "l", "L", "_l", ".L", "_L_", "l"), "R": ("Right", "r", "R", "_r", ".R", "_R_", "r")}
CANDS = {
    "hips": ["mixamorig:Hips", "Hips", "pelvis", "hips", "CC_Base_Hip", "hip", "DEF-spine", "root"],
    "head": ["mixamorig:Head", "Head", "head", "CC_Base_Head", "DEF-spine.006"],
    "neck": ["mixamorig:Neck", "Neck", "neck_01", "neck01", "CC_Base_NeckTwist01", "neckLower", "DEF-spine.004"],
}
LIMB = {  # canonical -> patterns ({S}=Left/Right, {s}=l/r, {X}=L/R)
    "shoulder": ["mixamorig:{S}Shoulder", "{S}Shoulder", "clavicle_{s}", "clavicle.{X}", "CC_Base_{X}_Clavicle", "{s}Collar", "DEF-shoulder.{X}"],
    "upperarm": ["mixamorig:{S}Arm", "{S}Arm", "upperarm_{s}", "upperarm01.{X}", "CC_Base_{X}_Upperarm", "{s}ShldrBend", "DEF-upper_arm.{X}"],
    "forearm": ["mixamorig:{S}ForeArm", "{S}ForeArm", "lowerarm_{s}", "lowerarm01.{X}", "CC_Base_{X}_Forearm", "{s}ForearmBend", "DEF-forearm.{X}"],
    "hand": ["mixamorig:{S}Hand", "{S}Hand", "hand_{s}", "wrist.{X}", "CC_Base_{X}_Hand", "{s}Hand", "DEF-hand.{X}"],
    "thigh": ["mixamorig:{S}UpLeg", "{S}UpLeg", "thigh_{s}", "upperleg01.{X}", "CC_Base_{X}_Thigh", "{s}ThighBend", "DEF-thigh.{X}"],
    "shin": ["mixamorig:{S}Leg", "{S}Leg", "calf_{s}", "lowerleg01.{X}", "CC_Base_{X}_Calf", "{s}Shin", "DEF-shin.{X}"],
    "foot": ["mixamorig:{S}Foot", "{S}Foot", "foot_{s}", "foot.{X}", "CC_Base_{X}_Foot", "{s}Foot", "DEF-foot.{X}"],
    "toe": ["mixamorig:{S}ToeBase", "{S}ToeBase", "ball_{s}", "toe1-1.{X}", "CC_Base_{X}_ToeBase", "{s}Toe", "DEF-toe.{X}"],
    "eye": ["eye.{X}", "mixamorig:{S}Eye", "{S}Eye", "eye_{s}", "CC_Base_{X}_Eye", "{s}Eye"],
}
FINGERS = ["thumb", "index", "middle", "ring", "pinky"]
FPAT = {  # finger f (name variants), segment n
    "thumb": ["mixamorig:{S}HandThumb{n}", "{S}HandThumb{n}", "thumb_0{n}_{s}", "finger1-{n}.{X}", "CC_Base_{X}_Thumb{n}", "{s}Thumb{n}", "DEF-thumb.0{n}.{X}"],
    "index": ["mixamorig:{S}HandIndex{n}", "{S}HandIndex{n}", "index_0{n}_{s}", "finger2-{n}.{X}", "CC_Base_{X}_Index{n}", "{s}Index{n}", "DEF-f_index.0{n}.{X}"],
    "middle": ["mixamorig:{S}HandMiddle{n}", "{S}HandMiddle{n}", "middle_0{n}_{s}", "finger3-{n}.{X}", "CC_Base_{X}_Mid{n}", "{s}Mid{n}", "DEF-f_middle.0{n}.{X}"],
    "ring": ["mixamorig:{S}HandRing{n}", "{S}HandRing{n}", "ring_0{n}_{s}", "finger4-{n}.{X}", "CC_Base_{X}_Ring{n}", "{s}Ring{n}", "DEF-f_ring.0{n}.{X}"],
    "pinky": ["mixamorig:{S}HandPinky{n}", "{S}HandPinky{n}", "pinky_0{n}_{s}", "finger5-{n}.{X}", "CC_Base_{X}_Pinky{n}", "{s}Pinky{n}", "DEF-f_pinky.0{n}.{X}"],
}
ARKIT = ["browDownLeft", "browDownRight", "browInnerUp", "browOuterUpLeft", "browOuterUpRight", "cheekPuff",
         "cheekSquintLeft", "cheekSquintRight", "eyeBlinkLeft", "eyeBlinkRight", "eyeLookDownLeft", "eyeLookDownRight",
         "eyeLookInLeft", "eyeLookInRight", "eyeLookOutLeft", "eyeLookOutRight", "eyeLookUpLeft", "eyeLookUpRight",
         "eyeSquintLeft", "eyeSquintRight", "eyeWideLeft", "eyeWideRight", "jawForward", "jawLeft", "jawOpen", "jawRight",
         "mouthClose", "mouthDimpleLeft", "mouthDimpleRight", "mouthFrownLeft", "mouthFrownRight", "mouthFunnel",
         "mouthLeft", "mouthLowerDownLeft", "mouthLowerDownRight", "mouthPressLeft", "mouthPressRight", "mouthPucker",
         "mouthRight", "mouthRollLower", "mouthRollUpper", "mouthShrugLower", "mouthShrugUpper", "mouthSmileLeft",
         "mouthSmileRight", "mouthStretchLeft", "mouthStretchRight", "mouthUpperUpLeft", "mouthUpperUpRight",
         "noseSneerLeft", "noseSneerRight", "tongueOut"]
VISEMES = ["sil", "PP", "FF", "TH", "DD", "kk", "CH", "SS", "nn", "RR", "aa", "E", "I", "O", "U"]


def _fmt(p, side, n=""):
    S, s, X = ("Left", "l", "L") if side == "L" else ("Right", "r", "R")
    return p.format(S=S, s=s, X=X, n=n)


def _basis(local_a, world_a, local_b, world_b):
    """Rotation mapping local frame (a, b) onto world frame (a, b)."""
    def frame(a, b):
        a = Vector(a).normalized(); b = (Vector(b) - Vector(b).project(a)).normalized(); c = a.cross(b)
        return Matrix((a, b, c)).transposed()
    return (frame(world_a, world_b) @ frame(local_a, local_b).inverted()).to_quaternion()


def signed_angle(a, b, n):
    ang = a.angle(b, 0.0)
    return -ang if a.cross(b).dot(n) < 0 else ang


class Character:
    def __init__(self, name, rig, coll=None):
        self.name, self.rig = name, rig
        self.coll = coll
        bones = rig.data.bones
        self.b = {}
        for k, c in CANDS.items():
            self.b[k] = next((n for n in c if n in bones), None)
        for side in "LR":
            for k, pats in LIMB.items():
                self.b[f"{k}.{side}"] = next((_fmt(p, side) for p in pats if _fmt(p, side) in bones), None)
            for f in FINGERS:
                for n in (1, 2, 3):
                    self.b[f"{f}{n}.{side}"] = next((_fmt(p, side, n) for p in FPAT[f] if _fmt(p, side, n) in bones), None)
        missing = [k for k in ("hips", "head", "upperarm.L", "forearm.L", "hand.L", "thigh.L", "shin.L", "foot.L") if not self.b.get(k)]
        if missing:
            raise RuntimeError(f"rig {rig.name}: cannot map bones {missing}")
        # spine chain = bones between hips and neck/head
        chain, cur = [], bones[self.b["neck"] or self.b["head"]].parent
        while cur and cur.name != self.b["hips"]:
            chain.append(cur.name); cur = cur.parent
        self.spine = list(reversed(chain))
        self.meshes = [o for o in bpy.data.objects if o.type == "MESH" and (o.parent == rig or any(
            m.type == "ARMATURE" and m.object == rig for m in o.modifiers))]
        self._face_keys()
        self._calibrate()

    # ---------------------------------------------------------- discovery
    def _face_keys(self):
        """arkit name -> list of (Key datablock, key name) to animate."""
        self.face = {}
        for o in self.meshes:
            sk = o.data.shape_keys
            if not sk:
                continue
            for kb in sk.key_blocks:
                n = kb.name
                base = n[4:] if n.startswith("!ex-") else n
                base = base[7:] if base.startswith("viseme_") and base[7:] in VISEMES else base
                key = ("v:" + base) if n.startswith("viseme_") else base
                if base in ARKIT or n.startswith("viseme_"):
                    if any(k[0] == sk and k[1] == n for k in self.face.get(key, [])):
                        continue
                    if self._driven(sk, n):
                        continue
                    self.face.setdefault(key, []).append((sk, n))
        self.has_face = "jawOpen" in self.face

    @staticmethod
    def _driven(sk, kname):
        ad = sk.animation_data
        return bool(ad and any(d.data_path == f'key_blocks["{kname}"].value' for d in ad.drivers))

    def bone(self, k):
        return self.rig.data.bones.get(self.b.get(k) or "")

    def arm_head(self, k):  # rest head in armature space
        return self.bone(k).head_local.copy()

    def _calibrate(self):
        bones = self.rig.data.bones
        self.height = self.rig.get("s3d_height") or max(b.tail_local.z for b in bones)
        self.fwd = Vector((0, -1, 0))  # rest facing (MakeHuman/Mixamo convention); verified below
        eL, eR = self.bone("eye.L"), self.bone("eye.R")
        hd = self.bone("head")
        self.eye_mid = ((eL.head_local + eR.head_local) / 2) if (eL and eR) else hd.head_local + Vector((0, -0.08, 0.09))
        if self.rig.get("s3d_mouth"):
            self.mouth = Vector(self.rig["s3d_mouth"])
        else:
            self.mouth = Vector((self.eye_mid.x, self.eye_mid.y - 0.025, self.eye_mid.z - 0.085))
        self.mouth_rel_head = self.mouth - hd.head_local
        # true lip centre from the face itself: vertices that move most when the lips pucker
        self.lip_front = 0.012
        for o in self.meshes:
            sk = o.data.shape_keys
            kn = next((k for k in ("!ex-mouthPucker", "mouthPucker", "!ex-mouthFunnel", "mouthFunnel") if sk and k in sk.key_blocks), None)
            if kn:
                basis, key = sk.reference_key, sk.key_blocks[kn]
                Mi = self.rig.matrix_world.inverted() @ o.matrix_world
                disp = sorted(((key.data[i].co - basis.data[i].co).length, i) for i in range(len(basis.data)))[-60:]
                if disp and disp[-1][0] > 1e-4:
                    c = sum((Mi @ basis.data[i].co for _, i in disp), Vector()) / len(disp)
                    self.mouth = c - self.fwd * 0.006      # just behind the lip surface
                    self.mouth_rel_head = self.mouth - hd.head_local
                    self.lip_front = 0.006
                    self.lips_from_mesh = True
                break
        for o in ([] if getattr(self, "lips_from_mesh", False) else self.meshes):
            if o.get("s3d_part", "body") == "body" and len(o.data.vertices) > 5000:
                Mi = self.rig.matrix_world.inverted() @ o.matrix_world
                best = None
                for v in o.data.vertices:
                    p = Mi @ v.co
                    if (p - self.mouth).length < 0.035:
                        d = (p - self.mouth).dot(self.fwd)
                        best = d if best is None else max(best, d)
                if best is not None:
                    self.lip_front = best
                break
        ua, fa, hn = self.bone("upperarm.L"), self.bone("forearm.L"), self.bone("hand.L")
        self.arm_len = (ua.head_local - fa.head_local).length + (fa.head_local - hn.head_local).length
        self.leg_len = (self.bone("thigh.L").head_local - self.bone("foot.L").head_local).length
        self.hips_h = self.bone("hips").head_local.z
        self.hand = {}
        for side in "LR":
            h = self.bone(f"hand.{side}")
            info = {"len": h.length}
            tips, bases, curl = [], [], Vector()
            for f in ("index", "middle", "ring", "pinky"):
                b1, b3 = self.bone(f"{f}1.{side}"), self.bone(f"{f}3.{side}")
                if b1 and b3:
                    v1 = (b1.tail_local - b1.head_local).normalized(); v3 = (b3.tail_local - b3.head_local).normalized()
                    curl += v3 - v1; tips.append(b3.tail_local); bases.append(b1.head_local)
            hy = (h.tail_local - h.head_local).normalized()
            if bases:
                fingers_dir = ((sum(tips, Vector()) / len(tips)) - h.head_local).normalized()
            else:
                fingers_dir = hy
            palm = curl - curl.project(fingers_dir)
            if palm.length < 0.05:  # straight fingers: assume palm faces down/inward in rest
                inward = Vector((-1 if side == "L" else 1, 0, 0))
                palm = Vector((0, 0, -1)) + inward * 0.5
                palm -= palm.project(fingers_dir)
            palm.normalize()
            B = h.matrix_local.to_3x3()
            info["palm_l"] = B.inverted() @ palm           # palm normal in hand-bone local space
            info["fing_l"] = (B.inverted() @ fingers_dir).normalized()
            mid1 = self.bone(f"middle1.{side}")
            pc = (h.head_local + (mid1.head_local if mid1 else h.tail_local)) / 2
            info["palm_center_l"] = B.inverted() @ (pc - h.head_local)  # offset from wrist, local
            info["palm_thick"] = 0.018
            # flexion axis per finger segment: rotate about local X or Z, sign toward palm
            flex = {}
            for f in FINGERS:
                for n in (1, 2, 3):
                    b = self.bone(f"{f}{n}.{side}")
                    if not b:
                        continue
                    M = b.matrix_local.to_3x3(); vec = b.tail_local - b.head_local
                    target = palm if f != "thumb" else (palm * 0.6 + (fingers_dir if bases else hy) * 0.15 +
                                                       ((bases[0] if bases else h.tail_local) - b.head_local).normalized() * 0.4)
                    best = None
                    for ai, ax in ((0, "X"), (2, "Z")):
                        axis = M.col[ai].to_3d() if hasattr(M.col[ai], "to_3d") else Vector(M.col[ai])
                        for sg in (1, -1):
                            d = (axis * sg).cross(vec)
                            score = d.normalized().dot(target.normalized()) if d.length > 1e-9 else -1
                            if best is None or score > best[0]:
                                best = (score, ai, sg)
                    flex[f"{f}{n}"] = (best[1], best[2])
            info["flex"] = flex
            self.hand[side] = info
        # forward axis of head (for look constraint)
        Hm = hd.matrix_local.to_3x3()
        axes = [("TRACK_X", Hm.col[0]), ("TRACK_NEGATIVE_X", -Hm.col[0]), ("TRACK_Y", Hm.col[1]),
                ("TRACK_NEGATIVE_Y", -Hm.col[1]), ("TRACK_Z", Hm.col[2]), ("TRACK_NEGATIVE_Z", -Hm.col[2])]
        self.head_track = max(axes, key=lambda a: Vector(a[1]).dot(self.fwd))[0]
        if eL:
            Em = eL.matrix_local.to_3x3()
            ea = [("TRACK_Y", Em.col[1]), ("TRACK_NEGATIVE_Y", -Em.col[1]), ("TRACK_Z", Em.col[2]),
                  ("TRACK_NEGATIVE_Z", -Em.col[2]), ("TRACK_X", Em.col[0]), ("TRACK_NEGATIVE_X", -Em.col[0])]
            self.eye_track = max(ea, key=lambda a: Vector(a[1]).dot(self.fwd))[0]

    # ---------------------------------------------------------- controls
    def setup_controls(self):
        c = core.ensure_coll(f"CTRL_{self.name}")
        rig, pb = self.rig, self.rig.pose.bones
        for p in pb:
            p.rotation_mode = "QUATERNION"
        rig.rotation_mode = "XYZ"
        self.ctrl = {}
        for side in "LR":
            fa, hn, ua = self.bone(f"forearm.{side}"), self.bone(f"hand.{side}"), self.bone(f"upperarm.{side}")
            t = core.empty(f"{self.name}_ik_hand.{side}", rig.matrix_world @ hn.head_local, c, 0.04)
            t.rotation_mode = "QUATERNION"
            t.rotation_quaternion = (rig.matrix_world.to_quaternion() @ hn.matrix_local.to_quaternion())
            pole_loc = (ua.tail_local + Vector((0.0, 0.35, -0.05)))  # elbows point back
            p = core.empty(f"{self.name}_pole_arm.{side}", rig.matrix_world @ pole_loc, c, 0.03)
            ik = pb[fa.name].constraints.new("IK"); ik.name = "s3d_ik"
            ik.target, ik.pole_target, ik.chain_count = t, p, 2
            ik.pole_angle = self._pole_angle(ua, fa, pole_loc)
            cr = pb[hn.name].constraints.new("COPY_ROTATION"); cr.name = "s3d_rot"; cr.target = t
            self.ctrl[f"hand.{side}"], self.ctrl[f"pole_arm.{side}"] = t, p
            th, sh, ft = self.bone(f"thigh.{side}"), self.bone(f"shin.{side}"), self.bone(f"foot.{side}")
            ftg = core.empty(f"{self.name}_ik_foot.{side}", rig.matrix_world @ ft.head_local, c, 0.04)
            ftg.rotation_mode = "QUATERNION"
            ftg.rotation_quaternion = rig.matrix_world.to_quaternion() @ ft.matrix_local.to_quaternion()
            kpole = sh.head_local + Vector((0, -0.5, 0))
            kp = core.empty(f"{self.name}_pole_leg.{side}", rig.matrix_world @ kpole, c, 0.03)
            ik2 = pb[sh.name].constraints.new("IK"); ik2.name = "s3d_ik"
            ik2.target, ik2.pole_target, ik2.chain_count = ftg, kp, 2
            ik2.pole_angle = self._pole_angle(th, sh, kpole)
            cr2 = pb[ft.name].constraints.new("COPY_ROTATION"); cr2.name = "s3d_rot"; cr2.target = ftg
            self.ctrl[f"foot.{side}"], self.ctrl[f"pole_leg.{side}"] = ftg, kp
        look = core.empty(f"{self.name}_look", rig.matrix_world @ (self.eye_mid + Vector((0, -2.0, 0))), c, 0.06, "SPHERE")
        gaze = core.empty(f"{self.name}_gaze", rig.matrix_world @ (self.eye_mid + Vector((0, -2.0, 0))), c, 0.03, "SPHERE")
        self.ctrl["look"], self.ctrl["gaze"] = look, gaze
        for k, infl in (("neck", 0.25), ("head", 0.55)):
            if self.b.get(k):
                d = pb[self.b[k]].constraints.new("DAMPED_TRACK"); d.name = "s3d_look"; d.target = look
                d.track_axis = self.head_track; d.influence = infl
        for side in "LR":
            e = self.bone(f"eye.{side}")
            if e:
                d = pb[e.name].constraints.new("DAMPED_TRACK"); d.name = "s3d_look"; d.target = gaze
                d.track_axis = self.eye_track
        return self.ctrl

    @staticmethod
    def _pole_angle(base, tip, pole_loc):
        a, b = base.head_local, tip.tail_local
        pn = (b - a).cross(pole_loc - a)
        proj = pn.cross(base.tail_local - base.head_local)
        return signed_angle(base.x_axis, proj, base.tail_local - base.head_local)

    # ---------------------------------------------------------- hand helpers
    def hand_world_rot(self, side, palm_world, fingers_world):
        """Hand-bone world rotation that puts the palm normal / finger direction onto given world vectors."""
        h = self.hand[side]
        return _basis(h["fing_l"], fingers_world, h["palm_l"], palm_world)

    def wrist_for_palm(self, side, palm_point, rot):
        """Wrist (IK target) location so that the palm surface center lands on palm_point."""
        h = self.hand[side]
        return Vector(palm_point) - rot @ h["palm_center_l"] - (rot @ h["palm_l"]) * h["palm_thick"]

    def finger_quats(self, side, curls, spread=0.0):
        """curls: dict finger->0..1 (1 = fist). Returns {bone_name: Quaternion} local rotations."""
        out = {}
        maxdeg = {1: 70, 2: 95, 3: 70}
        tmax = {1: 25, 2: 45, 3: 60}
        for f in FINGERS:
            c = curls.get(f, 0.0)
            for n in (1, 2, 3):
                bn = self.b.get(f"{f}{n}.{side}")
                if not bn:
                    continue
                ai, sg = self.hand[side]["flex"][f"{f}{n}"]
                ang = math.radians((tmax if f == "thumb" else maxdeg)[n]) * c * sg
                axis = Vector((1, 0, 0)) if ai == 0 else Vector((0, 0, 1))
                q = Quaternion(axis, ang)
                if n == 1 and f != "thumb" and spread:
                    other = Vector((0, 0, 1)) if ai == 0 else Vector((1, 0, 0))
                    k = {"index": -1, "middle": -0.3, "ring": 0.4, "pinky": 1}[f]
                    q = Quaternion(other, math.radians(8) * spread * k) @ q
                out[bn] = q
        return out

    def fingertip_points(self, side):
        """Evaluated world points along each finger (for contact tests)."""
        pts = {}
        pb = self.rig.pose.bones; M = self.rig.matrix_world
        for f in FINGERS:
            seg = []
            for n in (1, 2, 3):
                bn = self.b.get(f"{f}{n}.{side}")
                if bn:
                    p = pb[bn]; seg += [M @ p.head, M @ ((p.head + p.tail) / 2), M @ p.tail]
            pts[f] = seg
        return pts
