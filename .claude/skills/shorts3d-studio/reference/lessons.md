# Lessons learned (read when something looks wrong, or before extending the engine)

Every row was a real failure while building this skill. The engine now prevents it; the last column is what YOU
still decide in `shots.yaml`. Check here before writing new bpy code - most "new" problems are one of these.

## Bodies, hands, contacts
| Symptom | Root cause | Engine now | You do |
|---|---|---|---|
| Legs bent "sitting on air", elbows folded the wrong way on an imported character | FBX/glTF bones have meaningless tails; Blender IK uses the tail as the hand/foot point | `assetchar.fix_skeleton`: tails -> next joint, limb rolls aligned, legs re-parented to pelvis, arms to chest | Always import with `s3d.py add-character` (never link a raw FBX into a scene) |
| Elbow/knee points sideways or flips mid-move | IK pole angle depends on bone roll | `rigkit._calibrate_pole`: numeric search in a bent test pose (T- and A-pose rigs) | Nothing; if QA still says "pole weak", re-import the character |
| Wrist bent backwards / palm twisted like a broken arm | hand rotation chosen in world space, ignoring the forearm | `Performer._wrist_limit`: flex 75 / ext 60 / deviation 25 / twist 95 deg vs the IK forearm (free + gesturing hands) | For grasps pick a grip that faces the hand: `grip: top|side` |
| Left hand holds a mug with fingers pointing outward | side grip ignored which hand | `props.grip(..., side=)` mirrors: fingers wrap toward the midline; top grips angle 12 deg inward | `hand: L|R` nearest to the object |
| Crouch looks like sitting on an invisible chair | pelvis only dropped vertically | hip hinge: pelvis moves back 0.45 m per metre of drop + torso lean | `crouch: {depth: 0.15-0.35}` (deeper = kneel, not supported) |
| Feet parallel like a mannequin | rest pose feet | toes turned out 7 deg, real idle clip adds weight shifts | keep `idle: auto` |
| Robotic idle / talking | procedural noise only | mocap layer (`clip` verb, auto idle) | one talk/think clip per explaining shot |
| Hand passes through bread / fingers inside prop | no contact model | finger sweep vs prop BVH, palm backs off, penetration guard | give props real `size_cm` |
| Hand can't reach; actor stands inside the table | target outside arm length; stand point inside footprint | reach posture search (lean <= 60 deg, crouch <= 32 cm); stand point outside furniture + detour | start the actor at the action (`at:` near the table edge) |
| Bun misses the mouth / bite in the air | mouth position estimated, not measured | deferred exact mouth solve; lips from the pucker shape key | `lift: {to: mouth}` then `bite` (never place food by coordinates) |
| Palm turns outward at every bite | the deferred mouth solve rebuilt the hand rotation with a flipped 'inward' sign | one shared `_mouth_rot()` for planning and the exact solve | - |
| Bread slice tilted up across the nose | flat food oriented like a burger | flat foods (`pinch_edge`) are bitten level, palm on top, edge into the mouth | thin items: keep `grip: auto` (pinch_edge) |
| Side-eye / whites of the eyes showing | gaze stayed on a near target after the bite; eyes had no range limit | `_clamp_gaze`: eyes within 26 deg sideways / 14 up / 20 down of the head, focus >= 35 cm; eyes leave the food after the bite | `look_at` the camera-side target after eating |
| Bread "floats" in the hand | fingers pressed from above only; the thumb (single-axis curl) never opposed them | grip types with opposition; thumb opposition + abduction calibrated per rig; 3-DOF thumb solver; contact check per grasp | read the `grip-*` close-ups on the sheet |
| Wrist looks boneless / twisted like a candy wrapper | rigs had no forearm twist bones: all twist in one joint | `rigkit.add_twist_bones` on every character (50 % of the hand twist, smooth weight ramp) | - |
| Wrist bent back 60-170 deg at grasps, bites, sips | hand angle chosen from the object only, arm ignored | elbow swivel optimisation + every grip/hold/mouth angle searched for wrist comfort; exact contacts re-solved with a limited wrist | object within easy reach; other hand for objects on the other side |
| Hand jumps for one frame before a bite | stale planning key kept after the exact mouth solve | posture keyed before planning; every contact key re-solved | - |
| Object released in the air after `place` | no lean for the put-down; height planned from stale data | place leans like a reach, settles the object onto the support | `place: {on: table, at: [x, y]}` |
| Mug treated as a box | bounding-box centre shifted by the handle | circle fit through ray hits (handle ignored) | `grip: wrap_side` to force |
| Action ignored its `at: C1+0.2` | only shot ids starting with S were anchors | any shot id works; unknown anchors are reported | - |
| Elbow pops / wrist flips for one frame | two elbow positions equally good -> the solver alternated | swivel hysteresis: stay on the current side unless the other is clearly better | - |
| Pen/phone picked up with a twisted wrist | the thumb was always put on the same side | span/pinch plans try the thumb on either side and keep the more natural wrist | - |
| Torn trousers, floating shoes (MakeHuman) | helper geometry had zero weights | nearest-body weight transfer | - |
| Every face key at full strength (Blender 5.x) | new shape keys default to 1.0 | all keys reset to 0 on import | - |
| Weak smiles on asset faces | small shape-key amplitudes | `face_gain` measured from the smile key (x1.0-1.6) | `face: {amt: 1.2}` for phone readability |
| Hair grey/silver, jagged | shiny alpha cards reflecting the set | hair: roughness 0.62, low specular, alpha clip | - |

## Camera, sets, look
| Symptom | Cause | Engine now | You do |
|---|---|---|---|
| Hook frame empty / subject cut off | actor walked in; framing on the wrong part | composition by subject part, caption-safe zone | actor starts at the action; `cam.target: bun` or `hero.hand_R` |
| Blank grey mechanism shot | parts hidden inside an opaque prop | - | build mechanism scenes in the open, or `glass`/`xray` the shell |
| Boolean cutaway artefacts | mesh booleans on skinned/complex meshes | shader cutaway (clip plane in the material) | `fx: cutaway` |
| Third-party logo on a downloaded texture | asset baked a brand | `clean_texture` + own brand decals | parody/no real brands |
| Brand logo stamped twice (chest + neck) on a scanned actor | texture atlas: neck island sits next to the shirt in UV space | decal masked by distance from the chest on the rest mesh ('Generated' coords) | `brand.shirt_logo_size` to resize |
| Prop floating or sunk | raw z coordinates | `on: <surface>` raycast placement, QA +-4 mm | always `on:` |

## Pipeline
| Symptom | Cause | Fix |
|---|---|---|
| `on:` key read as `True` | YAML 1.1 booleans | custom loader keeps only true/false |
| Shots > 4 s, viewers swipe | narration lines too long | `plan` warns; split the line into two shots |
| Captions orphan one word / bad case | naive chunking | 3-word groups, punctuation-aware |
| Loudness off between videos | no normalisation | -14 LUFS integrated, limiter, ducked music |
| Preview and final frames mixed in one MP4 | re-render of some shots in another quality | frames tagged review/final; changing class wipes; EEVEE final + Cycles hero shots may mix |
| Render killed (sleep, crash) | long laptop renders | `build ... --resume` continues where it stopped |
| Context/tokens burned re-reading old videos | no memory | CHANNEL.md is the memory; read only it + the current video folder |
| Flow looks like bubbles / fake | particle blobs | streamlines (`PT.stream`) with fading tails, `stream_width_keys` per shot |
| Spinning wheel strobes / runs backwards | 1-9 rev/s at 30 fps | SLOW ≤0.9 or FAST ≥12 rev/s + motion blur; quick transitions |
| Loop freezes or hitches at the seam (moving car) | last frame = first position, speed/wheel/pitch jump | end one frame of travel before S0, same speed, `spin_start`, `noise_offset=-NF` |
| Parked car creeps in a later shot | drive keys ended early, interpolation to the next drive | key the hold until the scene cut |
| Hood/strut bar pass through the body | imported parts embedded at rest | lift off the seat straight first (15 mm / 10 cm), QA with the seat-transition rule |
| Engine running while being pulled out | flows/pistons keyed for all frames | engine off from the stop until it is shown running again |
| A described mechanism does nothing | static part | animate it (wastegate valve opens, bypass flow through its dump tube) |
| Engine louder than the narrator | raw recording levels | engine bed ~8 dB under the voice, duck, check speech-to-text WER on the mix |
| Studio frames rendered at the wrong time | `render(scene=)` uses the context scene's frame | set both frames before rendering |

## How the motion system thinks (so you can extend it without breaking contacts)
- All torso poses are **armature-space deltas** `D[bone]` (rotation from rest). `chain_deltas()` builds them;
  `posed_point(D, bone, point)` is the exact forward kinematics used for planning (mouth, shoulders, reach).
  Bake writes `core.rest_local_rot(bone, D[bone], D[parent])` - works for any bone roll / naming.
- Hands are IK targets (`hand_target`) = rest/clip pose -> + gesture offsets (`hb`, `hbq`/`hbw`) -> reach/hold
  (`hs` weight blends to `hw`/`hq`) -> wrist limit on the free part. Elbows follow `_elbow_pole`.
- Fingers: curl tracks (0 open .. 1 fist) per finger -> `finger_quats`; contacts close fingers until the BVH hit.
- Mocap: `mocap.Clip.sample(t)` gives torso deltas + arm directions; `clip_at(t)` blends layers (later wins).

### Add a gesture (copy this pattern)
```python
def v_salute(self, t0, a):
    s = (a.get("hand") or "R")[0].upper(); g = self._sg(s)
    brow = lambda t: self.ch.mouth + Vector((-g * 0.05, -0.02, 0.11))        # head-relative target (armature space)
    return self._gesture(s, t0, 0.45, float(a.get("hold", 1.0)), brow,          # T_in, hold, wrist target
                         palm=(0, 0.3, -1), fingers=(g * 0.6, -0.2, 0.8),      # palm normal / finger dir (world-ish)
                         curls={"thumb": .1, "index": 0, "middle": 0, "ring": .05, "pinky": .1},
                         pole_out=0.6, look=None)                              # elbow out to the side
```
Then add a row to `spec.md` and run `build --q sheet --fast --shots <id>`: the wrist limit keeps the palm anatomical.

### Add a mocap clip
`name: /path/any_humanoid_clip.fbx` works directly (converted once to `~/.shorts3d/clips/<name>.json`). For a
permanent alias add it to `library/clips/clips.json` (`clips` + `alias`).
