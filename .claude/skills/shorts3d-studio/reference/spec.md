# shots.yaml — full DSL reference

Units: metres, degrees, seconds. World: +Z up, floor z=0. A character with `face: <target>` turns to it.
Times on the **video clock** (0 = first frame). Everything is optional except `shots[].id/scene/say`.

## Top level
| key | meaning |
|---|---|
| `title` | working title (also default publish title) |
| `fps` | 30 (default) · 60 for very fast action |
| `res` | `[1080, 1920]` default (9:16) |
| `voice` | `{engine: kokoro|edge|eleven|piper|file, voice, speed, audio}` (file = your recording; aligned by Whisper) |
| `music` | `{track: auto|none, file: path, gain_db: -22}` music is ducked under the voice automatically |
| `captions` | `{max_words: 3, max_chars: 18, case: sentence|upper|none, size: 0.047, y_from_bottom: 0.205, font}` |
| `tail` | seconds after the last word (default 0.6; keep short for loops) |
| `publish` | `{title, description, hashtags: [...]}` → `publish.txt` |
| `scenes` | named scenes (below) |
| `shots` | ordered list (below) |

## scenes.<name>
```yaml
set:   {preset: studio|kitchen|room|outdoor|void, color: "#hex", light: soft|bright|drama|night|xray,
        light_scale: 1.0, ground: grass|asphalt|sand, world: {kind: color|sky, color: "#hex", strength: 0.55, hdri: <polyhaven_id>}}
props: {<name>: {...}}       # created in order; supports before the things that sit on them
cast:  {<actor>: {...}}
script: [ {<actor|prop>.<verb>: {args}} , ... ]
fx:    [ {...} ]             # scene-wide FX: time range with start/end anchors
```
Set objects you can put things on: kitchen → `counter`; every set → `floor`.

### props.<name>
| key | meaning |
|---|---|
| `src` | `proc:table|plate|bread_slice|mug|phone|apple|coin|box` · `poly:<id>[@2k]` (Poly Haven CC0, auto-download) · `file:<path.blend/.glb/.fbx/.obj>` · `lib:<name>` (~/.shorts3d/library/props) |
| `size_cm` | real-world size of the LARGEST dimension (always set for imported assets) |
| `on` | rest on a surface prop/set object (exact contact, no floating) |
| `at` | `[x, y]` on the surface (world) or `[x, y, z]` when not using `on` |
| `yaw` | degrees |
| `surface: true` | other props can be placed on it, actors stand outside it |
| `obstacle: true` | actors path around it |
| `label: front|top|side` + `label_size` | brand logo decal on the prop |
| `color` | override base colour of simple materials |
| `free: true` | skip the "resting" QA check (falling/flying props) |

### cast.<actor>
`{char: rb_male|rb_female|hero|heroine|<added>, at: [x, y], face: <prop|deg>, scale: 1.0, idle: auto|none|<clip>, idle_amt: 0.7, shirt: "#hex"}`
- `rb_male` / `rb_female`: realistic Rocketbox actors (`setup --actors`); any free rigged human: `s3d.py add-character f.fbx --name X`.
- `hero`/`heroine`: MakeHuman presets built from `library/characters/<char>.json` (cached `.blend`; delete it to rebuild).
- `idle: auto` layers real mocap idle (weight shifts, breathing) under everything when clips are installed.
- `scale: 0.08` = tiny-worker metaphor.

### script verbs (actor)
Common args: `at:` anchor · `delay: s` · `with: prev` (start together with the previous action) · `dur: s`.
| verb | args | natural duration |
|---|---|---|
| `walk_to` | `target` or `pos: [x,y]`, `stop: reach|near|at|<m>`, `speed: 1.0`, `face` | dist/1.25 m/s + turns; paths around furniture |
| `turn_to` | `target` | ~0.4 s / 40° with foot steps |
| `look_at` | `target`, `head: true` | eyes 0.08 s, head 0.45 s |
| `reach` | `target`, `hand: R|L|auto`, `grip: auto|top|side|pinch|pinch_edge` | 0.55–1.4 s (distance) + lean/crouch solved |
| `grasp` | `prop`, `hand` | 0.45 s; fingers close until they touch (contact solver) |
| `lift` | `to: mouth|show|chest|[x,y,z]`, `hand`, `dur` | 0.75 s |
| `bite` | – | 0.8 s: mouth opens before contact, bite mark cut at contact |
| `chew` | `cycles: 4`, `expr: yum` | 0.62 s/cycle |
| `swallow` | – | 0.5 s |
| `place` | `on`, `at: [x,y]`, `hand` | 0.6–1.2 s then release |
| `release` | `hand`, `drop: false` | 0.35 s + return to rest |
| `rest` | `hand: both|R|L` | 0.7 s |
| `point` | `target`, `hand`, `hold` | 0.45 + hold + 0.5 |
| `wave` `thumbs_up` `facepalm` `shrug` `hands_on_hips` `cross_arms` | `hand`, `hold`, `expr` | ~1.5–2.5 s |
| `nod` `shake_head` | `times` | 0.4 s each |
| `lean` | `deg`, `dur` | 0.6 s |
| `crouch` / `stand` | `depth: 0.35`, `dur` | 0.8 s (hips hinge back, torso leans: balanced) |
| `clip` | `name: talk|think|agree|disagree|shrug|laugh|present|scratch_head|cheer|clap|wave|look_around|<file>`, `dur`, `amt: 1`, `fade: 0.4`, `speed`, `from`, `parts: [torso, arms, fingers, hips]` | clip length or `dur` (loops) |
| `startle` | `hold` | 0.9 s jump-back + shocked face |
| `face` | `expr`, `amt: 1.0`, `dur: 1.5`, `block: false` | overlays body actions (non-blocking) |
| `talk` | `text`, `dur` | lip-sync for on-screen speech |
| `wait` | `dur` | pause |

### script verbs (prop) — `- bread.pop: {at: S03}`
`pop` (scale in w/ overshoot) · `move {to, dur}` · `spin {deg, dur}` · `fall` (gravity + bounce onto surface) ·
`shake {dur}` · `hide` / `show`.

## Anchors (`at:`, `start:`, `end:`)
`S03` shot start · `S03.end` shot end · `S03+0.4` · `"w:crystals"` time the word is spoken (`"w:it#2"` 2nd occurrence,
`"w:it+0.2"`) · number = absolute seconds (script, scene fx) or seconds inside the shot (shot fx: `start: 0.3, end: -0.2`).
`from:`/`to:` are always POSITIONS (arrow/lightning endpoints), never times.

## shots[]
```yaml
- id: S04
  scene: kitchen
  say: "Heat melts those *crystals*,"     # narration chunk; *word* = accent colour in captions
  hold: 0.4                               # extra silent beat after the line (reaction)
  dur: 2.0                                # for shots without say (visual beat) or minimum length
  cam: {...}
  fx: [...]
  overlays: [{anchor: bread}]             # exports screen positions (for 2D graphics)
  sfx: [{name: whoosh|pop|click|ding|thud|crunch|grab|tap|zap|riser|squish|boom|<your file>, at: 0|"w:word", gain: 0}]
```

### cam
| key | values |
|---|---|
| `size` | `ecu cu mcu medium wide xwide insert macro` (sets distance, lens, f-stop, aim) |
| `target` | `hero` · `hero.face|eyes|mouth|hand_R|hand_L|chest|waist` · `<prop>` · `[x,y,z]` |
| `angle` | `front front_left front_right left right back_left back_right back top low pov` (relative to actor facing) |
| `az`, `el` | exact azimuth / elevation degrees (override angle) |
| `move` | `static push_in pull_out orbit crane_up crane_down dolly_zoom snap_zoom handheld` · `amount: 1.0` |
| `lens`, `dist`, `fstop`, `dof: false`, `roll`, `shake`, `lock: true` (no follow), `look` (POV target) |

### fx (shot-level default to the shot's time range; scene-level use start/end)
| type | args |
|---|---|
| `xray` | `target: hero|<prop>`, `inner: [props]`, `color` — skin/clothes turn into glowing translucent shell |
| `cutaway` | `target: hero`, `at: hero.chest`, `radius: 0.09`, `inner: [props]` — round hole with glowing rim |
| `arrow` | `from`, `to` (points/props/actor parts), `color`, `bend: 0.15`, `radius` — draws itself on |
| `label` | `text`, `at`, `offset: [0,0,0.08]`, `size: 0.06`, `color` — billboard 3D text, pops in |
| `glow` | `target`, `color`, `strength` — pulsing emission |
| `glass` | `target` — make a shell transparent to see the mechanism inside |
| `flow` | `along: <wire/pipe prop>` (follows its centre-line) or `path: [p1, p2, ...]`, `count`, `radius`, `color` — particles streaming (blood, air, current) |
| `lightning` | `from`, `to`, `color` — flickering electric arc |
| `exploded` | `parts: [...]`, `center`, `dist` — parts slide apart and back |
| `crumbs` | `at: hero.mouth`, `count`, `floor_z` — debris falling with gravity |

## Minimal complete example
See `templates/explainer.yaml` (kitchen + macro scenes, 7 shots, loop ending).
