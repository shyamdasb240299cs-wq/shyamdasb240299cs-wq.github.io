# shorts3d-studio — a Claude Code skill for realistic 3D explainer Shorts

Give Claude an idea ("why does bread go stale?") and it produces a finished 9:16 MP4: hooked + looping script,
voiceover, realistic free actors animated in Blender (walking, reaching, grasping, eating, expressions, real mocap
body language — contact-accurate hands, nothing passing through anything), explainer FX, your brand on everything,
captions, sound effects and music. You review a fast preview first; the final Cycles render comes after you approve.

## Install (once, on your laptop)
1. **Blender 4.2+** (5.2 LTS recommended) — https://www.blender.org/download/ . Latest GPU driver.
2. **ffmpeg** on PATH, **Python 3.10+** with `pip install pyyaml pillow numpy`.
3. Copy this folder to `~/.claude/skills/shorts3d-studio` (or keep it in a project's `.claude/skills/`).
4. `python ~/.claude/skills/shorts3d-studio/bin/s3d.py setup --actors` → CC0 human data, 20 mocap clips and two
   realistic actors (`rb_male`, `rb_female`, Microsoft Rocketbox, MIT), SFX library. (~600 MB, once.)
5. `python .../s3d.py gpu --bench` → finds your GPU (OptiX/CUDA/HIP/Metal), VRAM tier, render-time estimates.
6. Voice (free, local): `pip install kokoro soundfile && python -m spacy download en_core_web_sm` + `espeak-ng`.
7. Brand: `python .../s3d.py brand --name "Your Channel" --color "#2EC4E6" --logo logo.png`
8. Make a channel folder and run `python .../s3d.py init` inside it → `CHANNEL.md` (the channel's memory), `videos/`,
   `assets/`. Check everything: `python .../s3d.py doctor`.

## Use
Open Claude Code in the channel folder: **"/shorts3d-studio make a video about why we yawn"**. Claude reads only
`CHANNEL.md` (not your old videos — cheap on tokens), writes the script, shows you the hook + narration, builds,
checks contact sheets, renders **`videos/NNN-<slug>/preview.mp4`** for your review, applies your notes, then renders
the final (Cycles on your GPU) → `final.mp4` + `publish.txt`, and `finish` deletes everything else in that folder.

Commands: `init new status voice plan build assemble inspect finish gpu add-character analyze assets brand doctor`
(`python .../s3d.py -h`). Interrupted render? Same build command + `--resume`.

## Characters
`rb_male` / `rb_female` are ready after `setup --actors`. Any free rigged human (.fbx/.glb/.blend/.obj — Rocketbox,
Mixamo, Sketchfab CC0/CC-BY, VRoid, Daz, CC): `python .../s3d.py add-character file.fbx --name maya` — the skeleton,
fingers and face (ARKit/visemes) are mapped automatically and the materials rebuilt. Then `char: maya` in a spec.

## Licences
Engine code: yours to use. Human data: MakeHuman CC0. Actors + mocap: Microsoft Rocketbox, MIT (credit appreciated).
Font: Montserrat (SIL OFL, `library/fonts/OFL.txt`). Built-in SFX are synthesized (original).
See `reference/assets.md` before adding third-party assets.
