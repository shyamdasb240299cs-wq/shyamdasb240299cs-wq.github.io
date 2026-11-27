# Rendering on a laptop GPU (preview first, final after approval)

## Once per laptop
`s3d.py gpu --bench` -> detects the backend (OptiX > CUDA > HIP > Metal > oneAPI), VRAM, and benchmarks EEVEE +
Cycles. Saved in `config.json`; every build uses it. Re-run after a driver/Blender update.
| tier | VRAM | Cycles | textures | advice |
|---|---|---|---|---|
| high | >= 10 GB (RTX 3080/4080 laptop, 4070 Ti+) | 128 spp | full | Cycles for every shot |
| mid | 6-10 GB (RTX 3060/4060/4070 laptop) | 96 spp | 4K | Cycles final; EEVEE previews |
| low | 3.5-6 GB (GTX 1650, RTX 3050, 2050) | 64 spp | 2K | EEVEE `final`; Cycles only `--shots` macro/hero |
| cpu | no GPU | - | 1K | EEVEE only, use `--step 2` drafts |
Cycles settings applied: GPU devices only (CPU off), adaptive sampling 0.03, OptiX/OIDN GPU denoise, persistent
data (BVH + textures stay in VRAM between frames), light tree, bounces 3/3/8, indirect clamp 10, motion blur.

## The two-stage loop (what you run, in order)
1. `build <v> --q sheet --fast` -> sheet.jpg (seconds) -> fix -> one `--q sheet` (real look).
2. `build <v> --q preview` + `assemble <v>` -> **preview.mp4** (EEVEE 50 %, 8 spp: fast). Prints an ETA.
3. `inspect <v>` -> every frame checked; read the flags (and `build/inspect.jpg` only if there are flags).
4. Send preview.mp4 to the user. Apply their notes -> rebuild only changed shots: `--q preview --shots S03,S05`.
5. On approval: `build <v> --q cycles` (tier high/mid) or `--q final` (EEVEE, tier low) -> `assemble` -> **final.mp4**.
   Mixing is allowed: `--q final` for all, then `--q cycles --shots S03` for the macro shot.
6. `inspect <v> --final`, then `finish <v>` (keeps final.mp4, shots.yaml, publish.txt, voice; deletes the rest).

## Long renders
- Run in the background and poll cheaply: `s3d.py status <v>` (one line per scene: frames, s/frame, ETA).
- Interrupted (sleep, crash, closed lid)? `build <v> --q cycles --resume` keeps finished frames.
- `--jobs 1` on a GPU (several Blender processes fight for VRAM). `--jobs 2` only for EEVEE on >= 12 GB.
- Out of VRAM (Cycles "out of memory" in the log) -> lower the tier in `config.json` (`gpu.texture_limit: "2048"`,
  `gpu.samples: 64`) or render that scene in EEVEE `final`.

## Laptop checklist (tell the user once)
Plugged in + "best performance" power mode; Blender set to the dedicated GPU (Windows: Settings > Display >
Graphics > Blender = High performance; NVIDIA Control Panel the same); close browsers/games; keep the vents free -
expect 10-30 % slower frames after ~15 min of thermal throttling (the ETA adapts).
