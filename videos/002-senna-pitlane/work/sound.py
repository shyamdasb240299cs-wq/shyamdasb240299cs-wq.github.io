"""Sound design + mix for the Senna pit-lane short (system python: numpy, scipy, ffmpeg).
Engines are physically driven: a seamless loop of a real F1 engine recording is varispeed-played at the rpm of a
1993 V8 (from the car's real speed + gearing), with Doppler from the radial speed to the camera, 1/r level, air
absorption (distance low-pass) and stereo pan from the camera's view. Beds: rain, track/crowd ambience, pit lane.
Map: whoosh / ticks / pops / chime. The narrator (George) on top with sidechain ducking. -14 LUFS master."""
import json, os, sys, subprocess, math
import numpy as np
from scipy import signal

VD = os.path.abspath(sys.argv[1]); UP = "/root/.claude/uploads/9d2567ac-2881-58a8-b77d-52de40e0710d"
SR = 48000
TL = json.load(open(os.path.join(VD, "build", "timeline.json")))
TR = json.load(open(os.path.join(VD, "build", "tracks.json")))
CAMLOG = json.load(open(os.path.join(VD, "build", "camlog.json")))["cam"]
CUTS = json.load(open(os.path.join(VD, "build", "cuts.json")))["cuts"]
FPS = TL["fps"]; NF = TR["n"]; DUR = NF / FPS; N = int(round(DUR * SR))
OUT = os.path.join(VD, "build", "audio"); os.makedirs(OUT, exist_ok=True)
rng = np.random.default_rng(3)
SH = {s["id"]: (s["t0"], s["t1"]) for s in TL["shots"]}


def load(name, mono=True):
    f = name if os.path.isabs(name) else next(os.path.join(UP, x) for x in os.listdir(UP) if name in x)
    ch = 1 if mono else 2
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", f, "-ac", str(ch), "-ar", str(SR), "-f", "f32le", "-"], capture_output=True).stdout
    y = np.frombuffer(raw, np.float32).astype(np.float64)
    return y if mono else y.reshape(-1, 2)


def db(x): return 10 ** (x / 20.0)


def norm(y, peak=0.9):
    m = np.max(np.abs(y)) + 1e-12; return y * (peak / m)


def loop_buffer(y, a, b, xf=0.12):
    """seamless loop of y[a:b] seconds (equal-power crossfade of the tail into the head)"""
    s = y[int(a * SR):int(b * SR)].copy(); n = int(xf * SR)
    head, tail = s[:n].copy(), s[-n:].copy(); w = np.linspace(0, 1, n)
    s[:n] = head * np.sqrt(w) + tail * np.sqrt(1 - w)
    return s[:-n]


def varispeed(loop, rate):
    """play a loop with a per-sample playback rate (pitch AND speed follow the engine)"""
    idx = np.cumsum(rate) % len(loop)
    i0 = np.floor(idx).astype(int); fr = idx - i0; i1 = (i0 + 1) % len(loop)
    return loop[i0] * (1 - fr) + loop[i1] * fr


def per_sample(arr_frames):
    t_f = np.arange(len(arr_frames)) / FPS; t_s = np.arange(N) / SR
    return np.interp(t_s, t_f, arr_frames)


def cut_of_frame():
    out = [None] * NF
    for name, f0, f1 in CUTS:
        for f in range(f0, f1 + 1):
            if f < NF: out[f] = name
    return out


CUT = cut_of_frame()
cam = np.array(TR["cam"]); P = {"senna": np.array(TR["senna"]), "rival": np.array(TR["rival"])}
Q = np.array([CAMLOG[str(f)]["q"] if str(f) in CAMLOG else CAMLOG[f]["q"] for f in range(NF)]) if isinstance(next(iter(CAMLOG)), str) else None


def qrot(q, v):
    w, x, y, z = q
    R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                  [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                  [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    return R.T @ v                       # world -> camera space


def speeds(Pc):
    """m/s per frame, ignoring the jumps at cuts (hold the neighbour value)"""
    v = np.zeros(NF)
    for f in range(1, NF):
        if CUT[f] == CUT[f - 1] and Pc[f][2] > -100 and Pc[f - 1][2] > -100:
            v[f] = np.linalg.norm(Pc[f] - Pc[f - 1]) * FPS
        else:
            v[f] = v[f - 1]
    v[0] = v[1]
    return signal.medfilt(v, 5)


GEAR_TOP = np.array([24.0, 33.0, 43.0, 53.0, 64.0, 77.0])   # m/s at 13,500 rpm per gear (1993 Ford HB V8)


def rpm_track(v):
    rpm = np.zeros_like(v); g = 0
    for i, vi in enumerate(v):
        while g < 5 and vi > GEAR_TOP[g] * 0.97: g += 1
        while g > 0 and vi < GEAR_TOP[g - 1] * 0.72: g -= 1
        rpm[i] = max(4500.0, 13500.0 * vi / GEAR_TOP[g])
    return signal.savgol_filter(rpm, 9, 2)


def engine_layer(which, loop, src_rpm, base_gain, slowmo=None):
    Pc = P[which]; v = speeds(Pc); rpm = rpm_track(v)
    acc = np.gradient(signal.savgol_filter(v, 15, 2)) * FPS
    r = np.linalg.norm(Pc - cam, axis=1); r = np.maximum(r, 2.5)
    visible = Pc[:, 2] > -100
    rdot = np.gradient(r) * FPS
    rdot = np.where([CUT[f] == CUT[max(f - 1, 0)] for f in range(NF)], rdot, 0.0)
    dop = 1.0 / (1.0 + np.clip(rdot, -150, 150) / 343.0)
    onboard = np.array([c == "chase_entry" and which == "senna" for c in CUT])
    rate_f = (rpm / src_rpm) * np.where(onboard, 1.0, dop)
    load_db = np.clip(acc / 4.0, -1.0, 1.0) * 3.0                            # on throttle louder, overrun quieter
    gain_f = np.where(onboard, db(-4.0), (12.0 / r)) * db(load_db) * visible
    lp_f = np.where(onboard, 5200.0, np.clip(18000.0 / (1 + r / 60.0), 1500, 16000))
    if slowmo is not None:                                                    # slow-motion: everything a third the pitch
        rate_f = np.where(slowmo, rate_f * 0.4, rate_f)
    pan_f = np.zeros(NF)
    for f in range(NF):
        q = CAMLOG.get(str(f), CAMLOG.get(f)) if isinstance(CAMLOG, dict) else None
        if q is None or not visible[f]: continue
        rel = qrot(q["q"], Pc[f] - cam[f]); pan_f[f] = np.clip(rel[0] / max(-rel[2], 1.0), -1, 1) if rel[2] < 0 else np.sign(rel[0])
    rate = per_sample(rate_f); g = per_sample(np.maximum(signal.savgol_filter(gain_f, 7, 2), 0.0)); lp = per_sample(lp_f)
    pan = np.clip(per_sample(signal.savgol_filter(pan_f, 9, 2)), -1.0, 1.0)
    y = varispeed(loop, rate)
    # time-varying low-pass in 10 ms blocks (one-pole, state carried)
    out = np.empty_like(y); state = 0.0; blk = SR // 100
    for i in range(0, N, blk):
        a = math.exp(-2 * math.pi * lp[i] / SR)
        seg = y[i:i + blk]; o = np.empty_like(seg)
        for j in range(len(seg)):
            state = (1 - a) * seg[j] + a * state; o[j] = state
        out[i:i + blk] = o
    y = out * g * db(base_gain)
    L = y * np.sqrt(0.5 * (1 - pan)); Rr = y * np.sqrt(0.5 * (1 + pan))
    return np.stack([L, Rr], 1), v, r


def place(dst, src, t, gain_db=0.0):
    i = int(t * SR)
    if i >= N: return
    if src.ndim == 1: src = np.stack([src, src], 1)
    n = min(len(src), N - i); dst[i:i + n] += src[:n] * db(gain_db)


def circular(y, n, xf=0.3):
    """bed of exactly n samples whose end flows into its start (the short loops): crossfade its overflow into the head"""
    if y.ndim == 1: y = np.stack([y, np.roll(y, 977)], 1)
    m = int(xf * SR); z = np.resize(y, (n + m, y.shape[1])).copy(); w = np.linspace(0, 1, m)[:, None]
    z[:m] = z[:m] * np.sqrt(w) + z[n:n + m] * np.sqrt(1 - w)
    return z[:n]


def env_follow(x, att=0.02, rel=0.35):
    e = np.abs(x).mean(1) if x.ndim == 2 else np.abs(x)
    e = signal.lfilter([1], [1, -math.exp(-1 / (0.05 * SR))], e) * (1 - math.exp(-1 / (0.05 * SR)))
    out = np.empty_like(e); s = 0.0; ka = math.exp(-1 / (att * SR)); kr = math.exp(-1 / (rel * SR))
    for i in range(0, len(e), 64):
        v = e[i:i + 64].max(); k = ka if v > s else kr; s = k * s + (1 - k) * v; out[i:i + 64] = s
    return out


def main():
    os.chdir(OUT)
    mix_beds = np.zeros((N, 2)); mix_eng = np.zeros((N, 2)); mix_fx = np.zeros((N, 2))
    # ---------------- engines
    lg19 = load("77919__lg"); senna_loop = loop_buffer(lg19, 3.9, 5.7)
    lg09 = load("77669__lg"); rival_loop = loop_buffer(lg09, 8.6, 10.4)
    slowmo = np.array([c == "hero_slow" for c in CUT])
    e1, v1, r1 = engine_layer("senna", senna_loop, 17500.0, -3.0, slowmo)
    e2, v2, r2 = engine_layer("rival", rival_loop, 17000.0, -6.0, slowmo)
    mix_eng += e1 + e2
    # tyre spray hiss (wet road), follows speed and distance
    wet = load("462862__breviceps"); b, a = signal.butter(2, 1800 / (SR / 2), "high"); wet = signal.lfilter(b, a, wet)
    wl = loop_buffer(wet, 0.5, 5.0, 0.3)
    for v, r in ((v1, r1), (v2, r2)):
        gf = np.clip(v / 60.0, 0, 1) * (10.0 / np.maximum(r, 3.0)) * 0.5
        y = np.resize(wl, N) * per_sample(gf); mix_eng += np.stack([y, np.roll(y, 211)], 1) * db(-8)
    # overrun pops when braking hard for the pit entry / Goddards
    pops = load("105351__ceebfrack")
    for t in (3.6, 4.1, 12.2, 12.5):
        place(mix_fx, norm(pops) * 0.35, t, -10)
    # ---------------- beds: rain (whole video, circular), distant race ambience, pit lane
    rain = load("200272__jmbphilmes"); rain = rain[int(8 * SR):int(60 * SR)]        # skip the thunderclap region
    b, a = signal.butter(2, 300 / (SR / 2), "high"); rain = signal.lfilter(b, a, rain)
    rain_st = np.stack([rain, np.roll(rain, 4801)], 1)
    rb = circular(rain_st, N); mix_beds += norm(rb) * db(-21)
    amb = load("150337__ears68", mono=False); amb = amb[int(20 * SR):int(80 * SR)]
    b, a = signal.butter(2, 2500 / (SR / 2), "low"); amb = signal.lfilter(b, a, amb, axis=0)
    ab = circular(amb, N)
    ext = per_sample(np.array([0.0 if c in ("map", "box_wait", "box_pass", "chase_entry") else 1.0 for c in CUT]))
    mix_beds += norm(ab, 0.9) * db(-26) * ext[:, None]
    pit = load("34526__erdie", mono=False)
    pb = circular(pit, N)
    inpit = per_sample(signal.savgol_filter(np.array([1.0 if c in ("box_wait", "box_pass", "pit_side", "limit") else 0.0 for c in CUT]), 15, 2))
    mix_beds += norm(pb, 0.9) * db(-24) * np.clip(inpit, 0, 1)[:, None]
    crowd = load("472615__timothyd4y", mono=False)
    place(mix_beds, norm(crowd[int(18 * SR):int(26 * SR)], 0.9), SH["S02"][0] + 2.2, -20)
    wrench = load("33191__heigh-hoo"); place(mix_fx, norm(wrench), SH["S04"][0] + 0.3, -26)
    # ---------------- map + graphics sounds (synthesised, CC0 by construction)
    def tone(f, d, a=0.004, r=0.2, harm=(1, 0.4, 0.2)):
        t = np.arange(int(d * SR)) / SR; y = sum(h * np.sin(2 * np.pi * f * (k + 1) * t) for k, h in enumerate(harm))
        env = np.minimum(1, t / a) * np.exp(-t / r); return y * env
    def whoosh(d=0.9, up=True):
        n = int(d * SR); x = rng.standard_normal(n); t = np.linspace(0, 1, n)
        fc = (300 + 4000 * t ** 2) if up else (4300 - 4000 * t ** 0.5)
        y = np.zeros(n); st = 0.0
        for i in range(0, n, 256):
            a = math.exp(-2 * math.pi * fc[i] / SR); seg = x[i:i + 256]
            for j in range(len(seg)): st = (1 - a) * seg[j] + a * st; y[i + j] = st
        env = np.sin(np.pi * t) ** 1.5; return y * env
    f_r = SH["S07"][0]; place(mix_fx, norm(whoosh(1.6, True)), f_r + 1.2, -14)
    for k in range(10): place(mix_fx, tone(1800 + 120 * (k % 3), 0.06, r=0.03), SH["S09"][0] + 0.25 + k * 0.4, -24)
    for k in range(6): place(mix_fx, tone(2200 + 150 * (k % 2), 0.06, r=0.03), SH["S10"][0] + 0.15 + k * 0.36, -23)
    for k, dt in enumerate((0.07, 0.27, 0.6, 1.0)): place(mix_fx, tone(900 + 200 * k, 0.12, r=0.05, harm=(1, 0.2)), SH["S11"][0] + dt, -18)
    place(mix_fx, tone(1320, 1.2, r=0.45, harm=(1, 0.5, 0.25, 0.12)), SH["S11"][0] + 0.6, -15)     # the -200 m chime
    place(mix_fx, norm(whoosh(1.2, False)), SH["S12"][0] - 0.2, -15)
    w = [w_ for w_ in TL["words"] if w_["w"].lower().startswith("minute")]
    if w: place(mix_fx, tone(1568, 1.4, r=0.5, harm=(1, 0.45, 0.2, 0.1)), w[0]["s"] - 0.1, -14)   # lap-time chime
    boom = tone(55, 1.8, a=0.01, r=0.6, harm=(1, 0.5)); place(mix_fx, boom, SH["S13"][0], -12)
    riser = whoosh(2.4, True) * np.linspace(0, 1, int(2.4 * SR)); place(mix_fx, norm(riser), SH["S10"][0] - 0.2, -24)
    # ---------------- narrator + ducking
    vo = load(os.path.join(VD, "audio", "vo.wav")); vo = np.pad(vo, (0, max(0, N - len(vo))))[:N]
    vo_st = np.stack([vo, vo], 1) * db(-1.0)
    env = env_follow(vo_st); duck = 1.0 - 0.55 * np.clip(env / (np.percentile(env[env > 1e-4], 60) + 1e-9), 0, 1)
    duck_e = 1.0 - 0.38 * np.clip(env / (np.percentile(env[env > 1e-4], 60) + 1e-9), 0, 1)
    mix = vo_st + mix_beds * duck[:, None] + mix_eng * duck_e[:, None] * db(-1) + mix_fx * duck_e[:, None]
    raw = os.path.join(OUT, "mix_raw.wav")
    write_wav(raw, mix)
    out = os.path.join(OUT, "mix.wav")
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", raw, "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True)
    import re
    I = float(re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)[-1])
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", raw, "-af",
                    f"volume={-14.0 - I:.2f}dB,alimiter=limit=0.84:level=false", "-ar", "48000", out], check=True)
    for nm, y in (("stem_vo.wav", vo_st), ("stem_engines.wav", mix_eng), ("stem_beds.wav", mix_beds), ("stem_fx.wav", mix_fx)):
        write_wav(os.path.join(OUT, nm), y)
    print(f"[sound] mix {DUR:.1f}s, input {I:.1f} LUFS -> -14 LUFS: {out}")


def write_wav(path, y):
    y = np.clip(y, -1, 1).astype(np.float32)
    p = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "-", path], input=y.tobytes())


if __name__ == "__main__":
    main()
