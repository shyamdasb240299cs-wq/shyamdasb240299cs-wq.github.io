"""Re-voice by SENTENCES at the voice's natural pace (no time-stretching): each sentence (split at . ! ? and the
dramatic "...") is synthesised on its own and placed where the old take spoke it, centred on it so beat words drift as
little as possible; the time the new voice saves becomes short pauses between sentences. Keeps the loop seam (the
last line runs into the first in one breath). Writes voice/voice_<tag>.wav + voice/words_<tag>.json (words.json format).
args: <video_dir> <voice> <tag> [speed]"""
import sys, os, glob, re, json
import numpy as np, soundfile as sf, espeakng_loader
_libs = glob.glob("/usr/lib/x86_64-linux-gnu/libespeak-ng.so.1")
_data = [d for d in ("/usr/lib/x86_64-linux-gnu/espeak-ng-data", "/usr/share/espeak-ng-data") if os.path.exists(os.path.join(d, "phontab"))]
if _libs and _data:
    espeakng_loader.get_library_path = lambda: _libs[0]; espeakng_loader.get_data_path = lambda: _data[0]
from kokoro import KPipeline
VD, VOICE, TAG = sys.argv[1], sys.argv[2], sys.argv[3]; SPEED = float(sys.argv[4]) if len(sys.argv) > 4 else 1.08
SR = 24000; MIN_GAP = 0.16; MAX_GAP = 0.62
OLD = json.load(open(f"{VD}/voice/words_michael.json" if os.path.exists(f"{VD}/voice/words_michael.json") else f"{VD}/voice/words.json"))
DUR = OLD["duration"]; NS = int(round(DUR * SR)); L = [ln["text"] for ln in OLD["lines"]]
pipe = KPipeline(lang_code=VOICE[0], repo_id="hexgrad/Kokoro-82M")
norm = lambda w: re.sub(r"[^a-z0-9]", "", w.lower())


def toks(text):
    return text.replace("…", "… ").split()


COMMA_SPLITS = set(json.loads(os.environ.get("COMMA_SPLITS", "[]")))   # "line:token_index" commas to also cut after


def split_sentences(line_text, line_no=0):
    """[(sentence_text, n_words)] split after tokens ending in . ! ? or the ellipsis (and after chosen commas)."""
    out, cur = [], []
    for k, t in enumerate(toks(line_text)):
        cur.append(t)
        if re.search(r"[.!?…]$", t) or f"{line_no}:{k}" in COMMA_SPLITS:
            out.append(cur); cur = []
    if cur: out.append(cur)
    return [(" ".join(c), len(c)) for c in out]


def sentence_end(text):
    return bool(re.search(r"[.!?…]$", text))


def synth(text, speed):
    aud, ws, off = [], [], 0.0
    for r in pipe(text, voice=VOICE, speed=speed):
        a = r.audio.numpy() if hasattr(r.audio, "numpy") else np.asarray(r.audio)
        for t in (r.tokens or []):
            if getattr(t, "start_ts", None) is not None and re.search(r"\w", t.text):
                ws.append({"w": t.text, "s": off + t.start_ts, "e": off + t.end_ts})
        aud.append(a); off += len(a) / SR
    return np.concatenate(aud).astype(np.float32), ws


def quiet_cut(a, t0, t1):
    """Quietest 5 ms point between t0 and t1 (s): where to cut between two sentences of one breath."""
    i0, i1 = int(t0 * SR), max(int(t0 * SR) + 1, int(t1 * SR)); hop = int(0.005 * SR)
    if i1 - i0 <= hop: return (t0 + t1) / 2
    env = [np.sqrt(np.mean(a[j:j + hop] ** 2)) for j in range(i0, i1 - hop, hop)]
    return (i0 + int(np.argmin(env)) * hop + hop // 2) / SR


# ---- old take: sentence windows from its word timings
OLDS = []                                       # [(line, text, old_words)]
for i, ln in enumerate(L, 1):
    ow = [w for w in OLD["words"] if w["line"] == i]; k = 0
    for text, n in split_sentences(ln, i):
        OLDS.append((i, text, ow[k:k + n])); k += n
    assert k == len(ow), f"line {i}: {k} tokens vs {len(ow)} words"

# ---- new take: every sentence on its own, except the seam (line 10 runs into line 1 in one breath)
seam_txt = L[9].rstrip() + ", " + L[0][0].lower() + L[0][1:]
sa, sw = synth(seam_txt, SPEED)
k_why = max(i for i, w in enumerate(sw) if w["w"].lower().startswith("why"))
cut_seam = quiet_cut(sa, sw[k_why]["e"], sw[k_why + 1]["s"])
end_w = [w for w in sw if w["s"] < cut_seam]; hook_w = [w for w in sw if w["s"] >= cut_seam]
pieces = {}                                     # sentence index -> (audio, words relative to the piece start)
def from_breath(ws_all, a, start_cut, end_cut, idxs):
    """Split a breath (audio a, words ws_all between start_cut..end_cut) into the sentences idxs at quiet points."""
    pos = 0; cuts = [start_cut]
    for si in idxs[:-1]:
        n = len(OLDS[si][2]); pos += n
        cuts.append(quiet_cut(a, ws_all[pos - 1]["e"], ws_all[pos]["s"]))
    cuts.append(end_cut); pos = 0
    for j, si in enumerate(idxs):
        n = len(OLDS[si][2]); c0, c1 = cuts[j], cuts[j + 1]
        pieces[si] = (a[int(c0 * SR):int(c1 * SR)], [dict(w, s=w["s"] - c0, e=w["e"] - c0) for w in ws_all[pos:pos + n]])
        pos += n
hook_idx = [i for i, s in enumerate(OLDS) if s[0] == 1]; end_idx = [i for i, s in enumerate(OLDS) if s[0] == 10]
from_breath(end_w, sa, 0.0, cut_seam, end_idx)
from_breath(hook_w, sa, cut_seam, len(sa) / SR, hook_idx)
# phrases cut at a comma are spoken as ONE sentence and cut at the comma pause (the intonation stays whole)
groups, cur = [], []
for si, (line, text, ow) in enumerate(OLDS):
    if si in pieces: continue
    cur.append(si)
    if sentence_end(text) or si + 1 >= len(OLDS) or OLDS[si + 1][0] != line:
        groups.append(cur); cur = []
for g in groups:
    if len(g) == 1:
        pieces[g[0]] = synth(OLDS[g[0]][1], SPEED); continue
    a_, ws_ = synth(" ".join(OLDS[si][1] for si in g), SPEED)
    from_breath(ws_, a_, 0.0, len(a_) / SR, g)
for si, (line, text, ow) in enumerate(OLDS):
    nw = pieces[si][1]
    assert [norm(w["w"]) for w in nw] == [norm(w["w"]) for w in ow], (line, [w["w"] for w in nw], [w["w"] for w in ow])

# ---- placement: centre each unit on the old one; >= MIN_GAP between units; the first and the last are fixed
U = []
for si, (line, text, ow) in enumerate(OLDS):
    a, nw = pieces[si]
    on = nw[0]["s"]
    if si != hook_idx[0]:                                        # keep 50 ms of lead-in before the first word
        lead = min(on, 0.05); a = a[int((on - lead) * SR):]; nw = [dict(w, s=w["s"] - (on - lead), e=w["e"] - (on - lead)) for w in nw]
    U.append({"si": si, "line": line, "text": text, "ow": ow, "a": a, "nw": nw, "on": nw[0]["s"], "off": nw[-1]["e"],
              "fixed": si in (hook_idx[0], end_idx[-1])})
st = [0.0] * len(U); prev_end = 0.0
for k, u in enumerate(U):
    if u["si"] == hook_idx[0]:
        st[k] = 0.0                                              # the video's first sample is the hook's first sample
    elif u["si"] == end_idx[-1]:
        st[k] = DUR - len(u["a"]) / SR                           # 'why' ends on the last sample (loop)
    else:
        o_mid = (u["ow"][0]["s"] + u["ow"][-1]["e"]) / 2
        st[k] = max(o_mid - (u["on"] + u["off"]) / 2, prev_end + MIN_GAP - u["on"])
    prev_end = st[k] + u["off"]
def drift(k, s):
    u = U[k]; return max(abs(s + w["s"] - o["s"]) for w, o in zip(u["nw"], u["ow"]))
def gap(k):                                                      # silence before unit k
    return st[k] + U[k]["on"] - (st[k - 1] + U[k - 1]["off"])
BUDGET = 0.32; ELLIPSIS = 0.30
for _ in range(3):
    for k in range(1, len(U)):
        # a long pause is pulled in by moving the later unit earlier (within the drift budget)
        if gap(k) > MAX_GAP and not U[k]["fixed"]:
            s_new = st[k] - (gap(k) - MAX_GAP)
            while drift(k, s_new) > BUDGET and s_new < st[k]: s_new += 0.01
            st[k] = min(st[k], s_new)
        # a "..." is a beat: at least ELLIPSIS of silence after it (move the "..." unit earlier if it may)
        if U[k - 1]["text"].endswith("…") and gap(k) < ELLIPSIS and not U[k - 1]["fixed"]:
            s_new = st[k - 1] - (ELLIPSIS - gap(k))
            if k - 1 >= 1: s_new = max(s_new, st[k - 2] + U[k - 2]["off"] + MIN_GAP - U[k - 1]["on"])
            while drift(k - 1, s_new) > BUDGET + 0.1 and s_new < st[k - 1]: s_new += 0.01
            st[k - 1] = min(st[k - 1], s_new)
out = np.zeros(NS, np.float32); words = []; rep = []
for k, u in enumerate(U):
    a, nw, off, start = u["a"], u["nw"], u["off"], st[k]
    last = u["si"] == end_idx[-1]
    tail = int(min(0.12, len(a) / SR - off) * SR) if not last else len(a) - int(off * SR)
    seg = a[:int(off * SR) + tail].copy()
    f = min(len(seg), int(0.006 * SR))
    if u["si"] != hook_idx[0]: seg[:f] *= np.linspace(0, 1, f)
    if not last:
        g = min(len(seg), int(0.04 * SR)); seg[-g:] *= np.linspace(1, 0, g)
    i0 = int(round(start * SR)); n = min(len(seg), NS - i0)
    out[i0:i0 + n] += seg[:n]
    for w, o in zip(nw, u["ow"]):
        words.append({"w": o["w"], "s": round(start + w["s"], 3), "e": round(start + w["e"], 3), "line": u["line"]})
    rep.append({"sentence": u["text"][:40], "start": round(start, 2), "gap_before": round(gap(k), 2) if k else 0.0,
                "drift_ms_max": int(drift(k, start) * 1000)})
for r in rep: print(r)
for si, r in enumerate(rep):
    if r["drift_ms_max"] > 220:
        line, text, ow = OLDS[si]
        base = sum(len(OLDS[j][2]) for j in range(si) if OLDS[j][0] == line)
        cands = [f"{line}:{base + k}" for k, w in enumerate(toks(text)[:-1]) if w.endswith(",")]
        print("SPLIT_CANDIDATES", si, text[:30], cands)
lines = []
for i, ln in enumerate(L, 1):
    ws = [w for w in words if w["line"] == i]
    lines.append({"i": i, "text": ln, "s": ws[0]["s"], "e": ws[-1]["e"]})
out = out / (np.max(np.abs(out)) + 1e-9) * 0.89
sf.write(f"{VD}/voice/voice_{TAG}.wav", out, SR)
json.dump({"duration": DUR, "lines": lines, "words": words}, open(f"{VD}/voice/words_{TAG}.json", "w"), indent=1)
gaps = [r["gap_before"] for r in rep[1:]]
print("gaps: min %.2f  median %.2f  max %.2f s;  max word drift vs picture %d ms" % (min(gaps), float(np.median(gaps)), max(gaps), max(r["drift_ms_max"] for r in rep)))
