# Story engine: idea → hook → beats → narration → title

Goal: a 35–50 s vertical short that stops the scroll in <1.5 s, explains ONE surprising truth about an everyday
thing, and ends on a line that flows back into the first line (viewers rewatch = retention).

## 1. Find the angle (30 seconds of thinking, not more)
Ask about the user's idea:
- **Invisible everyday**: what do people do/touch daily without noticing *how/why* it works? (outlet, zipper, toaster, yawning)
- **Counter-intuitive cause**: the common belief is wrong (stale bread isn't dry; "brain freeze" is a blood-vessel thing)
- **What if**: push one variable to the extreme (what if your hair had nerves) — highest median views in the data
- **Hidden mechanism**: show the inside (cross-section, x-ray, tiny workers) — the genre's signature
- **Practical payoff**: a trick the viewer can try today (how to find tiny dropped things)
Pick ONE reveal sentence: "X happens because Y" — everything in the video serves it. Verify it (see §8).

## 2. Hook (frame 0 + first ≤ 9 spoken words)
Visual: start **mid-action on hands + the everyday object** in close-up (≈50 % of top hooks), or a relatable situation
already in progress, or an extreme close-up of something surprising/gross. No logo, no title card, no greeting.
Verbal formulas (seen across the top 400 shorts):
| formula | example |
|---|---|
| If you [everyday action], [unexpected result]. | "If you leave bread out overnight, it turns hard as a rock." |
| Imagine you [situation with stakes]. | "Imagine you're driving and your car starts sinking." |
| You know when [relatable moment]? | "You know when your ear gets water stuck in it?" |
| [Imperative step]. (how-to) | "Take a hot can and press it on a drum." |
| This is what happens when [X]. | "This is what happens when you crack your knuckles." |
| What if [you/body part] had [other ability]? | "What if your hair had nerves?" |
| Why do [things] [do Y]? | "Why do drummers cut holes in their bass drums?" |
| [Number] — that's [superlative]. | "Twelve millimetres. That's how small an earring is on a carpet." |
Write 3 hooks, pick the one with the most concrete image + stakes in the fewest words.

## 3. Beat map (≈ 2.7 words/s → 110–130 words for 40–48 s)
| time | beat | visual device |
|---|---|---|
| 0–2 s | HOOK | hands + object ECU / insert, push-in |
| 2–8 s | STAKES / relatable context, "but here's the weird part" | character CU reaction (skeptical/surprised) |
| 8–30 s | MECHANISM in 3–5 micro-steps, one fact per sentence | macro, x-ray, cutaway, arrows, flow, exploded, tiny workers |
| 30–40 s | PAYOFF / twist / practical tip | character acts it out (eats, fixes, uses) + payoff expression |
| 40–46 s | LOOP line that completes into the hook sentence | wide or the hook framing again |

Pattern-interrupt every 2–3 s: new shot size/angle, a sound, an FX, or a reaction. Never two similar shots in a row.

## 4. Narration rules
- Second person, present tense, active verbs. 6–14 words per sentence. One idea per sentence.
- Concrete > abstract ("hard as a rock" > "less pleasant"). Say numbers plainly ("twelve millimetres").
- Momentum connectors: *But… So… Which means… And that's why…*  Open loops: "but that's not the weird part".
- Mark ≤ 1 emphasis word per 2 shots with `*word*` (accent caption).
- No filler: never "In this video", "let's", "basically", "actually", channel plugs, or questions without stakes.
- Speed: Kokoro `speed: 1.08–1.15` (≈ 160–175 wpm). Stop at ~130 words; cut adjectives first.

## 5. Loop endings (rewatch engine)
Make the last line grammatically finish with the first line:
- end: "…which is exactly what happens"  → start: "If you leave bread out overnight, it turns hard as a rock."
- end: "…and that's why"  → start: "Drummers cut holes in their bass drums."
Keep `tail: 0.3` for loops so the cut back to frame 0 feels seamless; match the last framing to the first if possible.

## 6. Formats (title pattern → beats)
| format | title | beats |
|---|---|---|
| How X works | "How Electrical Outlets Work 🤔" | use it → inside view → step 1-2-3 → payoff (charging) → loop |
| Why X | "Why Bread Goes Stale 🤔" | phenomenon → wrong belief → real cause (macro) → tip → loop |
| What if | "What If Humans Had Hummingbird Hearts 🤔" | premise → body change (x-ray) → consequence 1-2 → twist |
| How to (hack) | "How To Find Tiny Things You Dropped 🤔" | relatable fail → trick → why it works (physics view) → success |
| Patent Pending | "The Bathroom Mode 😏 (Patent Pending)" | relatable annoyance → invented gadget (fiction) → demo → joke |
| Story | "He Drifted At Sea For 133 Days 😨" | stakes → 3 escalating moments → resolution/fact |
| Myth test | "Does Running Like Naruto Make You Faster? 🤨" | claim → test setup → physics → verdict |

## 7. Title + emoji
3–9 words, Title Case, ends with ONE emoji: 🤔 how/why · 😮 surprising · 😨 danger · 😱 shocking · 🤯 mind-blowing ·
🤢 gross · 😏 clever · 😢 emotional. Data (median views): What If 15M > Why 12M > Can/Does 11M ≈ Patent 11M > How-to 9M.
Description: one curiosity sentence + 3–5 hashtags (`#shorts` first).

## 8. Truth & safety
Check the core claim against reliable sources (encyclopedias, .gov/.edu, peer-reviewed summaries) before scripting.
Health topics: no dosage/medical advice; say "doctors recommend…" only if true. Fictional gadgets/stories get
"(fiction)" or "(Patent Pending)" in the title. No real brands/logos on props (use parody names or your brand).

## 9. Deliver to the user (one compact message)
```
Hook: "<line>"   (visual: <frame-0 description>)
Narration (≈N words, ≈S s): <full text with *emphasis*>
Title: <title + emoji>   Loop: <last line> → <first line>
```
Then write `shots.yaml` (one shot per sentence or half-sentence) and continue the pipeline.

## Worked example (bread)
Angle: counter-intuitive cause (staling = starch recrystallising, not water loss; heat reverses it).
Hook: "If you leave bread out overnight, it turns hard as a rock." — frame 0: hand reaching for a slice on a plate.
Beats: weird part (skeptical CU) → baking melts starch (macro) → cooling: starch snaps into crystals (macro + flow)
→ feels dry though water is still there → heat melts crystals, soft again (bite, delighted) → loop "Which is exactly what happens…".
