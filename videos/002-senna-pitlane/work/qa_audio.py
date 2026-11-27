"""Intelligibility check: transcribe the final mix with speech recognition and compare with the narration
(word error rate), so the engines/rain never bury George. Run with the TTS venv python."""
import sys, re, json, os
from faster_whisper import WhisperModel
VD = os.path.abspath(sys.argv[1]); wav = sys.argv[2] if len(sys.argv) > 2 else os.path.join(VD, "build", "audio", "mix.wav")
ref = open(os.path.join(VD, "audio", "narration.txt")).read()
norm = lambda t: re.sub(r"[^a-z0-9' ]", " ", t.lower().replace("1993", "nineteen ninety three")).split()
m = WhisperModel("base.en", device="cpu", compute_type="int8")
segs, _ = m.transcribe(wav, beam_size=5, language="en")
hyp = " ".join(s.text for s in segs)
r, h = norm(ref), norm(hyp)
d = [[0] * (len(h) + 1) for _ in range(len(r) + 1)]
for i in range(len(r) + 1): d[i][0] = i
for j in range(len(h) + 1): d[0][j] = j
for i in range(1, len(r) + 1):
    for j in range(1, len(h) + 1):
        d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (r[i - 1] != h[j - 1]))
wer = d[-1][-1] / max(1, len(r))
print(f"[qa-audio] WER {wer * 100:.1f}%  ({len(r)} ref words)\n  heard: {hyp.strip()[:400]}")
json.dump({"wer": wer, "heard": hyp}, open(os.path.join(VD, "build", "qa_audio.json"), "w"), indent=1)
