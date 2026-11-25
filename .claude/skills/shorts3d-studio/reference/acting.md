# Directing realistic acting (body, hands, face)

The engine already does the physics-level realism: planted feet with heel-toe roll, hip bob/sway and counter-rotating
shoulders, arm swing, eyes-lead-head gaze, blinks (+ blink on big gaze shifts), saccades, breathing, minimum-jerk
reaches with curved paths, grip aperture pre-shaping, finger-contact solving, lean/crouch to reach, exact lip contact,
bite marks, chewing with closed lips. **Your job is the choices and the timing.**

## Interaction recipes (copy these)
**Pick up & eat** (the classic):
```yaml
- hero.walk_to: {target: bun, stop: reach}      # skip if the hook needs hands at t=0 (start at the table)
- hero.reach: {target: bun, hand: R}            # gaze lands first, hand follows (~0.25 s later)
- hero.grasp: {prop: bun}                       # palm arrives, fingers close until contact, prop attaches
- hero.lift: {to: show}                         # optional: inspect it (pair with face: curious/skeptical)
- hero.lift: {to: mouth}
- hero.bite: {}                                 # jaw opens BEFORE contact, snaps shut, bite mark, hand pulls away
- hero.chew: {cycles: 4, expr: yum}
- hero.swallow: {}
```
**Put it down**: `hero.place: {on: plate}` or `{on: table, at: [x, y]}` - leans to reach, releases, the object settles
exactly onto the surface.
**Drink**: mug/can/bottle/glass → `reach` → `grasp` → `drink: {dur: 1.1, tilt: 38}` (rim on the lower lip, head tips back,
eyes half close, swallow, cup lowered) → `place`.
**Carry**: `lift: {to: chest}` then `walk_to` / `turn_to` - held objects travel with the body.
**Show the viewer**: `lift: {to: show}` + `face: {expr: excited}` + camera `cu` on `hero.hand_R`.
**React to a fact**: `face` + one gesture: surprised → `startle`; gross → `face: disgust` + `lean: {deg: -8}`;
confused → `shrug`; smug → `cross_arms` + `smirk`; pain → `wince` + `crouch: {depth: 0.15}`.
**Explain/point**: `point: {target: X, hold: 1.2}` + `look_at` the camera-side target.
**Two actors**: each actor has its own script; they can look at / point at / react to each other. One prop should be
held by only one actor per scene (hand-overs between actors are not supported yet — cut between two scenes instead).

## Timing rules (feel human)
- Anticipation: the engine moves eyes first; add `delay: 0.2` before big actions after a cut.
- Overlap: let faces run during actions: `face: {expr: x, with: prev}` or anchor to a word `at: "w:weird"`.
- Holds: 0.3–0.6 s of stillness after a key action reads better than constant motion (`wait`).
- One clear action per shot. If an action spans two shots, the cut should land mid-action (cut on motion).
- Speed: real reaches 0.5–1.2 s, grasp close 0.15–0.25 s, bite 0.3 s, chew 1.6 Hz. Do not compress below these.
- Start the hook mid-action at t=0 (put the actor where the action begins; no walk-ins in shot 1).

## Expressions (ARKit-based, readable on a phone)
happy · big_smile · delighted · yum · surprised · shocked · scared · disgust · nauseous · pain · wince · sad · angry ·
thinking · confused · smirk · satisfied · determined · embarrassed · exhausted · skeptical · relieved · excited.
`amt: 0.5–1.2` scales intensity. Combine sparingly (one dominant emotion per shot). Faces on phones need ~20 % more
intensity than film; `cu`/`ecu` shots can stay subtle.

## Contact & collision rules (what keeps it real)
- Always give imported props a real `size_cm`. A 30 % wrong scale makes perfect hands look fake.
- Put things on things with `on:` (never raw z). The QA flags floating/sinking > 4 mm.
- Actors never stand inside furniture: `walk_to … stop: reach` computes a stand point outside the footprint and paths
  around it. If QA says "body inside furniture", move `cast.at` or approach from another side.
- If QA says "hand can't reach", bring the prop closer to the edge or let the actor walk closer; the solver already
  leans up to 60° and crouches up to 32 cm.
- Grips (`grip:` on reach; `auto` picks by shape - every grip clamps the object between thumb and fingers):
  | grip | used for | how it holds |
  |---|---|---|
  | `span` | flat or boxy, up to ~13 cm across (bread slice, phone, cookie, book spine, cereal box top) | fingertips on the far edge, thumb on the near edge, hand angled diagonally |
  | `pinch` | tiny or thin (coin, key, pen, grape) | thumb tip against index tip from above |
  | `wrap_top` | round and low (apple, bun, ball, orange) | palm on top, fingers around the far side, thumb near side |
  | `wrap_side` | round and tall (mug, can, bottle, glass) | palm on the side, fingers wrap (diagonally if the arm comes from above) |
  The hand angle is chosen for a NATURAL WRIST (the planner tries yaw/pitch/roll and the elbow swivel). Every sheet
  shows close-ups of each grasp (`grip-<prop>-..` stills): thumb and fingers must touch opposite sides.
- Joints are limited like a real body: wrist 70° flex / 50° back / 22° sideways (twist bones spread the forearm
  twist), elbow swings out/in to keep the wrist natural, neck 75° turn / 50° down / 38° up, forward bends hinge
  ~35 % at the hips, thumb and finger ranges anatomical. QA reports `L/R wrist beyond its natural range` with
  frames - move the object, use the other hand or set `grip:`.

## Talking characters
Narration is voice-over (genre default). For on-screen lines: `hero.talk: {text: "No way!", at: "w:way"}` — visemes
are generated from the text; keep lines under 2 s.

## Real human motion (mocap clips) - use it for body language
Procedural verbs are exact (contacts); mocap is human (weight, rhythm, asymmetry). Combine them:
- `idle: auto` (default) puts a real idle under the whole performance: never a frozen mannequin.
- While the narration explains, give the actor a talking/thinking clip instead of stacking gestures:
  `hero.clip: {name: talk, dur: 3.5, at: S02}` · reacting: `think`, `agree` (nods), `disagree`, `shrug`, `laugh`,
  `scratch_head`, `present` (opens to the right side - put the explained object there), `cheer`, `clap`.
- Hands that `reach`/`grasp`/`lift`/gesture ignore the clip automatically (contacts stay exact); the rest of the
  body keeps the clip. Use `parts: [torso]` to keep only the body sway while both hands act.
- One clip per shot; let it start ~0.3 s before the line (`at: "w:word"`, `delay: -0.3`); `amt: 0.6` = calmer.
- Clip library: `library/clips/clips.json` (Rocketbox, MIT). Any FBX/BVH humanoid clip works: `name: /path/clip.fbx`.
