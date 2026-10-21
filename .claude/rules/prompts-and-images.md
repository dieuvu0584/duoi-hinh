# Prompt and image rules

- The answer/message and required clue mapping are source-of-truth fields, decided before image generation.
- Generate provider-specific prompts for FLUX and Gemini while preserving identical intended meaning and clues.
- Do not ask image models to render exact Vietnamese captions, puzzle answers, UI labels or long readable text. Add text after generation with Pillow.
- For rebus, prefer generating/using separate clue assets and composing them deterministically. Check that clue order, separation and visual semantics survive composition.
- Structured LLM outputs must validate against `docs/schemas/*.json`; reject and retry or hold otherwise.
- Rebus: decompose the answer into syllables (`rebus_spec`); Vietnamese tones/diacritics are a known weak point of models, so a human verifies the answer.
- Reels are narrated in Vietnamese (`docs/video-pipeline.md`, Narration). The narration text belongs to the content revision and `content_hash`; for rebus it must never contain the answer (`safety_rules.leaks_answer`); normalise numbers/dates/abbreviations in code before synthesis; cache audio by hash of (text, voice, model, settings); narrated Reels always set `is_ai_generated=true`; never clone a real person's voice.
- The deliverable is an mp4 (`docs/video-pipeline.md`); motion must never push a clue out of the safe zone.
- Master image is square 1:1 at 1920x1920; outputs are 9:16 (1080x1920) and 16:9 (1920x1080) by centre-crop (`fill`), or `fit` (whole square over a blurred copy) when clues do not fit. Never stretch. Prompts ask for a full-bleed picture (no border, frame or white margin) with the subject and all clues compact in the centre; prompts alone do not guarantee this, so verify the clue boxes against the central safe square (normalised 0.21875-0.78125 on both axes) before rendering.
- Reels UI covers the top ~250 px and bottom ~400 px of the 9:16 frame; the central safe square (rows 420-1500) sits inside the usable band.
- Prompts specify subject/action, layout, visual hierarchy, camera/framing, style, palette, lighting, background, safe areas, mobile readability and prohibited artifacts.
- Use negative prompts only when the provider actually supports them. Do not send unsupported fields.
- Compare the final image to the frozen answer and clue mapping. If mismatch, reject or regenerate; never rationalize a mismatch by changing the answer silently.
- Each image candidate has a private explanation ticket: answer/message, intended meaning, visual explanation, clue mapping, ambiguity risks, caption and safety status.
- Scores are heuristics, not guarantees. Hard failures override numeric score.
