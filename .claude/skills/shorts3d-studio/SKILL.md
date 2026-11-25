---
name: shorts3d-studio
description: Make realistic 3D animated YouTube Shorts / Reels / TikToks in Blender — "how it works / why / what if" explainers about everyday things. Turns a one-line idea into a hooked, looping script, a voiceover with word timings, a compact shot spec, contact-accurate character animation (walking, reaching, grasping, eating, gestures, facial expressions), explainer FX (x-ray, cutaways, arrows, flow), brand-consistent renders, captions, SFX and a finished 9:16 MP4. Use whenever the user wants to create, script, storyboard, animate, render or fix a 3D short video, or analyze a reference video's style.
---

# shorts3d-studio

You are the director. The engine is the crew. **You write ONE file per video (`shots.yaml`) and run commands; the
engine does the 3D work** (characters, physics-correct contacts, cameras, lighting, QA). Never hand-write bpy for
things the spec already covers — that is where tokens and realism are lost.

`S = <this skill dir>` · CLI = `python S/bin/s3d.py` · videos live in `./videos/<slug>/`

## Golden rules
1. **Load docs on demand** (table below). Do not read engine source unless a feature is truly missing.
2. **Hook in frame 0**: hands acting on the everyday object, mid-action, narration starts at 0.0 s.
3. **One visual idea per shot, 1.5–3.5 s per shot**; the planner warns at >4 s → split.
4. **Look at ONE image per iteration**: `build/sheet.jpg` (3 stills per shot). Fix only flagged shots (`--shots S03,S05`).
5. **Trust QA text before pixels**: it names frames + the fix (reach, penetration, floating, foot slide, framing).
6. Realism comes from *direction*, not code: correct object scale (`size_cm`), props resting `on:` surfaces,
   actors walking to `stop: reach`, eyes→head→hand order (automatic), readable expressions, motivated camera moves.
7. Max 3 fix loops on the sheet; then render. Keep chat output short: decisions, links, QA summary.
8. Originality: own characters, own brand (`s3d.py brand`), parody/no real brands on props. Fact-check claims.

## Pipeline
| Step | Do | Command / output |
|---|---|---|
| 0 setup (once) | check, install data | `doctor` → if missing: `setup` (CC0 human data, SFX); `brand --name "X" --color "#hex" [--logo f.png]` |
| 1 story | idea → angle → 3 hooks → beats → narration (100–130 words ≈ 40–50 s, loop ending) | load `reference/story.md`; show user: chosen hook + narration + title (one message). Proceed unless they object. |
| 2 spec | `new <slug> --template explainer|howitworks|whatif` then edit `videos/<slug>/shots.yaml` | cheat-sheet below; full DSL `reference/spec.md` |
| 3 voice | TTS + word timings | `voice <slug>` (Kokoro local default) |
| 4 plan | time shots/actions from words | `plan <slug>` → fix any shot >4 s |
| 5 block | stills + QA | `build <slug> --q sheet` → Read `build/sheet.jpg` + QA lines → edit spec → repeat (≤3) |
| 6 render | final frames | `build <slug> --q final` (EEVEE) · macro/glass/liquid shots: `--q cycles --shots ...` |
| 7 finish | captions + VO + SFX + foley + music, -14 LUFS | `assemble <slug>` → `final.mp4`, `publish.txt` |
Optional: `build --q blocking` (fast workbench motion/timing check), `--q draft` (EEVEE 35 %), `--q preview` (EEVEE 50 %);
any quality + `assemble` gives a reviewable MP4. `--step 2` renders every 2nd frame (assembly holds frames) for quick drafts.
Reference study: `analyze <video.mp4>` → `report.md` + `shots.jpg` + `hook.jpg` (cuts, pacing, camera moves, colours).

## Load only what you need
| Need | Read |
|---|---|
| writing the script / hook / title | `reference/story.md` |
| any spec field you are unsure of | `reference/spec.md` |
| directing realistic actions, faces, gestures | `reference/acting.md` |
| choosing shots / camera moves / composition | `reference/camera.md` |
| showing HOW something works (x-ray, cutaway, gears, circuits, anatomy) | `reference/explainers.md` |
| finding free 3D models / sounds / music, licenses | `reference/assets.md` |
| realism of materials, light, render settings | `reference/look.md` |
| the visual language of the genre (data) | `reference/style-bible.md` |
| QA message you don't understand / visual review checklist | `reference/qa.md` |
| adding a new verb / prop / fx to the engine | `reference/engine.md` |

## Spec cheat-sheet (covers ~90% of videos)
```yaml
title: "Why X Happens 🤔"
voice: {engine: kokoro, voice: am_michael, speed: 1.1}     # af_heart female · edge/eleven/file also
scenes:
  kitchen:                                                 # a scene = set + props + cast + performance
    set: {preset: kitchen|studio|room|outdoor|void, light: soft|bright|drama|night|xray, color: "#hex"}
    props:                                                 # src: proc:<table|plate|bread_slice|mug|phone|apple|coin|box>
      table: {src: proc:table, at: [0, 0], surface: true}  #      poly:<polyhaven_id> · file:<path> · lib:<name>
      plate: {src: proc:plate, on: table, at: [0.05, -0.18]}
      bun:   {src: poly:hamburger_buns, size_cm: 11, on: plate, yaw: 20, label: front}
    cast:  {hero: {char: hero, at: [-1.3, -1.6], face: table}}
    script:                                                # runs in order; `at:` S03 | S03+0.4 | "w:word" | 2.5
      - hero.walk_to: {target: bun, stop: reach}
      - hero.reach: {target: bun, hand: R}     # then grasp → lift{to: mouth|show|chest} → bite → chew → swallow
      - hero.grasp: {prop: bun}
      - hero.lift: {to: mouth}
      - hero.bite: {}
      - hero.chew: {cycles: 4, expr: yum}
      - hero.face: {expr: surprised, at: "w:weird", dur: 1.2}
shots:
  - id: S01
    scene: kitchen
    say: "If you leave bread out overnight, it turns *hard* as a rock."   # *word* = accent caption
    cam: {size: insert, target: bun, angle: front_right, move: push_in}   # size: ecu cu mcu medium wide xwide insert macro
    fx: [{type: arrow, from: hero.hand_R, to: bun}]                       # xray cutaway arrow label glow flow lightning crumbs
    sfx: [{name: whoosh, at: 0}]
```
Verbs: walk_to turn_to look_at reach grasp lift bite chew swallow place release rest point wave thumbs_up shrug
facepalm hands_on_hips cross_arms nod shake_head lean crouch stand startle face talk wait · prop verbs: pop move spin
fall shake hide show. Expressions: happy big_smile delighted yum surprised shocked scared disgust nauseous pain wince
sad angry thinking confused smirk satisfied determined embarrassed exhausted skeptical relieved excited.

## When something looks wrong
| Symptom (sheet/QA) | Fix in spec |
|---|---|
| hand can't reach | `walk_to … stop: reach`; move prop nearer the table edge (`at` y toward actor) |
| finger inside prop / odd grip | `grip: top|side|pinch|pinch_edge` on reach; set real `size_cm` |
| body inside furniture | move `cast.at` back / approach from another side |
| prop floating / sinking | place with `on: <surface>` (never raw z) |
| subject under captions / cut off | change `size`, `angle`, `el`, or target `hero.face` |
| shot boring | add motivated `move` (push_in on reveal, orbit on mechanism), FX, or split the beat |
| feels robotic | add `face`, `look_at`, a gesture; vary timings with `delay`; let actions overlap with `with: prev` |
