#!/usr/bin/env python3
"""shorts3d-studio CLI  (compact output; full logs in videos/<slug>/build/logs/)

  s3d.py setup                      download CC0 human data + packs, find Blender, make SFX
  s3d.py doctor                     check everything (Blender, ffmpeg, data, TTS)
  s3d.py init [--name N]            make this folder the channel workspace (CHANNEL.md memory, videos/, assets/)
  s3d.py new <slug>                 start videos/NNN-<slug>/ from a template (logged in CHANNEL.md)
  s3d.py status [slug]              videos + sizes, or render progress/ETA of one video
  s3d.py inspect <slug>             check EVERY frame of preview/final.mp4 (pops, flicker, black, frozen)
  s3d.py finish <slug>              keep deliverables only (final.mp4, shots.yaml, publish.txt, voice), delete the rest
  s3d.py voice <slug>               narration -> audio/vo.wav + words.json
  s3d.py plan <slug>                time every shot/action from the narration -> build/plan.json
  s3d.py build <slug> [--q sheet|blocking|preview|final|cycles] [--shots S01,S02] [--scene X]
  s3d.py assemble <slug>            frames + captions + VO + SFX + music -> final.mp4
  s3d.py analyze <video>            frame-by-frame breakdown of a reference video
  s3d.py assets <query>             search CC0 Poly Haven models/HDRIs
  s3d.py brand --name N --color #hex [--logo file.png]
  s3d.py add-character <file> --name X [--textures dir]   free rigged human -> library (skeleton+face mapped)
  s3d.py gpu [--bench]              detect laptop GPU + VRAM tier, render-time estimates
  s3d.py build ... --resume         continue an interrupted render
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
    c.setdefault("clips", os.path.join(HOME, "clips"))
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


def channel_root(start="."):
    """Folder holding CHANNEL.md (the channel workspace), searching upward; else the current folder."""
    d = os.path.abspath(start)
    while True:
        if os.path.exists(os.path.join(d, "CHANNEL.md")):
            return d
        if os.path.dirname(d) == d:
            return os.path.abspath(start)
        d = os.path.dirname(d)


def vdir(slug):
    root = channel_root()
    for cand in (slug, os.path.join(root, "videos", slug), os.path.join("videos", slug)):
        if os.path.isdir(cand) and (os.path.exists(os.path.join(cand, "shots.yaml")) or os.path.isdir(os.path.join(cand, "build"))):
            return os.path.abspath(cand)
    hits = sorted(glob.glob(os.path.join(root, "videos", f"[0-9][0-9][0-9]-{slug}")))
    if hits:
        return hits[-1]
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
    _rocketbox(c, getattr(a, "actors", False))
    print(f"[s3d] setup done. Blender: {b or 'NOT FOUND (set S3D_BLENDER)'}")
    print("  TTS (free, local):  pip install kokoro soundfile && python -m spacy download en_core_web_sm  (+ espeak-ng)")


def _rocketbox(c, actors=False):
    """Real human mocap clips (+ optional realistic actors) from Microsoft Rocketbox (MIT)."""
    lib = json.load(open(os.path.join(SKILL, "library", "clips", "clips.json")))
    os.makedirs(c["clips"], exist_ok=True)
    n = 0
    for name in lib["clips"]:
        dst = os.path.join(c["clips"], name + ".max.fbx")
        if not os.path.exists(dst) and not os.path.exists(os.path.join(c["clips"], name + ".json")):
            try: _dl(lib["url"].format(name), dst); n += 1
            except Exception as e: print(f"  clip {name}: {e}")
    print(f"  mocap clips ready in {c['clips']} ({len(lib['clips'])} curated, {n} new) - {lib['license']}")
    if not actors:
        return
    raw = "https://raw.githubusercontent.com/microsoft/Microsoft-Rocketbox/master/"
    api = "https://api.github.com/repos/microsoft/Microsoft-Rocketbox/contents/"
    for name, folder in lib["actors"].items():
        if os.path.exists(os.path.join(c["chars"], name + ".blend")):
            continue
        base = os.path.join(HOME, "downloads", name)
        stem = os.path.basename(folder)
        fbx = _dl(raw + f"{folder}/Export/{stem}_facial.fbx", os.path.join(base, "Export", f"{stem}_facial.fbx"))
        try:
            req = urllib.request.Request(api + folder + "/Textures", headers={"User-Agent": "shorts3d-studio/1.0"})
            for it in json.load(urllib.request.urlopen(req, timeout=60)):
                _dl(it["download_url"], os.path.join(base, "Textures", it["name"]))
        except Exception as e:
            print(f"  textures for {name}: {e}")
        cmd_add_character(argparse.Namespace(file=fbx, name=name, textures=os.path.join(base, "Textures")))


TIERS = [  # (min VRAM GB, tier, cycles samples, texture limit, advice)
    (10, "high", 128, "OFF", "Cycles for every shot is fine"),
    (6, "mid", 96, "4096", "Cycles final OK; review with EEVEE previews"),
    (3.5, "low", 64, "2048", "EEVEE final recommended; Cycles only for hero/macro shots (--shots)"),
    (0, "cpu", 32, "1024", "no usable GPU: EEVEE only (Cycles on CPU is very slow)")]


def _vram():
    """(gpu name, VRAM GB) from the driver tools; 0 if unknown."""
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=20)
        if r.returncode == 0 and r.stdout.strip():
            name, mem = r.stdout.strip().splitlines()[0].rsplit(",", 1)
            return name.strip(), round(float(mem) / 1024, 1)
    except Exception:
        pass
    if sys.platform == "darwin":  # Apple silicon: unified memory, Metal may use ~65 %
        try:
            mem = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout) / 2 ** 30
            return "Apple GPU (unified memory)", round(mem * 0.65, 1)
        except Exception:
            pass
    if os.name == "nt":
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-Command",
                                "Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM | ConvertTo-Json"],
                               capture_output=True, text=True, timeout=30)
            d = json.loads(r.stdout); d = d if isinstance(d, list) else [d]
            best = max(d, key=lambda x: x.get("AdapterRAM") or 0)
            return best["Name"], round((best.get("AdapterRAM") or 0) / 2 ** 30, 1)  # (caps at 4 GB on some drivers)
        except Exception:
            pass
    return "", 0.0


def cmd_gpu(a):
    """Detect the laptop GPU, pick a render tier, optionally benchmark (writes config.json -> every build uses it)."""
    c = cfg(); bl = find_blender(c)
    if not bl: sys.exit("Blender not found")
    out = os.path.join(HOME, "gpu.json")
    run([bl, "-b", "--factory-startup", "-P", os.path.join(SKILL, "engine", "gpu.py"), "--", out] + (["bench"] if a.bench else []),
        log=_mk(os.path.join(HOME, "logs", "gpu.log")))
    info = json.load(open(out)) if os.path.exists(out) else {"backend": "NONE", "devices": []}
    name, vram = _vram()
    if info["backend"] == "NONE":
        vram = 0.0
    elif vram == 0:
        vram = 6.0  # GPU present but VRAM unknown: assume a mid laptop GPU
    tier = next(t for t in TIERS if vram >= t[0])
    g = {"backend": info["backend"], "devices": info["devices"], "gpu": name or ", ".join(info["devices"]), "vram_gb": vram,
         "tier": tier[1], "samples": tier[2], "texture_limit": tier[3], "bench": info.get("bench") or c.get("gpu", {}).get("bench")}
    c["gpu"] = g; save_cfg(c)
    print(f"[s3d] GPU: {g['gpu'] or 'none'} ({g['backend']}, {vram:g} GB) -> tier '{tier[1]}': {tier[2]} spp, textures {tier[3]} - {tier[4]}")
    if g["bench"]:
        b = g["bench"]; n = 30 * 45
        print(f"  per 1080x1920 frame: EEVEE ~{b['eevee_spf']} s, Cycles ~{b['cycles_spf']} s ({b['cycles_device']})  ->  "
              f"45 s Short: preview ~{n * b['eevee_spf'] * 0.12 / 60:.0f} min, EEVEE final ~{n * b['eevee_spf'] / 60:.0f} min, "
              f"Cycles ~{n * b['cycles_spf'] * tier[2] / 96 / 60:.0f} min")
    else:
        print("  run 's3d.py gpu --bench' once for render-time estimates")


def _eta(c, q, frames):
    b = (c.get("gpu") or {}).get("bench")
    if not b or q in ("sheet", "blocking"):
        return ""
    spf = {"draft": b["eevee_spf"] * 0.08, "preview": b["eevee_spf"] * 0.12, "final": b["eevee_spf"],
           "cycles": b["cycles_spf"] * (c["gpu"].get("samples", 96) / 96)}.get(q, b["eevee_spf"])
    return f"~{frames * spf / 60:.0f} min estimated ({frames} frames)"


def cmd_doctor(a):
    c = cfg(); ok = True
    b = find_blender(c)
    ver = subprocess.run([b, "-b", "--version"], capture_output=True, text=True).stdout.splitlines()[0] if b else "missing"
    rows = [("blender", ver), ("ffmpeg", "ok" if shutil.which("ffmpeg") else "missing"),
            ("human data", "ok" if os.path.exists(os.path.join(c["mh_data"], "3dobjs", "base.obj")) else "missing -> s3d.py setup"),
            ("face units", "ok" if os.path.isdir(os.path.join(c["mh_assets"], "targets", "faceunits")) else "missing -> s3d.py setup"),
            ("characters", ", ".join(sorted({os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(c["chars"], "*.json")) + glob.glob(os.path.join(c["chars"], "*.blend"))})) or "none"),
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
    root = channel_root(); vids = os.path.join(root, "videos"); os.makedirs(vids, exist_ok=True)
    nums = [int(os.path.basename(p)[:3]) for p in glob.glob(os.path.join(vids, "[0-9][0-9][0-9]-*"))]
    old = glob.glob(os.path.join(vids, f"[0-9][0-9][0-9]-{a.slug}"))
    d = old[0] if old else os.path.join(vids, f"{(max(nums) + 1 if nums else 1):03d}-{a.slug}")
    os.makedirs(os.path.join(d, "audio"), exist_ok=True)
    if not old:
        _log_video(root, os.path.basename(d), "-", "started")
    src = os.path.join(SKILL, "templates", a.template + ".yaml")
    dst = os.path.join(d, "shots.yaml")
    if not os.path.exists(dst):
        shutil.copy(src, dst)
    print(f"[s3d] {dst} created from {a.template} template")


def cmd_add_character(a):
    """Free realistic character (.fbx/.glb/.gltf/.blend/.obj, rigged) -> engine-ready library character."""
    c = cfg(); bl = find_blender(c)
    if not bl: sys.exit("Blender not found")
    log = os.path.join(HOME, "logs", f"add_{a.name}.log")
    cmd = [bl, "-b", "--factory-startup", "-P", os.path.join(SKILL, "engine", "assetchar.py"), "--",
           os.path.abspath(a.file), a.name, c["chars"]] + ([os.path.abspath(a.textures)] if a.textures else [])
    run(cmd, log=_mk(log))
    txt = open(log, errors="ignore").read()
    print("\n".join(l for l in txt.splitlines() if l.startswith("[s3d]")))
    if "saved character" not in txt:
        sys.exit(f"[s3d] add-character failed (log {log})")
    print(f"  use it in shots.yaml:  cast: {{hero: {{char: {a.name}, at: [x, y], face: <target>}}}}")


CHANNEL_MD = """# CHANNEL.md - {name}
> Claude: at the start of a session read THIS file + the current video's folder only. Never open other videos'
> folders or old builds; everything needed for consistency is here. Keep it short: update Decisions/Learned when
> the user states a preference or a fix worked.

## Identity (same in every video)
- Brand: {name} - colour {color} - logo {logo} - captions {caps}
- Voice: {voice} x{speed} - music: auto, -22 dB under the voice
- Cast: {cast}
- Format: 9:16, 30 fps, 35-55 s, hook = hands on the everyday object at frame 0, loop ending
- Render: GPU {gpu} (tier {tier}) - review: `build --q preview` -> preview.mp4 - final: `build --q {final_q}`

## Decisions (user preferences, newest first)
-

## Learned (one line per fix that worked)
-

## Videos
| # | folder | title | status | length |
|---|--------|-------|--------|--------|
"""


def _log_video(root, folder, title, status, length="-"):
    f = os.path.join(root, "CHANNEL.md")
    if not os.path.exists(f):
        return
    lines = open(f, encoding="utf-8").read().rstrip("\n").split("\n")
    row = f"| {folder[:3]} | {folder} | {title} | {status} | {length} |"
    idx = next((i for i, l in enumerate(lines) if l.startswith(f"| {folder[:3]} | {folder} |")), None)
    if idx is None: lines.append(row)
    else: lines[idx] = row
    open(f, "w", encoding="utf-8").write("\n".join(lines) + "\n")


def cmd_init(a):
    """Make the current folder a channel workspace: CHANNEL.md (memory), videos/, assets/."""
    c = cfg(); b = brand(c); root = os.path.abspath(".")
    for sub in ("videos", "assets/models", "assets/characters", "assets/music", "assets/refs"):
        os.makedirs(os.path.join(root, sub), exist_ok=True)
    f = os.path.join(root, "CHANNEL.md")
    if os.path.exists(f):
        print(f"[s3d] {f} exists (kept)"); return
    chars = sorted({os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(c["chars"], "*.blend"))})
    real = [x for x in chars if x.startswith("rb_")] or chars
    g = c.get("gpu") or {}
    open(f, "w", encoding="utf-8").write(CHANNEL_MD.format(
        name=a.name or b["name"], color=b["color"], logo=os.path.basename(b.get("logo", "")), caps="3 words, " + b.get("font", ""),
        voice=c["tts"].get("voice"), speed=c["tts"].get("speed"), cast=", ".join(f"{k}" for k in real[:4]) or "hero (MakeHuman)",
        gpu=g.get("gpu") or "not detected (run s3d.py gpu --bench)", tier=g.get("tier", "?"),
        final_q="cycles" if g.get("tier") in ("high", "mid") else "final"))
    print(f"[s3d] channel workspace ready: {f}\n  new video: s3d.py new <slug>   (creates videos/NNN-<slug>/)")


KEEP = ("shots.yaml", "final.mp4", "publish.txt", "thumbnail.jpg", "audio/vo.wav", "audio/words.json")


def _size(path):
    return sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(path) for f in fs) if os.path.isdir(path) else (
        os.path.getsize(path) if os.path.exists(path) else 0)


def cmd_finish(a):
    """Video done: keep only the deliverables, delete builds/tests/duplicates, log it in CHANNEL.md."""
    d = vdir(a.slug)
    if not os.path.exists(os.path.join(d, "final.mp4")) and not a.force:
        sys.exit("[s3d] no final.mp4 yet (render + assemble first, or --force)")
    before = _size(d)
    keep = {os.path.normpath(os.path.join(d, k)) for k in KEEP}
    for r, dirs, fs in os.walk(d, topdown=False):
        for fn in fs:
            p = os.path.normpath(os.path.join(r, fn))
            if p not in keep:
                os.remove(p)
        if r != d and not os.listdir(r):
            os.rmdir(r)
    after = _size(d)
    spec = load_spec(spec_path(d))
    dur = "-"
    try:
        dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                              os.path.join(d, "final.mp4")], capture_output=True, text=True).stdout.strip()
        dur = f"{float(dur):.0f}s"
    except Exception:
        pass
    _log_video(channel_root(), os.path.basename(d), spec.get("title", "-").replace("|", "/"), "done", dur)
    print(f"[s3d] finished {os.path.basename(d)}: freed {(before - after) / 1e6:.0f} MB, kept {', '.join(sorted(os.path.relpath(k, d) for k in keep if os.path.exists(k)))}")
    print("  add a 1-line 'Learned' note to CHANNEL.md if something new worked in this video")


def cmd_status(a):
    root = channel_root()
    if a.slug:
        d = vdir(a.slug)
        for pj in glob.glob(os.path.join(d, "build", "*", "progress.json")):
            pr = json.load(open(pj)); sc = os.path.basename(os.path.dirname(pj))
            print(f"  {sc}: {pr['q']} {pr['done']}/{pr['total']} frames, {pr['spf']} s/frame, ETA {pr['eta_min']} min")
        outs = [f for f in ("preview.mp4", "final.mp4") if os.path.exists(os.path.join(d, f))]
        print(f"[s3d] {os.path.basename(d)}: {_size(d) / 1e6:.0f} MB, outputs: {', '.join(outs) or 'none'}")
        return
    for v in sorted(glob.glob(os.path.join(root, "videos", "*"))):
        outs = "final" if os.path.exists(os.path.join(v, "final.mp4")) else ("preview" if os.path.exists(os.path.join(v, "preview.mp4")) else "-")
        print(f"  {os.path.basename(v):32s} {_size(v) / 1e6:7.0f} MB  {outs}")
    print(f"[s3d] workspace {root}: {_size(root) / 1e6:.0f} MB")


def cmd_inspect(a):
    """Every frame of preview.mp4/final.mp4 -> flagged frames sheet (pops, flicker, black, frozen)."""
    d = vdir(a.slug)
    vid = next((os.path.join(d, f) for f in ("preview.mp4", "final.mp4") if os.path.exists(os.path.join(d, f))), None)
    if a.final and os.path.exists(os.path.join(d, "final.mp4")):
        vid = os.path.join(d, "final.mp4")
    if not vid:
        sys.exit("[s3d] nothing to inspect: render + assemble first")
    sys.path.insert(0, HERE); import s3d_inspect
    tl = json.load(open(os.path.join(d, "build", "timeline.json")))
    out = os.path.join(d, "build", "inspect.jpg")
    lines, top = s3d_inspect.inspect(vid, tl, out)
    print(f"[s3d] inspected every frame of {os.path.basename(vid)}: " + (f"{len(top)} flags -> {out}" if top else "clean"))
    for l in lines:
        print("  " + l)


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
            "paths": {"mh_data": c["mh_data"], "mh_assets": c["mh_assets"], "chars": c["chars"], "cache": HOME, "clips": c["clips"]},
            "gpu": c.get("gpu", {}),
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
    nfr = sum(s["f1"] - s["f0"] + 1 for s in plan["shots"] if s["scene"] in scenes and (not a.shots or s["id"] in a.shots.split(","))) // (a.step or 1)
    eta = _eta(c, a.q, nfr)
    if eta:
        print(f"[s3d] rendering {a.q}: {eta}  (progress: s3d.py status {a.slug})")
    for sc in scenes:
        out = os.path.join(d, "build", sc)
        if a.q == "sheet" and os.path.isdir(os.path.join(out, "sheet")):
            shutil.rmtree(os.path.join(out, "sheet"))
        cmd = [bl, "-b", "--factory-startup", "-P", os.path.join(SKILL, "engine", "build.py"), "--",
               os.path.join(d, "build", "plan.json"), sc, out, a.q] + ([f"shots={a.shots}"] if a.shots else []) + ([f"step={a.step}"] if a.step else []) + (["fast=1"] if a.fast else []) + (["resume=1"] if a.resume else [])
        log = os.path.join(d, "build", "logs", f"{sc}_{a.q}.log")
        procs.append((sc, log, subprocess.Popen(cmd, stdout=open(_mk(log), "w"), stderr=subprocess.STDOUT)))
        if len([p for p in procs if p[2].poll() is None]) >= a.jobs:
            procs[-1][2].wait()
    for sc, log, p in procs:
        shown = 0
        while p.poll() is None:  # quiet progress: at most 4 lines per scene
            time.sleep(15)
            pj = os.path.join(d, "build", sc, "progress.json")
            if os.path.exists(pj):
                try:
                    pr = json.load(open(pj)); k = int(4 * pr["done"] / max(1, pr["total"]))
                    if k > shown:
                        shown = k; print(f"  {sc}: {pr['done']}/{pr['total']} frames, {pr['spf']} s/frame, ETA {pr['eta_min']} min", flush=True)
                except (ValueError, KeyError):
                    pass
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
    qs = {open(m).read().strip() for m in glob.glob(os.path.join(d, "build", "*", "frames", ".quality"))}
    is_final = bool(qs) and qs <= {"final", "cycles"}
    out = os.path.join(d, "final.mp4" if is_final else "preview.mp4")
    vf = f"subtitles='{_esc(ass)}':fontsdir='{_esc(os.path.join(SKILL, 'library', 'fonts'))}'" if not a.nocaps else "null"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps), "-i", os.path.join(seq, f"%05d{ext}"), "-i", mix,
           "-vf", f"{vf},format=yuv420p", "-c:v", "libx264", "-preset", "slow" if is_final else "veryfast", "-crf", "17" if is_final else "23", "-profile:v", "high",
           "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", out]
    if subprocess.run(cmd).returncode != 0:
        sys.exit("[s3d] ffmpeg failed")
    meta = s3d_audio.publish_text(tl)
    open(os.path.join(d, "publish.txt"), "w", encoding="utf-8").write(meta)
    print(f"[s3d] {os.path.basename(out)}: {dur:.1f}s {os.path.getsize(out) / 1e6:.1f} MB -> {out}\n  publish text -> {os.path.join(d, 'publish.txt')}")


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
    p = sp.add_parser("setup"); p.add_argument("--actors", action="store_true", help="also fetch 2 realistic Rocketbox actors (~120 MB)")
    sp.add_parser("doctor")
    p = sp.add_parser("new"); p.add_argument("slug"); p.add_argument("--template", default="explainer")
    for nm in ("voice", "plan", "assemble"):
        p = sp.add_parser(nm); p.add_argument("slug")
        if nm == "assemble": p.add_argument("--nocaps", action="store_true")
    p = sp.add_parser("build"); p.add_argument("slug"); p.add_argument("--q", default="sheet",
                                                                     choices=["sheet", "blocking", "draft", "preview", "final", "cycles"])
    p.add_argument("--shots"); p.add_argument("--scene"); p.add_argument("--jobs", type=int, default=1); p.add_argument("--step", type=int)
    p.add_argument("--fast", action="store_true", help="sheet stills in solid shading (seconds; checks motion/contacts/framing)")
    p.add_argument("--resume", action="store_true", help="continue an interrupted render (keeps finished frames)")
    p = sp.add_parser("gpu"); p.add_argument("--bench", action="store_true")
    p = sp.add_parser("init"); p.add_argument("--name")
    p = sp.add_parser("finish"); p.add_argument("slug"); p.add_argument("--force", action="store_true")
    p = sp.add_parser("status"); p.add_argument("slug", nargs="?")
    p = sp.add_parser("inspect"); p.add_argument("slug"); p.add_argument("--final", action="store_true")
    p = sp.add_parser("analyze"); p.add_argument("video"); p.add_argument("--out")
    p = sp.add_parser("add-character"); p.add_argument("file"); p.add_argument("--name", required=True); p.add_argument("--textures")
    p = sp.add_parser("assets"); p.add_argument("query"); p.add_argument("--hdri", action="store_true"); p.add_argument("-n", type=int, default=12)
    p = sp.add_parser("brand"); p.add_argument("--name"); p.add_argument("--color"); p.add_argument("--accent"); p.add_argument("--logo"); p.add_argument("--font")
    a = ap.parse_args()
    {"setup": cmd_setup, "doctor": cmd_doctor, "new": cmd_new, "voice": cmd_voice, "plan": cmd_plan, "build": cmd_build,
     "assemble": cmd_assemble, "analyze": cmd_analyze, "assets": cmd_assets, "brand": cmd_brand,
     "add-character": cmd_add_character, "gpu": cmd_gpu, "init": cmd_init, "finish": cmd_finish,
     "status": cmd_status, "inspect": cmd_inspect}[a.cmd](a)


if __name__ == "__main__":
    main()
