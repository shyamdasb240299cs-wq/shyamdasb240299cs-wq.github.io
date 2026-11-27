"""Post: broadcast-style graphics on the rendered frames (crisp, PIL), captions (skill), mix -> preview/final MP4.
    python3 work/post.py <video_dir> [--final]
FASTEST LAP tag is anchored to Senna's car through the real camera projection of every frame."""
import json, os, sys, math, shutil, subprocess, glob
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

VD = os.path.abspath(sys.argv[1]); FINAL = "--final" in sys.argv
SKILL = os.path.abspath(os.path.join(VD, "..", "..", ".claude", "skills", "shorts3d-studio"))
sys.path.insert(0, os.path.join(SKILL, "bin"))
import s3d_audio
TL = json.load(open(os.path.join(VD, "build", "timeline.json"))); FPS = TL["fps"]
CAM = json.load(open(os.path.join(VD, "build", "camlog.json")))["cam"]
TR = json.load(open(os.path.join(VD, "build", "tracks.json")))
SH = {s["id"]: (s["f0"], s["f1"]) for s in TL["shots"]}
FR = os.path.join(VD, "build", "donington", "frames"); OUTF = os.path.join(VD, "build", "post_frames")
FONT = os.path.join(SKILL, "library", "fonts", "Montserrat-ExtraBold.ttf")
YELLOW = (255, 212, 59); WHITE = (245, 247, 250); DARK = (12, 16, 24)


def font(px): return ImageFont.truetype(FONT, max(8, int(px)))


def qmat(q):
    w, x, y, z = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def project(f, P, W, H):
    c = CAM.get(str(f)) or CAM.get(f)
    if c is None: return None
    R = qmat(c["q"]); rel = R.T @ (np.asarray(P) - np.asarray(c["loc"]))
    if rel[2] >= -0.5: return None
    th_v = 18.0 / c["lens"]; th_h = th_v * W / H
    u = (rel[0] / -rel[2]) / th_h; v = (rel[1] / -rel[2]) / th_v
    return (u + 1) / 2 * W, (1 - v) / 2 * H


def ease(t): t = min(max(t, 0.0), 1.0); return 1 - (1 - t) ** 3


def panel(d, box, r, fill):
    d.rounded_rectangle(box, r, fill=fill)


def lap_tag(img, f, f_in, W, H, record_from=None):
    """FASTEST LAP 1:18.029 tag that rides beside Senna's car (screen projection), slides in, never covers captions"""
    p = TR["senna"][f]
    xy = project(f, [p[0], p[1], p[2] + 0.9], W, H)
    k = ease((f - f_in) / 9.0)
    s = W / 1080.0; bw, bh = 520 * s, 210 * s
    if xy is None: ax, ay = W * 0.5, H * 0.35
    else: ax, ay = xy
    x0 = min(max(ax + 60 * s, 40 * s), W - bw - 40 * s); y0 = min(max(ay - bh - 90 * s, 150 * s), H * 0.62 - bh)
    ov = Image.new("RGBA", img.size, (0, 0, 0, 0)); d = ImageDraw.Draw(ov)
    a = int(255 * k); xs = x0 + (1 - k) * 60 * s
    if xy is not None:
        d.line([(ax, ay), (xs + 18 * s, y0 + bh)], fill=(*YELLOW, a), width=max(2, int(4 * s)))
        d.ellipse([ax - 9 * s, ay - 9 * s, ax + 9 * s, ay + 9 * s], outline=(*YELLOW, a), width=max(2, int(4 * s)))
    panel(d, [xs, y0, xs + bw, y0 + bh], int(14 * s), (*DARK, int(215 * k)))
    d.rectangle([xs, y0, xs + 12 * s, y0 + bh], fill=(*YELLOW, a))
    d.text((xs + 36 * s, y0 + 22 * s), "FASTEST LAP", font=font(34 * s), fill=(*YELLOW, a))
    d.text((xs + 34 * s, y0 + 64 * s), "1:18.029", font=font(92 * s), fill=(*WHITE, a))
    d.text((xs + 36 * s, y0 + 168 * s), "A. SENNA  ·  LAP 57", font=font(26 * s), fill=(200, 205, 215, a))
    if record_from is not None and f >= record_from:
        k2 = ease((f - record_from) / 8.0); a2 = int(255 * k2)
        rb = [xs, y0 + bh + 14 * s, xs + 330 * s, y0 + bh + 74 * s]
        panel(d, rb, int(12 * s), (*YELLOW, a2))
        d.text((rb[0] + 22 * s, rb[1] + 12 * s), "LAP RECORD", font=font(34 * s), fill=(*DARK, a2))
    img.alpha_composite(ov)


def title_card(img, f, f0, f1, W, H):
    s = W / 1080.0; k = min(ease((f - f0) / 10.0), ease((f1 - f) / 10.0))
    if k <= 0: return
    ov = Image.new("RGBA", img.size, (0, 0, 0, 0)); d = ImageDraw.Draw(ov); a = int(255 * k)
    x0, y0 = 54 * s, 170 * s - (1 - k) * 20 * s
    panel(d, [x0, y0, x0 + 700 * s, y0 + 168 * s], int(14 * s), (*DARK, int(200 * k)))
    d.rectangle([x0, y0, x0 + 10 * s, y0 + 168 * s], fill=(*YELLOW, a))
    d.text((x0 + 34 * s, y0 + 20 * s), "EUROPEAN GRAND PRIX", font=font(30 * s), fill=(*YELLOW, a))
    d.text((x0 + 32 * s, y0 + 58 * s), "DONINGTON PARK", font=font(58 * s), fill=(*WHITE, a))
    d.text((x0 + 34 * s, y0 + 126 * s), "11 APRIL 1993  ·  RAIN", font=font(26 * s), fill=(200, 205, 215, a))
    img.alpha_composite(ov)


def year_card(img, f, f0, f1, W, H):
    s = W / 1080.0; k = min(ease((f - f0) / 10.0), ease((f1 - f) / 8.0))
    if k <= 0: return
    ov = Image.new("RGBA", img.size, (0, 0, 0, 0)); d = ImageDraw.Draw(ov); a = int(255 * k)
    x0, y0 = 54 * s, 170 * s
    panel(d, [x0, y0, x0 + 620 * s, y0 + 150 * s], int(14 * s), (*DARK, int(200 * k)))
    d.rectangle([x0, y0, x0 + 10 * s, y0 + 150 * s], fill=(*YELLOW, a))
    d.text((x0 + 34 * s, y0 + 18 * s), "1994", font=font(64 * s), fill=(*YELLOW, a))
    d.text((x0 + 34 * s, y0 + 92 * s), "PIT-LANE SPEED LIMITS ARRIVE", font=font(30 * s), fill=(*WHITE, a))
    img.alpha_composite(ov)


def main():
    frames = sorted(glob.glob(os.path.join(FR, "*.jpg")) + glob.glob(os.path.join(FR, "*.png")))
    if not frames: sys.exit("no rendered frames")
    shutil.rmtree(OUTF, ignore_errors=True); os.makedirs(OUTF)
    W, H = Image.open(frames[0]).size
    words = TL["words"]
    w_min = next((w["s"] for w in words if w["w"].lower().startswith("minute")), None)
    f_lap = int(round((w_min - 0.6) * FPS)) if w_min else SH["S12"][0] + 60
    w_rec = next((w["s"] for w in words if w["w"].lower().startswith("record")), None)
    f_rec = int(round(w_rec * FPS)) if w_rec else SH["S13"][0] + 60
    for path in frames:
        f = int(os.path.splitext(os.path.basename(path))[0])
        img = Image.open(path).convert("RGBA")
        if SH["S02"][0] + 4 <= f <= SH["S02"][1] - 2: title_card(img, f, SH["S02"][0] + 4, SH["S02"][1] - 2, W, H)
        if f_lap <= f <= SH["S13"][1] - 4: lap_tag(img, f, f_lap, W, H, record_from=f_rec)
        if SH["S14"][0] + 6 <= f <= SH["S14"][1] - 2: year_card(img, f, SH["S14"][0] + 6, SH["S14"][1] - 2, W, H)
        img.convert("RGB").save(os.path.join(OUTF, os.path.basename(path)[:-4] + ".jpg"), quality=94)
    # captions (skill) at the render resolution + mix -> mp4
    brand = {"accent": "#FFD43B", "font": "Montserrat ExtraBold", "caption_size": 0.047}
    ass = s3d_audio.captions(TL, brand, os.path.join(VD, "build", "captions.ass"), (W, H))
    mix = os.path.join(VD, "build", "audio", "mix.wav")
    out = os.path.join(VD, "final.mp4" if FINAL else "preview.mp4")
    esc = lambda p: p.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    vf = f"subtitles='{esc(ass)}':fontsdir='{esc(os.path.join(SKILL, 'library', 'fonts'))}',format=yuv420p"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-start_number", "0", "-i", os.path.join(OUTF, "%05d.jpg"),
           "-i", mix, "-vf", vf, "-c:v", "libx264", "-preset", "slow" if FINAL else "medium", "-crf", "16" if FINAL else "20",
           "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", out]
    subprocess.run(cmd, check=True)
    print("[post]", out, os.path.getsize(out) // 1000, "KB")


if __name__ == "__main__":
    main()
