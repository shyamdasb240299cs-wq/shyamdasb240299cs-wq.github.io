"""Performance engine: verbs -> time tracks -> baked keys, with contact-accurate grasping, eating and walking.

Pass A  bake body: root, hips, spine, feet (planted steps), hand IK targets, look/gaze, face.
Pass B  contacts: solve finger curls until each finger touches the prop surface (no interpenetration);
        measure real mouth position and correct hand targets so food meets the lips exactly.
Pass C  attachments: props follow the actual hand bone between grasp and release; bite marks appear on contact.
"""
import bpy, math
from mathutils import Vector, Quaternion, Matrix
from . import core, acting, props as P
from .core import Track, log, minjerk, smooth, clamp

RELAX = {"thumb": .15, "index": .2, "middle": .27, "ring": .33, "pinky": .4}
FIST = {"thumb": .6, "index": .95, "middle": 1.0, "ring": 1.0, "pinky": 1.0}
OPEN = {"thumb": .02, "index": .02, "middle": .03, "ring": .04, "pinky": .05}
FRAD = {"thumb": .0095, "index": .0085, "middle": .0088, "ring": .0082, "pinky": .0072}


def Rz(a):
    return Quaternion((0, 0, 1), a)


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
        self.curl = {s: {f: Track(v) for f, v in RELAX.items()} for s in "LR"}
        self.spread = {s: Track(0.0) for s in "LR"}
        eye = self._to_world(ch.eye_mid, start, yaw)
        ahead = eye + Rz(yaw) @ Vector((0, -2.5, 0))
        self.look = Track(ahead).key(0, ahead, "hold")
        self.gaze = Track(ahead).key(0, ahead, "hold")
        self.face = acting.FaceTracks(seed)
        self.events = []
        self.holding = {"L": None, "R": None}
        self.noise = core.Noise1D(seed)

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

    def chain_deltas(self, lean, twist=0.0, back=0.0, nod=0.0, tilt=0.0, headyaw=0.0, shrug=0.0, pyaw=0.0, breath=0.0):
        """Armature-space total rotation of every torso bone - the SAME model the bake writes."""
        ch = self.ch; D = {}
        hips = ch.b["hips"]
        D[hips] = Quaternion((0, 0, 1), pyaw) @ Quaternion((1, 0, 0), back * 0.3)
        nsp = max(1, len(ch.spine)); top = D[hips]
        tw = twist - pyaw
        for i, bn in enumerate(ch.spine):
            fr = (i + 1) / nsp
            D[bn] = Quaternion((1, 0, 0), lean * fr + back * fr + breath * 2 * (i == nsp - 1)) @ Quaternion((0, 0, 1), tw * fr)
            top = D[bn]
        for bn, k in ((ch.b.get("neck"), 0.4), (ch.b.get("head"), 1.0)):
            if bn:
                D[bn] = Quaternion((1, 0, 0), lean + back + nod * k) @ Quaternion((0, 0, 1), tw + headyaw * k) @ Quaternion((0, 1, 0), tilt * k)
        for s_ in "LR":
            cb = ch.b.get(f"shoulder.{s_}")
            if cb:
                g = 1 if s_ == "L" else -1
                D[cb] = Quaternion((0, 1, 0), -g * shrug) @ top
        return D

    def posed_point(self, D, bone_name, p_arm, crouch=0.0):
        """Forward kinematics of a point attached to bone_name through the torso chain (armature space)."""
        bones = self.ch.rig.data.bones
        chain = []
        b = bones[bone_name]
        while b is not None:
            chain.append(b); b = b.parent
        chain.reverse()
        hp = chain[0].head_local + Vector((0, 0, -crouch))
        for i in range(1, len(chain)):
            par = chain[i - 1]
            hp = hp + D.get(par.name, Quaternion()) @ (chain[i].head_local - par.head_local)
        last = chain[-1]
        return hp + D.get(last.name, Quaternion()) @ (Vector(p_arm) - last.head_local)

    def deltas_at(self, t):
        return self.chain_deltas(self.lean.at(t), self.twist.at(t), self.back.at(t), self.nod.at(t), self.tilt.at(t),
                                 self.headyaw.at(t), self.shrug.at(t))

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
        w = Vector((sh.x + sg * 0.05, sh.y - 0.015, sh.z - ch.arm_len * 0.9))
        D = self.deltas_at(t)
        cb = ch.b.get(f"shoulder.{s}") or ch.spine[-1]
        shp = self.posed_point(D, cb, sh, self.crouch.at(t))
        w = shp + (w - sh)  # arms hang under the (possibly leaning) shoulder
        q = ch.hand_world_rot(s, Vector((-sg, 0.25, 0.05)), Vector((0, -0.12, -1)))
        return w, q

    def hand_target(self, s, t):
        """World wrist location + world hand rotation for frame time t."""
        w_rest, q_rest = self.rest_hand(s, t)
        w_rest = w_rest + self.hb[s].at(t) + self._swing(s, t)
        bw = self.hbw[s].at(t)
        if bw > 0:
            q_rest = q_rest.slerp(self.hbq[s].at(t), bw)
        M = self.M(t); Mq = M.to_quaternion()
        wr, qr = M @ w_rest, Mq @ q_rest
        k = self.hs[s].at(t)
        if k <= 0:
            return wr + self.ha[s].at(t), qr
        w = wr.lerp(self.hw[s].at(t) + self.hcorr[s].at(t), k) + self.ha[s].at(t)
        return w, qr.slerp(self.hq[s].at(t), k)

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
        return t

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
        return self._turn(t0, self._yaw_to(self._target_point(a["target"]), t0))

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
        g = P.grip(ob, sh, a.get("grip", "auto")) if ob else {
            "palm_point": p, "palm_n": (p - sh).normalized(), "fingers": (p - sh).normalized(), "approach": (p - sh).normalized(), "type": "point", "width": 0.05}
        q = self.ch.hand_world_rot(s, g["palm_n"], g["fingers"])
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
        lean, crouch = self._reach_posture(s, t0 + T, wg)
        self.lean.key(t0, self.lean.at(t0), "hold").key(t0 + T, lean, "mj")
        self.crouch.key(t0, self.crouch.at(t0), "hold").key(t0 + T, crouch, "mj")
        self._pending = {"side": s, "grip": g, "wg": wg, "q": q, "pre": pre, "prop": tgt}
        return t0 + T

    def _reach_posture(self, s, t, wrist):
        """Smallest torso lean (then knee bend) that brings the wrist within comfortable arm reach."""
        ch = self.ch; M = self.M(t); Mi = M.inverted()
        wl = Mi @ Vector(wrist)
        sh0 = ch.arm_head(f"upperarm.{s}")
        cb = ch.b.get(f"shoulder.{s}") or ch.spine[-1]
        lim = ch.arm_len * 0.92
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
        best, bd = None, -1e9
        fing_l = self.ch.hand[s]["fing_l"]
        for v in ob.data.vertices[:: max(1, len(ob.data.vertices) // 400)]:
            pw = ob.matrix_world @ v.co
            pl = inv @ (pw - w)
            score = pl.dot(fing_l) + 0.3 * pl.length
            if score > bd:
                bd, best = score, pl
        return best

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
            qm = _basis(bl.normalized(), (-f + up * 0.25).normalized(), pl, (up * 0.6 + inward * 0.8).normalized())
            tm = t0 + T
            mouth = self.mouth_world(tm)
            contact = mouth + f * 0.006
            w_contact = contact - qm @ bl
            w_pre = w_contact + f * 0.03 - up * 0.01
            self.hw[s].key(t0, self.hw[s].at(t0), "hold").key(tm, w_pre, "mj")
            self.hq[s].key(t0, self.hq[s].at(t0), "hold").key(tm, qm, "mj")
            self.ha[s].key(t0, Vector(), "hold").key(t0 + T * 0.5, Vector((0, 0, 0.04)), "smooth").key(tm, Vector(), "smooth")
            self._mouth_plan = {"side": s, "w_contact": w_contact, "q": qm, "bl": bl, "t_pre": tm, "t_lift0": t0}
            ahead = self.mouth_world(t0) + f * 1.5
            self.look.key(t0, self.look.at(t0), "hold").key(t0 + 0.4, ahead, "mj")
            self.gaze.key(t0 + 0.05, self.gaze.at(t0), "hold").key(t0 + 0.2, mouth + f * 0.2 - up * 0.05, "smooth")
            self.lean.key(t0, self.lean.at(t0), "hold").key(tm, 0.06, "mj")
            self.crouch.key(t0, self.crouch.at(t0), "hold").key(tm, 0.0, "mj")
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

    def v_bite(self, t0, a):
        mp = getattr(self, "_mouth_plan", None)
        if not mp:
            t0 = self.v_lift(t0, {"to": "mouth", "hand": a.get("hand")}); mp = self._mouth_plan
        s = mp["side"]; f = self.fwd(t0); up = Vector((0, 0, 1))
        tc = t0 + 0.32
        self.hw[s].key(t0 + 0.08, self.hw[s].at(t0 + 0.08), "hold").key(tc, mp["w_contact"], "out")
        self.nod.key(t0, self.nod.at(t0), "hold").key(tc, math.radians(5), "smooth").key(tc + 0.35, 0.0, "smooth")
        self.face.bite(tc)
        self.face.no_blink.append((t0, tc + 0.2))
        self.events.append({"type": "bite", "side": s, "prop": self.holding[s], "t": tc + 0.09, "t_contact": tc,
                            "t_pre": mp.get("t_pre"), "t_hold": tc + 0.12, "t_away": tc + 0.6})
        away = mp["w_contact"] + f * 0.14 - up * 0.16   # food comes down to chest height while chewing
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
        xy = a.get("at")
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
        return self.v_release(t0 + T, {"hand": s})

    def v_release(self, t0, a):
        s = (a.get("hand") or ("R" if self.holding["R"] else "L"))[0].upper()
        if self.holding[s]:
            self.events.append({"type": "detach", "side": s, "prop": self.holding[s], "t": t0 + 0.05, "drop": a.get("drop", False)})
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
    def _gesture(self, s, t0, T_in, hold, wrist_arm_off, palm, fingers, curls, T_out=0.5, osc=None):
        """Move hand s to a body-space pose (offset from shoulder, armature space), hold, return."""
        ch = self.ch; sh = ch.arm_head(f"upperarm.{s}")
        w_rest, _ = self.rest_hand(s, t0)
        target = sh + Vector(wrist_arm_off)
        q = ch.hand_world_rot(s, Vector(palm), Vector(fingers))
        t1, t2 = t0 + T_in, t0 + T_in + hold
        self.hb[s].key(t0, self.hb[s].at(t0), "hold").key(t1, target - w_rest, "mj").key(t2, target - w_rest, "hold")
        self.hbq[s].key(t1, q, "hold"); self.hbw[s].key(t0, self.hbw[s].at(t0), "hold").key(t1, 1.0, "mj").key(t2, 1.0, "hold")
        if osc:
            amp, hz, axis = osc
            k = 0; tt = t1
            while tt < t2:
                self.hb[s].key(tt, target - w_rest + Vector(axis) * amp * (1 if k % 2 else -1), "smooth")
                tt += 0.5 / hz; k += 1
            self.hb[s].key(t2, target - w_rest, "smooth")
        for f, c in curls.items():
            self.curl[s][f].key(t0, self.curl[s][f].at(t0), "hold").key(t1, c, "smooth").key(t2, c, "hold")
        if T_out:
            self.hb[s].key(t2 + T_out, Vector(), "mj"); self.hbw[s].key(t2 + T_out, 0.0, "mj")
            for f, c in RELAX.items():
                self.curl[s][f].key(t2 + T_out, c, "smooth")
        return t2 + (T_out or 0)

    def _sg(self, s):
        return 1 if s == "L" else -1

    def v_point(self, t0, a):
        s = (a.get("hand") or "R")[0].upper()
        p = self._target_point(a["target"])
        loc = self.M(t0).inverted() @ p
        sh = self.ch.arm_head(f"upperarm.{s}")
        d = (loc - sh).normalized()
        self._look_to(t0, p)
        return self._gesture(s, t0, 0.45, float(a.get("hold", 1.2)), d * self.ch.arm_len * 0.85,
                             Vector((0, 0, -1)).cross(d).cross(d) * -1 + Vector((0, 0, -0.3)), d,
                             {"thumb": .55, "index": .0, "middle": .95, "ring": 1, "pinky": 1})

    def v_wave(self, t0, a):
        s = (a.get("hand") or "R")[0].upper(); g = self._sg(s)
        self.face.expr("happy", t0, t0 + float(a.get("hold", 1.4)) + 0.4)
        return self._gesture(s, t0, 0.4, float(a.get("hold", 1.4)), (g * 0.28, -0.1, 0.22), (0, -1, 0), (0, 0, 1),
                             OPEN, osc=(0.06, 2.2, (g, 0, 0)))

    def v_thumbs_up(self, t0, a):
        s = (a.get("hand") or "R")[0].upper(); g = self._sg(s)
        self.face.expr("happy", t0, t0 + 1.6)
        return self._gesture(s, t0, 0.4, float(a.get("hold", 1.2)), (g * 0.1, -0.38, -0.3), (-g, 0, 0), (0, -1, 0.1),
                             {"thumb": 0.0, "index": 1, "middle": 1, "ring": 1, "pinky": 1})

    def v_shrug(self, t0, a):
        hold = float(a.get("hold", 0.9))
        self.shrug.key(t0, 0.0, "hold").key(t0 + 0.3, math.radians(10), "smooth").key(t0 + 0.3 + hold, math.radians(10), "hold").key(t0 + 0.8 + hold, 0, "smooth")
        self.tilt.key(t0, 0.0, "hold").key(t0 + 0.3, math.radians(8), "smooth").key(t0 + 0.8 + hold, 0, "smooth")
        self.face.expr(a.get("expr", "confused"), t0, t0 + 0.3 + hold)
        for s in "LR":
            g = self._sg(s)
            t = self._gesture(s, t0, 0.35, hold, (g * 0.3, -0.26, -0.42), (0, 0, 1), (g * 0.6, -0.8, 0), OPEN)
        return t

    def v_facepalm(self, t0, a):
        s = (a.get("hand") or "R")[0].upper()
        fh = self.ch.eye_mid + Vector((0, -0.07, 0.05)); sh = self.ch.arm_head(f"upperarm.{s}")
        self.nod.key(t0, 0, "hold").key(t0 + 0.4, math.radians(14), "smooth").key(t0 + 1.6, math.radians(14), "hold").key(t0 + 2.0, 0, "smooth")
        self.face.expr("embarrassed", t0, t0 + 1.8)
        return self._gesture(s, t0, 0.45, float(a.get("hold", 1.1)), fh - sh, (0, 1, 0), (0, 0, 1), RELAX)

    def v_hands_on_hips(self, t0, a):
        hold = float(a.get("hold", 1.5)); t = t0
        for s in "LR":
            g = self._sg(s)
            t = self._gesture(s, t0, 0.5, hold, (g * 0.07, 0.02, -0.5), (-g, 0, 0), (0, -0.3, -1), FIST)
        return t

    def v_cross_arms(self, t0, a):
        hold = float(a.get("hold", 1.5)); t = t0
        for s in "LR":
            g = self._sg(s)
            t = self._gesture(s, t0, 0.55, hold, (-g * 0.33, -0.2, -0.33 + (0.03 if s == "R" else 0)), (0, 1, 0), (-g, 0, 0), RELAX)
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
        q_yaw = Rz(yaw)
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
            off = Vector((sway + shiftw, 0, bob - self.crouch.at(t)))
            loc = H3i @ off
            kw.put(rig, f'pose.bones["{hips.name}"].location', f, loc)
            DD = self.chain_deltas(self.lean.at(t), self.twist.at(t), self.back.at(t), self.nod.at(t), self.tilt.at(t),
                                   self.headyaw.at(t), self.shrug.at(t), pyaw, breath)
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
                kw.put(ch.ctrl[f"pole_arm.{s}"], "location", f, self._elbow_pole(s, t, w))
            look = self.look.at(t)
            gz = self.gaze.at(t) + Vector((self.noise(t * 1.7 + 40) * 0.012, 0, self.noise(t * 1.3 + 80) * 0.008)) * (look - M.translation).length
            kw.put(ch.ctrl["look"], "location", f, look)
            kw.put(ch.ctrl["gaze"], "location", f, gz)
            for k in face_keys:
                v = fs.get(k, 0.0)
                for (skd, kname) in ch.face.get(k, []):
                    kw.put(skd, f'key_blocks["{kname}"].value', f, v)
        self._bake_fingers(kw, frames)

    def _spine_top(self, t):
        return Quaternion((1, 0, 0), self.lean.at(t) + self.back.at(t)) @ Quaternion((0, 0, 1), self.twist.at(t))

    def _elbow_pole(self, s, t, wrist):
        ch = self.ch; g = 1 if s == "L" else -1
        sh = ch.arm_head(f"upperarm.{s}")
        M = self.M(t)
        wl = M.inverted() @ wrist
        u = smooth((wl.z - (sh.z - 0.45)) / 0.45)
        return M @ Vector((sh.x + g * (0.2 + 0.35 * u), sh.y + 0.45 * (1 - u) + 0.05, sh.z - 0.3 - 0.15 * u))

    def _bake_fingers(self, kw, frames):
        rig = self.ch.rig
        for s in "LR":
            for f in frames:
                t = f / self.fps
                curls = {k: tr.at(t) for k, tr in self.curl[s].items()}
                for bn, q in self.ch.finger_quats(s, curls, self.spread[s].at(t)).items():
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
            for _ in range(5):
                depth = self._penetration(s, Hw, tree)
                if depth < 0.0015:
                    break
                fix = -pn * (depth + 0.002); total += fix
                Hw = Matrix.Translation(fix) @ Hw
            if total.length > 0:
                self.hcorr[s].key(ev["t_open"] - 0.2, Vector(), "hold").key(ev["t_open"], total, "smooth")
                ev["wrist"] = ev["wrist"] + total
                log(f"grip {ev['prop']}: hand backed off {total.length*1000:.0f} mm to avoid penetration")
                changed = True
            solved = self._solve_fingers(s, Hw, tree, ev["grip"]["type"])
            ev["solved"] = solved
            t_open, t_close = ev["t_open"], ev["t_close"]
            rel = next((e["t"] for e in self.events if e["type"] == "detach" and e["side"] == s and e["t"] > t_close), None)
            for fname, c in solved.items():
                tr = self.curl[s][fname]
                hold_end = rel if rel else frames[-1] / self.fps + 1
                tr.keys = [k for k in tr.keys if k[0] <= t_open or k[0] >= hold_end]
                tr.key(t_close, c, "out")
                if rel:
                    tr.key(rel - 1e-3, c, "hold")
            log(f"grip {ev['prop']} ({ev['grip']['type']}, {s}): " + " ".join(f"{k[:2]}={v:.2f}" for k, v in solved.items()))
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
            pts = [O @ v.co for v in ob.data.vertices[:: max(1, len(ob.data.vertices) // 500)]]
            bl = max(pts, key=lambda p: p.dot(fing_l) + 0.3 * p.length)  # far edge = goes into the mouth
            pl = self.ch.hand[s]["palm_l"]

            def mouth_at(t):
                sc.frame_set(round(t * self.fps))
                ph = self.ch.rig.pose.bones[head.name]
                Mh = self.ch.rig.matrix_world @ ph.matrix @ head.matrix_local.inverted()
                return Mh @ self.ch.mouth, (Mh.to_3x3() @ self.ch.fwd).normalized()
            up = Vector((0, 0, 1))
            m_c, f_c = mouth_at(ev["t_contact"])
            sg = 1 if s == "L" else -1
            inward = (f_c.cross(up)).normalized() * sg * -1
            qm = _basis(bl.normalized(), (-f_c + up * 0.2).normalized(), pl, (up * 0.6 + inward * 0.8).normalized())
            contact = m_c + f_c * (self.ch.lip_front - 0.008)
            w_c = contact - qm @ bl
            m_p, f_p = mouth_at(ev["t_pre"])
            w_p = (m_p + f_p * (self.ch.lip_front + 0.03)) - qm @ bl - up * 0.008
            self.hw[s].set_at(ev["t_pre"], w_p); self.hq[s].set_at(ev["t_pre"], qm)
            self.hw[s].set_at(ev["t_contact"], w_c); self.hw[s].set_at(ev["t_hold"], w_c)
            self.hw[s].set_at(ev["t_away"], w_c + f_c * 0.14 - up * 0.16)
            ev["mouth"] = contact
            log(f"bite: exact mouth contact solved (lips {self.ch.lip_front*1000:.0f} mm ahead of mouth joint)")
            changed = True
        if changed or True:
            self._rebake_hands(kw, frames)

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

    def _penetration(self, s, Hw, tree, curls=None):
        """Deepest intrusion (m) of palm/finger segments into the prop at the given hand pose."""
        ch = self.ch; Mw = ch.rig.matrix_world
        pts = self._finger_points(s, Mw.inverted() @ Hw, ch.finger_quats(s, curls or OPEN))
        deep = 0.0
        pc = Hw @ ch.hand[s]["palm_center_l"]
        cand = [(pc, 0.012)] + [(Mw @ p, FRAD[f]) for f, pl in pts.items() for p in pl[2:]]
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
        ch = self.ch; Mw = ch.rig.matrix_world
        H_arm = Mw.inverted() @ Hw
        res = {}
        maxc = {"pinch": .85, "pinch_edge": .95}.get(gtype, 1.0)
        for f in acting_fingers():
            def contact(c):
                q = ch.finger_quats(s, {f: c})
                pts = self._finger_points(s, H_arm, q)[f]
                for p in pts[3:]:
                    pw = Mw @ p
                    loc, nrm, _, dist = tree.find_nearest(pw)
                    if loc is None:
                        continue
                    if dist < FRAD[f] or ((pw - loc).dot(nrm) < 0 and dist < 0.03):
                        return True
                return False
            if gtype in ("pinch", "pinch_edge") and f in ("ring", "pinky"):
                res[f] = 0.5 if not contact(0.5) else 0.25; continue
            if contact(0.0):
                res[f] = 0.0; continue
            # sweep closed in small steps (no tunnelling through thin props), refine at first contact
            prev, hit = 0.0, None
            c = 0.05
            while c <= maxc + 1e-6:
                if contact(c):
                    hit = c; break
                prev = c; c += 0.05
            if hit is None:
                res[f] = maxc if f != "thumb" else min(maxc, 0.45)
                continue
            lo, hi = prev, hit
            for _ in range(6):
                mid = (lo + hi) / 2
                if contact(mid): hi = mid
                else: lo = mid
            res[f] = lo
        return res

    def _rebake_hands(self, kw, frames):
        ch = self.ch
        for f in frames:
            t = f / self.fps
            for s in "LR":
                w, hq = self.hand_target(s, t)
                kw.put(ch.ctrl[f"hand.{s}"], "location", f, w)
                kw.put(ch.ctrl[f"hand.{s}"], "rotation_quaternion", f, hq)
                kw.put(ch.ctrl[f"pole_arm.{s}"], "location", f, self._elbow_pole(s, t, w))
        self._bake_fingers(kw, frames)

    # ------------------------------------------------------------ pass C: attachments + bites
    def bake_attachments(self, kw, frames, coll):
        sc = bpy.context.scene
        ivs = []
        for ev in self.events:
            if ev["type"] == "attach":
                det = next((e for e in self.events if e["type"] == "detach" and e["prop"] == ev["prop"] and e["t"] > ev["t"]), None)
                ivs.append((ev["prop"], ev["side"], round(ev["t"] * self.fps), round(det["t"] * self.fps) if det else frames[-1]))
        for prop, s, fa, fd in ivs:
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
                else:
                    Mf = last
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
