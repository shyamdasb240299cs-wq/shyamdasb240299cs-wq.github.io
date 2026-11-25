# Realism & render settings

## The genre look (from frame analysis)
Bright high-key light, soft but defined shadows, saturated colours with a **cyan/sky-blue** dominant palette, warm
skin against cool backgrounds, shallow depth of field in close-ups, wide-angle close-ups for punch, simple sets.

## Defaults the engine applies
- Colour: AgX view transform + Punchy/High-Contrast look, exposure -0.15.
- Light rigs (`set.light`): `soft` (key 45° high-left, warm; cool weak fill; strong rim; soft top) · `bright` ·
  `drama` (hard key, weak fill, strong rim) · `night` (warm practical + blue fill) · `xray` (cyan rims, dark).
- World: brand-tinted colour at 0.35 strength (ambient + reflections), `sky` for outdoor, or any Poly Haven HDRI.
- Skin: MakeHuman texture + saturation 1.18 + subsurface (radius R1 G0.35 B0.18, 1.2 cm) + micro-pore bump.
- Eyes glossy (coat), hair cards alpha-clipped (crisp), clothes sheen, wood/ceramic/bread/metal procedural materials.
- Camera DOF on the subject; f-stops by shot size; motion blur on final renders.

## Quality levels
| `--q` | engine | use | cost |
|---|---|---|---|
| `sheet` | EEVEE 40 %, 12 spp | 3 stills/shot to review composition, acting, contacts | seconds/still on a GPU |
| `blocking` | Workbench 50 % | full-motion timing check, very fast | ~0.1–0.3 s/frame |
| `draft` | EEVEE 35 %, 8 spp | look + motion draft | fast on GPU |
| `preview` | EEVEE 50 % | full video draft with captions & audio | fast |
| `final` | EEVEE 100 %, 64 spp, raytracing, motion blur | publish | ~1–4 s/frame on a modern GPU |
| `cycles` | Cycles GPU, 96 spp adaptive + OIDN denoise | macro/glass/liquids/skin hero shots (`--shots`) | 10–60 s/frame |
Mix: render most shots EEVEE `final`, hero macro shots `cycles --shots S03,S04` (same frame numbering → assemble picks
up whatever is newest per shot).

## Make it look more real (cheap wins, in order)
1. Correct scale (`size_cm`) and contact (`on:`), visible soft contact shadows.
2. Brand-coloured but not pure-saturated walls (pastel/mid tones); add 2–3 background props (counter, cabinets).
3. Rim light separation on characters (`soft` already has it) and slightly darker backgrounds than the subject.
4. Shallow DOF on inserts/macros; avoid everything-in-focus wide shots unless establishing.
5. Imperfection: `yaw` on props, slightly off-centre placements, wood/stone textures over flat colours.
6. Poly Haven HDRI for reflective materials (`set.world.hdri: studio_small_09`).
7. Motion blur on final (default), 30 fps.

## Hardware notes
EEVEE needs a GPU (any modern NVIDIA/AMD/Apple). On machines without a GPU use `--q cycles` (CPU) or `blocking`.
Cycles picks OptiX/CUDA/HIP/Metal automatically.
