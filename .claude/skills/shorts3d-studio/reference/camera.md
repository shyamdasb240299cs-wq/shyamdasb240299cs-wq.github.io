# Camera & cutting for 9:16 explainers

Cameras are solved after the performance is baked, so they frame the REAL motion. Moving subjects are followed with
smoothing; subjects that move < 12 cm get a rock-steady locked frame (`lock: true/false` overrides).
The subject point is placed at ~58–60 % screen height so the caption band (~78–80 % from the top) stays clear.

## Shot sizes (`cam.size`)
| size | frames | default lens | use for |
|---|---|---|---|
| `macro` | 10 cm | 100 mm f/2.4 | textures, crumbs, mechanisms, "inside the object" |
| `insert` | 24 cm | 55 mm f/2.8 | **hooks**: hands acting on the object |
| `ecu` | eyes/mouth | 85 mm f/2 | emotion peaks, biting, gross details |
| `cu` | face | 65 mm f/2.8 | reactions after every surprising fact |
| `mcu` | head+shoulders | 50 mm | talking/thinking beats |
| `medium` | waist up | 35 mm | actions with props, gestures |
| `wide` / `xwide` | full body / room | 26 / 24 mm | establishing, walking, loop ending |

## Angles (relative to the actor's facing; for props too)
`front` (looks at the face) · `front_left/right` (3/4, default, most flattering) · `left/right` (profile; best for
hand-to-object and eating) · `back_*` (over-the-shoulder feel) · `top` (overhead: recipes, desks, hands) ·
`low` (hero/power, objects looming) · `pov` (camera = eyes, `look: <prop>`; immersive "Imagine you…" hooks).
Fine-tune with `az` (deg) and `el` (deg).

## Moves (`cam.move`, `amount` scales)
| move | when |
|---|---|
| `static` | dialogue, reactions, clear demonstrations |
| `push_in` | hooks, reveals, "here's the weird part" |
| `pull_out` | context reveals, loop endings |
| `orbit` | mechanisms, objects, x-ray/cutaway (shows depth) |
| `crane_up/down` | establishing, scale reveals |
| `dolly_zoom` | shock/realisation moments (use once per video) |
| `snap_zoom` | comedic punch, sudden detail |
| `handheld` (or `handheld: true` + any move) | POV, chaos, danger, documentary feel |

## Cutting rhythm (retention)
- Cut every 1.5–3.5 s; never two consecutive shots with the same size AND angle.
- Alternate: object → face → mechanism → face → action. Every surprising fact gets a reaction shot within 2 s.
- Cut on action (mid-reach, mid-bite) for invisible cuts; cut on the beat of a word for punch (`say` boundaries).
- 180° rule: keep the camera on one side of the actor/table axis within a scene (`front_left`, `left`, `back_left`
  OR the `right` family). Break it only on purpose (disorientation).
- Hook shot: `insert`/`ecu`/`pov`, `push_in`, object large in frame, hand entering, frame 0 already interesting.
- Loop shot: match the first shot's framing (same target/size/angle) so the restart feels continuous.

## Composition checklist for the sheet
Subject in the upper 2/3 · eyes on the upper third line in faces · object of the sentence visible when it is spoken ·
nothing important within 12 % of the edges (UI overlays on Shorts) · horizon level unless `roll` is intended ·
foreground framing for depth (place props between camera and subject on purpose: plates, bars, doorframes).

## DOF & lenses
Close shots get shallow DOF automatically (focus tracks the subject). Wide lenses (24–28 mm) close to faces/objects
give the genre's punchy exaggerated perspective; use `lens: 24` with `size: cu` for comedic close-ups.
