"""Narration -> vo.wav + words.json  ([{w, s, e}] seconds). Run with the Python that has the TTS engine installed.

python s3d_tts.py <script.txt> <out_dir> --engine kokoro|edge|eleven|piper|file --voice VOICE --speed 1.0
  kokoro : local, free, Apache-2.0 (pip install kokoro soundfile; python -m spacy download en_core_web_sm; espeak-ng)
  edge   : Microsoft Edge online voices, word timings (pip install edge-tts)  [check ToS for monetized use]
  eleven : ElevenLabs API (env ELEVENLABS_API_KEY), character timings
  piper  : local fast voices (pip install piper-tts), aligned with faster-whisper
  file   : your own recording (--audio path.wav), aligned with faster-whisper
"""
import argparse, json, os, re, sys, subprocess, wave

SR = 24000


def _fix_espeak():
    try:
        import glob, espeakng_loader
        libs = (glob.glob("/usr/lib/x86_64-linux-gnu/libespeak-ng.so.1") + glob.glob("/usr/lib/aarch64-linux-gnu/libespeak-ng.so.1")
                + glob.glob("/usr/lib/libespeak-ng.so.1") + glob.glob("/opt/homebrew/lib/libespeak-ng.1.dylib")
                + glob.glob("/usr/local/lib/libespeak-ng.1.dylib"))
        data = [d for d in ("/usr/lib/x86_64-linux-gnu/espeak-ng-data", "/usr/lib/aarch64-linux-gnu/espeak-ng-data",
                            "/usr/share/espeak-ng-data", "/opt/homebrew/share/espeak-ng-data", "/usr/local/share/espeak-ng-data")
                if os.path.exists(os.path.join(d, "phontab"))]
        if libs and data:
            espeakng_loader.get_library_path = lambda: libs[0]
            espeakng_loader.get_data_path = lambda: data[0]
    except Exception:
        pass


def write_wav(path, samples, sr):
    import numpy as np
    a = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(a.tobytes())


def kokoro(text, out, voice, speed):
    _fix_espeak()
    import numpy as np
    from kokoro import KPipeline
    lang = voice[0] if voice[0] in "abefhijpz" else "a"
    pipe = KPipeline(lang_code=lang, repo_id="hexgrad/Kokoro-82M")
    audio, words, off = [], [], 0.0
    # sentence by sentence keeps prosody natural and lets us insert micro-pauses
    for sent in re.split(r"(?<=[.!?])\s+", text.strip()):
        if not sent: continue
        for r in pipe(sent, voice=voice, speed=speed):
            a = r.audio.numpy() if hasattr(r.audio, "numpy") else np.asarray(r.audio)
            for tok in (r.tokens or []):
                if getattr(tok, "start_ts", None) is not None and re.search(r"\w", tok.text):
                    words.append({"w": tok.text, "s": round(off + tok.start_ts, 3), "e": round(off + tok.end_ts, 3)})
            audio.append(a); off += len(a) / SR
        gap = 0.12 if sent.endswith((".", "!")) else 0.08
        audio.append(np.zeros(int(SR * gap), dtype=np.float32)); off += gap
    write_wav(os.path.join(out, "vo.wav"), np.concatenate(audio), SR)
    return words


def edge(text, out, voice, speed):
    import asyncio, edge_tts
    rate = f"{int(round((speed - 1) * 100)):+d}%"
    words = []
    mp3 = os.path.join(out, "vo.mp3")

    async def run():
        c = edge_tts.Communicate(text, voice or "en-US-AndrewNeural", rate=rate, boundary="WordBoundary")
        with open(mp3, "wb") as f:
            async for ch in c.stream():
                if ch["type"] == "audio":
                    f.write(ch["data"])
                elif ch["type"] == "WordBoundary":
                    s = ch["offset"] / 1e7; words.append({"w": ch["text"], "s": round(s, 3), "e": round(s + ch["duration"] / 1e7, 3)})
    asyncio.run(run())
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp3, "-ar", str(SR), "-ac", "1", os.path.join(out, "vo.wav")], check=True)
    return words


def eleven(text, out, voice, speed):
    import base64, urllib.request
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key: sys.exit("set ELEVENLABS_API_KEY")
    vid = voice or "pNInz6obpgDQGcFmaJgB"
    body = json.dumps({"text": text, "model_id": "eleven_multilingual_v2",
                       "voice_settings": {"stability": 0.45, "similarity_boost": 0.8, "style": 0.35, "speed": speed}}).encode()
    req = urllib.request.Request(f"https://api.elevenlabs.io/v1/text-to-speech/{vid}/with-timestamps", body,
                                 {"xi-api-key": key, "Content-Type": "application/json"})
    d = json.load(urllib.request.urlopen(req, timeout=180))
    mp3 = os.path.join(out, "vo.mp3"); open(mp3, "wb").write(base64.b64decode(d["audio_base64"]))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp3, "-ar", str(SR), "-ac", "1", os.path.join(out, "vo.wav")], check=True)
    al = d["alignment"]; words, cur, s0 = [], "", None
    for ch, st, en in zip(al["characters"], al["character_start_times_seconds"], al["character_end_times_seconds"]):
        if ch.strip() and not re.match(r"[^\w']", ch):
            if not cur: s0 = st
            cur += ch; last = en
        elif cur:
            words.append({"w": cur, "s": round(s0, 3), "e": round(last, 3)}); cur = ""
    if cur: words.append({"w": cur, "s": round(s0, 3), "e": round(last, 3)})
    return words


def align(wav, text):
    """Word timings for any audio (own recording, piper, ...) with faster-whisper."""
    from faster_whisper import WhisperModel
    m = WhisperModel(os.environ.get("S3D_WHISPER", "small.en"), device="auto", compute_type="int8")
    segs, _ = m.transcribe(wav, word_timestamps=True, initial_prompt=text[:200], vad_filter=False)
    return [{"w": w.word.strip(), "s": round(w.start, 3), "e": round(w.end, 3)} for s in segs for w in (s.words or []) if w.word.strip()]


def piper(text, out, voice, speed):
    wav = os.path.join(out, "vo.wav")
    subprocess.run(["piper", "--model", voice or "en_US-ryan-high", "--length_scale", str(1 / speed), "--output_file", wav],
                   input=text.encode(), check=True)
    return align(wav, text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("script"); ap.add_argument("out")
    ap.add_argument("--engine", default="kokoro"); ap.add_argument("--voice", default="am_michael")
    ap.add_argument("--speed", type=float, default=1.05); ap.add_argument("--audio")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    text = open(a.script, encoding="utf-8").read().strip()
    if a.engine == "file":
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", a.audio, "-ar", str(SR), "-ac", "1", os.path.join(a.out, "vo.wav")], check=True)
        words = align(os.path.join(a.out, "vo.wav"), text)
    else:
        words = {"kokoro": kokoro, "edge": edge, "eleven": eleven, "piper": piper}[a.engine](text, a.out, a.voice, a.speed)
    json.dump(words, open(os.path.join(a.out, "words.json"), "w"))
    with wave.open(os.path.join(a.out, "vo.wav")) as w:
        dur = w.getnframes() / w.getframerate()
    print(f"[s3d] voice ok: {len(words)} words, {dur:.1f}s ({len(words) / max(dur, 1e-3) * 60:.0f} wpm)")


if __name__ == "__main__":
    main()
