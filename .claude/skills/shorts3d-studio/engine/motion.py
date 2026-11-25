"""Performance engine: verbs -> time tracks -> baked keys, with contact-accurate grasping, eating and walking.

Pass A  bake body: root, hips, spine, feet (planted steps), hand IK targets, look/gaze, face.
Pass B  contacts: solve finger curls until each finger touches the prop surface (no interpenetration);
        measure real mouth position and correct hand targets so food meets the lips exactly.
Pass C  attachments: props follow the actual hand bone between grasp and release; bite marks appear on contact.
"""
import bpy, math
from mathutils import Vector, Quaternion, Matrix
from . import core, acting, props as P, mocap
from .core import Track, log, minjerk, smooth, clamp

FINGERS = ("thumb", "index", "middle", "ring", "pinky")
RELAX = {"thumb": .15, "index": .2, "middle": .27, "ring": .33, "pinky": .4}
FIST = {"thumb": .6, "index": .95, "middle": 1.0, "ring": 1.0, "pinky": 1.0}
OPEN = {"thumb": .02, "index": .02, "middle": .03, "ring": .04, "pinky": .05}
FRAD = {"thumb": .0095, "index": .0085, "middle": .0088, "ring": .0082, "pinky": .0072}


def Rz(a):
    return Quaternion((0, 0, 1), a)


HIP_SHARE = 0.35  # share of a forward lean taken by the hip joints (pelvis tilts) - spine alone looks hunched
HINGE = 0.45      # crouching pushes the pelvis back 0.45 m per metre of drop (hip hinge keeps balance)
TURNOUT = math.radians(7)  # natural stance: toes turned out
NOGAIN = ("jaw", "eyeBlink", "eyeLook", "v:", "mouthClose", "tongue")


class Performer:
    def __init__(self, ch, start, yaw, fps, seed=1, scene_props=None):
        self.ch, self.fps = ch, fps
        self.props = scene_props or {}
        start = Vector((start[0], start[1], 0.0))
        self.root_pos = Track(start).key(0, start, "hold")
        self.root_yaw = Track(yaw).key(0, yaw, "hold")
        self.cursor, self.prev_start = 0.0, 0.0
        self.steps = []
        side_off = {s: self._foot_rest(s) for s in "LR"}
        self.plant = {s: (self._to_world(side_off[s], start, yaw), yaw) for s in "LR"}
        self.first_plant = dict(self.plant)
        self.walk_iv = []
        for nm in ("lean", "crouch", "nod", "tilt", "twist", "shrug", "headyaw", "back"):
            setattr(self, nm, Track(0.0))
        self.hs = {s: Track(0.0) for s in "LR"}
        self.hw = {s: Track(Vector()) for s in "LR"}
        self.hq = {s: Track(Quaternion()) for s in "LR"}
        self.ha = {s: Track(Vector((0, 0, 0))) for s in "LR"}
        self.hb = {s: Track(Vector((0, 0, 0))) for s in "LR"}
        self.hbq = {s: Track(Quaternion()) for s in "LR"}
        self.hbw = {s: Track(0.0) for s in "LR"}
        self.hcorr = {s: Track(Vector((0, 0, 0))) for s in "LR"}
        self.pole_out = {s: Track(0.0) for s in "LR"}  # 1 = elbow flares outward (hands on hips, crossed arms)
        self.curl = {s: {f: Track(v) for f, v in RELAX.items()} for s in "LR"}
        self.spread = {s: Track(0.0) for s in "LR"}
        self.topp = {s: Track(0.0) for s in "LR"}   # thumb opposition (0 rest .. 1 facing the finger pads)
        self.tabd = {s: Track(0.0) for s in "LR"}   # thumb radial abduction (0 rest .. 1 spread wide)
        self.exact = {s: Track(0.0) for s in "LR"}  # 1 = contact moment: hand rotation must stay exact
        self.qa_notes = []
        eye = self._to_world(ch.eye_mid, start, yaw)
        ahead = eye + Rz(yaw) @ Vector((0, -2.5, 0))
        self.look = Track(ahead).key(0, ahead, "hold")
        self.gaze = Track(ahead).key(0, ahead, "hold")
        self.face = acting.FaceTracks(seed)
        self.events = []
        self.holding = {"L": None, "R": None}
        self.noise = core.Noise1D(seed)
        self.clip_layers, self._cc, self.clips_dir = [], {}, None
        self.L_ua = {s: (ch.arm_head(f"forearm.{s}") - ch.arm_head(f"upperarm.{s}")).length for s in "LR"}
        self.L_fa = {s: (ch.arm_head(f"hand.{s}") - ch.arm_head(f"forearm.{s}")).length for s in "LR"}

    def holding_at(self, prop, f):
        """Side ('L'/'R') holding prop at frame f, else None."""
        t = f / self.fps
        for ev in self.events:
            if ev["type"] == "attach" and ev["prop"] == prop and ev["t"] <= t:
                det = next((e for e in self.events if e["type"] == "detach" and e["prop"] == prop and e["t"] > ev["t"]), None)
                if det is None or t <= det["t"]:
                    return ev["side"]
        return None

    # ------------------------------------------------------------ frames
    def M(self, t):
        return Matrix.Translation(self.root_pos.at(t)) @ Rz(self.root_yaw.at(t)).to_matrix().to_4x4()

    def _to_world(self, p_arm, pos, yaw):
        return Vector(pos) + Rz(yaw) @ Vector(p_arm)

    def _foot_rest(self, s):
        return self.ch.arm_head(f"foot.{s}")

    def fwd(self, t):
        return Rz(self.root_yaw.at(t)) @ Vector((0, -1, 0))

    def spine_rot(self, t, frac=1.0):
        return Quaternion((1, 0, 0), self.lean.at(t) * frac) @ Quaternion((0, 0, 1), self.twist.at(t) * frac)

    def chain_deltas(self, lean, twist=0.0, back=0.0, nod=0.0, tilt=0.0, headyaw=0.0, shrug=0.0, pyaw=0.0, breath=0.0, clip=None):
        """Armature-space total rotation of every torso bone - the SAME model the bake writes."""
        ch = self.ch; D = {}
        hips = ch.b["hips"]
        hip = HIP_SHARE * lean  # forward bends: ~35 % at the hip joints, the rest spread up the spine
        D[hips] = Quaternion((0, 0, 1), pyaw) @ Quaternion((1, 0, 0), back * 0.3 + hip)
        nsp = max(1, len(ch.spine)); top = D[hips]
        tw = twist - pyaw
        for i, bn in enumerate(ch.spine):
            fr = (i + 1) / nsp
            D[bn] = Quaternion((1, 0, 0), hip + (lean - hip) * fr + back * fr + breath * 2 * (i == nsp - 1)) @ Quaternion((0, 0, 1), tw * fr)
            top = D[bn]
        for bn, k in ((ch.b.get("neck"), 0.4), (ch.b.get("head"), 1.0)):
            if bn:
                D[bn] = Quaternion((1, 0, 0), lean + back + nod * k) @ Quaternion((0, 0, 1), tw + headyaw * k) @ Quaternion((0, 1, 0), tilt * k)
        for s_ in "LR":
            cb = ch.b.get(f"shoulder.{s_}")
            if cb:
                g = 1 if s_ == "L" else -1
                D[cb] = Quaternion((0, 1, 0), -g * shrug) @ top
        if clip and clip[0] > 0 and "torso" in clip[1]["parts"]:
            W, C = clip; I = Quaternion()
            lay = lambda d, q: I.slerp(q, W) @ d
            if "hips" in C["q"]:
                D[hips] = lay(D[hips], C["q"]["hips"])
            for i, bn in enumerate(ch.spine):
                D[bn] = lay(D[bn], mocap.spine_delta(C["sp"], i, nsp))
            for k in ("neck", "head"):
                if ch.b.get(k) and k in C["q"]:
                    D[ch.b[k]] = lay(D[ch.b[k]], C["q"][k])
            for s_ in "LR":
                cb = ch.b.get(f"shoulder.{s_}")
                if cb and f"shoulder.{s_}" in C["q"]:
                    D[cb] = lay(D[cb], C["q"][f"shoulder.{s_}"])
        return D

    def posed_point(self, D, bone_name, p_arm, crouch=0.0):
        """Forward kinematics of a point attached to bone_name through the torso chain (armature space)."""
        bones = self.ch.rig.data.bones
        chain = []
        b = bones[bone_name]
        while b is not None:
            chain.append(b); b = b.parent
        chain.reverse()
        hp = chain[0].head_local + Vector((0, HINGE * crouch, -crouch))
        for i in range(1, len(chain)):
            par = chain[i - 1]
            hp = hp + D.get(par.name, Quaternion()) @ (chain[i].head_local - par.head_local)
        last = chain[-1]
        return hp + D.get(last.name, Quaternion()) @ (Vector(p_arm) - last.head_local)

    def deltas_at(self, t):
        return self.chain_deltas(self.lean.at(t), self.twist.at(t), self.back.at(t), self.nod.at(t), self.tilt.at(t),
                                 self.headyaw.at(t), self.shrug.at(t), clip=self.clip_at(t))

    def head_point(self, t, p_arm):
        """World position of a head-attached point (exact torso FK; look-at constraint corrected in pass B)."""
        return self.M(t) @ self.posed_point(self.deltas_at(t), self.ch.b["head"], p_arm, self.crouch.at(t))

    def mouth_world(self, t):
        return self.head_point(t, self.ch.mouth)

    def shoulder_world(self, t, s):
        cb = self.ch.b.get(f"shoulder.{s}") or self.ch.spine[-1]
        return self.M(t) @ self.posed_point(self.deltas_at(t), cb, self.ch.arm_head(f"upperarm.{s}"), self.crouch.at(t))

    # ------------------------------------------------------------ hand poses
    def rest_hand(self, s, t):
        ch = self.ch; sg = 1 if s == "L" else -1
        sh = ch.arm_head(f"upperarm.{s}")
        w = Vector((sh.x + sg * 0.07, sh.y - 0.02, sh.z - ch.arm_len * 0.95))
        D = self.deltas_at(t)
        cb = ch.b.get(f"shoulder.{s}") or ch.spine[-1]
        shp = self.posed_point(D, cb, sh, self.crouch.at(t))
        w = shp + (w - sh)  # arms hang under the (possibly leaning) shoulder
        q = ch.hand_world_rot(s, Vector((-sg, 0.25, 0.05)), Vector((0, -0.12, -1)))
        return w, q

    def hand_target(self, s, t):
        """World wrist location + world hand rotation for frame time t."""
        w_rest, q_rest = self.rest_hand(s, t)
        bw = self.hbw[s].at(t)
        wa = self._clip_arm_w(s, t)
        if wa > 0:
            wc, qc, _ = self._clip_arm(s, t)
            w_rest, q_rest = w_rest.lerp(wc, wa), q_rest.slerp(qc, wa)
        w_rest = w_rest + self.hb[s].at(t) + self._swing(s, t)
        if bw > 0:
            q_rest = q_rest.slerp(self.hbq[s].at(t), bw)
        M = self.M(t); Mq = M.to_quaternion()
        wr, qr = M @ w_rest, Mq @ q_rest
        k = self.hs[s].at(t)
        if k <= 0:
            w, q = wr + self.ha[s].at(t), qr
        else:
            w = wr.lerp(self.hw[s].at(t) + self.hcorr[s].at(t), k) + self.ha[s].at(t)
            q = qr.slerp(self.hq[s].at(t), k)
        lim_w = max(1.0 - k, 1.0 - self.exact[s].at(t))  # holds obey anatomy too; contact moments stay exact
        if lim_w > 0:
            q = q.slerp(self._wrist_limit(s, t, w, q), lim_w)
        return w, q

    def _exact(self, s, t0, t1, ramp=0.12):
        """Mark a contact moment (grasp closing, placing, bite, sip): the hand keeps its planned rotation."""
        tr = self.exact[s]
        tr.key(t0 - ramp, tr.at(t0 - ramp), "hold").key(t0, 1.0, "smooth").key(t1, 1.0, "hold").key(t1 + ramp, 0.0, "smooth")

    WRIST = {"flex": 70, "ext": 50, "dev": 22, "twist": 85}  # degrees from the forearm (comfortable range, not the extreme)

    def _wrist_limit(self, s, t, w, q):
        """Clamp the hand to the anatomical range of the wrist (relative to the forearm the IK will produce):
        flexion 75 / extension 60 / deviation 25 deg, pronation-supination 95 deg from 'handshake' neutral."""
        h = self.ch.hand[s]; L1, L2 = self.L_ua[s], self.L_fa[s]
        S = self.shoulder_world(t, s); P_ = self._elbow_pole(s, t, w, q)
        d = w - S; dist = min(max(d.length, 1e-4), (L1 + L2) * 0.999); dn = d.normalized()
        a_ = (L1 * L1 - L2 * L2 + dist * dist) / (2 * dist); hh = math.sqrt(max(0.0, L1 * L1 - a_ * a_))
        pv = P_ - S; pv -= pv.project(dn)
        if pv.length < 1e-6:
            return q
        E = S + dn * a_ + pv.normalized() * hh
        f = (w - E).normalized()
        fing = (q @ h["fing_l"]).normalized(); palm = (q @ h["palm_l"]).normalized()
        pal = palm - palm.project(f)
        if pal.length < 1e-6:
            return q
        pal.normalize(); lat = f.cross(pal)
        x, y, z = fing.dot(f), fing.dot(pal), fing.dot(lat)
        flex = math.atan2(y, x); dev = math.atan2(z, math.hypot(x, y))
        L = self.WRIST
        fc = clamp(flex, -math.radians(L["ext"]), math.radians(L["flex"])); dc = clamp(dev, -math.radians(L["dev"]), math.radians(L["dev"]))
        if abs(fc - flex) > 1e-4 or abs(dc - dev) > 1e-4:
            nf = f * (math.cos(dc) * math.cos(fc)) + pal * (math.cos(dc) * math.sin(fc)) + lat * math.sin(dc)
            q = fing.rotation_difference(nf.normalized()) @ q
        nb = (E - S).cross(w - E)
        if nb.length > 1e-4:  # twist about the forearm, around the handshake-neutral palm direction
            ref = nb.normalized() * (1 if s == "L" else -1)
            palm = (q @ h["palm_l"]); pal = palm - palm.project(f)
            if pal.length > 1e-6:
                pal.normalize()
                tau = math.atan2(f.dot(ref.cross(pal)), ref.dot(pal))
                lim = math.radians(L["twist"])
                if abs(tau) > lim:
                    q = Quaternion(f, math.copysign(lim, tau) - tau) @ q
        return q

    def _swing(self, s, t):
        for (t0, t1, step_t) in self.walk_iv:
            if t0 <= t <= t1:
                env = smooth((t - t0) / 0.4) * smooth((t1 - t) / 0.4)
                ph = (t - t0) / (2 * step_t) * 2 * math.pi
                sg = 1 if s == "L" else -1
                a = 0.13 * env * math.sin(ph) * sg
                return Vector((0, -a, 0.015 * abs(math.sin(ph)) * env))
        return Vector((0, 0, 0))

    # ------------------------------------------------------------ timeline helpers
    def _start(self, args):
        t = self.cursor
        if args.get("with") == "prev":
            t = self.prev_start
        if "at_s" in args and args["at_s"] is not None:
            t = float(args["at_s"]) if args.get("with") != "prev" else max(t, float(args["at_s"]))
        t += float(args.get("delay", 0.0))
        return t

    def do(self, verb, args):
        fn = getattr(self, "v_" + verb, None)
        if fn is None:
            log(f"unknown verb {verb} (skipped)"); return
        t0 = self._start(args)
        t1 = fn(t0, args) or t0
        if args.get("dur") and verb not in ("walk_to", "turn_to", "face", "talk"):
            t1 = max(t1, t0 + float(args["dur"]))
        self.prev_start = t0
        if args.get("with") != "prev":
            self.cursor = max(self.cursor, t1)
        else:
            self.cursor = max(self.cursor, t1)
        return t0, t1

    def _target_point(self, tgt):
        if isinstance(tgt, (list, tuple)):
            return Vector(tgt if len(tgt) == 3 else (tgt[0], tgt[1], 1.0))
        ob = self.props.get(tgt) or bpy.data.objects.get(tgt)
        if ob is None:
            raise RuntimeError(f"unknown target {tgt}")
        mn, mx = core.world_bbox(ob)
        return (mn + mx) / 2

    def _side_for(self, p, t):
        rel = self.M(t).inverted() @ Vector(p)
        return "L" if rel.x > 0 else "R"

    def _look_to(self, t, p, head=True):
        p = Vector(p)
        old = self.gaze.at(t)
        e = self.mouth_world(t)
        if (old - e).length > 1e-3 and (p - e).length > 1e-3 and (old - e).angle(p - e) > math.radians(25):
            self.face.add_blink(t + 0.03)
        self.gaze.key(t, old, "hold").key(t + 0.08, p, "smooth")
        if head:
            ol = self.look.at(t)
            self.look.key(t + 0.02, ol, "hold").key(t + 0.45, p, "mj")

    # ------------------------------------------------------------ locomotion
    # ------------------------------------------------------------ mocap layer
    def v_clip(self, t0, a):
        """Real human motion: {name: talk|idle|shrug|think|agree|... or a clip file, dur, amt, fade, speed, loop,
        from (s into clip), parts: [torso, arms, fingers, hips]}"""
        clip = mocap.find(str(a.get("name", "idle")), self.clips_dir or "", log)
        if clip is None:
            return t0
        speed = float(a.get("speed", 1.0)); loop = a.get("loop", True)
        dur = float(a["dur"]) if a.get("dur") else (clip.dur - float(a.get("from", 0))) / speed
        lay = {"clip": clip, "t0": t0, "t1": t0 + dur, "amt": float(a.get("amt", 1.0)), "fade": float(a.get("fade", 0.4)),
               "speed": speed, "loop": loop, "off": float(a.get("from", 0.0)),
               "parts": set(a.get("parts", ["torso", "arms", "fingers", "hips"]))}
        self.clip_layers.append(lay); self._cc.clear()
        return t0 + dur

    def add_idle(self, name="idle", amt=0.75):
        """Whole-timeline base layer of real idle motion (weight shifts, breathing, small arm drift)."""
        clip = mocap.find(name, self.clips_dir or "", log)
        if clip:
            self.clip_layers.insert(0, {"clip": clip, "t0": -1.0, "t1": 1e9, "amt": amt, "fade": 0.0, "speed": 1.0,
                                        "loop": True, "off": 0.0, "parts": {"torso", "arms", "fingers", "hips"}})
            self._cc.clear()

    def clip_at(self, t):
        """(weight, blended sample) of all clip layers at time t (later layers win)."""
        key = round(t, 4)
        if key in self._cc:
            return self._cc[key]
        W, S = 0.0, None
        for L in self.clip_layers:
            if not (L["t0"] <= t <= L["t1"]):
                continue
            fd = L["fade"]
            w = L["amt"] * (smooth((t - L["t0"]) / fd) * smooth((L["t1"] - t) / fd) if fd > 0 else 1.0)
            if w <= 0:
                continue
            u = (t - L["t0"]) * L["speed"] + L["off"]
            if L["loop"] and L["clip"].dur > 0:
                u = u % L["clip"].dur
            smp = L["clip"].sample(u); smp["parts"] = L["parts"]
            if S is None:
                S, W = smp, w
            else:  # later layers win by their own weight (a gesture fully replaces the idle under it)
                S = mocap.blend(S, smp, w); W = W + (1 - W) * w
        self._cc[key] = (W, S) if S is not None else (0.0, {"parts": set()})
        return self._cc[key]

    def _clip_arm_w(self, s, t, part="arms"):
        W, S = self.clip_at(t)
        if W <= 0 or part not in S["parts"]:
            return 0.0
        return W * (1 - self.hbw[s].at(t))

    def _clip_arm(self, s, t):
        """Clip arm pose on THIS actor (armature space): wrist, hand rotation, (elbow, shoulder)."""
        C = self.clip_at(t)[1]; ch = self.ch
        D = self.deltas_at(t)
        cb = ch.b.get(f"shoulder.{s}") or ch.spine[-1]
        shp = self.posed_point(D, cb, ch.arm_head(f"upperarm.{s}"), self.crouch.at(t))
        el = shp + C["ua"][s] * self.L_ua[s]
        wr = el + C["fa"][s] * self.L_fa[s]
        q = C["q"].get(f"hand.{s}", Quaternion()) @ ch.bone(f"hand.{s}").matrix_local.to_quaternion()
        return wr, q, (el, shp)

    def v_walk_to(self, t0, a):
        tgt = self._target_point(a["target"]) if "target" in a else Vector((*a["pos"], 0))
        start = self.root_pos.at(t0); stop = a.get("stop", "near")
        d = Vector((tgt.x - start.x, tgt.y - start.y, 0))
        dist = {"reach": 0.4, "near": 0.8, "at": 0.0}.get(stop, float(stop) if isinstance(stop, (int, float)) else 0.8)
        if "target" in a and a["target"] in self.props:
            mn, mx = core.world_bbox(self.props[a["target"]])
            if stop == "reach":
                dist = 0.33 + 0.5 * max(0.0, min(mx.x - mn.x, mx.y - mn.y)) * 0.2
        if d.length < 1e-4:
            return t0
        dirn = d.normalized()
        end = Vector((tgt.x, tgt.y, 0)) - dirn * dist
        end, via = self._stand_point(start, tgt, end, stop)
        yaw_t = math.atan2(dirn.x, -dirn.y)  # facing -Y at yaw 0
        pts = [start] + via + [end]
        t = t0
        for i in range(len(pts) - 1):
            seg = pts[i + 1] - pts[i]
            if seg.length < 0.05:
                continue
            t = self._turn(t, math.atan2(seg.x, -seg.y))
            t = self._walk_path(t, [pts[i], pts[i + 1]], float(a.get("speed", 1.0)))
        face_t = a.get("face") or (a.get("target") if stop == "reach" else None)
        if face_t:
            t = self._turn(t, self._yaw_to(self._target_point(face_t), t))
        self._look_to(t0, tgt + Vector((0, 0, 0)), head=True)
        self._carry(t0, t)
        return t

    def _carry(self, t0, t1):
        """Held objects travel with the body (hold targets are world-space) during walks and turns."""
        M0 = self.M(t0); M0i, q0i = M0.inverted(), M0.to_quaternion().inverted()
        for s in "LR":
            if not (self.holding[s] and self.hs[s].at(t0) > 0.5):
                continue
            w0, h0 = M0i @ self.hw[s].at(t0), q0i @ self.hq[s].at(t0)
            n = max(2, int((t1 - t0) / 0.1))
            for i in range(1, n + 1):
                t = t0 + (t1 - t0) * i / n; M = self.M(t)
                self.hw[s].key(t, M @ w0, "lin"); self.hq[s].key(t, M.to_quaternion() @ h0, "lin")

    def _obstacles(self):
        out = []
        for nm, ob in self.props.items():
            if ob.get("s3d_surface") or ob.get("s3d_obstacle"):
                mn, mx = core.world_bbox(ob)
                if mx.z > 0.3:  # furniture-height things block walking
                    out.append((mn, mx))
        return out

    def _stand_point(self, start, tgt, end, stop):
        """Keep the actor outside furniture footprints; stand at the edge facing the target."""
        body_r = 0.2
        via = []
        for mn, mx in self._obstacles():
            x0, x1, y0, y1 = mn.x - body_r, mx.x + body_r, mn.y - body_r, mx.y + body_r
            inside = lambda p: x0 < p.x < x1 and y0 < p.y < y1
            if inside(end) or (stop == "reach" and x0 < tgt.x < x1 and y0 < tgt.y < y1):
                cands = [Vector((x0, clamp(tgt.y, y0, y1), 0)), Vector((x1, clamp(tgt.y, y0, y1), 0)),
                         Vector((clamp(tgt.x, x0, x1), y0, 0)), Vector((clamp(tgt.x, x0, x1), y1, 0))]
                # prefer the side whose edge is closest to the target (shortest reach), then nearest to start
                end = min(cands, key=lambda c: (Vector((c.x - tgt.x, c.y - tgt.y)).length * 2 + (c - start).length * 0.3))
                end = end + (end - Vector((clamp(end.x, mn.x, mx.x), clamp(end.y, mn.y, mx.y), 0))).normalized() * 0.04 if True else end
            # detour around the footprint if the straight path crosses it
            if self._seg_hits(start, end, x0, x1, y0, y1):
                corners = [Vector((x, y, 0)) for x in (x0 - 0.05, x1 + 0.05) for y in (y0 - 0.05, y1 + 0.05)]
                c = min(corners, key=lambda c: (c - start).length + (end - c).length)
                via.append(c)
        return end, via

    @staticmethod
    def _seg_hits(a, b, x0, x1, y0, y1):
        for i in range(1, 20):
            p = a.lerp(b, i / 20)
            if x0 < p.x < x1 and y0 < p.y < y1:
                return True
        return False

    def _yaw_to(self, p, t):
        d = Vector(p) - self.root_pos.at(t)
        return math.atan2(d.x, -d.y)

    def v_turn_to(self, t0, a):
        t = self._turn(t0, self._yaw_to(self._target_point(a["target"]), t0))
        self._carry(t0, t)
        return t

    def _turn(self, t0, yaw_t):
        y0 = self.root_yaw.at(t0)
        dy = (yaw_t - y0 + math.pi) % (2 * math.pi) - math.pi
        if abs(dy) < math.radians(12):
            if abs(dy) > 1e-3:
                self.root_yaw.key(t0, y0, "hold").key(t0 + 0.3, y0 + dy, "smooth")
            return t0 + (0.3 if abs(dy) > 1e-3 else 0)
        n = max(2, math.ceil(abs(dy) / math.radians(40)))
        T = 0.42 * n
        self.root_yaw.key(t0, y0, "hold").key(t0 + T, y0 + dy, "smooth")
        pos = self.root_pos.at(t0)
        order = ("L", "R") if dy > 0 else ("R", "L")
        for i in range(n):
            s = order[i % 2]
            tl, td = t0 + i * T / n, t0 + i * T / n + 0.36
            yaw = y0 + dy * (i + 1) / n if i < n - 1 else y0 + dy
            self._step(s, tl, td, self._to_world(self._foot_rest(s), pos, yaw), yaw)
        last = order[n % 2]
        if True:  # closing step: both feet end square to the new heading
            self._step(last, t0 + T - 0.05, t0 + T + 0.3, self._to_world(self._foot_rest(last), pos, y0 + dy), y0 + dy)
        self.face.no_blink.append((t0, t0))
        return t0 + T + 0.3

    def _step(self, s, tl, td, to, yaw):
        fr, fy = self.plant[s]
        self.steps.append((s, tl, td, fr.copy(), Vector(to), fy, yaw))
        self.plant[s] = (Vector(to), yaw)

    def _walk_path(self, t0, pts, speed=1.0):
        a, b = Vector(pts[0]), Vector(pts[-1])
        D = (b - a).length
        v = 1.25 * speed
        L = min(0.72, 0.4 * self.ch.height) * (0.85 + 0.15 * speed)
        n = max(2, round(D / L) + 1)
        T = D / v + 0.6
        step_t = T / (n + 0.5)
        yaw = self._yaw_to(b, t0) if D > 1e-3 else self.root_yaw.at(t0)
        dirn = (b - a).normalized()

        def s_of(u):  # trapezoid velocity: ease in/out
            return smooth(u) * 0.35 + u * 0.65 if 0 < u < 1 else clamp(u)
        steps_k = 24
        for i in range(steps_k + 1):
            u = i / steps_k
            self.root_pos.key(t0 + u * T, a.lerp(b, s_of(u)), "lin")
        self.root_yaw.key(t0, self.root_yaw.at(t0), "hold").key(t0 + T, yaw, "hold")
        first = "L"
        for i in range(n):
            s = first if i % 2 == 0 else ("R" if first == "L" else "L")
            land = t0 + (i + 1) * step_t
            lift = land - 0.8 * step_t
            u = min(1.0, (land - t0 + 0.25 * step_t) / T)
            centre = a.lerp(b, s_of(u)) if i < n - 1 else b
            self._step(s, max(t0, lift), land, self._to_world(self._foot_rest(s), centre, yaw), yaw)
        last_s = first if (n - 1) % 2 == 0 else ("R" if first == "L" else "L")
        trail = "R" if last_s == "L" else "L"
        self._step(trail, t0 + T - 0.1, t0 + T + 0.3, self._to_world(self._foot_rest(trail), b, yaw), yaw)
        self.walk_iv.append((t0, t0 + T, step_t))
        return t0 + T + 0.3

    # ------------------------------------------------------------ hands
    def _wrist_now(self, s, t):
        return self.hand_target(s, t)[0]

    def v_reach(self, t0, a):
        tgt = a.get("target") or a.get("prop")
        ob = self.props.get(tgt)
        p = self._target_point(tgt)
        s = a.get("hand", "auto")
        s = self._side_for(p, t0) if s in ("auto", None) else s[0].upper()
        sh = self.shoulder_world(t0, s)
        g = P.grip(ob, sh, a.get("grip", "auto"), side=s) if ob else {
            "palm_point": p, "palm_n": (p - sh).normalized(), "fingers": (p - sh).normalized(), "approach": (p - sh).normalized(), "type": "point", "width": 0.05}
        q = self.ch.hand_world_rot(s, g["palm_n"], g["fingers"])
        self._plan_t = t0 + 0.8
        if g["type"] in ("wrap_top", "wrap_side") and ob:
            g, q = self._plan_wrap(s, q, g, ob)
        if g["type"] in ("span", "pinch") and ob:
            wg, q = self._plan_span(s, q, g, ob)
        else:
            wg = self.ch.wrist_for_palm(s, g["palm_point"], q)
        pre = wg - g["approach"] * 0.075 + Vector((0, 0, 0.015))
        w0 = self._wrist_now(s, t0)
        T = clamp(0.45 + 0.45 * (pre - w0).length, 0.55, 1.4)
        self._look_to(max(0.0, t0 - 0.25), g["palm_point"])
        self.hs[s].key(t0, self.hs[s].at(t0), "hold").key(t0 + T, 1.0, "mj")
        self.hw[s].key(t0 + T, pre, "hold"); self.hq[s].key(t0 + T, q, "hold")
        self.ha[s].key(t0, Vector(), "hold").key(t0 + T * 0.5, Vector((0, 0, 0.05)), "smooth").key(t0 + T, Vector(), "smooth")
        ap = min(0.25, 0.05 + 0.6 * g.get("width", 0.05))
        for f, c in OPEN.items():
            self.curl[s][f].key(t0, self.curl[s][f].at(t0), "hold").key(t0 + T * 0.7, c + (0.0 if g["type"].startswith("pinch") and f in ("ring", "pinky") else 0), "smooth")
        self.spread[s].key(t0 + T * 0.7, 0.6, "smooth")
        if g["type"] in ("span", "pinch"):  # thumb swings out and starts to oppose while the hand travels
            self.topp[s].key(t0, self.topp[s].at(t0), "hold").key(t0 + T * 0.8, 0.25, "smooth")
        lean, crouch = self._reach_posture(s, t0 + T, wg)
        self.lean.key(t0, self.lean.at(t0), "hold").key(t0 + T, lean, "mj")
        self.crouch.key(t0, self.crouch.at(t0), "hold").key(t0 + T, crouch, "mj")
        self._pending = {"side": s, "grip": g, "wg": wg, "q": q, "pre": pre, "prop": tgt}
        return t0 + T

    def _anchor_wrist(self, s, q, g):
        """span/pinch grips: wrist placed so the index pad lands on the far face at a natural curl."""
        ch = self.ch; Mw = ch.rig.matrix_world
        c0 = 0.62 if g["type"] == "pinch" else (0.36 if g.get("width", 0) > 0.085 else 0.52)  # wide spans: flatter fingers
        pts = self._finger_points(s, Mw.inverted() @ q.to_matrix().to_4x4(), ch.finger_quats(s, {"index": c0, "middle": c0}))
        ch_pts = pts.get("index") or pts.get("middle")
        if not ch_pts:
            return ch.wrist_for_palm(s, g["palm_point"], q)
        tip = Mw @ ch_pts[-1]   # wrist at the world origin -> offset of the index tip
        return Vector(g["finger_target"]) + Vector(g["fingers"]) * FRAD["index"] * 0.9 - tip

    def _thumb_pad(self, s, H_arm, tree, a, o, c, Tpad, oppose=None):
        """Thumb pad (world) for abduction a, opposition o, curl c -> (cost, pad). With oppose=(centre, finger
        centroid) the target is 'touch the surface anywhere on the side opposite the fingers' (power grips)."""
        ch = self.ch; Mw = ch.rig.matrix_world
        pw = [Mw @ p for p in self._finger_points(s, H_arm, ch.finger_quats(s, {"thumb": c}, 0.0, o, a))["thumb"]]
        pad = pw[-2].lerp(pw[-1], 0.6)
        pen = 0.0
        for p in pw[3:]:
            l2, n2, _, dd = tree.find_nearest(p)
            if l2 is None:
                continue
            if (p - l2).dot(n2) < 0:
                pen = max(pen, dd + FRAD["thumb"])
            elif dd < FRAD["thumb"] * 0.75:
                pen = max(pen, FRAD["thumb"] * 0.75 - dd)
        if oppose is not None:
            cen, fc = oppose
            l3, n3, _, dd = tree.find_nearest(pad)
            dd = dd or 1.0
            ax = (fc - cen).normalized() if (fc - cen).length > 1e-6 else Vector()
            side = (pad - cen).normalized().dot(ax)
            facing = (n3.dot(ax) if n3 is not None else 0.0)   # -1: on the face opposite the fingers (true opposition)
            return abs(dd - FRAD["thumb"]) + 0.012 * (1 + side) + 0.02 * (1 + facing) + 25 * pen, pad
        return (pad - Tpad).length + 25 * pen, pad

    def _digit_reach(self, s, H_arm, tree, finger, target):
        """Best distance (m, + penetration penalty) the pad of `finger` can get to `target` from this hand pose."""
        ch = self.ch; Mw = ch.rig.matrix_world
        loc, nrm, _, _ = tree.find_nearest(target)
        tpad = (loc + nrm * FRAD[finger]) if loc is not None else target
        if finger == "thumb":
            return min(self._thumb_pad(s, H_arm, tree, a, o, c, tpad)[0] for a in (0, .3, .6, .9) for o in (-.2, .2, .6, 1.)
                       for c in (-.15, .1, .35, .6, .85))
        best = 9.0
        for c in [x / 20 for x in range(0, 21)]:
            pw = [Mw @ p for p in self._finger_points(s, H_arm, ch.finger_quats(s, {finger: c}))[finger]]
            if not pw:
                return best
            pad = pw[-2].lerp(pw[-1], 0.6)
            pen = 0.0
            for p in pw[3:]:
                l2, n2, _, dd = tree.find_nearest(p)
                if l2 is not None and (p - l2).dot(n2) < 0:
                    pen = max(pen, dd + FRAD[finger])
            best = min(best, (pad - tpad).length + 25 * pen)
        return best

    def _plan_wrap(self, s, q, g, ob):
        """Power grips: choose where around the object the palm goes and how the hand is rolled so the wrist stays
        natural for the arm that is coming (cylinders: angle around the axis + finger tilt; round: yaw + tilt)."""
        tree = P.bvh(ob); up = Vector((0, 0, 1)); c = Vector(g["center"]); tw = self._plan_t
        best = None
        if g["type"] == "wrap_side":
            p0 = Vector(g["palm_point"]); zg = p0.z
            n0 = Vector(g["palm_n"]); n0.z = 0; n0.normalize()
            for ang in range(-75, 76, 15):
                n = Quaternion(up, math.radians(ang)) @ n0          # palm faces the axis from this side
                hit = tree.ray_cast(Vector((c.x, c.y, zg)) - n * 0.5, n)
                if hit[0] is None:
                    continue
                for tilt in (-50, -35, -20, -5, 10, 25, 40):   # fingers may wrap diagonally, following the forearm
                    fing = Quaternion(n, math.radians(tilt)) @ n.cross(up).normalized()
                    for sg in (1, -1):
                        qq = self.ch.hand_world_rot(s, n, fing * sg)
                        w = self.ch.wrist_for_palm(s, hit[0], qq)
                        cost = self._wrist_cost_at(s, tw, w, qq) + 0.15 * abs(ang) / 75 + 0.04 * abs(tilt) / 50
                        if best is None or cost < best[0]:
                            best = (cost, hit[0], n, fing * sg, qq)
        else:
            f0 = Vector(g["fingers"])
            for yaw in range(-75, 76, 15):
                for tilt in (0, 15, 30):
                    fy = Quaternion(up, math.radians(yaw)) @ f0
                    pn = (Quaternion(fy.cross(up).normalized(), math.radians(tilt)) @ -up).normalized()
                    qq = self.ch.hand_world_rot(s, pn, fy)
                    w = self.ch.wrist_for_palm(s, Vector(g["palm_point"]), qq)
                    cost = self._wrist_cost_at(s, tw, w, qq) + 0.1 * abs(yaw) / 75
                    if best is None or cost < best[0]:
                        best = (cost, Vector(g["palm_point"]), pn, fy, qq)
        if best is None:
            return g, q
        g = dict(g); g["palm_point"], g["palm_n"], g["fingers"] = best[1], best[2], best[3]
        if g["type"] == "wrap_side":
            g["approach"] = best[2].copy()
        return g, best[4]

    def _plan_span(self, s, q, g, ob):
        """span/pinch: search hand yaw (thumb-to-fingers opposition runs DIAGONALLY across a real hand, ~14 cm max),
        slide along the span axis and height until BOTH the thumb pad (near face) and the index pad (far face) can
        touch - a real two-sided pinch, never fingertips resting on top. Returns (wrist, hand rotation)."""
        ch = self.ch; Mwi = ch.rig.matrix_world.inverted()
        tree = P.bvh(ob)
        f0 = Vector(g["fingers"]); up = Vector((0, 0, 1))
        Ft, Tt = Vector(g["finger_target"]), Vector(g["thumb_target"])
        loc, nrm, _, _ = tree.find_nearest(Tt)
        Tpad = (loc + nrm * FRAD["thumb"]) if loc is not None else Tt
        best = None
        tside = 1 if s == "R" else -1  # yaw that swings the thumb toward the near face
        tw = self._plan_t
        pitches = (0, 20, 40, 60, 72) if g["type"] == "pinch" else ((0, 16, 32, 46) if g.get("dims", Vector((0, 0, 1))).z > 0.05 else (0, 14, 28))
        cands = []
        for yaw in (-36, -18, 0, 18, 36, 54):
            for pitch in pitches:
                fy = Quaternion(up, math.radians(yaw) * tside) @ f0
                Rq = Quaternion(fy.cross(up).normalized(), -math.radians(pitch)) @ Quaternion(up, math.radians(yaw) * tside)
                qy = Rq @ q
                gy = dict(g); gy["fingers"] = Rq @ f0
                base = self._anchor_wrist(s, qy, gy)
                cands.append((self._wrist_cost_at(s, tw, base, qy), yaw, qy, base))
        cands.sort(key=lambda c: c[0])
        for wc, yaw, qy, base in cands[:10]:   # the 10 most wrist-friendly hand angles get the full two-sided contact test
            if wc > 2.5:
                continue  # this hand angle would break the wrist - a person never grabs like that
            Q4 = qy.to_matrix().to_4x4()
            for df in (-0.05, -0.035, -0.02, -0.01, 0.0, 0.015):
                for dz in ((-0.01, 0.0, 0.015, 0.03) if g["type"] == "span" else (0.0, 0.02, 0.04, 0.06)):
                    w = base + f0 * df + up * dz
                    Hw = Matrix.Translation(w) @ Q4
                    pc = Hw @ ch.hand[s]["palm_center_l"]
                    l2, n2, _, dd = tree.find_nearest(pc)
                    if l2 is not None and ((pc - l2).dot(n2) < 0 or dd < ch.hand[s]["palm_thick"]):
                        continue
                    if g["type"] == "pinch" and pc.z < g.get("top", pc.z) + 0.045:
                        continue  # tiny/thin things are pinched from above with the fingertips, never a flat hand
                    H_arm = Mwi @ Hw
                    di = self._digit_reach(s, H_arm, tree, "index", Ft)
                    if di > 0.02:
                        continue
                    dt = min(self._thumb_pad(s, H_arm, tree, a, o, c, Tpad)[0]
                             for a in (0.0, 0.5, 1.0) for o in (-0.4, 0.2, 0.8) for c in (0.0, 0.3, 0.6))
                    if self._finger_intrusion(s, Hw, tree) > 0.005:
                        continue  # resting fingers would already be inside the object
                    cost = di + dt + 0.04 * abs(df) + 0.02 * abs(dz) + 0.004 * abs(yaw) / 12 + 0.005 * self._wrist_cost_at(s, tw, w, qy)
                    if best is None or cost < best[0]:
                        best = (cost, w, qy, di, dt, yaw)
        if best is None:
            return self._anchor_wrist(s, q, g), q
        return best[1], best[2]

    def _reach_posture(self, s, t, wrist):
        """Smallest torso lean (then knee bend) that brings the wrist within comfortable arm reach."""
        ch = self.ch; M = self.M(t); Mi = M.inverted()
        wl = Mi @ Vector(wrist)
        sh0 = ch.arm_head(f"upperarm.{s}")
        cb = ch.b.get(f"shoulder.{s}") or ch.spine[-1]
        lim = ch.arm_len * 0.86   # people lean/bend before the arm is straight: bent elbow = natural wrist
        for crouch in (0.0, 0.08, 0.16, 0.24, 0.32):
            for deg in range(0, 72, 3):
                shp = self.posed_point(self.chain_deltas(math.radians(deg)), cb, sh0, crouch)
                if (wl - shp).length <= lim:
                    return math.radians(deg), crouch
        log(f"reach: target {tuple(round(x, 2) for x in wrist)} out of reach - move the actor closer")
        return math.radians(55), 0.32

    def v_grasp(self, t0, a):
        pend = getattr(self, "_pending", None)
        prop = a.get("prop") or a.get("target") or (pend or {}).get("prop")
        if not pend or pend["prop"] != prop:
            t0 = self.v_reach(t0, {"target": prop, "hand": a.get("hand", "auto"), "grip": a.get("grip", "auto")})
            pend = self._pending
        s, wg, q = pend["side"], pend["wg"], pend["q"]
        t1 = t0 + 0.24
        self.hw[s].key(t0, pend["pre"], "hold").key(t1, wg, "out")
        tc = t1 + 0.16
        self.spread[s].key(t1, self.spread[s].at(t1), "hold").key(tc, 0.0, "smooth")  # fingers draw together to grip
        self._exact(s, t1 - 0.1, tc + 0.12)
        self.events.append({"type": "grip", "side": s, "prop": prop, "t_open": t1, "t_close": tc,
                            "grip": pend["grip"], "wrist": wg, "rot": q})
        self.events.append({"type": "attach", "side": s, "prop": prop, "t": tc})
        self.holding[s] = prop
        self._pending = None
        return tc + 0.05

    def _held(self, s):
        ev = [e for e in self.events if e["type"] == "grip" and e["side"] == s]
        return ev[-1] if ev else None

    def _bite_local(self, s):
        """Bite point in hand (wrist) local space: prop point farthest from the wrist along the fingers."""
        g = self._held(s)
        ob = self.props[g["prop"]]
        q, w = g["rot"], g["wrist"]
        inv = q.inverted()
        gp = g["grip"]
        cps = [inv @ (Vector(gp[k]) - w) for k in ("finger_target", "thumb_target", "palm_point") if gp.get(k) is not None]
        return self._free_point([inv @ (ob.matrix_world @ v.co - w) for v in list(ob.data.vertices)[:: max(1, len(ob.data.vertices) // 400)]],
                                cps, self.ch.hand[s]["fing_l"])

    @staticmethod
    def _free_point(pts, contacts, fing_l):
        """Point of the held object farthest from the fingers/thumb/palm (that is where the mouth goes)."""
        if not contacts:
            return max(pts, key=lambda p: p.dot(fing_l) + 0.3 * p.length)
        return max(pts, key=lambda p: min((p - c).length for c in contacts) + 0.3 * p.dot(fing_l))

    def _mouth_rot_natural(self, s, t, bl, pl, f, inward, grip_type, contact):
        """Like _mouth_rot, but the free roll around the bite direction (and +-20 deg of tilt) is chosen so the
        wrist stays natural with the arm that brings it up."""
        q0 = self._mouth_rot(bl, pl, f, inward, grip_type)
        up = Vector((0, 0, 1)); base_dir = (-f + up * (0.12 if grip_type == "span" else 0.22)).normalized()
        best = None
        for tilt_ax in (None, up.cross(f).normalized(), up):
            for tilt in ((0,) if tilt_ax is None else (-35, -18, 18, 35)):
                d = base_dir if tilt_ax is None else (Quaternion(tilt_ax, math.radians(tilt)) @ base_dir)
                align = (q0 @ bl).normalized().rotation_difference(d)
                for k in range(0, 24):
                    qq = Quaternion(d, math.radians(15 * k)) @ align @ q0
                    w = contact - qq @ bl
                    pal = qq @ pl
                    pref = 0.08 * (1 - pal.dot((-up * 0.85 + inward * 0.35).normalized() if grip_type == "span" else (up * 0.6 + inward * 0.8).normalized()))
                    cost = self._wrist_cost_at(s, t, w, qq) + pref + (0.05 if tilt_ax is not None else 0.0)
                    if best is None or cost < best[0]:
                        best = (cost, qq)
        return best[1]

    @staticmethod
    def _mouth_rot(bl, pl, f, inward, grip_type):
        """Hand rotation that brings the bite point to the mouth: flat foods level with the palm on top,
        others palm up/inward like a burger. inward = toward the body midline for THIS hand."""
        from .rigkit import _basis
        up = Vector((0, 0, 1))
        if grip_type == "pinch_edge":
            return _basis(bl.normalized(), (-f + up * 0.12).normalized(), pl, (-up * 0.85 + inward * 0.35).normalized())
        return _basis(bl.normalized(), (-f + up * 0.22).normalized(), pl, (up * 0.6 + inward * 0.8).normalized())

    def v_lift(self, t0, a):
        s = a.get("hand") or ("R" if self.holding["R"] else "L")
        s = s[0].upper()
        to = a.get("to", "mouth")
        T = float(a.get("dur", 0.75))
        f = self.fwd(t0)
        up = Vector((0, 0, 1))
        sg = 1 if s == "L" else -1
        inward = Rz(self.root_yaw.at(t0)) @ Vector((-sg, 0, 0))
        g = self._held(s)
        if to == "mouth" and g:
            bl = self._bite_local(s)
            pl = self.ch.hand[s]["palm_l"]
            qm = Quaternion()
            # orient: bite direction points back toward the face; palm faces up/inward
            from .rigkit import _basis
            tm = t0 + T
            self.lean.key(t0, self.lean.at(t0), "hold").key(tm, 0.06, "mj")      # posture first: the mouth is planned on it
            self.crouch.key(t0, self.crouch.at(t0), "hold").key(tm, 0.0, "mj")
            mouth = self.mouth_world(tm)
            contact = mouth + f * 0.006
            qm = self._mouth_rot_natural(s, tm, bl, pl, f, inward, g.get("grip", {}).get("type"), contact)
            w_contact = contact - qm @ bl
            w_pre = w_contact + f * 0.03 - up * 0.01
            self.hw[s].key(t0, self.hw[s].at(t0), "hold").key(tm, w_pre, "mj")
            self.hq[s].key(t0, self.hq[s].at(t0), "hold").key(tm, qm, "mj")
            self.ha[s].key(t0, Vector(), "hold").key(t0 + T * 0.5, Vector((0, 0, 0.04)), "smooth").key(tm, Vector(), "smooth")
            self._mouth_plan = {"side": s, "w_contact": w_contact, "q": qm, "bl": bl, "t_pre": tm, "t_lift0": t0}
            ahead = self.mouth_world(t0) + f * 1.5
            self.look.key(t0, self.look.at(t0), "hold").key(t0 + 0.4, ahead, "mj")
            self.gaze.key(t0 + 0.05, self.gaze.at(t0), "hold").key(t0 + 0.2, mouth + f * 0.2 - up * 0.05, "smooth")
            return tm
        # show / chest / point: stand up while bringing it up
        self.lean.key(t0, self.lean.at(t0), "hold").key(t0 + T, 0.0, "mj")
        self.crouch.key(t0, self.crouch.at(t0), "hold").key(t0 + T, 0.0, "mj")
        if to == "show":
            p = self.head_point(t0 + T, self.ch.eye_mid) + f * 0.32 - up * 0.08
        elif to == "chest":
            p = self.head_point(t0 + T, self.ch.eye_mid) + f * 0.3 - up * 0.42 + inward * 0.05
        else:
            p = Vector(to)
        q = self.hq[s].at(t0)
        self.hw[s].key(t0, self.hw[s].at(t0), "hold").key(t0 + T, p, "mj")
        self.ha[s].key(t0, Vector(), "hold").key(t0 + T * 0.5, Vector((0, 0, 0.04)), "smooth").key(t0 + T, Vector(), "smooth")
        if to == "show":
            self._look_to(t0 + 0.1, p)
        return t0 + T

    def v_drink(self, t0, a):
        """Held cup/can/bottle: rim to the lower lip, tilt toward the face, sip (head tips back), lower again."""
        s = (a.get("hand") or ("R" if self.holding["R"] else "L"))[0].upper()
        g = self._held(s)
        if not g:
            log("drink: nothing held"); return t0
        ob = self.props[g["prop"]]
        T, sip = float(a.get("lift", 0.75)), float(a.get("dur", 1.1))
        tilt = math.radians(float(a.get("tilt", 38)))
        f = self.fwd(t0); up = Vector((0, 0, 1)); lat = f.cross(up).normalized()
        q_hold, w_hold = g["rot"], g["wrist"]
        inv = q_hold.inverted()
        mn, mx = core.world_bbox(ob)
        rim = [inv @ (ob.matrix_world @ v.co - w_hold) for v in ob.data.vertices if (ob.matrix_world @ v.co).z > mx.z - 0.006]
        if not rim:
            return t0
        t1 = t0 + T; t2 = t1 + sip
        cen0 = (mn + mx) / 2
        best_psi = (1e9, 0.0)
        for psi in range(-75, 76, 15):   # spin the cup about its own vertical axis (hand goes round with it)
            Rs = Quaternion(up, math.radians(psi))
            qd = Quaternion(lat, tilt) @ Rs @ q_hold
            rp = max(rim, key=lambda p: (qd @ p).dot(-f))
            wt = self.mouth_world(t1) - up * 0.011 - qd @ rp
            cst = self._wrist_cost_at(s, t1, wt, qd) + 0.002 * abs(psi) / 15
            if cst < best_psi[0]:
                best_psi = (cst, psi)
        q_hold = Quaternion(up, math.radians(best_psi[1])) @ q_hold
        self.lean.key(t0, self.lean.at(t0), "hold").key(t1, 0.04, "mj")
        self.crouch.key(t0, self.crouch.at(t0), "hold").key(t1, 0.0, "mj")
        out = []
        for k, ang in ((t1, tilt), (t1 + sip * 0.5, tilt + math.radians(14)), (t2, tilt)):
            qd = Quaternion(lat, ang) @ q_hold
            rp = max(rim, key=lambda p: (qd @ p).dot(-f))          # rim point nearest the face
            lip = self.mouth_world(k) - up * 0.011 + f * (self.ch.lip_front * 0.6)
            out.append((k, lip - qd @ rp, qd))
        self.hw[s].key(t0, self.hw[s].at(t0), "hold"); self.hq[s].key(t0, self.hq[s].at(t0), "hold")
        self.ha[s].key(t0, Vector(), "hold").key(t0 + T * 0.5, Vector((0, 0, 0.03)), "smooth").key(t1, Vector(), "smooth")
        (ka, wa, qa), (kb, wb, qb), (kc, wc, qc) = out
        self.hw[s].key(ka, wa + f * 0.02, "mj").key(ka + 0.15, wa, "smooth").key(kb, wb, "smooth").key(kc, wc, "smooth")
        self.hq[s].key(ka, qa, "mj").key(ka + 0.15, qa, "smooth").key(kb, qb, "smooth").key(kc, qc, "smooth")
        self._exact(s, ka - 0.1, kc + 0.05)
        self.events.append({"type": "drink", "side": s, "prop": g["prop"], "psi": best_psi[1],
                            "keys": [(ka, tilt, 0.02), (ka + 0.15, tilt, 0.0), (kb, tilt + math.radians(14), 0.0), (kc, tilt, 0.0)]})
        self.nod.key(t1, self.nod.at(t1), "hold").key(t1 + 0.3, -math.radians(9), "smooth").key(t2, -math.radians(6), "hold").key(t2 + 0.4, 0.0, "smooth")
        self.face.keys({"mouthFunnel": 0.35, "jawOpen": 0.08, "mouthPucker": 0.2}, t1 - 0.1, t2, 0.2, 0.2)
        self.face.keys({"eyeBlinkLeft": 0.55, "eyeBlinkRight": 0.55}, t1 + 0.2, t2 - 0.1, 0.25, 0.2)
        self.face.swallow(t2 + 0.1)
        self._look_ahead(t0)
        down = self.shoulder_world(t2 + 0.7, s) + f * 0.3 - up * 0.22    # cup lowered in front of the chest
        self.hw[s].key(t2 + 0.7, down, "mj")
        self.hq[s].key(t2 + 0.7, q_hold, "mj")
        return t2 + 0.7

    def v_bite(self, t0, a):
        mp = getattr(self, "_mouth_plan", None)
        if not mp:
            t0 = self.v_lift(t0, {"to": "mouth", "hand": a.get("hand")}); mp = self._mouth_plan
        s = mp["side"]; f = self.fwd(t0); up = Vector((0, 0, 1))
        tc = t0 + 0.32
        self._exact(s, mp.get("t_pre", t0) - 0.05, tc + 0.6)
        self.hw[s].key(t0 + 0.08, self.hw[s].at(t0 + 0.08), "hold").key(tc, mp["w_contact"], "out")
        self.nod.key(t0, self.nod.at(t0), "hold").key(tc, math.radians(5), "smooth").key(tc + 0.35, 0.0, "smooth")
        self.face.bite(tc)
        self.face.no_blink.append((t0, tc + 0.2))
        self.events.append({"type": "bite", "side": s, "prop": self.holding[s], "t": tc + 0.09, "t_contact": tc,
                            "t_pre": mp.get("t_pre"), "t_pre2": t0 + 0.08, "t_hold": tc + 0.12, "t_away": tc + 0.6})
        away = mp["w_contact"] + f * 0.14 - up * 0.16   # food comes down to chest height while chewing
        self._look_ahead(tc + 0.25)                       # eyes leave the food once it is in the mouth
        self.hw[s].key(tc + 0.12, mp["w_contact"], "hold").key(tc + 0.6, away, "mj")
        self._mouth_plan = None
        return tc + 0.6

    def v_chew(self, t0, a):
        n = int(a.get("cycles", 4))
        t1 = self.face.chew(t0, n)
        for i in range(n):
            tb = t0 + i * 0.62
            self.nod.key(tb + 0.15, math.radians(1.5), "smooth").key(tb + 0.45, 0.0, "smooth")
        if a.get("expr", "yum"):
            self.face.expr(a.get("expr", "yum"), t0 + 0.2, t1, 0.8)
        return t1

    def v_swallow(self, t0, a):
        self.nod.key(t0, 0.0, "hold").key(t0 + 0.2, math.radians(4), "smooth").key(t0 + 0.45, 0.0, "smooth")
        return self.face.swallow(t0)

    def v_place(self, t0, a):
        s = (a.get("hand") or ("R" if self.holding["R"] else "L"))[0].upper()
        prop = self.holding[s]; ob = self.props[prop]
        sup = self.props.get(a.get("on")) or bpy.data.objects.get(a.get("on", ""))
        g = self._held(s)
        xy = a.get("xy") or (a.get("at") if isinstance(a.get("at"), (list, tuple)) else None)
        mn, mx = core.world_bbox(sup)
        tx, ty = (xy[0], xy[1]) if xy else ((mn.x + mx.x) / 2, (mn.y + mx.y) / 2)
        z = P.top_z(sup, tx, ty)
        pm_now = ob.matrix_world.translation; bmn, _ = core.world_bbox(ob)
        lift = pm_now.z - bmn.z
        delta = Vector((tx, ty, z + lift + 0.001)) - pm_now
        w = g["wrist"] + delta
        T = clamp(0.5 + 0.4 * (w - self.hw[s].at(t0)).length, 0.6, 1.2)
        self.hw[s].key(t0, self.hw[s].at(t0), "hold").key(t0 + T, w, "mj")
        self.hq[s].key(t0, self.hq[s].at(t0), "hold").key(t0 + T, g["rot"], "mj")
        self._look_to(t0, Vector((tx, ty, z)))
        lean, crouch = self._reach_posture(s, t0 + T, w)   # lean/bend to put it down, like reaching for it
        self._exact(s, t0 + T - 0.3, t0 + T + 0.1)
        self.lean.key(t0, self.lean.at(t0), "hold").key(t0 + T, lean, "mj")
        self.crouch.key(t0, self.crouch.at(t0), "hold").key(t0 + T, crouch, "mj")
        t_end = self.v_release(t0 + T, {"hand": s, "on": a.get("on")})
        self.lean.key(t0 + T + 0.15, lean, "hold").key(t_end + 0.3, 0.0, "mj")
        self.crouch.key(t0 + T + 0.15, crouch, "hold").key(t_end + 0.3, 0.0, "mj")
        return t_end

    def v_release(self, t0, a):
        s = (a.get("hand") or ("R" if self.holding["R"] else "L"))[0].upper()
        if self.holding[s]:
            self.events.append({"type": "detach", "side": s, "prop": self.holding[s], "t": t0 + 0.05, "drop": a.get("drop", False),
                                "on": a.get("on")})
        self.holding[s] = None
        for f, c in OPEN.items():
            self.curl[s][f].key(t0, self.curl[s][f].at(t0), "hold").key(t0 + 0.2, c + 0.05, "smooth")
        g = self._held(s)
        back = (g["grip"]["approach"] if g else self.fwd(t0)) * -0.06 + Vector((0, 0, 0.04))
        w = self.hw[s].at(t0)
        self.hw[s].key(t0 + 0.05, w, "hold").key(t0 + 0.35, w + back, "out")
        return self.v_rest(t0 + 0.35, {"hand": s})

    def v_rest(self, t0, a):
        sides = "LR" if a.get("hand", "both") in ("both", None) else a["hand"][0].upper()
        T = float(a.get("dur", 0.7))
        for s in sides:
            if self.holding[s]:
                continue
            self.hs[s].key(t0, self.hs[s].at(t0), "hold").key(t0 + T, 0.0, "mj")
            self.hbw[s].key(t0, self.hbw[s].at(t0), "hold").key(t0 + T, 0.0, "mj")
            self.hb[s].key(t0, self.hb[s].at(t0), "hold").key(t0 + T, Vector(), "mj")
            for f, c in RELAX.items():
                self.curl[s][f].key(t0 + T * 0.3, self.curl[s][f].at(t0 + T * 0.3), "hold").key(t0 + T, c, "smooth")
            self.spread[s].key(t0 + T, 0.0, "smooth")
        self.lean.key(t0, self.lean.at(t0), "hold").key(t0 + T, 0.0, "mj")
        return t0 + T

    # ------------------------------------------------------------ body-space gestures
    def _gesture(self, s, t0, T_in, hold, wrist_arm_off, palm, fingers, curls, T_out=0.5, osc=None,
                 absolute=False, pole_out=0.0, look=None):
        """Move hand s to a pose, hold, return. wrist_arm_off: offset from the shoulder (armature space), or an
        absolute armature-space point (absolute=True), or a callable(t)->armature point (head-relative poses)."""
        ch = self.ch; sh = ch.arm_head(f"upperarm.{s}")
        t1, t2 = t0 + T_in, t0 + T_in + hold
        if callable(wrist_arm_off):
            target = wrist_arm_off(t1)
        else:
            target = Vector(wrist_arm_off) if absolute else sh + Vector(wrist_arm_off)
        w_rest, _ = self.rest_hand(s, t1)
        q = ch.hand_world_rot(s, Vector(palm), Vector(fingers))
        off = target - w_rest
        self.hb[s].key(t0, self.hb[s].at(t0), "hold").key(t1, off, "mj").key(t2, off, "hold")
        self.hbq[s].key(t1, q, "hold"); self.hbw[s].key(t0, self.hbw[s].at(t0), "hold").key(t1, 1.0, "mj").key(t2, 1.0, "hold")
        if pole_out:
            self.pole_out[s].key(t0, self.pole_out[s].at(t0), "hold").key(t1, pole_out, "mj").key(t2, pole_out, "hold")
        if osc:
            amp, hz, axis = osc
            k = 0; tt = t1
            while tt < t2:
                self.hb[s].key(tt, off + Vector(axis) * amp * (1 if k % 2 else -1), "smooth")
                tt += 0.5 / hz; k += 1
            self.hb[s].key(t2, off, "smooth")
        for f, c in curls.items():
            self.curl[s][f].key(t0, self.curl[s][f].at(t0), "hold").key(t1, c, "smooth").key(t2, c, "hold")
        if look is not None:
            self._look_to(t0, look)
        if T_out:
            self.hb[s].key(t2 + T_out, Vector(), "mj"); self.hbw[s].key(t2 + T_out, 0.0, "mj")
            if pole_out:
                self.pole_out[s].key(t2 + T_out, 0.0, "mj")
            for f, c in RELAX.items():
                self.curl[s][f].key(t2 + T_out, c, "smooth")
            if look is not None:
                self._look_ahead(t2 + 0.1)
        return t2 + (T_out or 0)

    def _look_ahead(self, t):
        self._look_to(t, self.head_point(t, self.ch.eye_mid) + self.fwd(t) * 2.5)

    def _head_pose(self, p_arm):
        """Callable for head-relative gesture targets (follows nod/lean): armature point at time t."""
        def f(t):
            return self.M(t).inverted() @ self.head_point(t, p_arm)
        return f

    def _sg(self, s):
        return 1 if s == "L" else -1

    def v_point(self, t0, a):
        p = self._target_point(a["target"])
        s = (a.get("hand") or self._side_for(p, t0))[0].upper()
        loc = self.M(t0).inverted() @ p
        sh = self.ch.arm_head(f"upperarm.{s}")
        d = (loc - sh).normalized()
        palm = (Vector((0, 0, -1)) - d * d.z).normalized() if abs(d.z) < 0.95 else Vector((0, -1, 0))
        return self._gesture(s, t0, 0.45, float(a.get("hold", 1.2)), d * self.ch.arm_len * 0.86, palm, d,
                             {"thumb": .55, "index": .0, "middle": .95, "ring": 1, "pinky": 1}, look=p)

    def v_wave(self, t0, a):
        s = (a.get("hand") or "R")[0].upper(); g = self._sg(s)
        self.face.expr("happy", t0, t0 + float(a.get("hold", 1.4)) + 0.4)
        return self._gesture(s, t0, 0.4, float(a.get("hold", 1.4)), (g * 0.24, -0.08, 0.2), (0, -1, 0), (0, 0, 1),
                             OPEN, osc=(0.06, 2.2, (g, 0, 0)))

    def v_thumbs_up(self, t0, a):
        s = (a.get("hand") or "R")[0].upper(); g = self._sg(s)
        self.face.expr("happy", t0, t0 + 1.6)
        return self._gesture(s, t0, 0.4, float(a.get("hold", 1.2)), (-g * 0.05, -0.27, -0.15), (-g, 0, 0), (0, -1, 0.15),
                             {"thumb": 0.0, "index": 1, "middle": 1, "ring": 1, "pinky": 1})

    def v_shrug(self, t0, a):
        hold = float(a.get("hold", 0.9))
        self.shrug.key(t0, 0.0, "hold").key(t0 + 0.3, math.radians(10), "smooth").key(t0 + 0.3 + hold, math.radians(10), "hold").key(t0 + 0.8 + hold, 0, "smooth")
        self.tilt.key(t0, 0.0, "hold").key(t0 + 0.3, math.radians(8), "smooth").key(t0 + 0.8 + hold, 0, "smooth")
        self.face.expr(a.get("expr", "confused"), t0, t0 + 0.3 + hold)
        for s in "LR":
            g = self._sg(s)
            t = self._gesture(s, t0, 0.35, hold, (g * 0.16, -0.24, -0.38), (0, 0, 1), (g * 0.5, -0.85, 0), OPEN)
        return t

    def v_facepalm(self, t0, a):
        s = (a.get("hand") or "R")[0].upper(); g = self._sg(s)
        ch = self.ch
        self.nod.key(t0, 0, "hold").key(t0 + 0.4, math.radians(12), "smooth").key(t0 + 1.6, math.radians(12), "hold").key(t0 + 2.0, 0, "smooth")
        self.face.expr("embarrassed", t0, t0 + 1.8)
        palm_n = Vector((0, 1, 0)); fing = Vector((-g * 0.35, 0, 1)).normalized()
        q = ch.hand_world_rot(s, palm_n, fing)
        hc = ch.hand[s]
        palm_pt = ch.eye_mid + Vector((0, -0.10, 0.045))          # just in front of the forehead
        wrist = palm_pt - q @ hc["palm_center_l"] - (q @ hc["palm_l"]) * hc["palm_thick"]
        return self._gesture(s, t0, 0.45, float(a.get("hold", 1.1)), self._head_pose(wrist), palm_n, fing, RELAX, pole_out=0.3)

    def v_hands_on_hips(self, t0, a):
        hold = float(a.get("hold", 1.5)); t = t0; ch = self.ch
        for s in "LR":
            g = self._sg(s)
            th = ch.arm_head(f"thigh.{s}")
            w = Vector((th.x + g * 0.12, th.y + 0.01, ch.hips_h + 0.06))
            t = self._gesture(s, t0, 0.5, hold, w, (-g, 0.2, 0), (0, -0.25, -1), FIST, absolute=True, pole_out=1.0)
        return t

    def v_cross_arms(self, t0, a):
        hold = float(a.get("hold", 1.5)); t = t0; ch = self.ch
        sh = (ch.arm_head("upperarm.L") + ch.arm_head("upperarm.R")) / 2
        for s in "LR":
            g = self._sg(s)
            # elbows bent ~100 deg: wrists meet in front of the lower chest, R forearm on top
            w = Vector((-g * 0.015, sh.y - (0.21 if s == "L" else 0.25), sh.z - (0.29 if s == "L" else 0.26)))
            t = self._gesture(s, t0, 0.55, hold, w, (0, 1, 0), (-g, 0, 0.1), RELAX, absolute=True, pole_out=0.25)
        self.face.expr(a.get("expr", "skeptical"), t0, t0 + hold + 0.5)
        return t

    def v_nod(self, t0, a):
        n = int(a.get("times", 2)); t = t0
        for i in range(n):
            self.nod.key(t, 0.0, "hold").key(t + 0.18, math.radians(9), "smooth").key(t + 0.38, 0.0, "smooth"); t += 0.4
        return t

    def v_shake_head(self, t0, a):
        n = int(a.get("times", 3)); t = t0
        for i in range(n):
            self.headyaw.key(t, 0.0 if i == 0 else self.headyaw.at(t), "hold").key(t + 0.2, math.radians(12) * (1 if i % 2 else -1), "smooth"); t += 0.22
        self.headyaw.key(t + 0.2, 0.0, "smooth")
        return t + 0.2

    def v_lean(self, t0, a):
        T = float(a.get("dur", 0.6))
        self.lean.key(t0, self.lean.at(t0), "hold").key(t0 + T, math.radians(float(a.get("deg", 12))), "mj")
        return t0 + T

    def v_crouch(self, t0, a):
        T = float(a.get("dur", 0.8)); depth = float(a.get("depth", 0.35))
        self.crouch.key(t0, self.crouch.at(t0), "hold").key(t0 + T, depth, "mj")
        self.lean.key(t0, self.lean.at(t0), "hold").key(t0 + T, math.radians(12 + depth * 30), "mj")
        return t0 + T

    def v_stand(self, t0, a):
        T = float(a.get("dur", 0.8))
        self.crouch.key(t0, self.crouch.at(t0), "hold").key(t0 + T, 0.0, "mj")
        self.lean.key(t0, self.lean.at(t0), "hold").key(t0 + T, 0.0, "mj")
        return t0 + T

    def v_startle(self, t0, a):
        self.face.expr("shocked", t0, t0 + float(a.get("hold", 1.0)), attack=0.08)
        self.crouch.key(t0, 0.0, "hold").key(t0 + 0.12, 0.05, "out").key(t0 + 0.5, 0.0, "smooth")
        self.back.key(t0, 0.0, "hold").key(t0 + 0.15, math.radians(-8), "out").key(t0 + 0.9, 0.0, "smooth")
        self.shrug.key(t0, 0.0, "hold").key(t0 + 0.1, math.radians(9), "out").key(t0 + 0.8, 0.0, "smooth")
        return t0 + 0.9

    def v_look_at(self, t0, a):
        self._look_to(t0, self._target_point(a["target"]), head=a.get("head", True))
        return t0 + 0.45

    def v_face(self, t0, a):
        dur = float(a.get("dur", 1.5))
        self.face.expr(a.get("expr", "neutral"), t0, t0 + dur, float(a.get("amt", 1.0)))
        return t0 + (dur if a.get("block") else 0.0)

    def v_talk(self, t0, a):
        dur = float(a.get("dur", max(0.6, len(a.get("text", "")) * 0.065)))
        self.face.talk(t0, t0 + dur, a.get("text", "hello"))
        self.nod.key(t0, self.nod.at(t0), "hold").key(t0 + dur * 0.3, math.radians(3), "smooth").key(t0 + dur, 0.0, "smooth")
        return t0 + dur

    def v_wait(self, t0, a):
        return t0 + float(a.get("dur", 1.0))

    # ------------------------------------------------------------ feet
    def foot_at(self, s, t):
        """World ankle position + rotation (planted steps, heel-toe roll, swing arc)."""
        ch = self.ch
        cur = None
        for st in self.steps:
            if st[0] != s:
                continue
            _, tl, td, fr, to, fy, ty = st
            if t < tl:
                break
            cur = st
            if t <= td:
                u = (t - tl) / max(1e-3, td - tl)
                us = smooth(u)
                p = fr.lerp(to, us) + Vector((0, 0, 0.085 * math.sin(math.pi * u) ** 1.2))
                yaw = fy + (ty - fy) * us
                pitch = math.radians(22) * (1 - u) ** 2 - math.radians(12) * math.sin(math.pi * min(1, u * 1.15)) * (u > 0.6)
                return p, yaw, pitch
        if cur is None:
            p, yaw = self.first_plant[s]; return p.copy(), yaw, 0.0
        _, tl, td, fr, to, fy, ty = cur
        nxt = next((st for st in self.steps if st[0] == s and st[1] > td), None)
        pitch = 0.0
        if nxt and nxt[1] - 0.25 < t < nxt[1]:
            pitch = math.radians(22) * smooth((t - (nxt[1] - 0.25)) / 0.25)
        return to.copy(), ty, pitch

    def foot_world(self, s, t):
        p, yaw, pitch = self.foot_at(s, t)
        fb = self.ch.bone(f"foot.{s}")
        q_rest = fb.matrix_local.to_quaternion()
        q_yaw = Rz(yaw + (TURNOUT if s == "L" else -TURNOUT))
        lat = q_yaw @ Vector((1, 0, 0))
        q = Quaternion(lat, pitch) @ q_yaw @ q_rest
        if pitch > 0:  # heel lift: pivot at the ball of the foot
            toe = self.ch.bone(f"toe.{s}")
            pv = p + q_yaw @ ((toe.head_local if toe else fb.tail_local) - fb.head_local)
            pv.z = max(pv.z, 0.0)
            p = pv + Quaternion(lat, pitch) @ (p - pv)
        return p, q

    # ------------------------------------------------------------ bake
    def bake(self, kw, frames):
        ch, rig, pb = self.ch, self.ch.rig, self.ch.rig.pose.bones
        hips = ch.bone("hips")
        H3i = hips.matrix_local.to_3x3().inverted()
        chain = ch.spine + [b for b in (ch.b.get("neck"), ch.b.get("head")) if b]
        nsp = max(1, len(ch.spine))
        self.face.auto_blinks(frames[0] / self.fps, frames[-1] / self.fps)
        face_keys = set()
        gain = getattr(ch, "face_gain", 1.0)
        samples = []
        for f in frames:
            t = f / self.fps
            samples.append((f, t, self.face.sample(t)))
            face_keys |= set(samples[-1][2])
        for f, t, fs in samples:
            M = self.M(t)
            kw.put(rig, "location", f, M.translation)
            kw.put(rig, "rotation_euler", f, (0.0, 0.0, self.root_yaw.at(t)))
            # hips: walk bob/sway + crouch + pelvis rotation
            bob = sway = pyaw = 0.0
            for (t0, t1, st) in self.walk_iv:
                if t0 <= t <= t1:
                    env = smooth((t - t0) / 0.35) * smooth((t1 - t) / 0.35)
                    ph = (t - t0) / st * math.pi
                    bob = -0.022 * env * math.cos(2 * ph) - 0.012 * env
                    sway = 0.018 * env * math.sin(ph)
                    pyaw = math.radians(4) * env * math.sin(ph)
            breath = 0.004 * math.sin(t * 2 * math.pi / 3.8)
            shiftw = 0.006 * self.noise(t * 0.25)
            off = Vector((sway + shiftw, HINGE * self.crouch.at(t), bob - self.crouch.at(t)))
            cw, cs = self.clip_at(t)
            if cw > 0 and "hips" in cs["parts"]:
                off += cs["hip"] * (cw * ch.hips_h)
            loc = H3i @ off
            kw.put(rig, f'pose.bones["{hips.name}"].location', f, loc)
            DD = self.chain_deltas(self.lean.at(t), self.twist.at(t), self.back.at(t), self.nod.at(t), self.tilt.at(t),
                                   self.headyaw.at(t), self.shrug.at(t), pyaw, breath, clip=(cw, cs))
            kw.put(rig, f'pose.bones["{hips.name}"].rotation_quaternion', f, core.rest_local_rot(hips, DD[hips.name]))
            for bn in chain + [x for x in (ch.b.get("shoulder.L"), ch.b.get("shoulder.R")) if x]:
                b = rig.data.bones[bn]
                par = b.parent.name if b.parent else None
                kw.put(rig, f'pose.bones["{bn}"].rotation_quaternion', f, core.rest_local_rot(b, DD[bn], DD.get(par, Quaternion())))
            for s in "LR":
                # feet + knee poles
                p, q = self.foot_world(s, t)
                kw.put(ch.ctrl[f"foot.{s}"], "location", f, p)
                kw.put(ch.ctrl[f"foot.{s}"], "rotation_quaternion", f, q)
                knee = ch.bone(f"shin.{s}").head_local
                kw.put(ch.ctrl[f"pole_leg.{s}"], "location", f, M @ (knee + Vector((0, -0.6, 0))))
                # hands
                w, hq = self.hand_target(s, t)
                kw.put(ch.ctrl[f"hand.{s}"], "location", f, w)
                kw.put(ch.ctrl[f"hand.{s}"], "rotation_quaternion", f, hq)
                kw.put(ch.ctrl[f"pole_arm.{s}"], "location", f, self._elbow_pole(s, t, w, hq))
            look = self.look.at(t)
            gz = self.gaze.at(t) + Vector((self.noise(t * 1.7 + 40) * 0.012, 0, self.noise(t * 1.3 + 80) * 0.008)) * (look - M.translation).length
            look = self._clamp_look(t, look)
            gz = self._clamp_gaze(t, gz, look)
            kw.put(ch.ctrl["look"], "location", f, look)
            kw.put(ch.ctrl["gaze"], "location", f, gz)
            for k in face_keys:
                v = fs.get(k, 0.0)
                if gain != 1.0 and not k.startswith(NOGAIN):
                    v = min(1.0, v * gain)
                for (skd, kname) in ch.face.get(k, []):
                    kw.put(skd, f'key_blocks["{kname}"].value', f, v)
        self._bake_fingers(kw, frames)

    NECK = {"yaw": 75, "up": 38, "down": 50}   # head+neck range relative to the chest (deg)

    def _clamp_look(self, t, look):
        D = self.deltas_at(t)
        top = self.ch.spine[-1] if self.ch.spine else self.ch.b["hips"]
        cq = self.M(t).to_quaternion() @ D.get(top, Quaternion())
        cf = cq @ Vector((0, -1, 0)); cf.z = 0
        if cf.length < 1e-6:
            return look
        cf.normalize()
        base = self.head_point(t, self.ch.eye_mid)
        d = look - base; dist = d.length
        if dist < 1e-6:
            return look
        dn = d / dist; side = cf.cross(Vector((0, 0, 1))).normalized(); up = Vector((0, 0, 1))
        x, y, z = dn.dot(cf), dn.dot(side), dn.dot(up)
        yaw, pit = math.atan2(y, x), math.atan2(z, math.hypot(x, y))
        N = self.NECK
        yc = clamp(yaw, -math.radians(N["yaw"]), math.radians(N["yaw"])); pc = clamp(pit, -math.radians(N["down"]), math.radians(N["up"]))
        if abs(yc - yaw) < 1e-4 and abs(pc - pit) < 1e-4:
            return look
        nd = cf * (math.cos(pc) * math.cos(yc)) + side * (math.cos(pc) * math.sin(yc)) + up * math.sin(pc)
        return base + nd * dist

    EYE = {"yaw": 26, "up": 14, "down": 20, "near": 0.35}  # deg from where the head points; min focus distance (m)

    def _clamp_gaze(self, t, gz, look):
        """Keep the eyes inside their real range relative to the (look-tracking) head; the head does the rest."""
        eye = self.head_point(t, self.ch.eye_mid)
        hq = self.M(t).to_quaternion() @ self.deltas_at(t).get(self.ch.b["head"], Quaternion())
        lk = look - eye
        hf = (hq @ Vector((0, -1, 0))).lerp(lk.normalized() if lk.length > 1e-6 else hq @ Vector((0, -1, 0)), 0.7).normalized()
        g = gz - eye; dist = g.length
        if dist < 1e-6:
            return gz
        gd = g / dist
        side = hf.cross(Vector((0, 0, 1)))
        if side.length < 1e-6:
            return gz
        side.normalize(); upv = side.cross(hf).normalized()
        x, y, z = gd.dot(hf), gd.dot(side), gd.dot(upv)
        yaw, pitch = math.atan2(y, x), math.atan2(z, math.hypot(x, y))
        E = self.EYE
        yc = clamp(yaw, -math.radians(E["yaw"]), math.radians(E["yaw"]))
        pc = clamp(pitch, -math.radians(E["down"]), math.radians(E["up"]))
        if abs(yc - yaw) < 1e-4 and abs(pc - pitch) < 1e-4 and dist >= E["near"]:
            return gz
        nd = hf * (math.cos(pc) * math.cos(yc)) + side * (math.cos(pc) * math.sin(yc)) + upv * math.sin(pc)
        return eye + nd * max(dist, E["near"])

    def _spine_top(self, t):
        return Quaternion((1, 0, 0), self.lean.at(t) + self.back.at(t)) @ Quaternion((0, 0, 1), self.twist.at(t))

    def _elbow_pole(self, s, t, wrist, q=None):
        """IK pole for the elbow. With the hand rotation q known, the elbow is swung around the shoulder-wrist
        axis to where the WRIST bends least (real people turn the elbow out/in instead of breaking the wrist)."""
        pole = self._elbow_pole_base(s, t, wrist)
        return pole if q is None else self._swivel(s, t, wrist, q, pole)

    def _wrist_angles(self, s, f, q):
        """(flex, deviation, twist) of hand rotation q relative to forearm direction f (radians)."""
        h = self.ch.hand[s]
        fing = (q @ h["fing_l"]).normalized(); palm = (q @ h["palm_l"]).normalized()
        pal = palm - palm.project(f)
        if pal.length < 1e-6:
            return 0.0, 0.0, pal
        pal.normalize(); lat = f.cross(pal)
        x, y, z = fing.dot(f), fing.dot(pal), fing.dot(lat)
        return math.atan2(y, x), math.atan2(z, math.hypot(x, y)), pal

    def _wrist_cost_at(self, s, t, w, q):
        """How unnatural the wrist would be for hand pose (w, q) at time t, elbow swivel chosen at best
        (0 = relaxed; grows fast beyond the anatomical range). Used to choose grip, hold and mouth orientations."""
        L1, L2 = self.L_ua[s], self.L_fa[s]
        S = self.shoulder_world(t, s)
        d = w - S; dist = min(max(d.length, 1e-4), (L1 + L2) * 0.999); dn = d.normalized()
        a_ = (L1 * L1 - L2 * L2 + dist * dist) / (2 * dist); hh = math.sqrt(max(0.0, L1 * L1 - a_ * a_))
        C = S + dn * a_
        u = self._elbow_pole_base(s, t, w) - S; u -= u.project(dn)
        if u.length < 1e-6:
            u = Vector((0, 0, -1)) - Vector((0, 0, -1)).project(dn)
        u.normalize(); v = dn.cross(u)
        Wl = self.WRIST; rad = math.radians
        best = 1e9
        for k in range(-6, 7):
            th = math.radians(12 * k)
            E = C + (u * math.cos(th) + v * math.sin(th)) * hh
            f = (w - E).normalized()
            fl, dv, _ = self._wrist_angles(s, f, q)
            c = 0.3 * (fl / rad(55)) ** 2 + 0.3 * (dv / rad(22)) ** 2 + 0.25 * (th / 1.3) ** 2
            c += 40 * (max(0.0, fl - rad(Wl["flex"])) + max(0.0, -fl - rad(Wl["ext"])) + max(0.0, abs(dv) - rad(Wl["dev"]))) ** 2
            best = min(best, c)
        return best + (2.0 if (w - S).length > (L1 + L2) * 0.97 else 0.0)

    def _swivel(self, s, t, W, q, pole0):
        L1, L2 = self.L_ua[s], self.L_fa[s]
        S = self.shoulder_world(t, s)
        d = W - S; dist = min(max(d.length, 1e-4), (L1 + L2) * 0.999); dn = d.normalized()
        a_ = (L1 * L1 - L2 * L2 + dist * dist) / (2 * dist); hh = math.sqrt(max(0.0, L1 * L1 - a_ * a_))
        if hh < 0.02:
            return pole0  # arm (almost) straight: swivel does not change the wrist
        C = S + dn * a_
        u = pole0 - S; u -= u.project(dn)
        if u.length < 1e-6:
            return pole0
        u.normalize(); v = dn.cross(u)
        Wl = self.WRIST; rad = math.radians
        up = Vector((0, 0, 1)); mid_x = self.M(t).translation
        def cost(th):
            E = C + (u * math.cos(th) + v * math.sin(th)) * hh
            f = (W - E).normalized()
            fl, dv, pal = self._wrist_angles(s, f, q)
            c = 0.25 * (fl / rad(60)) ** 2 + 0.25 * (dv / rad(25)) ** 2
            c += 40 * (max(0.0, fl - rad(Wl["flex"])) + max(0.0, -fl - rad(Wl["ext"])) + max(0.0, abs(dv) - rad(Wl["dev"]))) ** 2
            nb = (E - S).cross(W - E)
            if nb.length > 1e-5 and pal.length > 1e-6:  # forearm twist from the handshake-neutral palm
                ref = nb.normalized() * (1 if s == "L" else -1)
                tau = math.atan2(f.dot(ref.cross(pal)), ref.dot(pal))
                c += 20 * max(0.0, abs(tau) - rad(Wl["twist"])) ** 2
            c += 6 * max(0.0, E.z - (S.z + 0.04)) ** 2 / 0.01 * (1 - smooth((W.z - S.z + 0.1) / 0.25))  # elbow above shoulder only for raised hands
            c += 0.35 * (th / 1.3) ** 2                                           # stay near the natural elbow
            return c
        ths = [math.radians(6 * k) for k in range(-15, 16)]
        cs = [cost(th) for th in ths]
        i = min(range(len(ths)), key=lambda j: cs[j])
        th = ths[i]
        if 0 < i < len(ths) - 1:  # parabolic refinement -> smooth from frame to frame
            c0, c1, c2 = cs[i - 1], cs[i], cs[i + 1]
            den = c0 - 2 * c1 + c2
            if den > 1e-9:
                th += 0.5 * (c0 - c2) / den * math.radians(6)
        E = C + (u * math.cos(th) + v * math.sin(th)) * hh
        return E + (E - C).normalized() * 0.35

    def _elbow_pole_base(self, s, t, wrist):
        ch = self.ch; g = 1 if s == "L" else -1
        sh = ch.arm_head(f"upperarm.{s}")
        M = self.M(t)
        wl = M.inverted() @ wrist
        u = smooth((wl.z - (sh.z - 0.45)) / 0.45)  # 0 = arm hanging, 1 = hand raised (to mouth/face)
        po = self.pole_out[s].at(t)
        pole = M @ Vector((sh.x + g * (0.04 + 0.3 * u + 0.55 * po), sh.y + 0.55 * (1 - u) * (1 - po) + 0.05 + 0.1 * po,
                           sh.z - 0.3 - 0.15 * u + 0.05 * po))
        wa = self._clip_arm_w(s, t) * (1 - self.hs[s].at(t))
        if wa > 0:
            wc, _, el = self._clip_arm(s, t)
            shp = el[1]; mid = (shp + wc) / 2; dv = el[0] - mid
            if dv.length > 0.01:
                pole = pole.lerp(M @ (el[0] + dv.normalized() * 0.35), wa)
        return pole

    def _bake_fingers(self, kw, frames):
        rig = self.ch.rig
        for s in "LR":
            for f in frames:
                t = f / self.fps
                curls = {k: tr.at(t) for k, tr in self.curl[s].items()}
                quats = self.ch.finger_quats(s, curls, self.spread[s].at(t), self.topp[s].at(t), self.tabd[s].at(t))
                wa = self._clip_arm_w(s, t, "fingers") * (1 - self.hs[s].at(t))
                if wa > 0:
                    C = self.clip_at(t)[1]
                    for fg in FINGERS:
                        for n in (1, 2, 3):
                            k, pk = f"{fg}{n}.{s}", (f"{fg}{n - 1}.{s}" if n > 1 else f"hand.{s}")
                            bn = self.ch.b.get(k)
                            if bn in quats and k in C["q"] and pk in C["q"]:
                                ql = core.rest_local_rot(rig.data.bones[bn], C["q"][k], C["q"][pk])
                                quats[bn] = quats[bn].slerp(ql, wa)
                for bn, q in quats.items():
                    kw.put(rig, f'pose.bones["{bn}"].rotation_quaternion', f, q)

    # ------------------------------------------------------------ pass B: contacts
    def solve_contacts(self, kw, frames):
        sc = bpy.context.scene
        changed = False
        for ev in [e for e in self.events if e["type"] == "grip"]:
            s, ob = ev["side"], self.props[ev["prop"]]
            fc = round(ev["t_close"] * self.fps)
            sc.frame_set(fc)
            tree = core.bvh_world(ob, evaluated=False)
            Hw = self._hand_world(s)
            # penetration guard: no finger segment or palm may start inside the prop -> back the hand off
            pn = (Hw.to_3x3() @ self.ch.hand[s]["palm_l"]).normalized()
            total = Vector()
            precision = ev["grip"]["type"] in ("span", "pinch")
            for _ in range(5):
                depth = self._penetration(s, Hw, tree, palm_only=precision)
                if depth < 0.0015:
                    break
                fix = -pn * (depth + 0.002); total += fix
                Hw = Matrix.Translation(fix) @ Hw
            gt = ev["grip"]["type"]
            if gt in ("wrap_top", "wrap_side"):  # power grip: bring the palm onto the surface (no gap under the hand)
                pc0 = Hw @ self.ch.hand[s]["palm_center_l"]
                loc, nrm, _, dist = tree.find_nearest(pc0)
                want = self.ch.hand[s]["palm_thick"] + 0.004
                if loc is not None and dist > want + 0.003:
                    lo, hi = 0.0, min(0.05, dist - want)
                    for _ in range(10):
                        mid = (lo + hi) / 2
                        if self._penetration(s, Matrix.Translation(pn * mid) @ Hw, tree) < 0.0015: lo = mid
                        else: hi = mid
                    if lo > 0.002:
                        total += pn * lo; Hw = Matrix.Translation(pn * lo) @ Hw
                        log(f"grip {ev['prop']}: palm moved {lo * 1000:.0f} mm onto the surface")
            if total.length > 0:
                self.hcorr[s].key(ev["t_open"] - 0.2, Vector(), "hold").key(ev["t_open"], total, "smooth")
                ev["wrist"] = ev["wrist"] + total
                changed = True
            solved = self._solve_fingers(s, Hw, tree, gt)
            opp, abd, tcurl, tgap = self._solve_thumb(s, Hw, tree, ev["grip"], solved)
            solved["thumb"] = tcurl
            ev["solved"] = solved; ev["opp"] = opp; ev["abd"] = abd
            t_open, t_close = ev["t_open"], ev["t_close"]
            rel = next((e["t"] for e in self.events if e["type"] == "detach" and e["side"] == s and e["t"] > t_close), None)
            hold_end = rel if rel else frames[-1] / self.fps + 1
            for fname, c in solved.items():
                tr = self.curl[s][fname]
                tr.keys = [k for k in tr.keys if k[0] <= t_open or k[0] >= hold_end]
                tr.key(t_close, c, "out")
                if rel:
                    tr.key(rel - 1e-3, c, "hold")
            for tr, val in ((self.topp[s], opp), (self.tabd[s], abd)):
                tr.keys = [k for k in tr.keys if k[0] <= t_open or k[0] >= hold_end]
                tr.key(t_close, val, "out")
                if rel:
                    tr.key(rel - 1e-3, val, "hold").key(rel + 0.35, 0.0, "smooth")
            # what actually touches -> contact points (bite/drink logic) + stability check
            Mw = self.ch.rig.matrix_world
            pts = self._finger_points(s, Mw.inverted() @ Hw, self.ch.finger_quats(s, solved, 0.0, opp, abd))
            gaps, cpts = {}, []
            for fname, pl in pts.items():
                best = min(((tree.find_nearest(Mw @ p)[3] or 1.0) - FRAD[fname], Mw @ p) for p in pl[3:])
                gaps[fname] = best[0]
                if best[0] < 0.004: cpts.append(best[1])
            if gt in ("wrap_top", "wrap_side"):
                cpts.append(Hw @ self.ch.hand[s]["palm_center_l"])
            ev["contacts"] = cpts
            touch = [f for f, gp in gaps.items() if gp < 0.004]
            fingers_on = [f for f in ("index", "middle", "ring", "pinky") if f in touch]
            if "thumb" not in touch or not fingers_on:
                miss = "thumb" if "thumb" not in touch else "fingers"
                self.qa_notes.append(f"grip {ev['prop']}: {miss} not touching (gap {1000 * (gaps.get('thumb', 0) if miss == 'thumb' else min(gaps.values())):.0f} mm) -> "
                                     f"would slip; set grip: span|pinch|wrap_top|wrap_side or size_cm")
            log(f"grip {ev['prop']} ({gt}, {s}): " + " ".join(f"{k[:2]}={v:.2f}" for k, v in solved.items())
                + f" opp={opp:.2f} abd={abd:.2f} thumb_gap={tgap * 1000:.0f}mm touching={'+'.join(touch) or 'none'}")
        # write updated grips so the attach offset below is measured on the real (solved) hand
        self._rebake_hands(kw, frames); kw.write()
        # exact hand-to-mouth: bite point from the REAL attach offset, mouth from the REAL evaluated head
        head = self.ch.bone("head")
        from .rigkit import _basis
        for ev in [e for e in self.events if e["type"] == "bite"]:
            s = ev["side"]; g = self._held(s); ob = self.props[ev["prop"]]
            sc.frame_set(round(g["t_close"] * self.fps))
            Hc = self._hand_world(s)
            O = Hc.inverted() @ ob.matrix_world  # prop in hand space
            fing_l = self.ch.hand[s]["fing_l"]
            pts = [O @ v.co for v in list(ob.data.vertices)[:: max(1, len(ob.data.vertices) // 500)]]
            Hci = Hc.inverted()
            bl = self._free_point(pts, [Hci @ c for c in g.get("contacts", [])], fing_l)  # free part -> into the mouth
            pl = self.ch.hand[s]["palm_l"]

            def mouth_at(t):
                sc.frame_set(round(t * self.fps))
                ph = self.ch.rig.pose.bones[head.name]
                Mh = self.ch.rig.matrix_world @ ph.matrix @ head.matrix_local.inverted()
                return Mh @ self.ch.mouth, (Mh.to_3x3() @ self.ch.fwd).normalized()
            up = Vector((0, 0, 1))
            m_c, f_c = mouth_at(ev["t_contact"])
            sg = 1 if s == "L" else -1
            inward = f_c.cross(up).normalized() * sg   # f x up = actor's right; R hand (sg -1) -> toward the midline
            grip = next((e.get("grip", {}) for e in reversed(self.events) if e["type"] == "grip" and e["side"] == s
                         and e["prop"] == ev["prop"]), {})
            contact = m_c + f_c * (self.ch.lip_front - 0.008)
            qm = self._mouth_rot_natural(s, ev["t_contact"], bl, pl, f_c, inward, grip.get("type"), contact)
            for _ in range(2):  # wrist inside its range; the bite point still lands exactly on the lips
                qm = self._wrist_limit(s, ev["t_contact"], contact - qm @ bl, qm)
            w_c = contact - qm @ bl
            m_p, f_p = mouth_at(ev["t_pre"])
            w_p = (m_p + f_p * (self.ch.lip_front + 0.03)) - qm @ bl - up * 0.008
            self.hw[s].set_at(ev["t_pre"], w_p); self.hq[s].set_at(ev["t_pre"], qm)
            if ev.get("t_pre2"):
                self.hw[s].set_at(ev["t_pre2"], w_p)
            self.hw[s].set_at(ev["t_contact"], w_c); self.hw[s].set_at(ev["t_hold"], w_c)
            self.hw[s].set_at(ev["t_away"], w_c + f_c * 0.14 - up * 0.16)
            ev["mouth"] = contact
            log(f"bite: exact mouth contact solved (lips {self.ch.lip_front*1000:.0f} mm ahead of mouth joint)")
            changed = True
        # exact cup-to-lips: rim from the REAL grip, lips from the REAL evaluated head (look constraint included)
        for ev in [e for e in self.events if e["type"] == "drink"]:
            s = ev["side"]; g = self._held(s); ob = self.props[ev["prop"]]
            sc.frame_set(round(g["t_close"] * self.fps))
            Hc = self._hand_world(s); O = Hc.inverted() @ ob.matrix_world
            mn, mx = core.world_bbox(ob)
            rim = [O @ v.co for v in ob.data.vertices if (ob.matrix_world @ v.co).z > mx.z - 0.006]
            if not rim:
                continue
            up = Vector((0, 0, 1)); q_hold = Quaternion(up, math.radians(ev.get("psi", 0.0))) @ Hc.to_quaternion()
            for tk, ang, back in ev["keys"]:
                m, fc = self._mouth_eval(tk)
                qd = Quaternion(fc.cross(up).normalized(), ang) @ q_hold
                rp = max(rim, key=lambda p: (qd @ p).dot(-fc))
                lip = m - up * 0.011 + fc * (self.ch.lip_front * 0.55 + back)
                for _ in range(2):  # anatomical wrist, rim still on the lip
                    qd = self._wrist_limit(s, tk, lip - qd @ rp, qd)
                    rp = max(rim, key=lambda p: (qd @ p).dot(-fc))
                self.hw[s].set_at(tk, lip - qd @ rp); self.hq[s].set_at(tk, qd)
            log(f"drink {ev['prop']}: rim placed on the lower lip (exact)")
            changed = True
        if changed or True:
            self._rebake_hands(kw, frames)
        import os as _os
        if _os.environ.get("S3D_DEBUG_KEYS"):
            a_, b_ = (float(x) for x in _os.environ["S3D_DEBUG_KEYS"].split(","))
            for nm in ("hw", "hs", "hb", "hcorr", "ha"):
                for sd in "LR":
                    ks = [(round(k[0], 3), tuple(round(x, 2) for x in k[1]) if hasattr(k[1], "__len__") else round(k[1], 2), k[2])
                          for k in getattr(self, nm)[sd].keys if a_ <= k[0] <= b_]
                    if ks:
                        log(f"KEYS {nm}.{sd}: {ks}")

    def _mouth_eval(self, t):
        """Real (evaluated) mouth position + facing at time t - includes the look-at constraint on the head."""
        bpy.context.scene.frame_set(round(t * self.fps))
        head = self.ch.bone("head"); ph = self.ch.rig.pose.bones[head.name]
        Mh = self.ch.rig.matrix_world @ ph.matrix @ head.matrix_local.inverted()
        return Mh @ self.ch.mouth, (Mh.to_3x3() @ self.ch.fwd).normalized()

    def _hand_world(self, s):
        pbn = self.ch.rig.pose.bones[self.ch.b[f"hand.{s}"]]
        return self.ch.rig.matrix_world @ pbn.matrix

    def _finger_points(self, s, Hw_arm, quats):
        """Analytic FK of the finger chains from the hand's armature-space matrix."""
        ch = self.ch; out = {}
        hb = ch.bone(f"hand.{s}")
        for f in acting_fingers():
            pts = []; parent_P, parent_B = Hw_arm, hb.matrix_local
            for n in (1, 2, 3):
                bn = ch.b.get(f"{f}{n}.{s}")
                if not bn:
                    continue
                b = ch.rig.data.bones[bn]
                Lq = quats.get(bn, Quaternion()).to_matrix().to_4x4()
                Pm = parent_P @ (parent_B.inverted() @ b.matrix_local) @ Lq
                head = Pm.translation; tail = Pm @ Vector((0, b.length, 0))
                pts += [head, (head + tail) / 2, tail]
                parent_P, parent_B = Pm, b.matrix_local
            out[f] = pts
        return out

    def _finger_intrusion(self, s, Hw, tree):
        """Deepest intrusion of the relaxed middle/ring/pinky fingers (planning check, thumb excluded)."""
        ch = self.ch; Mw = ch.rig.matrix_world
        pts = self._finger_points(s, Mw.inverted() @ Hw, ch.finger_quats(s, {"middle": 0.3, "ring": 0.35, "pinky": 0.4}))
        deep = 0.0
        for f in ("middle", "ring", "pinky"):
            for p in pts.get(f, [])[3:]:
                pw = Mw @ p
                loc, nrm, _, dist = tree.find_nearest(pw)
                if loc is not None and (pw - loc).dot(nrm) < 0:
                    deep = max(deep, dist)
        return deep

    def _penetration(self, s, Hw, tree, curls=None, palm_only=False):
        """Deepest intrusion (m) of palm/finger segments into the prop at the given hand pose."""
        ch = self.ch; Mw = ch.rig.matrix_world
        deep = 0.0
        pc = Hw @ ch.hand[s]["palm_center_l"]
        cand = [(pc, 0.012)]
        if not palm_only:
            pts = self._finger_points(s, Mw.inverted() @ Hw, ch.finger_quats(s, curls or OPEN))
            cand += [(Mw @ p, FRAD[f]) for f, pl in pts.items() for p in pl[2:]]
        for pw, rad in cand:
            loc, nrm, _, dist = tree.find_nearest(pw)
            if loc is None:
                continue
            if (pw - loc).dot(nrm) < 0 and dist < 0.05:
                deep = max(deep, dist + rad)
            elif dist < rad * 0.6:
                deep = max(deep, rad * 0.6 - dist)
        return deep

    def _solve_fingers(self, s, Hw, tree, gtype):
        """Close each finger until it touches the prop (no tunnelling: small steps + bisection). Precision grips
        (span/pinch) close only the working fingers; the others relax and just avoid the object."""
        ch = self.ch; Mw = ch.rig.matrix_world
        H_arm = Mw.inverted() @ Hw
        res = {}
        relaxed = {"span": {"ring": .45, "pinky": .55}, "pinch": {"middle": .5, "ring": .6, "pinky": .68}}.get(gtype, {})
        for f in ("index", "middle", "ring", "pinky"):
            def contact(c):
                pts = self._finger_points(s, H_arm, ch.finger_quats(s, {f: c}))[f]
                for p in pts[3:]:
                    pw = Mw @ p
                    loc, nrm, _, dist = tree.find_nearest(pw)
                    if loc is None:
                        continue
                    if dist < FRAD[f] or ((pw - loc).dot(nrm) < 0 and dist < 0.03):
                        return True
                return False
            maxc = relaxed.get(f, 1.0)
            if contact(0.0):
                res[f] = 0.0; continue
            prev, hit, c = 0.0, None, 0.05
            while c <= maxc + 1e-6:
                if contact(c):
                    hit = c; break
                prev = c; c += 0.05
            if hit is None:
                res[f] = maxc if f in relaxed else 0.85
                continue
            lo, hi = prev, hit
            for _ in range(6):
                mid = (lo + hi) / 2
                if contact(mid): hi = mid
                else: lo = mid
            res[f] = lo
        return res

    def _solve_thumb(self, s, Hw, tree, g, solved):
        """Opposition: swing + curl the thumb so its pad lands on the far side of the object from the fingers
        (span/pinch: the planned near-face point; wraps: the antipodal point of the finger contacts).
        Returns (opposition, curl, gap_m)."""
        ch = self.ch; Mw = ch.rig.matrix_world; H_arm = Mw.inverted() @ Hw
        if not ch.b.get(f"thumb3.{s}") or not ch.hand[s].get("opp"):
            return 0.0, 0.0, 0.3, 1.0
        if g["type"] in ("span", "pinch") and g.get("thumb_target") is not None:
            T = Vector(g["thumb_target"])
        else:
            pts = self._finger_points(s, H_arm, ch.finger_quats(s, solved))
            tips = [Mw @ pts[f][-1] for f in ("index", "middle", "ring") if pts.get(f)]
            cen = Vector(g["center"])
            d = cen - sum(tips, Vector()) / max(1, len(tips))
            d = d.normalized() if d.length > 1e-6 else -(Hw.to_3x3() @ ch.hand[s]["fing_l"]).normalized()
            hit = tree.ray_cast(cen + d * 0.4, -d)
            T = hit[0] if hit[0] is not None else cen + d * 0.03
        loc, nrm, _, _ = tree.find_nearest(T)
        Tpad = (loc + nrm * FRAD["thumb"]) if loc is not None else T
        opp_ = None
        if g["type"] != "pinch":  # span + wraps: anywhere on the side facing away from the fingers
            pts = self._finger_points(s, H_arm, ch.finger_quats(s, solved))
            tips = [Mw @ pts[f][-1] for f in ("index", "middle", "ring") if pts.get(f)]
            opp_ = (Vector(g["center"]), sum(tips, Vector()) / max(1, len(tips)))
        f_ = lambda a, o, c: self._thumb_pad(s, H_arm, tree, a, o, c, Tpad, opp_)[0]
        best = min((f_(a / 5, o / 5, c / 20), a / 5, o / 5, c / 20) for a in range(0, 6) for o in range(-2, 7) for c in range(-3, 21, 3))
        v, a, o, c = best; step = 0.08
        lim = ((0.0, 1.0), (-0.6, 1.0), (-0.15, 1.0))
        for _ in range(16):
            moved = False
            for i in range(3):
                for sg in (1, -1):
                    x = [a, o, c]; x[i] = clamp(x[i] + sg * step, *lim[i])
                    v2 = f_(*x)
                    if v2 < v - 1e-6:
                        v, (a, o, c), moved = v2, x, True
            if not moved:
                step *= 0.5
        pad = self._thumb_pad(s, H_arm, tree, a, o, c, Tpad, opp_)[1]
        gap = (tree.find_nearest(pad)[3] or 1.0) - FRAD["thumb"]
        return o, a, c, gap

    def _rebake_hands(self, kw, frames):
        ch = self.ch
        for f in frames:
            t = f / self.fps
            for s in "LR":
                w, hq = self.hand_target(s, t)
                kw.put(ch.ctrl[f"hand.{s}"], "location", f, w)
                kw.put(ch.ctrl[f"hand.{s}"], "rotation_quaternion", f, hq)
                kw.put(ch.ctrl[f"pole_arm.{s}"], "location", f, self._elbow_pole(s, t, w, hq))
        self._bake_fingers(kw, frames)

    # ------------------------------------------------------------ pass C: attachments + bites
    def bake_attachments(self, kw, frames, coll):
        sc = bpy.context.scene
        ivs = []
        for ev in self.events:
            if ev["type"] == "attach":
                det = next((e for e in self.events if e["type"] == "detach" and e["prop"] == ev["prop"] and e["t"] > ev["t"]), None)
                ivs.append((ev["prop"], ev["side"], round(ev["t"] * self.fps), round(det["t"] * self.fps) if det else frames[-1],
                            (det or {}).get("on")))
        for prop, s, fa, fd, on in ivs:
            settle = 0.0
            ob = self.props[prop]
            ob.rotation_mode = "QUATERNION"
            sc.frame_set(fa)
            rest = ob.matrix_world.copy()
            O = self._hand_world(s).inverted() @ rest
            for f in frames:
                if f < fa:
                    continue
                if f <= fd:
                    sc.frame_set(f)
                    Mf = self._hand_world(s) @ O
                    last = Mf
                    if f == fd and on and on in self.props:  # settle: rest exactly on the support after release
                        vs = list(ob.data.vertices)[:: max(1, len(ob.data.vertices) // 300)]
                        low = min(vs, key=lambda v: (Mf @ v.co).z); lp = Mf @ low.co
                        top = P.top_z(self.props[on], lp.x, lp.y)
                        settle = (top - lp.z) if top is not None and abs(top - lp.z) < 0.06 else 0.0
                        log(f"place {prop}: lowest point {lp.z:.3f}, support top {top}, settle {settle * 1000:.0f} mm")
                else:
                    k = smooth(min(1.0, (f - fd) / 3.0)) if on else 0.0
                    Mf = Matrix.Translation((0, 0, settle * k)) @ last if on else last
                loc, rot, _ = Mf.decompose()
                kw.put(ob, "location", f, loc); kw.put(ob, "rotation_quaternion", f, rot)
            for f in frames:
                if f < fa:
                    loc, rot, _ = rest.decompose()
                    kw.put(ob, "location", f, loc); kw.put(ob, "rotation_quaternion", f, rot)
        kw.write()
        # bites: bitten copy parented to the prop, visibility switched at the bite frame
        n = 0
        for ev in [e for e in self.events if e["type"] == "bite"]:
            ob = self.props[ev["prop"]]
            fb = round(ev["t"] * self.fps)
            sc.frame_set(fb)
            src = self.props.get(ev["prop"] + "__bitten", ob)
            n += 1
            new = P.bite_copy(src, ev.get("mouth") or self.mouth_world(ev["t"]), fb, coll, idx=n)
            new.animation_data_clear()
            new.parent = ob; new.matrix_parent_inverse = Matrix.Identity(4)
            new.location = (0, 0, 0); new.rotation_mode = "QUATERNION"; new.rotation_quaternion = (1, 0, 0, 0); new.scale = (1, 1, 1)
            if src is not ob:
                inv = ob.matrix_world.inverted() @ src.matrix_world
            for o, before, after in ((src, False, True), (new, True, False)):
                for fr, hid in ((frames[0], before), (fb - 1, before), (fb, after), (frames[-1], after)):
                    o.hide_render = hid; o.keyframe_insert("hide_render", frame=fr)
                    o.hide_viewport = hid; o.keyframe_insert("hide_viewport", frame=fr)
            self.props[ev["prop"] + "__bitten"] = new
            log(f"bite mark {n} on {ev['prop']} at frame {fb}")


def acting_fingers():
    return ("thumb", "index", "middle", "ring", "pinky")
