"""Re-voice the narration with another Kokoro voice WITHOUT moving the picture: every word of the new take is
time-mapped (Rubber Band, pitch-preserving) onto the start time of the same word in the current take, so captions,
labels, SFX and animation keyed to words stay in sync. Keeps the loop seam (last line flows into the first).
args: <video_dir> <voice_id>      -> voice/voice_<voice>.wav (+ report); does not overwrite voice.wav"""
import sys, os, glob, re, json, subprocess, tempfile
import numpy as np, soundfile as sf, espeakng_loader
_libs = glob.glob("/usr/lib/x86_64-linux-gnu/libespeak-ng.so.1")
_data = [d for d in ("/usr/lib/x86_64-linux-gnu/espeak-ng-data", "/usr/share/espeak-ng-data") if os.path.exists(os.path.join(d, "phontab"))]
if _libs and _data:
    espeakng_loader.get_library_path = lambda: _libs[0]; espeakng_loader.get_data_path = lambda: _data[0]
from kokoro import KPipeline
VD, VOICE = sys.argv[1], sys.argv[2]
SR = 24000
OLD = json.load(open(f"{VD}/voice/words.json")); DUR = OLD["duration"]; NS = int(round(DUR * SR))
L = [ln["text"] for ln in OLD["lines"]]
OW = {i + 1: [w for w in OLD["words"] if w["line"] == i + 1] for i in range(len(L))}
pipe = KPipeline(lang_code=VOICE[0], repo_id="hexgrad/Kokoro-82M")
wordy = lambda t: bool(re.search(r"\w", t))


def synth(text, speed):
    aud, ws, off = [], [], 0.0
    for r in pipe(text, voice=VOICE, speed=speed):
        a = r.audio.numpy() if hasattr(r.audio, "numpy") else np.asarray(r.audio)
        for t in (r.tokens or []):
            if getattr(t, "start_ts", None) is not None and wordy(t.text):
                ws.append({"w": t.text, "s": off + t.start_ts, "e": off + t.end_ts})
        aud.append(a); off += len(a) / SR
    return np.concatenate(aud).astype(np.float32), ws


def seam(speed):
    """Line 10 + line 1 in one breath, cut in the pause after 'why' (as the original take)."""
    txt = L[9].rstrip() + ", " + L[0][0].lower() + L[0][1:]
    sa, sw = synth(txt, speed)
    k = max(i for i, w in enumerate(sw) if w["w"].lower().startswith("why"))
    i0, i1 = int(sw[k]["s"] * SR), int(sw[k + 1]["e"] * SR); hop = int(0.005 * SR)
    env = np.array([np.sqrt(np.mean(sa[j:j + hop] ** 2)) for j in range(i0, i1, hop)])
    quiet = env < 0.04 * np.abs(sa).max(); best, run = (0, 0), None
    for j, q in enumerate(list(quiet) + [False]):
        if q and run is None: run = j
        if not q and run is not None:
            if j - run > best[1] - best[0]: best = (run, j)
            run = None
    g0, g1 = i0 + best[0] * hop, i0 + best[1] * hop
    end_a = sa[:min(g1, g0 + int(0.035 * SR))]; hs = max(g0, g1 - int(0.02 * SR)); hook_a = sa[hs:]
    end_w = [w for w in sw if w["s"] < g0 / SR]
    hook_w = [dict(w, s=w["s"] - hs / SR, e=w["e"] - hs / SR) for w in sw if w["s"] >= g0 / SR]
    return (hook_a, hook_w), (end_a, end_w)


def spoken(ws):
    return ws[-1]["e"] - ws[0]["s"]


def take(speeds):
    hook, end = seam(speeds[0])
    segs = {1: hook, 10: end}
    for i in range(2, 10):
        segs[i] = synth(L[i - 1], speeds[i - 1])
    return segs


# pass 1 at the house speed, then per-line speeds that make each line as long as the current take's line
segs = take([1.08] * 10)
speeds = []
for i in range(1, 11):
    r = spoken(segs[i][1]) / spoken(OW[i]); speeds.append(float(np.clip(1.08 * r, 0.8, 1.45)))
seam_speed = float(np.clip(1.08 * (spoken(segs[1][1]) + spoken(segs[10][1])) / (spoken(OW[1]) + spoken(OW[10])), 0.8, 1.45))
speeds[0] = speeds[9] = seam_speed
segs = take(speeds)
print("speeds", [round(s, 3) for s in speeds])

out = np.zeros(NS, np.float32); rep = []
tmp = tempfile.mkdtemp()
for i in range(1, 11):
    a, nw = segs[i]; ow = OW[i]
    if [re.sub(r"\W", "", w["w"].lower()) for w in nw] != [re.sub(r"\W", "", w["w"].lower()) for w in ow]:
        print("WORD MISMATCH line", i, [w["w"] for w in nw], [w["w"] for w in ow]); sys.exit(1)
    if i == 1:                                  # hook: begins on the first sample of the video
        in0, out0 = 0.0, 0.0
    else:
        pre = min(0.06, nw[0]["s"]); in0 = nw[0]["s"] - pre; out0 = ow[0]["s"] - pre
    if i == 10:                                 # last line: ends exactly on the last sample ('why' runs into the loop)
        in1, out1 = len(a) / SR, DUR
    else:
        post = min(0.08, len(a) / SR - nw[-1]["e"]); in1 = nw[-1]["e"] + post; out1 = ow[-1]["e"] + post
    seg = a[int(in0 * SR):int(in1 * SR)]
    pts = [(0.0, 0.0)]
    for k, (n, o) in enumerate(zip(nw, ow)):
        if i == 1 and k == 0: continue
        p = (n["s"] - in0, o["s"] - out0)
        if p[0] - pts[-1][0] > 0.07 and p[1] - pts[-1][1] > 0.07 and 0.55 < (p[1] - pts[-1][1]) / (p[0] - pts[-1][0]) < 1.8:
            pts.append(p)
    pe = (nw[-1]["e"] - in0, ow[-1]["e"] - out0)
    if pe[0] - pts[-1][0] > 0.04 and pe[1] - pts[-1][1] > 0.04: pts.append(pe)
    tot_in, tot_out = len(seg) / SR, out1 - out0
    fi, fo = os.path.join(tmp, f"in{i}.wav"), os.path.join(tmp, f"out{i}.wav"); mp = os.path.join(tmp, f"map{i}.txt")
    sf.write(fi, seg, SR, subtype="FLOAT")
    with open(mp, "w") as f:
        for x, y in pts[1:]:
            f.write(f"{int(round(x * SR))} {int(round(y * SR))}\n")
    subprocess.run(["rubberband", "-q", "-3", "--timemap", mp, "-D", f"{tot_out:.6f}", fi, fo], check=True)
    y, _ = sf.read(fo, dtype="float32")
    if y.ndim > 1: y = y.mean(1)
    n = min(len(y), NS - int(round(out0 * SR)))
    a0 = int(round(out0 * SR)); out[a0:a0 + n] += y[:n]
    ratios = [(q[1] - p[1]) / (q[0] - p[0]) for p, q in zip(pts, pts[1:])]
    rep.append({"line": i, "speed": round(speeds[i - 1], 3), "anchors": len(pts), "stretch_min": round(min(ratios), 3), "stretch_max": round(max(ratios), 3)})
    print("line", i, rep[-1], flush=True)
out = out / (np.max(np.abs(out)) + 1e-9) * 0.89
name = VOICE.replace(",", "_")
sf.write(f"{VD}/voice/voice_{name}.wav", out, SR)
json.dump(rep, open(f"{VD}/voice/conform_{name}.json", "w"), indent=1)
print("wrote", f"{VD}/voice/voice_{name}.wav", round(len(out) / SR, 3), "s")
