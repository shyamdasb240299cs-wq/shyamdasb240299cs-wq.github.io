#!/usr/bin/env python3
"""shorts3d-studio CLI  (compact output; full logs in videos/<slug>/build/logs/)

  s3d.py setup                      download CC0 human data + packs, find Blender, make SFX
  s3d.py doctor                     check everything (Blender, ffmpeg, data, TTS)
  s3d.py new <slug>                 start a video folder from the template
  s3d.py voice <slug>               narration -> audio/vo.wav + words.json
  s3d.py plan <slug>                time every shot/action from the narration -> build/plan.json
  s3d.py build <slug> [--q sheet|blocking|preview|final|cycles] [--shots S01,S02] [--scene X]
  s3d.py assemble <slug>            frames + captions + VO + SFX + music -> final.mp4
  s3d.py analyze <video>            frame-by-frame breakdown of a reference video
  s3d.py assets <query>             search CC0 Poly Haven models/HDRIs
  s3d.py brand --name N --color #hex [--logo file.png]
"""
import argparse, json, os, re, sys, subprocess, shutil, glob, math, time, zipfile, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
HOME = os.environ.get("S3D_HOME", os.path.expanduser("~/.shorts3d"))
CFG = os.path.join(HOME, "config.json")
PACKS = [
    "https://files.makehumancommunity.org/asset_packs/makehuman_system_assets/makehuman_system_assets_cc0.zip",
    "https://files.makehumancommunity.org/functional/faceunits01.zip",
    "https://files.makehumancommunity.org/functional/visemes01.zip",
    "https://files.makehumancommunity.org/functional/visemes02.zip",
    "https://files.makehumancommunity.org/asset_packs/skins01/skins01_cc0.zip",
    "https://files.makehumancommunity.org/asset_packs/eyebrows01/eyebrows01_cc0.zip",
    "https://files.makehumancommunity.org/asset_packs/eyelashes01/eyelashes01_cc0.zip",
    "https://files.makehumancommunity.org/asset_packs/shirts01/shirts01_cc0.zip",
    "https://files.makehumancommunity.org/asset_packs/pants01/pants01_cc0.zip",
    "https://files.makehumancommunity.org/asset_packs/shoes01/shoes01_cc0.zip",
]
MPFB_ZIP = "https://github.com/makehumancommunity/mpfb2/archive/refs/heads/master.zip"


# ---------------------------------------------------------------- config / io
def cfg():
    c = json.load(open(CFG)) if os.path.exists(CFG) else {}
    c.setdefault("mh_data", os.path.join(HOME, "mh", "data"))
    c.setdefault("mh_assets", os.path.join(HOME, "mh", "assets"))
    c.setdefault("chars", os.path.join(HOME, "library", "characters"))
    c.setdefault("brand", os.path.join(HOME, "brand.json"))
    c.setdefault("tts", {"engine": "kokoro", "voice": "am_michael", "speed": 1.05, "python": sys.executable})
    return c


def save_cfg(c):
    os.makedirs(HOME, exist_ok=True); json.dump(c, open(CFG, "w"), indent=1)


def load_spec(path):
    txt = open(path, encoding="utf-8").read()
    if path.endswith(".json"):
        return json.loads(txt)
    try:
        import yaml
    except ImportError:
        sys.exit("pip install pyyaml   (or write the spec as .json)")

    class L(yaml.SafeLoader):  # YAML 1.1 would turn `on:`/`yes:` into booleans; keep only true/false
        pass
    L.yaml_implicit_resolvers = {k: [r for r in v if r[0] != "tag:yaml.org,2002:bool"] for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()}
    L.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF"))
    return yaml.load(txt, Loader=L)


def vdir(slug):
    for cand in (slug, os.path.join("videos", slug)):
        if os.path.isdir(cand):
            return os.path.abspath(cand)
    sys.exit(f"no video folder '{slug}' (run: s3d.py new {slug})")


def spec_path(d):
    for n in ("shots.yaml", "shots.yml", "shots.json"):
        if os.path.exists(os.path.join(d, n)):
            return os.path.join(d, n)
    sys.exit("missing shots.yaml")


def find_blender(c=None):
    c = c or cfg()
    cands = [os.environ.get("S3D_BLENDER"), c.get("blender"), shutil.which("blender")]
    cands += sorted(glob.glob("C:/Program Files/Blender Foundation/Blender */blender.exe"), reverse=True)
    cands += ["/Applications/Blender.app/Contents/MacOS/Blender"]
    cands += sorted(glob.glob(os.path.expanduser("~/blender*/blender")) + glob.glob("/opt/blender*/blender"), reverse=True)
    for b in cands:
        if b and os.path.exists(b):
            return b
    return None


def run(cmd, log=None, quiet=True, env=None):
    if log:
        os.makedirs(os.path.dirname(log), exist_ok=True)
        with open(log, "w") as f:
            p = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, env=env)
        return p.returncode
    return subprocess.run(cmd, stdout=subprocess.DEVNULL if quiet else None, stderr=subprocess.STDOUT if quiet else None, env=env).returncode


def brand(c=None):
    c = c or cfg()
    b = {"name": "My Channel", "color": "#3EC6E0", "accent": "#FFD43B", "bg": "#3EC6E0", "shirt_color": None,
         "logo": os.path.join(HOME, "brand_logo.png"), "font": "Montserrat ExtraBold", "caption_size": 0.047}
    if os.path.exists(c["brand"]):
        b.update(json.load(open(c["brand"])))
    return b


# ---------------------------------------------------------------- setup / doctor
def _dl(url, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if not os.path.exists(dst):
        print(f"  downloading {os.path.basename(dst)} ...")
        req = urllib.request.Request(url, headers={"User-Agent": "shorts3d-studio/1.0"})
        with urllib.request.urlopen(req, timeout=600) as r, open(dst + ".part", "wb") as f:
            shutil.copyfileobj(r, f)
        os.replace(dst + ".part", dst)
    return dst


def _mpfb_data(dst_dir, dl):
    """MakeHuman base data (CC0 assets inside the MPFB repo): zip, codeload, or git clone fallback."""
    for url in (MPFB_ZIP, "https://codeload.github.com/makehumancommunity/mpfb2/zip/refs/heads/master"):
        try:
            z = _dl(url, os.path.join(dl, "mpfb2.zip"))
            with zipfile.ZipFile(z) as zf:
                for n in zf.namelist():
                    m = re.match(r"^[^/]+/src/mpfb/data/(.+)$", n)
                    if m and not n.endswith("/"):
                        p = os.path.join(dst_dir, m.group(1)); os.makedirs(os.path.dirname(p), exist_ok=True)
                        with zf.open(n) as src, open(p, "wb") as out: shutil.copyfileobj(src, out)
            return True
        except Exception as e:
            print(f"  ({url.split('/')[2]} failed: {e}; trying next source)")
    if shutil.which("git"):
        tmp = os.path.join(dl, "mpfb2_git")
        shutil.rmtree(tmp, ignore_errors=True)
        if subprocess.run(["git", "clone", "-q", "--depth", "1", "https://github.com/makehumancommunity/mpfb2.git", tmp]).returncode == 0:
            shutil.copytree(os.path.join(tmp, "src", "mpfb", "data"), dst_dir, dirs_exist_ok=True)
            shutil.rmtree(tmp, ignore_errors=True)
            return True
    return False


def cmd_setup(a):
    c = cfg(); os.makedirs(HOME, exist_ok=True)
    dl = os.path.join(HOME, "downloads")
    if not os.path.exists(os.path.join(c["mh_data"], "3dobjs", "base.obj")):
        if not _mpfb_data(c["mh_data"], dl):
            sys.exit("could not download the MakeHuman base data (check internet / install git)")
        print("  MakeHuman base data ready (CC0 assets from the MPFB repository)")
    for u in PACKS:
        z = _dl(u, os.path.join(dl, os.path.basename(u)))
        mark = os.path.join(c["mh_assets"], ".done_" + os.path.basename(u))
        if not os.path.exists(mark):
            with zipfile.ZipFile(z) as zf:
                zf.extractall(c["mh_assets"])
            open(mark, "w").close()
    os.makedirs(c["chars"], exist_ok=True)
    for f in glob.glob(os.path.join(SKILL, "library", "characters", "*.json")):
        dst = os.path.join(c["chars"], os.path.basename(f))
        if not os.path.exists(dst): shutil.copy(f, dst)
    b = find_blender(c)
    if b: c["blender"] = b
    save_cfg(c)
    sys.path.insert(0, HERE); import s3d_audio
    s3d_audio.synth_library(os.path.join(HOME, "sfx"))
    if not os.path.exists(c["brand"]):
        cmd_brand(argparse.Namespace(name="My Channel", color="#3EC6E0", accent="#FFD43B", logo=None, font=None))
    print(f"[s3d] setup done. Blender: {b or 'NOT FOUND (set S3D_BLENDER)'}")
    print("  TTS (free, local):  pip install kokoro soundfile && python -m spacy download en_core_web_sm  (+ espeak-ng)")


def cmd_doctor(a):
    c = cfg(); ok = True
    b = find_blender(c)
    ver = subprocess.run([b, "-b", "--version"], capture_output=True, text=True).stdout.splitlines()[0] if b else "missing"
    rows = [("blender", ver), ("ffmpeg", "ok" if shutil.which("ffmpeg") else "missing"),
            ("human data", "ok" if os.path.exists(os.path.join(c["mh_data"], "3dobjs", "base.obj")) else "missing -> s3d.py setup"),
            ("face units", "ok" if os.path.isdir(os.path.join(c["mh_assets"], "targets", "faceunits")) else "missing -> s3d.py setup"),
            ("characters", ", ".join(sorted(os.path.basename(p)[:-5] for p in glob.glob(os.path.join(c["chars"], "*.json")))) or "none"),
            ("brand", brand(c)["name"] + " " + brand(c)["color"])]
    tp = c["tts"].get("python", sys.executable)
    for mod in ("kokoro", "edge_tts", "faster_whisper"):
        r = subprocess.run([tp, "-c", f"import {mod}"], capture_output=True)
        rows.append((f"tts:{mod}", "ok" if r.returncode == 0 else "-"))
    try:
        import yaml  # noqa
        rows.append(("pyyaml", "ok"))
    except ImportError:
        rows.append(("pyyaml", "missing (pip install pyyaml)"))
    for k, v in rows:
        print(f"  {k:14s} {v}")
        ok &= "missing" not in str(v)
    print("[s3d] doctor:", "ALL GOOD" if ok else "fix the 'missing' rows")


def cmd_new(a):
    d = os.path.join("videos", a.slug); os.makedirs(os.path.join(d, "audio"), exist_ok=True)
    src = os.path.join(SKILL, "templates", a.template + ".yaml")
    dst = os.path.join(d, "shots.yaml")
    if not os.path.exists(dst):
        shutil.copy(src, dst)
    print(f"[s3d] {dst} created from {a.template} template")


def cmd_brand(a):
    c = cfg(); b = brand(c)
    b.update({k: v for k, v in (("name", a.name), ("color", a.color), ("accent", getattr(a, "accent", None))) if v})
    b["bg"] = b["color"]
    if getattr(a, "logo", None):
        dst = os.path.join(HOME, "brand_logo.png"); shutil.copy(a.logo, dst); b["logo"] = dst
    elif not os.path.exists(b["logo"]):
        sys.path.insert(0, HERE); import s3d_audio
        s3d_audio.make_logo(b["name"], b["color"], b["logo"])
    os.makedirs(HOME, exist_ok=True); json.dump(b, open(c["brand"], "w"), indent=1)
    print(f"[s3d] brand saved: {b['name']} {b['color']} logo={b['logo']}")


# ---------------------------------------------------------------- voice
def narration(spec):
    return " ".join(s.get("say", "").strip() for s in spec["shots"] if s.get("say", "").strip())


def cmd_voice(a):
    d = vdir(a.slug); spec = load_spec(spec_path(d)); c = cfg()
    v = {**c["tts"], **spec.get("voice", {})}
    txt = os.path.join(d, "audio", "narration.txt"); os.makedirs(os.path.dirname(txt), exist_ok=True)
    open(txt, "w", encoding="utf-8").write(re.sub(r"\*", "", narration(spec)))
    cmd = [v.get("python", sys.executable), os.path.join(HERE, "s3d_tts.py"), txt, os.path.join(d, "audio"),
           "--engine", v.get("engine", "kokoro"), "--voice", v.get("voice", "am_michael"), "--speed", str(v.get("speed", 1.05))]
    if v.get("audio"): cmd += ["--audio", v["audio"]]
    r = subprocess.run(cmd, capture_output=True, text=True)
    out = [l for l in (r.stdout + r.stderr).splitlines() if "[s3d]" in l or "Error" in l or "error" in l]
    print("\n".join(out[-6:]) or r.stderr[-800:])


# ---------------------------------------------------------------- plan
def _norm(w):
    return re.sub(r"[^a-z0-9']", "", w.lower().replace("’", "'"))


def _match_words(words, shots):
    """Assign each narrated shot a contiguous [i0, i1) range of word timings."""
    toks = [_norm(w["w"]) for w in words]
    i = 0; spans = []
    for s in shots:
        want = [_norm(x) for x in re.sub(r"\*", "", s.get("say", "")).split() if _norm(x)]
        if not want:
            spans.append(None); continue
        j0 = i
        # tolerant scan: find first token of the shot near the cursor
        for k in range(i, min(len(toks), i + 12)):
            if toks[k] == want[0] or toks[k].startswith(want[0][:3]):
                j0 = k; break
        n_left = len(want); j = j0
        while j < len(toks) and n_left > 0:
            if toks[j]: n_left -= 1
            j += 1
        spans.append((j0, j)); i = j
    return spans


def cmd_plan(a, quiet=False):
    d = vdir(a.slug); spec = load_spec(spec_path(d)); c = cfg(); b = brand(c)
    fps = int(spec.get("fps", 30)); tail = float(spec.get("tail", 0.6))
    wpath = os.path.join(d, "audio", "words.json")
    shots = spec["shots"]
    if os.path.exists(wpath):
        words = json.load(open(wpath))
    else:  # estimate (2.7 words/s) so you can block shots before recording the voice
        words, t = [], 0.25
        for w in narration(spec).split():
            dur = 0.12 + 0.055 * len(_norm(w)); words.append({"w": w, "s": t, "e": t + dur}); t += dur + (0.18 if w[-1] in ".!?" else 0.04)
    spans = _match_words(words, shots)
    T = 0.0; tl = []
    for k, (s, sp) in enumerate(zip(shots, spans)):
        if sp:
            a0 = 0.0 if k == 0 else words[sp[0]]["s"] - 0.05
            nxt = next((spans[m] for m in range(k + 1, len(shots)) if spans[m]), None)
            b0 = (words[nxt[0]]["s"] - 0.05) if nxt else words[sp[1] - 1]["e"] + tail
            dur = (b0 - a0) + float(s.get("hold", 0))
        else:
            a0 = b0 = None; dur = float(s.get("dur", 1.5))
        if s.get("dur") and sp:
            dur = max(dur, float(s["dur"]))
        t0, t1 = T, T + dur
        tl.append({"id": s["id"], "scene": s.get("scene"), "t0": round(t0, 3), "t1": round(t1, 3), "vo": [a0, b0] if sp else None,
                   "f0": round(t0 * fps), "f1": max(round(t0 * fps) + 1, round(t1 * fps) - 1)})
        T = t1
    # word times on the video clock
    vwords = []
    for s, sp, row in zip(shots, spans, tl):
        if sp:
            for w in words[sp[0]:sp[1]]:
                vwords.append({**w, "s": round(row["t0"] + (w["s"] - row["vo"][0]), 3), "e": round(row["t0"] + (w["e"] - row["vo"][0]), 3), "shot": s["id"]})
    sid = {r["id"]: r for r in tl}

    def anchor(v, shot_row=None):
        if v is None: return None
        if isinstance(v, (int, float)):
            return shot_row["t0"] + float(v) if shot_row else float(v)
        v = str(v).strip()
        m = re.match(r"^(S[\w-]+)(\.end)?([+-][\d.]+)?$", v)
        if m and m.group(1) in sid:
            r = sid[m.group(1)]; return (r["t1"] if m.group(2) else r["t0"]) + float(m.group(3) or 0)
        m = re.match(r"^w:([\w']+)(?:#(\d+))?([+-][\d.]+)?$", v)
        if m:
            hits = [w for w in vwords if _norm(w["w"]) == _norm(m.group(1))]
            if hits:
                return hits[min(len(hits), int(m.group(2) or 1)) - 1]["s"] + float(m.group(3) or 0)
            print(f"  ! word anchor {v} not found")
        try:
            return float(v)
        except ValueError:
            return None
    scenes = {}
    for nm, sc in spec.get("scenes", {}).items():
        S = {k: sc.get(k) for k in ("set", "props", "cast", "fx") if sc.get(k) is not None}
        S["script"] = []
        for it in sc.get("script", []) or []:
            (key, args), = it.items() if isinstance(it, dict) and len(it) == 1 else (("?", {}),)
            who, _, verb = key.partition(".")
            args = dict(args or {})
            if "at" in args:
                args["at_s"] = anchor(args.pop("at"))
            S["script"].append({"who": who, "verb": verb, "args": args})
        for fxi in S.get("fx", []) or []:  # scene fx: time range = start/end anchors (from/to are positions)
            fxi["f0"] = round((anchor(fxi.pop("start", 0.0)) or 0.0) * fps)
            fxi["f1"] = round((anchor(fxi.pop("end", None)) or 1e4) * fps)
        scenes[nm] = S
    out_shots = []
    for s, row in zip(shots, tl):
        o = {"id": s["id"], "scene": s.get("scene"), "f0": row["f0"], "f1": row["f1"], "cam": s.get("cam", {}),
             "overlays": s.get("overlays", [])}
        fxl = []
        def rel(v, end=False):  # numbers are seconds inside the shot (negative 'end' counts from the shot end)
            if isinstance(v, (int, float)):
                return (row["t1"] + v) if (end and v < 0) else (row["t0"] + v)
            return anchor(v)
        for fxi in s.get("fx", []) or []:
            fxi = dict(fxi)
            if "start" in fxi: fxi["f0"] = round(rel(fxi.pop("start")) * fps)
            if "end" in fxi: fxi["f1"] = round(rel(fxi.pop("end"), True) * fps)
            fxl.append(fxi)
        o["fx"] = fxl
        out_shots.append(o)
    plan = {"fps": fps, "res": spec.get("res", [1080, 1920]), "brand": b,
            "paths": {"mh_data": c["mh_data"], "mh_assets": c["mh_assets"], "chars": c["chars"], "cache": HOME},
            "scenes": scenes, "shots": out_shots}
    bd = os.path.join(d, "build"); os.makedirs(bd, exist_ok=True)
    json.dump(plan, open(os.path.join(bd, "plan.json"), "w"), indent=1)
    sfx = []
    for s, row in zip(shots, tl):
        for q in s.get("sfx", []) or []:
            at = q.get("at", 0)
            t = row["t0"] + float(at) if isinstance(at, (int, float)) else anchor(at)
            sfx.append({"name": q["name"], "t": round(t, 3), "gain": q.get("gain", 0)})
    json.dump({"fps": fps, "shots": tl, "words": vwords, "sfx": sfx, "duration": round(T, 3), "title": spec.get("title", ""),
               "music": spec.get("music", {}), "publish": spec.get("publish", {}), "captions": spec.get("captions", {}),
               "emph": sorted({_norm(w) for s in shots for m in re.findall(r"\*([^*]+)\*", s.get("say", "")) for w in m.split()})},
              open(os.path.join(bd, "timeline.json"), "w"), indent=1)
    if not quiet:
        src = "voice" if os.path.exists(wpath) else "ESTIMATE (run voice)"
        print(f"[s3d] plan: {len(tl)} shots, {T:.1f}s @ {fps}fps, timing from {src}")
        print("  " + "  ".join(f"{r['id']}:{r['t1'] - r['t0']:.1f}s" for r in tl))
        long_ = [r["id"] for r in tl if r["t1"] - r["t0"] > 4.0]
        if long_: print(f"  ! shots over 4s (split for retention): {', '.join(long_)}")
    return plan


# ---------------------------------------------------------------- build
def cmd_build(a):
    d = vdir(a.slug); c = cfg()
    plan = cmd_plan(a, quiet=True)
    bl = find_blender(c)
    if not bl: sys.exit("Blender not found: set S3D_BLENDER or install Blender 4.2+")
    scenes = [a.scene] if a.scene else sorted({s["scene"] for s in plan["shots"] if not a.shots or s["id"] in a.shots.split(",")})
    issues = []; t0 = time.time()
    procs = []
    for sc in scenes:
        out = os.path.join(d, "build", sc)
        if a.q == "sheet" and os.path.isdir(os.path.join(out, "sheet")):
            shutil.rmtree(os.path.join(out, "sheet"))
        cmd = [bl, "-b", "--factory-startup", "-P", os.path.join(SKILL, "engine", "build.py"), "--",
               os.path.join(d, "build", "plan.json"), sc, out, a.q] + ([f"shots={a.shots}"] if a.shots else []) + ([f"step={a.step}"] if a.step else []) + (["fast=1"] if a.fast else [])
        log = os.path.join(d, "build", "logs", f"{sc}_{a.q}.log")
        procs.append((sc, log, subprocess.Popen(cmd, stdout=open(_mk(log), "w"), stderr=subprocess.STDOUT)))
        if len([p for p in procs if p[2].poll() is None]) >= a.jobs:
            procs[-1][2].wait()
    for sc, log, p in procs:
        p.wait()
        txt = open(log, errors="ignore").read()
        for l in txt.splitlines():
            if l.startswith("[s3d] QA"): issues.append(f"{sc}: {l[9:]}")
        if "[s3d] DONE" not in txt:
            tb = [l for l in txt.splitlines() if l.strip() and not l.startswith(("Fra:", "EGL"))][-10:]
            print(f"[s3d] build FAILED for scene {sc} (log {log}):\n  " + "\n  ".join(tb)); return
    msg = f"[s3d] build {a.q}: {len(scenes)} scene(s) in {time.time() - t0:.0f}s"
    if a.q == "sheet":
        sheet = make_sheet(d, scenes)
        msg += f"  sheet -> {sheet}"
    print(msg)
    print("  QA: OK" if not issues else "  QA issues:\n   - " + "\n   - ".join(issues))


def _mk(p):
    os.makedirs(os.path.dirname(p), exist_ok=True); return p


def make_sheet(d, scenes, cols=9):
    imgs = sorted(glob.glob(os.path.join(d, "build", "*", "sheet", "*.jpg")), key=lambda p: os.path.basename(p))
    out = os.path.join(d, "build", "sheet.jpg")
    try:
        from PIL import Image, ImageDraw
        tw, th = 180, 320
        rows = math.ceil(len(imgs) / cols)
        sheet = Image.new("RGB", (cols * tw, rows * (th + 16)), (24, 24, 24)); dr = ImageDraw.Draw(sheet)
        for i, p in enumerate(imgs):
            im = Image.open(p).convert("RGB").resize((tw, th)); x, y = (i % cols) * tw, (i // cols) * (th + 16)
            sheet.paste(im, (x, y + 16)); dr.text((x + 4, y + 2), os.path.basename(p)[6:-4], fill=(255, 220, 0))
        sheet.save(out, quality=82)
    except ImportError:
        lst = os.path.join(d, "build", "sheet.txt")
        open(lst, "w").write("".join(f"file '{p}'\n" for p in imgs))
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-vf",
                        f"scale=180:320,tile={cols}x{math.ceil(len(imgs) / cols)}:padding=4", "-frames:v", "1", out])
    return out


# ---------------------------------------------------------------- assemble
def cmd_assemble(a):
    d = vdir(a.slug); c = cfg(); b = brand(c)
    cmd_plan(a, quiet=True)
    tl = json.load(open(os.path.join(d, "build", "timeline.json")))
    plan = json.load(open(os.path.join(d, "build", "plan.json")))
    sys.path.insert(0, HERE); import s3d_audio
    fps = tl["fps"]
    seq = os.path.join(d, "build", "final_frames"); shutil.rmtree(seq, ignore_errors=True); os.makedirs(seq)
    n = 0; ext = None
    for sh, ps in zip(tl["shots"], plan["shots"]):
        fdir = os.path.join(d, "build", ps["scene"], "frames")
        for f in range(ps["f0"], ps["f1"] + 1):
            src = next((p for p in (os.path.join(fdir, f"{f:05d}.png"), os.path.join(fdir, f"{f:05d}.jpg")) if os.path.exists(p)), None)
            if src is None:
                src = last if n else None
                if src is None: sys.exit(f"missing frame {f} of {ps['id']} (run build --q preview|final)")
            ext = os.path.splitext(src)[1]
            dst = os.path.join(seq, f"{n:05d}{ext}")
            try: os.link(src, dst)
            except OSError: shutil.copy(src, dst)
            last = src; n += 1
    dur = n / fps
    ass = s3d_audio.captions(tl, b, os.path.join(d, "build", "captions.ass"), plan["res"])
    events = []
    for f in glob.glob(os.path.join(d, "build", "*", "events.json")):
        events += json.load(open(f))
    mix = s3d_audio.mix(d, tl, events, os.path.join(HOME, "sfx"), dur)
    out = os.path.join(d, "final.mp4")
    vf = f"subtitles='{_esc(ass)}':fontsdir='{_esc(os.path.join(SKILL, 'library', 'fonts'))}'" if not a.nocaps else "null"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps), "-i", os.path.join(seq, f"%05d{ext}"), "-i", mix,
           "-vf", f"{vf},format=yuv420p", "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-profile:v", "high",
           "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", out]
    if subprocess.run(cmd).returncode != 0:
        sys.exit("[s3d] ffmpeg failed")
    meta = s3d_audio.publish_text(tl)
    open(os.path.join(d, "publish.txt"), "w", encoding="utf-8").write(meta)
    print(f"[s3d] final.mp4: {dur:.1f}s {os.path.getsize(out) / 1e6:.1f} MB -> {out}\n  publish text -> {os.path.join(d, 'publish.txt')}")


def _esc(p):
    return p.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


# ---------------------------------------------------------------- analyze / assets
def cmd_analyze(a):
    sys.path.insert(0, HERE); import s3d_analyze
    s3d_analyze.analyze(a.video, a.out or os.path.splitext(a.video)[0] + "_analysis")


def cmd_assets(a):
    kind = "hdris" if a.hdri else "models"
    d = json.load(urllib.request.urlopen(f"https://api.polyhaven.com/assets?type={kind}", timeout=60))
    q = a.query.lower().split()
    hits = [(k, v) for k, v in d.items() if all(w in (k + " " + " ".join(v.get("tags", []) + v.get("categories", []))).lower() for w in q)]
    for k, v in sorted(hits, key=lambda kv: -kv[1].get("download_count", 0))[:a.n]:
        print(f"  poly:{k:32s} {', '.join(v.get('categories', [])[:3])}")
    print(f"[s3d] {len(hits)} CC0 {kind} match '{a.query}' (use src: poly:<id>  |  set.world.hdri: <id>)")


def main():
    ap = argparse.ArgumentParser(description="shorts3d-studio")
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("setup"); sp.add_parser("doctor")
    p = sp.add_parser("new"); p.add_argument("slug"); p.add_argument("--template", default="explainer")
    for nm in ("voice", "plan", "assemble"):
        p = sp.add_parser(nm); p.add_argument("slug")
        if nm == "assemble": p.add_argument("--nocaps", action="store_true")
    p = sp.add_parser("build"); p.add_argument("slug"); p.add_argument("--q", default="sheet",
                                                                     choices=["sheet", "blocking", "draft", "preview", "final", "cycles"])
    p.add_argument("--shots"); p.add_argument("--scene"); p.add_argument("--jobs", type=int, default=1); p.add_argument("--step", type=int)
    p.add_argument("--fast", action="store_true", help="sheet stills in solid shading (seconds; checks motion/contacts/framing)")
    p = sp.add_parser("analyze"); p.add_argument("video"); p.add_argument("--out")
    p = sp.add_parser("assets"); p.add_argument("query"); p.add_argument("--hdri", action="store_true"); p.add_argument("-n", type=int, default=12)
    p = sp.add_parser("brand"); p.add_argument("--name"); p.add_argument("--color"); p.add_argument("--accent"); p.add_argument("--logo"); p.add_argument("--font")
    a = ap.parse_args()
    {"setup": cmd_setup, "doctor": cmd_doctor, "new": cmd_new, "voice": cmd_voice, "plan": cmd_plan, "build": cmd_build,
     "assemble": cmd_assemble, "analyze": cmd_analyze, "assets": cmd_assets, "brand": cmd_brand}[a.cmd](a)


if __name__ == "__main__":
    main()
