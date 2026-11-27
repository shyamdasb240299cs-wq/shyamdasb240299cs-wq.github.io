"""Remove sponsor logos from a texture atlas while keeping the livery: every pixel takes the colour class that
dominates its neighbourhood (white / red / dark). Logos are thinner than the panels around them, so they vanish;
panel edges stay where they are. Yellow tyre print is darkened. Optional boxes: fill:x0,y0,x1,y1,class  keep:x0,y0,x1,y1"""
import sys, numpy as np
from PIL import Image
from scipy import ndimage as ndi
src, dst = sys.argv[1], sys.argv[2]
A = np.asarray(Image.open(src).convert("RGB")).astype(np.float32); H, W, _ = A.shape
R, G, B = A[..., 0], A[..., 1], A[..., 2]; mx = A.max(-1); mn = A.min(-1)
cls = {"white": (mn > 140) & (mx - mn < 55),
       "red": (R > 80) & (R - G > 55) & (R - B > 45),
       "dark": mx < 90}
win = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].isdigit() else 121
s = 4  # work at 1/4 res for the neighbourhood statistics
frac = {k: ndi.zoom(ndi.uniform_filter(v[::s, ::s].astype(np.float32), win // s), s, order=1)[:H, :W] for k, v in cls.items()}
mean = {}
for k, v in cls.items():   # local mean colour of each class (keeps gentle shading / dirt of the panel)
    m = v[::s, ::s].astype(np.float32)
    num = np.stack([ndi.uniform_filter(A[::s, ::s, c] * m, win // s) for c in range(3)], -1)
    den = ndi.uniform_filter(m, win // s)[..., None]
    loc = num / np.maximum(den, 1e-4)
    glob = A[v].mean(0) if v.any() else np.zeros(3)
    loc = np.where(den > 0.02, loc, glob)
    mean[k] = np.stack([ndi.zoom(loc[..., c], s, order=1)[:H, :W] for c in range(3)], -1)
dom = np.argmax(np.stack([frac["white"], frac["red"], frac["dark"]]), 0)        # 0 white, 1 red, 2 dark
own = np.full((H, W), -1); own[cls["white"]] = 0; own[cls["red"]] = 1; own[cls["dark"]] = 2
out = A.copy()
panel = (dom < 2) & (np.maximum(frac["white"], frac["red"]) > 0.35)            # white/red panel neighbourhoods
m1 = panel & (own != dom)
for k, i in (("white", 0), ("red", 1)):
    mm = m1 & (dom == i); out[mm] = mean[k][mm]
lum = lambda X: X[..., 0] * 0.3 + X[..., 1] * 0.59 + X[..., 2] * 0.11
for k, i in (("white", 0), ("red", 1)):          # anti-aliased logo edges: anything off the local panel colour
    mm = panel & (dom == i) & (np.abs(lum(out) - lum(mean[k])) > 10); out[mm] = mean[k][mm]
yellowish = (R > 110) & (G > 80) & (B < 110) & (R - B > 50) & ~cls["red"]
olive = (R - B > 14) & (G - B > 8) & (mx < 150) & (frac["dark"] > 0.6)
m2 = (dom == 2) & (yellowish | olive | ((mn > 150) & (frac["dark"] > 0.75)))          # tyre print / small labels on carbon
out[m2] = mean["dark"][m2]
for b in sys.argv[3:]:
    if b.startswith(("fill:", "keep:")):
        kind, rest = b.split(":", 1); p = rest.split(","); x0, y0, x1, y1 = map(int, p[:4])
        if kind == "keep": out[y0:y1, x0:x1] = A[y0:y1, x0:x1]
        else: out[y0:y1, x0:x1] = {"white": (222, 220, 226), "red": (150, 18, 14), "dark": (22, 22, 24)}[p[4]]
print(f"panel px changed {int(m1.sum())}, tyre/label px {int(m2.sum())}")
Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).save(dst)
