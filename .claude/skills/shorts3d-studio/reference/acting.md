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
**Put it down**: `hero.place: {on: plate}` (exact surface contact, then release + hand returns to rest).
**Drink**: use a mug/bottle prop → `reach` (grip auto=side) → `grasp` → `lift: {to: mouth}` → `wait: {dur: 0.8}`.
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
- Grip types: `auto` picks by shape — thin flat items `pinch_edge`, tiny `pinch`, tall `side` (cups/bottles), else
  `top`. Override when the sheet shows an awkward wrist.

## Talking characters
Narration is voice-over (genre default). For on-screen lines: `hero.talk: {text: "No way!", at: "w:way"}` — visemes
are generated from the text; keep lines under 2 s.

## Mocap (advanced, optional)
For complex full-body moves (falling, dancing, sports) use a Mixamo/CMU clip on a Mixamo-named rig and keep IK verbs
for the hands. See `reference/engine.md#mocap`.
