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
CL = json.load(open(os.path.join(VD, "build", "camlog.json"))); CAM = CL["cam"]; MAPI = CL.get("map", {})
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


def unproject(f, x, y, W, H, z):
    """pixel (x, y) of frame f -> the world point on the plane at height z"""
    c = CAM[str(f)]; th_v = 18.0 / c["lens"]; th_h = th_v * W / H
    d = qmat(c["q"]) @ np.array([(2 * x / W - 1) * th_h, (1 - 2 * y / H) * th_v, -1.0])
    return np.asarray(c["loc"]) + (z - c["loc"][2]) / d[2] * d


def depth(f, P):
    c = CAM[str(f)]; return float(-(qmat(c["q"]).T @ (np.asarray(P) - np.asarray(c["loc"])))[2])


def ease(t): t = min(max(t, 0.0), 1.0); return 1 - (1 - t) ** 3


# ---------------------------------------------------------------- map labels (2D, crisp, collision-free)
def map_label_specs():
    """variants (lines), colour, font size (x W), frame in, frame out, anchor = the feature it names (fraction of the
    frame at the map's closest framing). Orientation labels leave when the head / the numbers take over."""
    Lr, Lg = MAPI["len"]; s08, s10, s11 = SH["S08"][0], SH["S10"][0], SH["S11"][0]; f_out = SH["S12"][0] + 6
    GOLD, RED = (255, 198, 66), (255, 104, 82)
    return [(["GODDARDS"], WHITE, 0.044, s08 + 18, s11 + 2, (0.54, 0.04)),
            (["1993 PIT ENTRY", "1993|PIT ENTRY"], WHITE, 0.040, s08 + 30, s10, (0.69, 0.50)),
            ([f"-{round((Lr - Lg) / 10) * 10:.0f} m"], GOLD, 0.090, s11 + 18, f_out, (0.52, 0.56)),
            ([f"{Lr:.0f} m"], RED, 0.056, s11 + 2, f_out, (0.33, 0.22)),
            ([f"{Lg:.0f} m"], GOLD, 0.056, s11 + 8, f_out, (0.45, 0.40)),
            (["NO SPEED LIMIT", "NO SPEED|LIMIT"], WHITE, 0.040, s11 + 30, f_out, (0.30, 0.55))]


def text_size(lines, fnt):
    bbs = [fnt.getbbox(t) for t in lines]; lh = fnt.size * 1.08
    return max(b[2] - b[0] for b in bbs), lh * (len(lines) - 1) + (bbs[-1][3] - bbs[0][1])


def layout_map_labels(W, H):
    """Each label goes to the free spot nearest the feature it names: clear (with a margin) of every bright map pixel in
    every frame it is on screen (lines, routes, the moving ring/head), of the labels it shares the screen with, of the
    captions band and of the Shorts UI (top bar, right-hand buttons, bottom). Layout runs at 432 px wide."""
    Wa = 432; Ha = int(round(Wa * H / W)); ref = SH["S11"][1]; zl = MAPI["z"] + 2.5
    Pc = unproject(ref, W / 2, H / 2, W, H, zl); d_ref = depth(ref, Pc); cache = {}

    def fmask(f):              # bright pixels of frame f in the reference framing (the map camera only dollies in)
        if f not in cache:
            p = os.path.join(FR, f"{f:05d}.jpg"); cache[f] = None
            if os.path.exists(p):
                im = Image.open(p).convert("L").resize((Wa, Ha)); k = depth(f, Pc) / d_ref
                if k > 1.001:
                    nw, nh = int(round(Wa * k)), int(round(Ha * k)); im = im.resize((nw, nh))
                    l, t = (nw - Wa) // 2, (nh - Ha) // 2; im = im.crop((l, t, l + Wa, t + Ha))
                cache[f] = np.asarray(im) > 60
        return cache[f]

    def box_free(mask, w, h):  # [y, x] = no True pixel in mask[y:y+h, x:x+w]
        S = np.pad(mask.astype(np.int32).cumsum(0).cumsum(1), ((1, 0), (1, 0)))
        return (S[h:, w:] - S[:-h, w:] - S[h:, :-w] + S[:-h, :-w]) == 0

    forb = np.zeros((Ha, Wa), bool)
    forb[:int(0.065 * Ha)] = True; forb[int(0.70 * Ha):] = True                 # top bar; captions + bottom UI
    forb[:, :int(0.045 * Wa)] = True; forb[:, int(0.955 * Wa):] = True
    forb[int(0.45 * Ha):, int(0.85 * Wa):] = True                               # right-hand buttons
    mg = round(0.014 * Wa); placed, out = [], []
    for variants, col, size, fi, fo, anc in map_label_specs():
        occ = np.zeros((Ha, Wa), bool)
        for f in range(fi, min(fo, ref) + 1):
            m = fmask(f)
            if m is not None: occ |= m
        for (bx0, by0, bx1, by1), pfi, pfo in placed:
            if pfi < fo and fi < pfo: occ[max(0, by0 - 4):by1 + 4, max(0, bx0 - 4):bx1 + 4] = True
        best = None
        for sc in (1.0, 0.9, 0.8):
            for vi, var in enumerate(variants):
                lines = var.split("|"); tw, th = text_size(lines, font(size * sc * Wa)); rw, rh = int(tw) + 1, int(th) + 1
                okf = box_free(forb, rw, rh); ok = box_free(occ, rw + 2 * mg, rh + 2 * mg); Hb, Wb = okf.shape
                valid = np.zeros_like(okf); valid[mg:Hb - mg, mg:Wb - mg] = okf[mg:Hb - mg, mg:Wb - mg] & ok[:Hb - 2 * mg, :Wb - 2 * mg]
                ys, xs = np.nonzero(valid)
                if not len(xs): continue
                cost = np.hypot(xs + rw / 2 - anc[0] * Wa, ys + rh / 2 - anc[1] * Ha) + vi * 0.02 * Wa + (1 - sc) * 0.5 * Wa
                i = int(np.argmin(cost))
                if best is None or cost[i] < best[0]: best = (cost[i], xs[i], ys[i], rw, rh, lines, sc)
        if best is None:
            print("[post] WARNING no free spot for map label", variants[0]); continue
        _c, x, y, rw, rh, lines, sc = best; placed.append(((x, y, x + rw, y + rh), fi, fo))
        P = unproject(ref, (x + rw / 2) / Wa * W, (y + rh / 2) / Ha * H, W, H, zl)
        out.append({"lines": lines, "col": col, "px": size * sc * W, "fi": fi, "fo": fo, "P": P, "d_ref": depth(ref, P)})
        print(f"[post] map label {'/'.join(lines)!r}: ({(x + rw / 2) / Wa:.2f}, {(y + rh / 2) / Ha:.2f}) x{sc}")
    for L in out:              # sprite at the reference size: crisp text + soft glow in its own colour
        fnt = font(L["px"]); tw, th = text_size(L["lines"], fnt); g = max(2, int(L["px"] * 0.22))
        im = Image.new("RGBA", (int(tw) + 2 * g + 1, int(th) + 2 * g + 1), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
        y = g - fnt.getbbox(L["lines"][0])[1]
        for t in L["lines"]:
            b = fnt.getbbox(t); d.text((g + (tw - (b[2] - b[0])) / 2 - b[0], y), t, font=fnt, fill=(*L["col"], 255)); y += fnt.size * 1.08
        a = np.asarray(im.filter(ImageFilter.GaussianBlur(g * 0.5))).copy(); a[..., 3] = (a[..., 3] * 0.55).astype(np.uint8)
        L["sprite"] = Image.alpha_composite(Image.fromarray(a), im)
    return out


def draw_map_labels(img, f, W, H, labs):
    for L in labs:
        if not L["fi"] <= f <= L["fo"]: continue
        a = min(ease((f - L["fi"]) / 7.0), ease((L["fo"] - f) / 6.0)); xy = project(f, L["P"], W, H)
        if a <= 0.01 or xy is None: continue
        sc = L["d_ref"] / max(1e-6, depth(f, L["P"])) * (0.82 + 0.18 * ease((f - L["fi"]) / 6.0)); sp = L["sprite"]
        if abs(sc - 1) > 0.01: sp = sp.resize((max(1, round(sp.width * sc)), max(1, round(sp.height * sc))), Image.LANCZOS)
        if a < 0.999:
            arr = np.asarray(sp).copy(); arr[..., 3] = (arr[..., 3] * a).astype(np.uint8); sp = Image.fromarray(arr)
        ov = Image.new("RGBA", img.size, (0, 0, 0, 0)); ov.paste(sp, (int(round(xy[0] - sp.width / 2)), int(round(xy[1] - sp.height / 2))))
        img.alpha_composite(ov)


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
    labs = layout_map_labels(W, H) if MAPI.get("labels") == "post" else []
    for path in frames:
        f = int(os.path.splitext(os.path.basename(path))[0])
        img = Image.open(path).convert("RGBA")
        if labs: draw_map_labels(img, f, W, H, labs)
        if SH["S02"][0] + 4 <= f <= SH["S02"][1] - 2: title_card(img, f, SH["S02"][0] + 4, SH["S02"][1] - 2, W, H)
        if f_lap <= f <= SH["S13"][1] - 4: lap_tag(img, f, f_lap, W, H, record_from=f_rec)
        if SH["S14"][0] + 6 <= f <= SH["S14"][1] - 2: year_card(img, f, SH["S14"][0] + 6, SH["S14"][1] - 2, W, H)
        img.convert("RGB").save(os.path.join(OUTF, os.path.basename(path)[:-4] + ".jpg"), quality=94)
    nf = max(f1 for _f0, f1 in SH.values()) + 1; held, last = 0, None
    for f in range(nf):              # sample render (render.py --step K): hold the last frame in gaps (ffmpeg stops at a gap)
        dst = os.path.join(OUTF, f"{f:05d}.jpg")
        if os.path.exists(dst): last = dst
        elif last: shutil.copyfile(last, dst); held += 1
    if held: print(f"[post] {held} frames held (sample render)")
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
