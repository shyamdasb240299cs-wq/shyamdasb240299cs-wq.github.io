# Free assets that work with this engine (and are safe for a monetised channel)

Licence check rule: prefer **CC0** (no attribution, commercial OK). **CC-BY** = OK with credit in the description.
**CC-BY-SA** = credit + share-alike (risky for videos: treat as "only if you accept SA terms"). **NC** = never on a
monetised channel. Always re-check the asset page; licences change.

## Characters (your recurring cast = your "trademark")
| source | licence | how it plugs in |
|---|---|---|
| **Microsoft Rocketbox (default realistic cast)** - 115 scanned-quality avatars (adults, kids, professions) with full face rig (ARKit + visemes), 4K PBR textures | **MIT** (credit appreciated) | `s3d.py setup --actors` installs `rb_male` + `rb_female`; more: download any `Assets/Avatars/*/<Name>/Export/<Name>_facial.fbx` + `Textures/` from github.com/microsoft/Microsoft-Rocketbox -> `s3d.py add-character <Name>_facial.fbx --name <id>` |
| Any rigged free human (Sketchfab CC-BY/CC0, Mixamo, VRoid/VRM, Daz, CC) | per asset (CC-BY: credit in description) | `s3d.py add-character file --name X` (skeleton + face mapped automatically) |
| A free human mesh with NO skeleton | per asset | rig it first: Mixamo auto-rigger (free: upload .fbx/.obj, mark chin/wrists/elbows/knees/groin, download FBX "with skin") -> `add-character`. Body + fingers animate; faces need ARKit shape keys (Rocketbox/CC/MetaHuman have them) |
| **MakeHuman data (built-in path)** — base mesh, rigs, ARKit face units, visemes, skins, hair, clothes | **CC0** | `s3d.py setup` downloads; `library/characters/*.json` presets → engine builds + caches the character. No add-on needed. |
| MPFB2 Blender add-on (same data, GUI editor, hair editor) | add-on GPL, assets CC0 | design a character visually, export as .blend with **Mixamo rig + ARKit face units** → drop in `~/.shorts3d/library/characters/<name>.blend` |
| Mixamo characters + animations | Adobe royalty-free (use in projects, no raw redistribution) | Mixamo bone names are supported natively (no face rig on Mixamo characters) |
| MetaHuman | free < $1M revenue, may be used outside Unreal (2025 licence; re-check) | heavy; export FBX with ARKit blendshapes; Mixamo/UE naming is auto-detected |
| Daz Genesis 8/9 (Daz Studio free) | Daz EULA: renders/videos OK, no raw redistribution | Diffeomorphic importer → rig names auto-detected (`lHand`…); FACS faces need mapping |
| Character Creator / ActorCore | paid / per-asset licence | `CC_Base_*` names are auto-detected |
Signature look: same character preset, same brand shirt (logo auto-printed), same brand colour backdrop/props.

## Props, environments, materials
| source | licence | notes |
|---|---|---|
| **Poly Haven** models / HDRIs / textures | **CC0** | `src: poly:<id>` auto-downloads; `s3d.py assets bread` searches. Food: croissant, hamburger_buns, food_apple_01, bananas, lemon, carrot_cake… Furniture: dining_table, chairs, cutting boards, bowls. HDRIs: `set.world.hdri: <id>` |
| ambientCG | CC0 | PBR textures/HDRIs (wood, tiles, fabric) |
| Kenney, Quaternius | CC0 | stylised low-poly (good for diagrams, toys, games) |
| Smithsonian Open Access 3D | CC0 (per item) | real scanned objects, fossils, tools |
| NASA 3D Resources | generally public domain | spacecraft, planets (no NASA logo use as endorsement) |
| BlenderKit | free tier: "Royalty Free"/CC0 per asset | huge library inside Blender; export the model, then `file:` |
| Sketchfab | per model (filter CC0 / CC-BY) | download glTF → `file:path.glb` + `size_cm` |
| Built-in procedural | yours | `proc:` everyday props + mechanism parts (see explainers.md) |

## Anatomy (for body explainers)
Z-Anatomy (CC BY-SA 4.0 — credit + share-alike), BodyParts3D (CC BY-SA), NIH 3D (licence per model, many CC0/CC-BY),
Sketchfab CC-BY anatomy. Prefer CC0/CC-BY; otherwise build stylised organs from procedural parts.

## Motion capture (body language)
**Rocketbox animations (MIT)**: 300+ clips (idles, talk/listen gestures, laugh, think, shrug, cheer, phone, drink,
sit) - 20 curated ones download with `setup`, aliases in `library/clips/clips.json`; any other: `clip: {name: /path/x.fbx}`.
Mixamo (royalty-free), CMU Graphics Lab mocap (free incl. commercial products; no resale of the data), Rokoko Vision /
FreeMoCap / your phone video → mocap (you own your performance). Bandai-Namco dataset is **NC** → do not use.

## Voices (TTS)
| engine | licence | quality / notes |
|---|---|---|
| **Kokoro-82M** (default) | Apache-2.0 | natural, fast on CPU, word timings; voices `am_michael am_puck am_fenrir af_heart bm_george`… |
| Chatterbox (Resemble AI) | MIT | expressive, voice cloning of YOUR voice (consistent channel voice) |
| ElevenLabs | paid plan grants commercial use | best quality; `engine: eleven` + `ELEVENLABS_API_KEY` |
| Piper | engine open; **each voice has its own licence** | light/fast; check the voice card |
| edge-tts | unofficial use of Microsoft's online voices | free, good, word timings; ToS grey area for monetised use |
| Your own voice | yours | `engine: file, audio: take.wav` — aligned automatically (the genre's top channel uses the creator's own voice) |
Avoid for monetised videos: Coqui XTTS-v2 (non-commercial model licence), F5-TTS released weights (NC).

## Sound effects & music
Built-in synthesized SFX (yours, CC0-like): whoosh pop click ding thud crunch step grab tap zap riser squish boom.
Better libraries: Sonniss GDC bundles (royalty-free), Freesound (filter **CC0**), Kenney audio (CC0), Pixabay SFX.
Music: YouTube Audio Library (safe on YouTube), Pixabay Music (check: some tracks are Content-ID registered → claims),
your own. Put music in `~/.shorts3d/music/` (track: auto uses the first file) and SFX in `~/.shorts3d/sfx/` or
`videos/<slug>/audio/sfx/` (same names override).

## Fonts
Montserrat ExtraBold (SIL OFL, bundled in `library/fonts`). Any OFL font works: put the .ttf in `library/fonts` and set
`brand.font` / `captions.font` to its family name.

## Brand kit = your "common trademark"
`s3d.py brand --name "Your Channel" --color "#hex" --accent "#hex" --logo logo.png` → logo printed on every actor's
shirt (UV decal, moves with cloth), on props with `label:`, brand colour on studio walls, arrows/flow/rims, caption
accent colour. Keep it identical across videos: viewers recognise the channel before they read the name.
