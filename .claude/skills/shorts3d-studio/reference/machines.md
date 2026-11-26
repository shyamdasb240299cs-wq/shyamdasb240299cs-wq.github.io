# Machines: cars, roads, engines, assemblies (engine/vehicle.py, road.py, nature.py, powertrain.py, turbokit.py, collide.py)

Read this before a video with a vehicle, a mountain/road set, an engine, or anything that is assembled / taken
apart on screen. Everything here was learned the hard way on `001-twin-turbo`; follow it and the first render is right.

## Vehicles (vehicle.py)
- `car = vehicle.load(glb, name, paint, remove=("badge","emblem","logo"), coll, hide=(...))` → rig with wheels
  (hub/steer/spin), steering wheel, pedals, gauge needles, lights. **No real brand marks**: strip badges/emblems
  (`strip_region` for grille emblems baked into a mesh).
- `vehicle.drive(car, path, speeds=[(frame, m/s)], s0, frames=(f0, f1), throttle=[...], wheelspin=[...])` keys
  everything per frame (spin from distance, Ackermann steer, steering wheel, pedal, tach/speedo, pitch/roll springs).
  It returns `spin_first/spin_last/radius` for continuing wheel angles.
- **Key the car until the last frame anyone sees it.** Keys that end early let Blender interpolate toward the next
  drive's start: a "parked" car slid 2 m in the engine-out shot. Hold it with keys up to the scene cut.
- Cameras that ride with the car: bake in car space (`space=car_space`), not parented (no body bounce).

### Seamless loop with a moving car (the end flows into frame 0)
1. The payoff ends **one frame of travel before** the hook's first position: `s_park = S0 - v/fps - S[-1]`
   (never "arrive at S0": frames NF-1 and 0 would be identical = a freeze).
2. Same speed both sides and zero acceleration: hold `V_LOOP` for the last ~14 payoff frames and the first ~12 hook
   frames (pitch/roll springs settle).
3. Wheel angles: run the payoff drive once, then again with
   `spin_start = (hook.spin_first - v/fps/r - payoff.spin_last) mod 2π`.
4. Road-texture noise: same `seed` and `noise_offset=-NF` on the payoff so its clock runs into frame 0.
5. Verify: car step, camera step, 2nd difference, wheel ΔAngle and needles are equal across NF-1 → 0 (`check_seam`).

## Roads + nature (road.py, nature.py)
- `Road(centreline(ctrl), width, shoulder, cut_side, cut_height, valley_drop)` + `road.build(...)`: real-scale road,
  rock cut on one side, valley on the other, far terrain blended to an HDRI horizon (aerial haze = median HDRI
  horizon colour). Sun lamp on the valley side (az = heading − 25°, el 14°) so the low sun reaches the road.
- `nature.ecosystem(...)`: Poisson-spaced clumps/clearings placed by BVH raycast on the real terrain. Rules that
  came from user feedback: **no roadside rocks** (they read as poking into the road), valley-side trees ≥ 24 m from
  the edge (open vista, sun on the road), undergrowth only near the camera, boulders ≥ 8 m from the edge on gentle
  slopes. Heavy needle-leaf variants cost ~1.3 KB/tri: skip them for 9–11 k trees (~6 GB peak).

## Engines + turbo kit (powertrain.py, turbokit.py)
- `PT.V8(coll, M)` - block, heads, plenum, manifolds, front accessories, moving pistons/rods/crank (`pose(theta, f)`
  also spins the pulleys; bolt heads + a timing mark make the rotation readable).
- `Kit(v8, coll, M)`: `stock()`, `single()`, `intercooler()`, `twins()`; `move(name, [(f, offset)])`,
  `show(name, f_on, f_off)`. All mates use `GAP` = 1 mm and pipes run straight along the mating normal for a lead-in
  (`pipe(start=(point, normal), end=(point, normal))`) so an end cap never digs into its mate.
- The **engine is off** while it is being removed (pistons, pulleys, air/exhaust all stop) and starts again where it
  is shown running. Wheels spin only when gas drives them.
- **Wagon-wheel rule** (30 fps): bladed wheels strobe / seem to run backwards between ~1 and ~9 rev/s. Plateaus are
  SLOW (≤ 0.9 rev/s, every blade readable) or FAST (≥ 12 rev/s + motion blur = smooth disc); change between them in
  ≤ 15 frames. Keep motion blur ON in previews when anything spins.
- A mechanism that the narration describes must physically do it: the wastegate valve lifts on "bleeds" and the
  bled exhaust visibly takes its dump tube round the turbine (back into the downpipe).
- X-ray: `xray_apply(mats, group)` + `xray_keys([(f, 0..1)])` per material family (block, pipes) to reveal flows.

### Air / exhaust flow = streamlines, not particles
`PT.stream(name, pts, coll, PT.stream_mat(name, color), count, radius, tail, fade_in, fade_out, r0, r1, swirl)` +
`PT.flow_keys(ob, speed_fn, density_fn, f0, f1)`. Thin glowing lines with fading tails gliding through the pipe
(users read "bubbles" as fake). Plume out of a pipe: `r1 > 1`; intake converging: `r0 > 1`; swirl through a
turbine. After the cameras exist call `PT.stream_width_keys(streams, shots)` - one key per shot keeps lines ~5 px
thick at any camera distance. Density 0 = no air: stop the flows when the engine stops.

## Opening a car to take the engine out
- Hood: a natural prop angle (~58° hinge rotation, panel ~50° up), not past vertical. Imported hoods sit
  embedded in the body: pop it **15 mm straight up** (latch release) before the swing, reverse on closing.
- Strut bar: **straight up ~10 cm** off the tower studs first, then carry it aside and set it down out of the
  camera's way (never vanish in mid-air on screen).
- Engine out: one smooth arc-length path (no stops at waypoints): slide off the cowl lip, straight up until the oil
  pan clears the radiator, forward under the raised hood's front edge, up and out of frame (match-cut to it coming
  down in the next scene). Re-aim the camera to follow.

## Collision QA (collide.py) - run on every frame of every motion before rendering
- `collide.check(groups, frames, allow=...)` → `[(frame, a, b, n)]`; `report`; `detail` for where.
- Kit assembly: strict, no `allow` at all (GAP mating makes it possible).
- Imported car parts embedded in the body: contact allowed **only** at rest, and during the seat transition only if
  the overlap shrinks (lifting off) / grows (settling) monotonically; everywhere else zero (`qa_hood` pattern).
- Exclude flows/streams from groups (not physical).

## Render gotchas (Blender 5.2)
- `bpy.ops.render.render(scene="X")` renders at the **context** scene's frame: `bpy.context.scene.frame_set(f)` and
  `sc.frame_set(f)` before every render.
- Geometry-nodes inputs: `md.properties.inputs.<Socket_N>.value` (+ `keyframe_insert("value")`) - `PT.mod_set/mod_key`.
- Collection instances of a shared collection did not render reliably: link the collection into each scene and
  parent its parts to one root that a constraint moves (influence keyed per scene).
- Streams change topology every frame: Cycles disables their motion blur (harmless, lines are thin).

## Post: tracked labels, loop-safe sound (see the video's `work/` for the full scripts)
- Export per-frame camera P·V matrices + part centres from Blender (`export_screen.py` pattern) → labels and leader
  lines stick to 3D parts in 2D post (PIL at 2×, then LANCZOS down). Pin-to-part labels re-pop after a cut; HUD
  pieces (counters, gauges, bars) do not. Place boxes with an occupancy resolver (HUD first) and keep out of the
  caption band and the Shorts UI.
- Sound on a **circular timeline** (every layer wraps; FFT filters without padding): the end flows into the start.
  Engine beds: varispeed a real recording; perspective per shot (exterior / cabin low-pass / aerial far); duck under
  the voice. Turbo whistle: extract the gliding tone from a dyno recording (STFT peak track + gaussian mask). Then
  check intelligibility: speech-to-text WER of the mix ≈ the dry voice's.
