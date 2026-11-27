---
name: shorts3d-studio
description: Make realistic 3D animated YouTube Shorts / Reels / TikToks in Blender — "how it works / why / what if" explainers about everyday things. Turns a one-line idea into a hooked, looping script, a voiceover with word timings, a compact shot spec, contact-accurate character animation with realistic free actors and real mocap body language (walking, reaching, grasping, eating, gestures, facial expressions), explainer FX (x-ray, cutaways, arrows, flow), brand-consistent renders on a laptop GPU (fast preview, then Cycles final), captions, SFX and a finished 9:16 MP4. Use whenever the user wants to create, script, storyboard, animate, render or fix a 3D short video, import a character, or analyze a reference video's style.
---

# shorts3d-studio

You are the director. The engine is the crew. **You write ONE file per video (`shots.yaml`) and run commands; the
engine does the 3D work** (actors, mocap, physics-correct contacts, cameras, lighting, QA). Never hand-write bpy for
things the spec covers — that is where tokens and realism are lost.

`S = <this skill dir>` · CLI = `python S/bin/s3d.py` · workspace = the folder with `CHANNEL.md`

## Session start (cheap context)
1. In the channel workspace read **`CHANNEL.md` only** (brand, cast, voice, decisions, learned fixes, video log) and
   the current video's folder. **Never open other videos' folders or old builds** — CHANNEL.md is the memory.
2. No `CHANNEL.md`? First time: `doctor` → `setup --actors` → `gpu --bench` → `brand --name … --color …` → `init`.
3. When the user states a preference or a fix works, add one line to CHANNEL.md (Decisions / Learned).

## Golden rules
1. **Load docs on demand** (table below). Do not read engine source unless a feature is truly missing.
2. **Hook in frame 0**: hands acting on the everyday object, mid-action, narration starts at 0.0 s.
3. **One visual idea per shot, 1.5–3.5 s per shot**; the planner warns at >4 s → split.
4. **Look at ONE image per iteration** (`build/sheet.jpg`: shot stills + automatic close-ups of every grasp); fix only
   flagged shots (`--shots S03,S05`).
5. **Trust QA/inspect text before pixels**: they name frames + the fix.
6. Realism comes from *direction*: real object scale (`size_cm`), props `on:` surfaces, actor starts where the action
   is, eyes→head→hand order (automatic), a **mocap clip** for explaining/reacting body language, readable faces.
7. Max 3 fix loops on sheets; the user reviews **preview.mp4** before any final render. Keep chat output short.
8. Originality: own brand (`brand`), MIT/CC0/CC-BY assets with credits, no real logos on props. Fact-check claims.

## Pipeline
| Step | Do | Command / output |
|---|---|---|
| 1 story | idea → angle → 3 hooks → beats → narration (100–130 words ≈ 40–50 s, loop ending) | load `reference/story.md`; show the user: hook + narration + title (one message). Proceed unless they object. |
| 2 spec | `new <slug> --template explainer|howitworks|whatif` → edit `videos/NNN-<slug>/shots.yaml` | cheat-sheet below; full DSL `reference/spec.md` |
| 3 voice + plan | TTS + word timings, then time shots/actions | `voice <slug>` · `plan <slug>` → fix shots >4 s |
| 4 block | stills + QA | `build <slug> --q sheet --fast` (seconds) → Read `build/sheet.jpg` + QA → edit → repeat ≤3; then one `--q sheet` (real look) |
| 5 preview | fast full video for the user | `build <slug> --q preview` → `assemble <slug>` → `inspect <slug>` → send **preview.mp4**; apply notes with `--shots` |
| 6 final | after the user approves | `build <slug> --q cycles` (GPU tier high/mid) or `--q final` (EEVEE) → `assemble` → `inspect --final` → **final.mp4** |
| 7 finish | clean the folder, log it | `finish <slug>` (keeps final.mp4, shots.yaml, publish.txt, voice) + one Learned line in CHANNEL.md |
Long renders: run in background, poll `status <slug>`; interrupted → same command + `--resume`. Laptop details: `reference/render.md`.
Reference study: `analyze <video.mp4>` → every native frame → `report.md` + `shots.jpg` + `hook.jpg`.

## Load only what you need
| Need | Read |
|---|---|
| writing the script / hook / title | `reference/story.md` |
| any spec field you are unsure of | `reference/spec.md` |
| directing actions, faces, gestures, mocap clips | `reference/acting.md` |
| choosing shots / camera moves / composition | `reference/camera.md` |
| showing HOW something works (x-ray, cutaway, gears, circuits, anatomy) | `reference/explainers.md` |
| cars, roads, engines, turbo kits, anything assembled/taken apart on screen | `reference/machines.md` |
| free actors / models / sounds / music, licences | `reference/assets.md` |
| materials, light, look | `reference/look.md` |
| GPU tiers, render times, preview→final, resume | `reference/render.md` |
| something looks wrong (pose, wrist, contact, camera) or before extending code | `reference/lessons.md` |
| the visual language of the genre (data) | `reference/style-bible.md` |
| QA message you don't understand | `reference/qa.md` |
| adding a verb / prop / fx / character type | `reference/engine.md` |

## Spec cheat-sheet (covers ~90% of videos)
```yaml
title: "Why X Happens 🤔"
voice: {engine: kokoro, voice: am_michael, speed: 1.1}     # af_heart female · edge/eleven/file also
scenes:
  kitchen:                                                 # a scene = set + props + cast + performance
    set: {preset: kitchen|studio|room|outdoor|void, light: soft|bright|drama|night|xray, color: "#hex"}
    props:                                                 # src: proc:<table|plate|bread_slice|mug|phone|apple|coin|box>
      table: {src: proc:table, at: [0, 0], surface: true}  #   proc:<gear|spring|coil|wire|pipe|outlet|plug|molecule|cell|…>
      plate: {src: proc:plate, on: table, at: [0.05, -0.18]}   # poly:<polyhaven_id> · file:<path> · lib:<name>
      bun:   {src: poly:hamburger_buns, size_cm: 11, on: plate, yaw: 20, label: front}
    cast:  {hero: {char: rb_male, at: [0.05, -0.64], face: table}}  # realistic actor; starts AT the action; idle mocap auto
    script:                                                # runs in order; `at:` S03 | S03+0.4 | "w:word" | 2.5
      - hero.reach: {target: bun, hand: R, at: 0.1}        # (walk_to {target, stop: reach} only in later shots)
      - hero.grasp: {prop: bun}
      - hero.lift: {to: mouth}
      - hero.bite: {}
      - hero.chew: {cycles: 4, expr: yum}
      - hero.clip: {name: talk, at: S04, dur: 3.2}         # real talking body language while the VO explains
      - hero.face: {expr: surprised, at: "w:weird", dur: 1.2}
shots:
  - id: S01
    scene: kitchen
    say: "If you leave bread out overnight, it turns *hard* as a rock."   # *word* = accent caption
    cam: {size: insert, target: bun, angle: right, move: push_in}         # size: ecu cu mcu medium wide xwide insert macro
    fx: [{type: arrow, from: hero.hand_R, to: bun}]   # xray cutaway arrow label glow glass flow{along} lightning exploded crumbs
    sfx: [{name: whoosh, at: 0}]
```
Verbs: walk_to turn_to look_at reach grasp lift bite chew swallow drink place release rest point wave thumbs_up shrug
facepalm hands_on_hips cross_arms nod shake_head lean crouch stand startle face talk clip wait · prop verbs: pop move
spin fall shake hide show. Clips: talk talk2 talk_excited talk_relaxed think agree disagree shrug laugh present
scratch_head touch_face cheer clap wave look_around waiting yawn. Expressions: happy big_smile delighted yum surprised
shocked scared disgust nauseous pain wince sad angry thinking confused smirk satisfied determined embarrassed
exhausted skeptical relieved excited.

## When something looks wrong
| Symptom (sheet/QA/inspect) | Fix in spec |
|---|---|
| hand can't reach | `walk_to … stop: reach`; move prop nearer the table edge (`at` y toward actor) |
| object floats in the hand / odd grip | check the `grip-*` close-ups; `grip: span|pinch|wrap_top|wrap_side`; real `size_cm`; nearer `hand:` |
| `wrist beyond its natural range` | move the object closer/higher or to that hand's side, or use the other hand |
| odd wrist / elbow / legs on an imported actor | re-import with `add-character` (fixes bones); see `reference/lessons.md` |
| body inside furniture | move `cast.at` back / approach from another side |
| prop floating / sinking | place with `on: <surface>` (never raw z) |
| subject under captions / cut off | change `size`, `angle`, `el`, or target `hero.face` |
| stiff / robotic | a `clip` (talk/think/agree) for the shot, `face` + `look_at`, overlap with `with: prev` |
| inspect: pop at fN | a prop/hand jumps: check `at:` timings around that frame, add `dur`, avoid two verbs on one hand at once |
| blank/grey mechanism shot | parts hidden inside an opaque prop → open scene or `glass` the shell |
| render too slow / out of VRAM | `reference/render.md` (tier, `--q final` instead of cycles, `--resume`) |
| user wants another narrator voice after rendering | audition voices, then `python bin/s3d_revoice.py <video_dir> <voice>` (tts venv): the new take is time-mapped onto the old word timings, so only the mix is redone - no re-render |
