# Engine internals (read only when extending)

```
bin/s3d.py          CLI: setup doctor new voice plan build assemble analyze assets brand
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
Any humanoid .blend with a Mixamo / UE / MPFB / Rigify-DEF / CC / Daz-named armature works: put it in
`~/.shorts3d/library/characters/<name>.blend` with the armature and meshes inside a collection named `CHAR_<name>`.
Face animation needs ARKit-named shape keys (optionally prefixed `!ex-`); clothes with `s3d_part = "clothes"`
custom property get the brand decal.

## Mocap
Not automated yet: for a complex full-body move, import a Mixamo FBX onto a Mixamo-named rig in the scene `.blend`,
mute the `s3d_ik` constraints for that time range, and keep engine verbs for the hands before/after.
(Retargeting tip: apply each bone's armature-space rotation delta from rest — `core.rest_local_rot` does the math.)
