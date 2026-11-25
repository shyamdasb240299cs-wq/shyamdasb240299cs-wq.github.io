# Engine internals (read only when extending)

```
bin/s3d.py          CLI: setup doctor init new status voice plan build assemble inspect finish gpu add-character analyze assets brand
bin/s3d_inspect.py  every-frame check of preview/final.mp4 (pops vs neighbours, flicker, black, frozen)
bin/s3d_tts.py      narration -> vo.wav + words.json (kokoro | edge | eleven | piper | file+whisper)
bin/s3d_audio.py    captions.ass, mix (VO segments + SFX + auto-foley + ducked music, loudnorm), SFX synth, logo
bin/s3d_analyze.py  reference video analysis (cuts, motion, colour, captions, transcript)
engine/build.py     Blender entry: one scene per process; set -> props -> cast -> script -> bake -> cameras -> fx -> QA -> render
engine/mhchar.py    CC0 MakeHuman data -> rigged character (Mixamo bones + eye bones, ARKit '!ex-*' + viseme keys, proxies)
engine/rigkit.py    rig adapter (Mixamo/UE/MPFB/Rigify/CC/Daz names), IK + look/gaze controls, hand calibration
engine/motion.py    Performer: verbs -> Tracks -> pass A body bake, pass B contacts (finger solver, exact mouth), pass C attach/bites
engine/acting.py    ARKit expression library, blinks, chew/bite/swallow, text lip-sync
engine/props.py     procedural props, Poly Haven/.glb/.fbx/.obj import, size normalisation, on-surface placement, grips, bites
engine/mech.py      procedural mechanism/science parts (gears, springs, coils, molecules, cells, outlets…)
engine/sets.py      sets, world, light rigs, render quality, brand decals (shirt UV decal, prop labels)
engine/camera.py    shot sizes, angles, moves, follow/lock, caption-safe composition, DOF, clip check
engine/fx.py        xray, cutaway, arrows, labels, glow, glass, flow, lightning, exploded, crumbs, pop
engine/qa.py        reach, finger penetration, body-vs-furniture, foot slide, floor, resting props, framing
engine/assetchar.py any free rigged human (.fbx/.glb/.blend/.obj) -> normalised, skeleton fixed, PBR rebuilt, library
engine/mocap.py     mocap clips -> skeleton-free JSON cache; layered by Performer.clip_at (torso deltas, arm dirs, fingers)
engine/gpu.py       backend/device detection + EEVEE/Cycles benchmark (s3d.py gpu)
```

## Data flow
`shots.yaml` →(plan: word timings, anchors→seconds/frames)→ `build/plan.json` + `build/timeline.json` →
Blender per scene → `build/<scene>/{<scene>.blend, frames/#####.png, sheet/*.jpg, qa_*.json, events.json}` →
assemble → `final.mp4`. The `.blend` per scene is a normal editable Blender file (open it to hand-tweak).

## Add a verb
In `motion.Performer` add `def v_<name>(self, t0, a): ... return t_end` writing keys into Tracks:
root_pos/root_yaw, lean/crouch/nod/tilt/twist/shrug/headyaw/back, hand world targets (`hs` blend, `hw`, `hq`, `ha`),
body-space gestures (`_gesture(...)`), finger curls (`curl[side][finger]`), look/gaze, `face.expr/keys/func`.
Events needing evaluated poses (contacts) go to `self.events` and are solved in pass B/C.

## Add a prop builder
`engine/mech.py`: `def thing(name, coll=None, **params) -> mesh object` (origin anywhere; it is re-centred to
bottom-centre), add to `BUILDERS`. Materials via `P.mat(...)`. Use `P.curve_tube`, `P.proc_lathe`, `P.proc_box`.

## Add an fx
`engine/fx.py` function + a branch in `build.apply_fx`. Time-range with `_vis(ob, f0, f1, frames)` or keyed inputs.

## Bring your own character
`s3d.py add-character file.fbx --name X [--textures dir]` (also .glb/.gltf/.blend/.obj). Bone names recognised:
Mixamo, 3ds Max biped (Bip01/Bip001, Rocketbox), UE, CC/iClone, Daz, VRM, Rigify DEF, MPFB. Unknown names fail with
the missing list -> add them to `rigkit.CANDS/LIMB/FPAT`. Face keys: ARKit 52 in any naming (`AK_NN_`, `!ex-`,
`blendShape.`), visemes `AA_VI_NN_`/`viseme_`. No face keys = body acting only. Unrigged meshes are not supported:
rig them first (Mixamo auto-rigger) or pick a rigged asset.

## Mocap
`clip` verb / `idle` cast option. Conversion: `P_clip @ R_ref^-1` per canonical bone in the character frame
(`R_ref` = family bind pose, `library/clips/biped_rest.json` for Rocketbox; the clip's own rest otherwise), arm
joint DIRECTIONS (rest-independent), pelvis offset / pelvis height. Feet stay procedural (planted) - full-body
locomotion clips (dance, fall) are not supported; walk with `walk_to`.
