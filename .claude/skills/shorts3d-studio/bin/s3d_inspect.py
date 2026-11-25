"""Every-frame inspection of a rendered Short (preview.mp4 / final.mp4): finds the frames a viewer would notice.

  black / blank frame      render or assembly failure
  pop                      sudden jump inside a shot (prop/hand teleport, snapping pose, camera jerk)
  flicker                  brightness alternating frame to frame (light/shadow/alpha noise)
  frozen                   identical frames inside a moving shot (missing frames held by assembly)
Cuts come from the timeline, so planned cuts are never flagged. Output: build/inspect.jpg (prev|frame|next per
flag) + a short text report - Claude reads that instead of watching the video.
needs: ffmpeg, numpy, pillow
"""
import json, os, subprocess


def _gray(video, w=90, h=160):
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", video, "-vf", f"scale={w}:{h},format=gray", "-f", "rawvideo", "-"],
                         capture_output=True).stdout
    n = len(raw) // (w * h)
    return np.frombuffer(raw[:n * w * h], dtype=np.uint8).reshape(n, h, w).astype("float32")


def inspect(video, timeline, out_jpg, max_flags=12):
    import numpy as np
    F = _gray(video)
    n = len(F)
    if n == 0:
        return ["no frames decoded"], []
    fps = timeline.get("fps", 30)
    cuts, names, f = set(), [], 0
    for sh in timeline["shots"]:
        k = int(round((sh["t1"] - sh["t0"]) * fps))
        names.append((f, f + k, sh["id"])); cuts.add(f); f += k
    shot_of = lambda i: next((nm for a, b, nm in names if a <= i < b), names[-1][2] if names else "?")
    mean = F.reshape(n, -1).mean(1); std = F.reshape(n, -1).std(1)
    mad = np.zeros(n); mad[1:] = np.abs(F[1:] - F[:-1]).reshape(n - 1, -1).mean(1)
    flags = []
    for i in range(n):
        if mean[i] < 6 or std[i] < 1.5:
            flags.append((i, "black/blank frame", 99.0))
    for a, b, nm in names:  # per shot, robust to camera moves
        seg = mad[a + 1:b]
        if len(seg) < 4:
            continue
        mov = seg[seg > 0.05]  # held frames (--step 2 drafts) are not motion
        med = float(np.median(mov if len(mov) >= 3 else seg)) + 0.6
        for i in range(a + 1, min(b, n)):
            if any(abs(i - c) <= 1 for c in cuts):
                continue
            nb = np.concatenate([mad[max(a + 1, i - 3):i], mad[i + 1:min(b, n, i + 4)]])
            nb = nb[nb > 0.05]
            base = (float(np.median(nb)) if len(nb) else med) + 0.8
            if mad[i] > max(8.0, 4.0 * base):  # a spike against its neighbours = teleport / snap, not smooth motion
                flags.append((i, f"pop: change x{mad[i] / base:.1f} vs neighbouring frames", mad[i] / base))
        d = np.diff(mean[a:b])
        for i in range(2, len(d)):
            if abs(d[i]) > 2.5 and abs(d[i - 1]) > 2.5 and np.sign(d[i]) != np.sign(d[i - 1]) and np.sign(d[i - 1]) != np.sign(d[i - 2]):
                flags.append((a + i, "flicker: brightness alternates", abs(d[i])))
        if med > 1.6:  # a moving shot
            run = 0
            for i in range(a + 1, min(b, n)):
                run = run + 1 if mad[i] < 0.05 else 0
                if run == 6:
                    flags.append((i - 5, "frozen: identical frames in a moving shot", 5.0))
    flags.sort(key=lambda x: -x[2])
    seen, top = set(), []
    for fl in flags:  # one flag per 0.3 s
        if all(abs(fl[0] - s) > fps * 0.3 for s in seen):
            seen.add(fl[0]); top.append(fl)
        if len(top) >= max_flags:
            break
    top.sort()
    lines = [f"f{i} ({shot_of(i)} @ {i / fps:.2f}s): {msg}" for i, msg, _ in top]
    if top:
        _sheet(video, [i for i, _, _ in top], lines, out_jpg)
    return lines, top


def _sheet(video, idx, labels, out_jpg, w=135, h=240):
    from PIL import Image, ImageDraw
    import numpy as np
    want = sorted({max(0, j) for i in idx for j in (i - 1, i, i + 1)})
    sel = "+".join(f"eq(n\\,{j})" for j in want)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", video, "-vf", f"select='{sel}',scale={w}:{h}", "-vsync", "0",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    imgs = np.frombuffer(raw, dtype=np.uint8)[: len(want) * w * h * 3].reshape(-1, h, w, 3)
    pos = {j: k for k, j in enumerate(want)}
    sheet = Image.new("RGB", (3 * w, len(idx) * (h + 18)), (20, 20, 20)); dr = ImageDraw.Draw(sheet)
    for r, (i, lab) in enumerate(zip(idx, labels)):
        for c, j in enumerate((i - 1, i, i + 1)):
            k = pos.get(max(0, j))
            if k is not None and k < len(imgs):
                sheet.paste(Image.fromarray(imgs[k]), (c * w, r * (h + 18) + 18))
        dr.text((4, r * (h + 18) + 3), lab[:70], fill=(255, 210, 0))
    sheet.save(out_jpg, quality=80)


if __name__ == "__main__":
    import sys
    tl = json.load(open(sys.argv[2]))
    print("\n".join(inspect(sys.argv[1], tl, sys.argv[3])[0]) or "clean")
