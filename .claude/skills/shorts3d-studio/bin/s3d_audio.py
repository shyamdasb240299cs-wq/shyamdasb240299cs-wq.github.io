"""Captions (ASS), audio mix (VO + SFX + auto-foley + ducked music, -14 LUFS), SFX synthesis, logo, publish text."""
import json, math, os, random, re, subprocess, wave, glob

FOLEY = {"step": ("step", -14), "grab": ("grab", -10), "bite": ("crunch", -4), "place": ("tap", -8), "pop": ("pop", -6),
         "arrow": ("whoosh", -12), "release": ("tap", -14), "cut": ("whoosh", -18)}


def _ass_time(t):
    t = max(0.0, t); h = int(t // 3600); m = int(t % 3600 // 60); s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _ass_col(hexs, alpha=0):
    h = hexs.lstrip("#"); r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def captions(tl, brand, out, res=(1080, 1920)):
    W, H = res; cap = tl.get("captions", {}) or {}
    size = int(H * float(cap.get("size", brand.get("caption_size", 0.047))))
    font = cap.get("font", brand.get("font", "Montserrat ExtraBold"))
    marginv = int(H * float(cap.get("y_from_bottom", 0.205)))
    maxw = int(cap.get("max_words", 3)); maxc = int(cap.get("max_chars", 18))
    accent = _ass_col(brand.get("accent", "#FFD43B"))
    emph = set(tl.get("emph", []))
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00141414,&H78000000,-1,0,0,0,100,100,0,0,1,{max(3, size // 16)},{max(2, size // 22)},2,70,70,{marginv},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    words = tl["words"]; chunks = []; cur = []
    for i, w in enumerate(words):
        cur.append(w)
        txt = " ".join(x["w"] for x in cur)
        nxt = words[i + 1] if i + 1 < len(words) else None
        brk = (len(cur) >= maxw or len(txt) >= maxc or re.search(r"[,.!?;:]$", w["w"]) or nxt is None
               or (nxt and nxt["s"] - w["e"] > 0.35) or (nxt and nxt.get("shot") != w.get("shot") and len(cur) > 1))
        if brk:
            chunks.append(cur); cur = []
    merged = []  # no orphan single-word cards
    for ch in chunks:
        if merged and len(ch) == 1 and len(merged[-1]) <= 3 and ch[0].get("shot") == merged[-1][-1].get("shot") \
                and not re.search(r"[.!?]$", merged[-1][-1]["w"]):
            merged[-1] = merged[-1] + ch
        else:
            merged.append(ch)
    chunks = merged
    lines = []
    for k, ch in enumerate(chunks):
        s = ch[0]["s"] - 0.04
        e = chunks[k + 1][0]["s"] - 0.04 if k + 1 < len(chunks) and chunks[k + 1][0]["s"] - ch[-1]["e"] < 0.6 else ch[-1]["e"] + 0.25
        parts = []
        for w in ch:
            t = re.sub(r"[{}\\]", "", w["w"]).strip()
            t = re.sub(r"[,.:;\u2026]+$", "", t)  # genre captions show no trailing punctuation (keep ? and !)
            if re.sub(r"[^a-z0-9']", "", t.lower()) in emph:
                parts.append(f"{{\\c{accent}}}{t}{{\\c&HFFFFFF&}}")
            else:
                parts.append(t)
        txt = " ".join(parts)
        starts = k == 0 or re.search(r"[.!?]$", chunks[k - 1][-1]["w"])
        if cap.get("case", "sentence") == "sentence":
            txt = (txt[:1].upper() + txt[1:]) if starts else (txt[:1].lower() + txt[1:] if not re.match(r"I\b|I'", txt) else txt)
        elif cap.get("case") == "upper":
            txt = txt.upper()
        pop = "{\\fad(25,0)\\fscx90\\fscy90\\t(0,90,\\fscx100\\fscy100)}"
        lines.append(f"Dialogue: 0,{_ass_time(s)},{_ass_time(e)},Cap,,0,0,0,,{pop}{txt}")
    open(out, "w", encoding="utf-8").write(head + "\n".join(lines) + "\n")
    return out


def _dur(path):
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path], capture_output=True, text=True)
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def mix(d, tl, events, sfx_dir, dur):
    """Voice segments on the video clock + SFX cues + auto-foley + ducked music bed -> -14 LUFS wav."""
    vo = os.path.join(d, "audio", "vo.wav")
    ins, flt, labels = [], [], []
    if os.path.exists(vo):
        for k, sh in enumerate(tl["shots"]):
            if not sh.get("vo"): continue
            a, b = sh["vo"]; ins += ["-i", vo]; i = len(ins) // 2 - 1
            flt.append(f"[{i}:a]atrim=start={max(0, a):.3f}:end={b:.3f},asetpts=PTS-STARTPTS,aresample=48000,adelay={int(sh['t0'] * 1000)}|{int(sh['t0'] * 1000)}[v{k}]")
            labels.append(f"[v{k}]")
    nvo = len(labels)
    vo_mix = None
    if nvo:
        flt.append("".join(labels) + f"amix=inputs={nvo}:normalize=0,apad=whole_dur={dur:.3f}[vo]"); vo_mix = "[vo]"
    cues = list(tl.get("sfx", []))
    seen = set()
    for ev in events:
        if ev.get("type") in FOLEY:
            name, g = FOLEY[ev["type"]]
            key = (name, round(ev["t"], 1))
            if key in seen: continue
            seen.add(key); cues.append({"name": name, "t": ev["t"], "gain": g, "auto": True})
    sfx_labels = []
    for j, q in enumerate(cues):
        f = _sfx_file(sfx_dir, q["name"], d)
        if not f or q["t"] > dur: continue
        ins += ["-i", f]; i = len(ins) // 2 - 1
        flt.append(f"[{i}:a]aresample=48000,volume={q.get('gain', 0)}dB,adelay={int(q['t'] * 1000)}|{int(q['t'] * 1000)}[s{j}]")
        sfx_labels.append(f"[s{j}]")
    music = tl.get("music", {}) or {}
    mfile = music.get("file")
    if music.get("track", "auto") == "auto" and not mfile:
        cands = sorted(glob.glob(os.path.join(os.path.dirname(sfx_dir), "music", "*.*")))
        mfile = cands[0] if cands else None
    if music.get("track") == "none":
        mfile = None
    bed = None
    if mfile and os.path.exists(mfile):
        ins += ["-stream_loop", "-1", "-i", mfile]
        i = sum(1 for x in ins if x == "-i") - 1
        flt.append(f"[{i}:a]aresample=48000,atrim=0:{dur:.3f},volume={music.get('gain_db', -20)}dB,afade=t=out:st={max(0, dur - 1.2):.2f}:d=1.2[m0]")
        if vo_mix:
            flt.append(f"{vo_mix}asplit=2[vo1][vok]"); vo_mix = "[vo1]"
            flt.append("[m0][vok]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=350[bed]")
        else:
            flt.append("[m0]anull[bed]")
        bed = "[bed]"
    parts = ([vo_mix] if vo_mix else []) + sfx_labels + ([bed] if bed else [])
    out = os.path.join(d, "build", "mix.wav")
    if not parts:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo", "-t", f"{dur:.3f}", out], check=True)
        return out
    flt.append("".join(parts) + f"amix=inputs={len(parts)}:normalize=0,atrim=0:{dur:.3f},aresample=48000[out]")
    fc = os.path.join(d, "build", "mix_filter.txt"); open(fc, "w").write(";\n".join(flt))
    raw = os.path.join(d, "build", "mix_raw.wav")
    cmd = ["ffmpeg", "-y", "-loglevel", "error"] + ins + ["-filter_complex_script", fc, "-map", "[out]", "-ac", "2", raw]
    if subprocess.run(cmd).returncode != 0:
        raise SystemExit("[s3d] audio mix failed (see build/mix_filter.txt)")
    # master: measure integrated loudness, apply exact gain to -14 LUFS, peak-limit at -1.5 dBTP
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", raw, "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True)
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
    gain = (-14.0 - float(m[-1])) if m else 0.0
    comp = os.path.join(d, "build", "mix_comp.wav")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", raw, "-af", "acompressor=threshold=-20dB:ratio=3:attack=5:release=90:makeup=2",
                    comp], check=True)
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", comp, "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True)
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
    gain = (-13.6 - float(m[-1])) if m else 0.0
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", comp, "-af", f"volume={gain:.2f}dB,alimiter=limit=0.84:level=false",
                    "-ar", "48000", out], check=True)
    return out


def _sfx_file(lib, name, d):
    for base in (os.path.join(d, "audio", "sfx"), lib):
        hits = sorted(glob.glob(os.path.join(base, name + ".*")) + glob.glob(os.path.join(base, name + "_*.*")))
        if hits:
            return random.Random(name).choice(hits)
    return None


# ---------------------------------------------------------------- SFX synthesis (original, CC0 by construction)
def synth_library(out):
    try:
        import numpy as np
    except ImportError:
        print("  (numpy missing: skipped SFX synthesis)"); return
    os.makedirs(out, exist_ok=True)
    sr = 48000; rng = np.random.default_rng(7)

    def t(sec): return np.arange(int(sr * sec)) / sr

    def env(x, a, r):
        n = len(x); e = np.ones(n); na = max(1, int(a * sr)); e[:na] = np.linspace(0, 1, na)
        e *= np.exp(-np.arange(n) / max(1, r * sr)); return x * e

    def bp(x, lo, hi):
        X = np.fft.rfft(x); f = np.fft.rfftfreq(len(x), 1 / sr); X[(f < lo) | (f > hi)] = 0; return np.fft.irfft(X, len(x))

    def save(name, x, peak=0.8):
        x = x / (np.max(np.abs(x)) + 1e-9) * peak
        with wave.open(os.path.join(out, name + ".wav"), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes((x * 32767).astype("<i2").tobytes())

    if os.path.exists(os.path.join(out, "whoosh.wav")):
        return
    n = rng.standard_normal(int(sr * 0.55)); tt = t(0.55)
    sweep = np.concatenate([bp(seg, 300 + 3000 * k / 10, 900 + 4000 * k / 10) for k, seg in enumerate(np.array_split(n, 11))])
    save("whoosh", sweep * np.sin(np.pi * tt / 0.55) ** 2)
    x = t(0.12); f = 900 * np.exp(-x * 25) + 250; save("pop", env(np.sin(2 * np.pi * np.cumsum(f) / sr), 0.002, 0.03))
    save("click", env(bp(rng.standard_normal(int(sr * 0.02)), 2000, 9000), 0.0005, 0.004))
    x = t(1.4); save("ding", env(sum(a * np.sin(2 * np.pi * 1320 * r * x) for a, r in ((1, 1), (.5, 2.76), (.25, 5.4))), 0.002, 0.45), 0.6)
    x = t(0.5); save("thud", env(np.sin(2 * np.pi * 58 * x) + 0.3 * bp(rng.standard_normal(len(x)), 40, 400), 0.002, 0.12))
    cr = np.zeros(int(sr * 0.28))
    for k in range(16):
        st = int(rng.uniform(0, 0.22) * sr); g = env(bp(rng.standard_normal(int(sr * 0.018)), 1500, 6500), 0.0005, 0.006) * rng.uniform(.3, 1)
        cr[st:st + len(g)] += g
    save("crunch", cr)
    for k in range(4):
        x = t(0.22); s = env(np.sin(2 * np.pi * (95 + 10 * k) * x), 0.002, 0.04) + 0.35 * env(bp(rng.standard_normal(len(x)), 300, 3000), 0.004, 0.03)
        save(f"step_{k}", s, 0.6)
    save("grab", env(bp(rng.standard_normal(int(sr * 0.12)), 200, 2500), 0.01, 0.04), 0.5)
    x = t(0.15); save("tap", env(np.sin(2 * np.pi * 1150 * x) + 0.4 * bp(rng.standard_normal(len(x)), 2000, 8000), 0.0005, 0.02), 0.6)
    x = t(0.6); f = 180 + 60 * rng.standard_normal(len(x)).cumsum() / 400
    save("zap", env(np.sign(np.sin(2 * np.pi * np.cumsum(np.abs(f) + 120) / sr)) * 0.5 + bp(rng.standard_normal(len(x)), 2000, 9000), 0.002, 0.25), 0.55)
    x = t(1.3); save("riser", (bp(rng.standard_normal(len(x)), 500, 6000) * 0.4 + np.sin(2 * np.pi * np.cumsum(200 + 900 * x / 1.3) / sr)) * (x / 1.3) ** 2, 0.6)
    x = t(0.35); f = 300 - 200 * x / 0.35; save("squish", env(bp(rng.standard_normal(len(x)), 80, 900) * (1 + np.sin(2 * np.pi * np.cumsum(f) / sr)), 0.01, 0.12), 0.6)
    x = t(1.6); save("boom", env(np.sin(2 * np.pi * 42 * x) + 0.6 * bp(rng.standard_normal(len(x)), 30, 300), 0.003, 0.5), 0.9)
    print(f"  synthesized SFX library -> {out}")


# ---------------------------------------------------------------- logo + publish text
def make_logo(name, color, path, size=512):
    initials = "".join(w[0] for w in re.findall(r"[A-Za-z0-9]+", name)[:2]).upper() or "S3"
    try:
        from PIL import Image, ImageDraw, ImageFont
        im = Image.new("RGBA", (size, size), (0, 0, 0, 0)); dr = ImageDraw.Draw(im)
        c = tuple(int(color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
        dr.rounded_rectangle((16, 16, size - 16, size - 16), radius=size // 5, fill=c + (255,))
        fnt = None
        for fp in glob.glob(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "library", "fonts", "*.ttf")):
            fnt = ImageFont.truetype(fp, int(size * 0.42)); break
        fnt = fnt or ImageFont.load_default()
        bb = dr.textbbox((0, 0), initials, font=fnt)
        dr.text(((size - (bb[2] - bb[0])) / 2 - bb[0], (size - (bb[3] - bb[1])) / 2 - bb[1]), initials, fill=(255, 255, 255, 255), font=fnt)
        os.makedirs(os.path.dirname(path), exist_ok=True); im.save(path)
    except ImportError:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={color}:s={size}x{size}", "-vf",
                        f"drawtext=text={initials}:fontcolor=white:fontsize={int(size * .42)}:x=(w-tw)/2:y=(h-th)/2", "-frames:v", "1", path])


def publish_text(tl):
    p = tl.get("publish", {}) or {}
    title = p.get("title") or tl.get("title", "")
    desc = p.get("description", "")
    tags = p.get("hashtags", ["#shorts", "#explained", "#howitworks"])
    return f"TITLE: {title}\n\nDESCRIPTION:\n{desc}\n\n{' '.join(tags)}\n"
