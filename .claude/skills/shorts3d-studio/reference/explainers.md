# Showing HOW things work (tech, science, body)

Rule: every mechanism sentence gets a NEW way of seeing inside. Pick the cheapest device that makes the idea obvious.

Never hide the mechanism inside an opaque prop (the camera then sees only the shell): put the parts in the open in a
`macro`/`inside` scene, or make the shell `glass`. Build the inside at a readable scale (10–30 cm) and frame it with
`insert`/`macro`; `pop` parts in on the words that name them.

## Device menu (cheapest first)
| device | spec | best for |
|---|---|---|
| macro look | `cam: {size: macro, target: X, move: orbit}` | textures, crumbs, fibres, surfaces |
| glass shell | `fx: [{type: glass, target: casing}]` | gadgets: see gears/springs inside (sharpener, lighter, lock) |
| cutaway hole | `fx: [{type: cutaway, target: hero, at: hero.chest, radius: 0.09, inner: [heart]}]` | body/organs, walls, pipes |
| x-ray | `fx: [{type: xray, target: hero, inner: [lungs]}]` | anatomy, swallowed objects, bones |
| flow | `fx: [{type: flow, path: [...], color: "#FF3B3B"}]` | blood, air, water, electricity, data |
| arrows / labels | `{type: arrow, from, to}` · `{type: label, text: "STARCH", at: X}` | naming parts, forces, direction |
| exploded view | `{type: exploded, parts: [a, b, c], center: a}` | assemblies (plug, battery, pen) |
| lightning / glow | `{type: lightning, from, to}` · `{type: glow, target: X}` | current, heat, danger, "this part" |
| scale shift | separate `macro` scene with big props + `cast.scale: 0.08` tiny workers | "inside the outlet / machine" metaphor |
| side-by-side | two props in the same frame, `label` each | myth vs fact, before/after |

## Mechanism building blocks (`src: proc:<part>`, `params: {...}`)
`gear {teeth, r, t, hole}` · `spring {r, length, turns, wire}` · `coil` (copper) · `pipe {points, r, glass}` ·
`wire {points, r, color}` · `piston {r, h, rod}` · `battery` · `chip {w, pins}` · `led {color}` · `outlet {style: us|eu}` ·
`plug` · `screw {r, length}` · `molecule {kind: water|co2|o2|salt}` · `cell {r, color}` · `wave {length, amp, cycles}` ·
`lattice {n, spacing}` (crystals) · `strands {count, length}` (fibres, starch, hair) · `droplet {r}` ·
plus everyday props `table plate bread_slice mug phone apple coin box` and any Poly Haven model (`poly:<id>`).
Combine parts in a `macro` scene; animate with prop verbs (`spin` gears, `move` pistons, `pop` new parts, `fall`).

### Recipe: "How a ___ works" gadget
```yaml
scenes:
  inside:
    set: {preset: studio, color: "#2EC4E6", light: soft}
    props:
      shell:  {src: poly:<casing_model>, size_cm: 9}        # or proc:box
      g1:     {src: proc:gear, params: {teeth: 18, r: 0.02}, at: [0, 0, 0.03]}
      g2:     {src: proc:gear, params: {teeth: 10, r: 0.011}, at: [0.03, 0, 0.03]}
      spring: {src: proc:spring, params: {r: 0.004, length: 0.03}, at: [-0.03, 0, 0.02]}
    script:
      - g1.spin: {deg: 720, dur: 3, at: S04}
      - g2.spin: {deg: -1296, dur: 3, at: S04}             # ratio = teeth ratio, opposite direction
    fx: [{type: glass, target: shell}]
```
Gear meshing: rotate neighbours in opposite directions with speeds inversely proportional to teeth counts.

### Recipe: electricity in a wall
Room scene: the actor grabs a `plug` (hook). Inside scene (see `templates/howitworks.yaml`): big `outlet` faceplate,
two `wire` props, `flow: {along: wire_hot}` + `flow: {along: wire_neu}`, a `coil` that `pop`s in, then `glow` +
`lightning` on the payoff word. Camera: `insert` push-in on the plug, then `insert`/`macro` inside.

### Recipe: body / anatomy
X-ray or cutaway on the actor, inner organ props placed at the bone positions (`at: hero.chest`). Free organ models:
see `assets.md` (Z-Anatomy CC BY-SA — attribution & share-alike; BodyParts3D; NIH 3D; Sketchfab CC-BY). Simple
stylised organs can also be built: heart = `apple` coloured `#B3121F`, lungs = two `cell` props scaled, stomach =
`droplet` scaled. Use `flow` for blood/air paths and a dark `void` set with `light: xray` for cross-section shots.

### Recipe: chemistry / cooking science
`molecule` props in a `void` scene, `pop` them in on the words that name them, `flow` for movement, `lattice` for
crystals forming (pop + glow), `strands` for starch/gluten/protein fibres, `droplet` for water leaving.

## Colour language (keep consistent across your channel)
brand colour = "the thing we explain" (arrows, flow, rims) · red = danger/heat/blood · cyan/blue = cold/water/air ·
yellow = electricity/attention · green = good/healthy. Labels: short UPPERCASE nouns, ≤ 2 words.
