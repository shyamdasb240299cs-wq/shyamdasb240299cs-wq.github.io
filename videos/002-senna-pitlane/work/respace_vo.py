"""Re-space the narration: natural-pace sentences, story-driven pauses (no time-stretching).
Cuts vo.wav at sentence ends (words ending . ? ! or ...), trims each sentence's own silence, re-joins with chosen pauses."""
import json, sys, numpy as np, soundfile as sf
d = sys.argv[1]; raw = f"{d}/vo_raw.wav"; words = json.load(open(f"{d}/words_raw.json"))
y, sr = sf.read(raw); y = y if y.ndim == 1 else y.mean(1)
PAUSE = [0.45, 0.25, 0.35, 0.45, 0.25, 0.40, 0.30, 0.45, 0.25, 0.25, 0.20, 0.45, 0.35, 0.45, 0.40, 0.30, 0.0]
sents, cur = [], []
for w in words:
    cur.append(w)
    if w["w"].rstrip().endswith((".", "?", "!")):
        sents.append(cur); cur = []
if cur: sents.append(cur)
assert len(sents) <= len(PAUSE), (len(sents), len(PAUSE))
out, nw, t = [], [], 0.20
out.append(np.zeros(int(sr * t)))
for k, s in enumerate(sents):
    a = max(0.0, s[0]["s"] - 0.04); b = s[-1]["e"] + 0.06          # keep a hair of breath around the words
    seg = y[int(a * sr):int(b * sr)].copy()
    n = int(0.012 * sr); seg[:n] *= np.linspace(0, 1, n); seg[-n:] *= np.linspace(1, 0, n)   # click-free edges
    for w in s:
        nw.append({"w": w["w"], "s": round(t + w["s"] - a, 3), "e": round(t + w["e"] - a, 3)})
    out.append(seg); t += len(seg) / sr
    p = PAUSE[k]; out.append(np.zeros(int(sr * p))); t += p
y2 = np.concatenate(out)
sf.write(f"{d}/vo.wav", y2, sr); json.dump(nw, open(f"{d}/words.json", "w"), indent=0)
print(f"{len(sents)} sentences, {len(y2)/sr:.1f}s (was {len(y)/sr:.1f}s)")
