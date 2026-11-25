# QA: what the checks mean and the visual review checklist

`build` prints only issues. Each line = problem @ frames → fix. Run again with `--shots` for just the fixed shots.

| QA line | cause | fix |
|---|---|---|
| hand can't reach target (max N cm) | prop beyond arm + lean + crouch | `walk_to: {target: X, stop: reach}` before reach; move prop toward the table edge facing the actor |
| finger inside held prop | odd grip orientation / wrong prop scale | `reach: {grip: top|side|pinch|pinch_edge}`; correct `size_cm` |
| body inside furniture (hand/forearm/shin/thigh) | actor placed in/through a table | move `cast.at` back 10–20 cm, or use `walk_to … stop: reach` (computes a stand point outside) |
| foot sliding while planted | very short/fast walks or turning in place | `speed: 0.8`, fewer direction changes, `turn_to` before walking |
| foot below floor | floor height mismatch | keep floors at z=0; never move set floors |
| prop not resting on support (+/- mm) | raw z placement or sinking | use `on: <surface>`; for intentionally floating things set `free: true` |
| subject outside caption-safe frame | camera framing (target, size, angle) | change `size`, `angle`, `el`, `target: hero.face`, or `lock: true` |
| camera … view blocked | wall/furniture between camera and subject | another `angle`/`az`, smaller `dist`, or move props |
| reach: target … out of reach (log) | same as first row | same |
| unknown verb / fx / expression | typo | see spec.md lists |

## Visual review of `build/sheet.jpg` (3 stills per shot: start, middle, end)
1. Hook (S01): is the object + hand readable at thumbnail size? Is frame 0 already mid-action?
2. Every shot: subject in the upper 2/3, nothing important in the bottom 25 % (captions) or edge 12 %.
3. Contacts: fingers on (not in) objects, food at the lips on bite frames, feet on the floor, props resting.
4. Faces: expression readable and matching the line; eyes looking at something meaningful.
5. Continuity: same hand/side holds the prop across cuts; 180° rule respected.
6. Variety: no two consecutive shots with the same size+angle; brand colour visible somewhere in most shots.
7. Light: subject brighter than background, rim light visible, no blown-out skin.
Fix by editing `shots.yaml` only, rebuild flagged shots. Stop after 3 loops and render.
