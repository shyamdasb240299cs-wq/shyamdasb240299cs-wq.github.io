# shorts3d-studio — a Claude Code skill for realistic 3D explainer Shorts

Give Claude an idea ("why does bread go stale?") and it produces a finished 9:16 MP4: hooked + looping script,
voiceover, realistic 3D character animation in Blender (walking, reaching, grasping, eating, expressions — with
contact-accurate hands and no objects passing through each other), explainer FX, your brand on everything,
captions, sound effects and music.

## Install (once, on your PC)
1. **Blender 4.2+** (5.2 LTS recommended) — https://www.blender.org/download/ (a GPU makes renders fast).
2. **ffmpeg** on PATH, **Python 3.10+** with `pip install pyyaml pillow numpy`.
3. Copy this folder to `~/.claude/skills/shorts3d-studio` (or keep it in a project's `.claude/skills/`).
4. In a terminal: `python ~/.claude/skills/shorts3d-studio/bin/s3d.py setup` → downloads the free CC0 human data
   (~400 MB), finds Blender, makes a starter SFX library.
5. Voice (free, local): `pip install kokoro soundfile && python -m spacy download en_core_web_sm` and install
   `espeak-ng` (Windows: installer from the espeak-ng GitHub releases; macOS: `brew install espeak-ng`).
6. Your brand: `python .../s3d.py brand --name "Your Channel" --color "#2EC4E6" --accent "#FFD43B" --logo logo.png`
7. Check: `python .../s3d.py doctor` → ALL GOOD.

## Use
In Claude Code, in any folder: **"/shorts3d-studio make a video about why we yawn"**. Claude writes the script,
shows you the hook + narration, then builds, checks a contact sheet, renders and assembles `videos/<slug>/final.mp4`
plus `publish.txt` (title, description, hashtags). Works well with Opus/Sonnet at medium effort: Claude only writes a
small `shots.yaml`; the engine does the heavy 3D work.

Commands you can also run yourself: `new`, `voice`, `plan`, `build --q sheet|blocking|preview|final|cycles`,
`assemble`, `analyze <reference.mp4>` (frame-by-frame breakdown of any video you have), `assets <query>`.

## Your recurring character
Edit `~/.shorts3d/library/characters/hero.json` (body sliders 0–1, skin, hair, clothes from the CC0 packs) and delete
`hero.blend` to rebuild. Or design one visually with the free MPFB add-on (Blender → Get Extensions → "MPFB"),
give it a Mixamo rig + ARKit face units, and save it as `~/.shorts3d/library/characters/<name>.blend`
(collection `CHAR_<name>`).

## Licences
Engine code: yours to use. Human data: MakeHuman CC0. Font: Montserrat (SIL OFL, `library/fonts/OFL.txt`).
Built-in SFX are synthesized (original). See `reference/assets.md` before adding third-party assets.
