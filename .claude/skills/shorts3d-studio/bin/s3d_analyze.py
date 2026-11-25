"""Reference video analyzer: cuts, pacing, camera motion, colour, caption zone, hook/loop transcript, contact sheets.
Every frame is inspected (at 15 fps analysis rate); the output is a compact report Claude can read cheaply.

python s3d.py analyze ref.mp4   ->  ref_analysis/{report.md, shots.jpg, hook.jpg, data.json}
needs: ffmpeg; numpy + opencv-python-headless (pip install numpy opencv-python-headless pillow); optional faster-whisper
"""
import json, os, subprocess, math


def _frames(video, fps=15, w=180):
    import numpy as np
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", video],
                       capture_output=True, text=True).stdout.strip().split(",")
    W, H = int(p[0]), int(p[1]); h = int(w * H / W) // 2 * 2
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", video, "-vf", f"fps={fps},scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True).stdout
    n = len(raw) // (w * h * 3)
    return np.frombuffer(raw[:n * w * h * 3], dtype=np.uint8).reshape(n, h, w, 3), (W, H)


def analyze(video, out, fps=15):
    import numpy as np
    try:
        import cv2
    except ImportError:
        cv2 = None
    os.makedirs(out, exist_ok=True)
    F, (W, H) = _frames(video, fps)
    n = len(F); dur = n / fps
    hsv = [cv2.cvtColor(f, cv2.COLOR_RGB2HSV) if cv2 else None for f in F]
    # --- cut detection: HSV histogram distance + adaptive threshold
    def hist(i):
        if cv2:
            hh = cv2.calcHist([hsv[i]], [0, 1, 2], None, [16, 8, 8], [0, 180, 0, 256, 0, 256]); return cv2.normalize(hh, hh).flatten()
        return np.histogram(F[i], bins=64)[0].astype(float)
    hs = [hist(i) for i in range(n)]
    d = np.array([0.0] + [(cv2.compareHist(hs[i - 1], hs[i], cv2.HISTCMP_BHATTACHARYYA) if cv2 else
                           np.abs(hs[i] - hs[i - 1]).sum() / (hs[i].sum() + 1)) for i in range(1, n)])
    thr = max(0.28, np.median(d) + 4 * np.std(d[d < np.percentile(d, 95)]))
    # second signal: pixel-difference spikes vs. a local median (catches cuts inside the same set/colours)
    g = [f.mean(-1).astype(np.float32) for f in F]
    pd = np.array([0.0] + [float(np.abs(g[i] - g[i - 1]).mean()) / 255 for i in range(1, n)])
    loc = np.array([np.median(pd[max(0, i - 7):i + 8]) for i in range(n)])
    spike = (pd > np.maximum(0.045, 5 * loc))
    cuts = [0] + [i for i in range(1, n) if (d[i] > thr or (spike[i] and d[i] > 0.1)) and i - 0 > 2]
    cuts = [c for k, c in enumerate(cuts) if k == 0 or c - cuts[k - 1] >= 4] + [n]
    shots = []
    for a, b in zip(cuts[:-1], cuts[1:]):
        seg = F[a:b]
        sh = {"start": round(a / fps, 2), "dur": round((b - a) / fps, 2)}
        hv = np.stack([hsv[i] for i in range(a, b)]) if cv2 else None
        sh["bright"] = round(float(seg.mean()) / 255, 2)
        sh["sat"] = round(float(hv[..., 1].mean()) / 255, 2) if hv is not None else None
        px = seg[::max(1, len(seg) // 4)].reshape(-1, 3)[::37].astype(np.float32)
        if cv2 and len(px) > 8:
            _, lab, cen = cv2.kmeans(px, 3, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0), 2, cv2.KMEANS_PP_CENTERS)
            cnt = np.bincount(lab.flatten(), minlength=3)
            sh["palette"] = ["#%02x%02x%02x" % tuple(int(v) for v in cen[i]) for i in np.argsort(-cnt)]
        # camera motion from dense optical flow (global translation / zoom / residual = subject motion)
        if cv2 and b - a > 2:
            mv = []
            for i in range(a + 1, b, max(1, (b - a) // 8)):
                g0 = cv2.cvtColor(F[i - 1], cv2.COLOR_RGB2GRAY); g1 = cv2.cvtColor(F[i], cv2.COLOR_RGB2GRAY)
                fl = cv2.calcOpticalFlowFarneback(g0, g1, None, 0.5, 3, 15, 3, 5, 1.2, 0)
                hh, ww = g0.shape; yy, xx = np.mgrid[0:hh, 0:ww]
                rx, ry = xx - ww / 2, yy - hh / 2
                zoom = float(((fl[..., 0] * rx + fl[..., 1] * ry) / (rx ** 2 + ry ** 2 + 1)).mean())
                mv.append((float(fl[..., 0].mean()), float(fl[..., 1].mean()), zoom, float(np.linalg.norm(fl, axis=2).mean())))
            m = np.array(mv).mean(0)
            pan, tilt, zoom, mag = m[0] * fps / ww, m[1] * fps / hh, m[2] * fps, m[3] * fps / ww
            move = "static"
            if abs(zoom) > 0.04: move = "push_in" if zoom > 0 else "pull_out"
            elif abs(pan) > 0.03: move = "pan_" + ("right" if pan < 0 else "left")
            elif abs(tilt) > 0.03: move = "tilt_" + ("down" if tilt < 0 else "up")
            sh.update({"cam": move, "motion": round(mag, 3)})
        # caption zone: bright text strokes with dark outline -> find their vertical band
        if cv2:
            mid = seg[len(seg) // 2]; g = cv2.cvtColor(mid, cv2.COLOR_RGB2GRAY)
            white = (g > 225).astype(np.uint8); edges = cv2.Canny(g, 80, 200)
            rows = (white.sum(1) * (edges.sum(1) > 0)) / g.shape[1]
            if rows.max() > 0.02:
                yb = np.where(rows > rows.max() * 0.4)[0]; sh["caption_y"] = round(float(yb.mean()) / g.shape[0], 2)
        shots.append(sh)
    data = {"video": os.path.basename(video), "res": [W, H], "duration": round(dur, 2), "shots": shots,
            "avg_shot": round(dur / max(1, len(shots)), 2), "cuts_per_10s": round(len(shots) / dur * 10, 1)}
    # --- transcript: hook line + loop check
    try:
        from faster_whisper import WhisperModel
        m = WhisperModel("base.en", device="auto", compute_type="int8")
        segs, _ = m.transcribe(video, word_timestamps=True)
        ws = [w for s in segs for w in (s.words or [])]
        data["wpm"] = round(len(ws) / dur * 60)
        data["first_words"] = " ".join(w.word.strip() for w in ws[:14]); data["first_word_at"] = round(ws[0].start, 2) if ws else None
        data["last_words"] = " ".join(w.word.strip() for w in ws[-10:])
    except Exception:
        pass
    json.dump(data, open(os.path.join(out, "data.json"), "w"), indent=1)
    # --- contact sheets: one mid-frame per shot; hook strip = first 2 s at 4 fps
    try:
        from PIL import Image, ImageDraw
        tw, th = 135, int(135 * H / W); cols = 8
        rows = math.ceil(len(shots) / cols)
        sheet = Image.new("RGB", (cols * tw, rows * (th + 14)), (20, 20, 20)); dr = ImageDraw.Draw(sheet)
        for k, sh in enumerate(shots):
            i = min(n - 1, int((sh["start"] + sh["dur"] / 2) * fps))
            x, y = (k % cols) * tw, (k // cols) * (th + 14)
            sheet.paste(Image.fromarray(F[i]).resize((tw, th)), (x, y + 14))
            dr.text((x + 3, y + 1), f"{k + 1} {sh['dur']}s {sh.get('cam', '')}", fill=(255, 220, 0))
        sheet.save(os.path.join(out, "shots.jpg"), quality=80)
        hook = [F[min(n - 1, int(t * fps))] for t in [i / 4 for i in range(8)]]
        strip = Image.new("RGB", (8 * tw, th), (0, 0, 0))
        for k, f in enumerate(hook):
            strip.paste(Image.fromarray(f).resize((tw, th)), (k * tw, 0))
        strip.save(os.path.join(out, "hook.jpg"), quality=80)
    except ImportError:
        pass
    lines = [f"# {data['video']}  {data['duration']}s  {len(shots)} shots  avg {data['avg_shot']}s  ({data['cuts_per_10s']} cuts/10s)",
             f"hook: \"{data.get('first_words', '?')}\" (speech starts {data.get('first_word_at', '?')}s)  wpm {data.get('wpm', '?')}",
             f"ending: \"{data.get('last_words', '?')}\"  <- does it flow back into the hook? (loop)", "",
             "| # | start | dur | cam | motion | bright | sat | caption_y | palette |", "|---|---|---|---|---|---|---|---|---|"]
    for k, s in enumerate(shots):
        lines.append(f"| {k + 1} | {s['start']} | {s['dur']} | {s.get('cam', '')} | {s.get('motion', '')} | {s['bright']} | {s.get('sat', '')} | {s.get('caption_y', '')} | {' '.join(s.get('palette', []))} |")
    open(os.path.join(out, "report.md"), "w").write("\n".join(lines) + "\n")
    print(f"[s3d] analyzed {n} frames: {len(shots)} shots, avg {data['avg_shot']}s -> {out}/report.md, shots.jpg, hook.jpg")
