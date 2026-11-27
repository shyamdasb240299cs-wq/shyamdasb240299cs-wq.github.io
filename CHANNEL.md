# CHANNEL.md - My Channel
> Claude: at the start of a session read THIS file + the current video's folder only. Never open other videos'
> folders or old builds; everything needed for consistency is here. Keep it short: update Decisions/Learned when
> the user states a preference or a fix worked.

## Identity (same in every video)
- Brand: My Channel - colour #3EC6E0 - logo brand_logo.png - captions 3 words, Montserrat ExtraBold
- Voice: bm_george x1.05 - music: auto, -22 dB under the voice
- Cast: hero (MakeHuman)
- Format: 9:16, 30 fps, 35-55 s, hook = hands on the everyday object at frame 0, loop ending
- Render: GPU not detected (run s3d.py gpu --bench) (tier ?) - review: `build --q preview` -> preview.mp4 - final: `build --q final`

## Decisions (user preferences, newest first)
- Preview simple and proper (CPU, lite crowd); the final on a GPU keeps the full detail (all 31 crowd avatars, SUBSURF, 2K textures)
- Audience = real 3D people (Rocketbox), randomised simple gestures, never boxes; nothing may overlap or cross another object
- Cars/props/stadium photoreal (no plain paint); camera moves like real operators/drones; narration George, storytelling, natural pace

## Learned (one line per fix that worked)
- Cycles CPU: a procedural volume (spray) doubles every frame (octree build + marching) -> show it per shot, only where it reads on screen
- Rain near-lens clip must scale with focal length (max(1.6, lens*0.08) m) and use the moved positions (fields re-evaluate per node)
- Run work/qa_visibility.py after each build: real LIDAR level steps (pit lane 1.2 m below the straight) hid the car behind walls
- Extruded walls need end caps (an open end renders as a black box)

## Videos
| # | folder | title | status | length |
|---|--------|-------|--------|--------|
| 002 | 002-senna-pitlane | The Fastest Lap Was Set In The Pit Lane | preview rendering | 57.7 s |
