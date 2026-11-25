"""Face acting: ARKit expression library, blinks, saccades, bite/chew/swallow, simple lip-sync."""
import math, random
from . import core

L, R = "Left", "Right"


def sym(**kw):
    """Expand 'mouthSmile=.7' into Left+Right ARKit keys."""
    out = {}
    for k, v in kw.items():
        if k in ("browInnerUp", "cheekPuff", "jawOpen", "jawForward", "mouthClose", "mouthFunnel", "mouthPucker",
                 "mouthRollLower", "mouthRollUpper", "mouthShrugLower", "mouthShrugUpper", "tongueOut",
                 "jawLeft", "jawRight", "mouthLeft", "mouthRight"):
            out[k] = v
        elif k.endswith(("Left", "Right")):
            out[k] = v
        else:
            out[k + L] = v; out[k + R] = v
    return out


# Readable, slightly exaggerated acting for vertical Shorts (faces are small on phones).
EXPR = {
    "neutral": {},
    "happy": sym(mouthSmile=.65, cheekSquint=.35, eyeSquint=.2, browInnerUp=.1),
    "big_smile": sym(mouthSmile=.9, jawOpen=.12, mouthUpperUp=.25, cheekSquint=.55, eyeSquint=.4, mouthDimple=.2),
    "delighted": sym(mouthSmile=.45, eyeSquint=.45, cheekSquint=.45, browInnerUp=.25, mouthPress=.1),
    "yum": sym(mouthSmile=.35, mouthPress=.35, eyeBlink=.55, browInnerUp=.35, cheekSquint=.3),
    "surprised": sym(browInnerUp=.8, browOuterUp=.75, eyeWide=.7, jawOpen=.3, mouthFunnel=.15),
    "shocked": sym(browInnerUp=1, browOuterUp=.9, eyeWide=1, jawOpen=.55, mouthStretch=.35, mouthFunnel=.1),
    "scared": sym(browInnerUp=.95, browOuterUp=.3, eyeWide=.85, mouthStretch=.6, jawOpen=.18, mouthFrown=.2),
    "disgust": sym(noseSneer=.8, mouthUpperUp=.55, browDown=.5, eyeSquint=.45, mouthFrown=.35, cheekSquint=.4) | {"mouthLeft": .15},
    "nauseous": sym(noseSneer=.5, mouthFrown=.5, browInnerUp=.5, eyeSquint=.3, cheekPuff=.0, mouthPress=.4) | {"cheekPuff": .45},
    "pain": sym(browDown=.85, eyeSquint=.8, eyeBlink=.35, noseSneer=.5, mouthStretch=.65, mouthUpperUp=.45, cheekSquint=.6, jawOpen=.08),
    "wince": sym(browDown=.6, eyeSquint=.9, eyeBlink=.5, noseSneer=.35, mouthStretch=.4, cheekSquint=.5),
    "sad": sym(browInnerUp=.75, mouthFrown=.6, mouthPress=.2, eyeLookDown=.25, mouthShrugLower=.0) | {"mouthShrugLower": .35},
    "angry": sym(browDown=.95, eyeSquint=.3, noseSneer=.35, mouthPress=.5, mouthFrown=.35) | {"jawForward": .15},
    "thinking": {"browInnerUp": .2, "browOuterUpLeft": .55, "mouthPressLeft": .3, "mouthPressRight": .3,
                 "mouthLeft": .25, "eyeLookUpLeft": .3, "eyeLookUpRight": .3, "eyeSquintRight": .2},
    "confused": {"browDownLeft": .55, "browOuterUpRight": .65, "mouthLeft": .3, "mouthPressLeft": .25, "jawLeft": .08},
    "smirk": {"mouthSmileLeft": .65, "mouthDimpleLeft": .35, "eyeSquintLeft": .3, "browDownRight": .2, "cheekSquintLeft": .3},
    "satisfied": sym(mouthSmile=.35, eyeBlink=.3, browInnerUp=.12, cheekSquint=.2),
    "determined": sym(browDown=.45, mouthPress=.55, eyeSquint=.2) | {"jawForward": .1},
    "embarrassed": sym(mouthSmile=.3, eyeLookDown=.5, browInnerUp=.45, mouthPress=.3),
    "exhausted": sym(eyeBlink=.45, browInnerUp=.35, mouthFrown=.2) | {"jawOpen": .12},
    "skeptical": {"browDownLeft": .5, "browOuterUpRight": .35, "eyeSquintLeft": .35, "mouthPressRight": .35, "mouthRight": .2},
    "relieved": sym(browInnerUp=.4, mouthSmile=.3, eyeBlink=.4) | {"jawOpen": .05},
    "excited": sym(mouthSmile=.85, jawOpen=.25, eyeWide=.4, browOuterUp=.5, browInnerUp=.3, cheekSquint=.4),
}
ALIASES = {"joy": "happy", "smile": "happy", "fear": "scared", "gross": "disgust", "hurt": "pain", "shock": "shocked",
           "wow": "surprised", "curious": "thinking", "mmm": "yum", "proud": "satisfied", "mad": "angry"}

# Grapheme -> ARKit mouth shapes for lightweight lip-sync (used when the actor talks on screen).
VIS = {
    "aa": {"jawOpen": .5, "mouthLowerDownLeft": .2, "mouthLowerDownRight": .2},
    "E": {"jawOpen": .25, "mouthStretchLeft": .35, "mouthStretchRight": .35},
    "I": {"jawOpen": .15, "mouthSmileLeft": .3, "mouthSmileRight": .3},
    "O": {"jawOpen": .3, "mouthFunnel": .6},
    "U": {"jawOpen": .12, "mouthPucker": .75},
    "PP": {"mouthClose": .2, "mouthPressLeft": .6, "mouthPressRight": .6},
    "FF": {"mouthRollLower": .55, "mouthUpperUpLeft": .2, "mouthUpperUpRight": .2, "jawOpen": .05},
    "TH": {"jawOpen": .15, "tongueOut": .25},
    "DD": {"jawOpen": .18, "mouthShrugUpper": .2},
    "kk": {"jawOpen": .22},
    "CH": {"jawOpen": .12, "mouthFunnel": .35, "mouthPucker": .2},
    "SS": {"jawOpen": .08, "mouthStretchLeft": .25, "mouthStretchRight": .25},
    "nn": {"jawOpen": .12},
    "RR": {"jawOpen": .15, "mouthPucker": .3},
    "sil": {},
}


def text_visemes(text):
    t = text.lower(); out = []; i = 0
    digr = {"th": "TH", "ch": "CH", "sh": "CH", "oo": "U", "ee": "I", "ou": "O", "ph": "FF"}
    single = {"a": "aa", "e": "E", "i": "I", "o": "O", "u": "U", "y": "I", "p": "PP", "b": "PP", "m": "PP",
              "f": "FF", "v": "FF", "t": "DD", "d": "DD", "l": "DD", "k": "kk", "g": "kk", "c": "kk", "q": "kk",
              "s": "SS", "z": "SS", "x": "SS", "n": "nn", "r": "RR", "w": "U", "j": "CH", "h": "kk"}
    while i < len(t):
        if t[i:i + 2] in digr:
            out.append(digr[t[i:i + 2]]); i += 2; continue
        c = t[i]
        if c in single:
            out.append(single[c])
        elif c in " ,.!?;:":
            out.append("sil")
        i += 1
    return out


class FaceTracks:
    """Layered face animation sampled per frame -> dict(arkit -> 0..1)."""
    def __init__(self, seed=1):
        self.layers = []   # (t0, t1, attack, release, weights)
        self.funcs = []    # (t0, t1, fn(t)->dict)
        self.rng = random.Random(seed)
        self.no_blink = []  # (t0,t1)

    def expr(self, name, t0, t1, intensity=1.0, attack=0.22, release=0.35):
        w = EXPR.get(ALIASES.get(name, name))
        if w is None:
            core.log("unknown expression", name, "-> neutral"); w = {}
        self.layers.append((t0, t1, attack, release, {k: v * intensity for k, v in w.items()}))

    def keys(self, weights, t0, t1, attack=0.15, release=0.2):
        self.layers.append((t0, t1, attack, release, dict(weights)))

    def func(self, t0, t1, fn):
        self.funcs.append((t0, t1, fn))

    def bite(self, t_contact, open_amt=0.45):
        """Mouth opens before the food arrives, snaps shut on contact."""
        to = t_contact - 0.32
        def f(t):
            if t < t_contact:
                u = core.smooth((t - to) / 0.3)
                return {"jawOpen": open_amt * u, "mouthFunnel": .15 * u, "browInnerUp": .15 * u}
            u = core.smooth((t - t_contact) / 0.11)
            return {"jawOpen": open_amt * (1 - u) + .05 * u, "mouthClose": .05 * u, "mouthPressLeft": .3 * u,
                    "mouthPressRight": .3 * u}
        self.func(to, t_contact + 0.13, f)

    def chew(self, t0, cycles=4, period=0.62):
        t1 = t0 + cycles * period
        def f(t):
            ph = (t - t0) / period * 2 * math.pi
            env = core.smooth((t - t0) / 0.15) * core.smooth((t1 - t) / 0.2)
            o = max(0.0, math.sin(ph)) * env
            side = math.sin(ph * 0.5) * env
            return {"jawOpen": .03 + .17 * o, "mouthClose": .15 * o, "mouthRollLower": .12 * o,
                    "cheekPuff": .1 * o, "jawLeft": max(0, side) * .12, "jawRight": max(0, -side) * .12,
                    "mouthPressLeft": .25 * env, "mouthPressRight": .25 * env}
        self.func(t0, t1, f)
        return t1

    def swallow(self, t0):
        self.keys({"mouthPressLeft": .5, "mouthPressRight": .5, "eyeSquintLeft": .2, "eyeSquintRight": .2,
                   "mouthRollLower": .2}, t0, t0 + 0.45, 0.1, 0.15)
        return t0 + 0.5

    def talk(self, t0, t1, text):
        vis = [v for v in text_visemes(text)] or ["sil"]
        dt = (t1 - t0) / len(vis)
        def f(t):
            i = min(len(vis) - 1, max(0, int((t - t0) / dt)))
            u = ((t - t0) - i * dt) / dt
            a = VIS[vis[i]]; b = VIS[vis[min(i + 1, len(vis) - 1)]]
            keys = set(a) | set(b)
            k = core.smooth(u)
            env = core.smooth((t - t0) / 0.08) * core.smooth((t1 - t) / 0.1)
            return {x: (a.get(x, 0) * (1 - k) + b.get(x, 0) * k) * 0.85 * env for x in keys}
        self.func(t0, t1, f)

    def auto_blinks(self, t0, t1):
        self.blinks = []
        t = t0 + self.rng.uniform(0.4, 1.8)
        while t < t1:
            if not any(a <= t <= b for a, b in self.no_blink):
                self.blinks.append(t)
            t += self.rng.uniform(2.2, 5.0)

    def add_blink(self, t):
        if not hasattr(self, "blinks"):
            self.blinks = []
        self.blinks.append(t)

    def sample(self, t):
        out = {}
        for t0, t1, a, r, w in self.layers:
            if t < t0 or t > t1 + r:
                continue
            env = core.smooth((t - t0) / max(a, 1e-3)) if t < t1 else 1 - core.smooth((t - t1) / max(r, 1e-3))
            for k, v in w.items():
                out[k] = out.get(k, 0.0) + v * env
        for t0, t1, fn in self.funcs:
            if t0 <= t <= t1:
                for k, v in fn(t).items():
                    out[k] = out.get(k, 0.0) + v
        for tb in getattr(self, "blinks", []):
            d = t - tb
            if -0.07 <= d <= 0.11:
                b = core.smooth((d + 0.07) / 0.07) if d < 0 else 1 - core.smooth(d / 0.11)
                for k in ("eyeBlinkLeft", "eyeBlinkRight"):
                    out[k] = max(out.get(k, 0.0), b)
        # anatomical coupling: lips shouldn't part when mouthClose compensates
        return {k: core.clamp(v) for k, v in out.items()}
