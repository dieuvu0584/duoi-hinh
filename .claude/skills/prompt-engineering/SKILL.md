---
name: prompt-engineering
description: Build image concepts and provider-specific prompts for the Nhìn Hình Đoán Chuyện brand while preserving answer and clue integrity.
---
# Prompt engineering skill

1. Decide and freeze the answer/message first.
2. Write the intended viewer inference in one sentence.
3. List required visual clues and the mapping from each clue to the answer.
4. Identify ambiguity risks and plausible alternate answers. If the answer is not reasonably inferable, revise the concept before generation.
5. Write one provider-neutral visual blueprint, then separate FLUX and Gemini prompts. Both prompts must preserve the same answer, clues and composition intent.
6. Include aspect ratio, composition, subject/action, visual hierarchy, style, palette, lighting, background and safe zones.
7. Do not rely on generated text. Keep answer hidden from public rebus output by default.
8. Add explicit constraints against missing/duplicated clues, unreadable clutter, logos/watermarks and misleading photorealistic news evidence.
9. Run a self-critique before returning JSON. Do not return free-form prose when a schema is requested.

## Initial QA rubric (experimental)
- clue accuracy: 30%
- answer inferability: 25%
- humor/surprise: 20%
- mobile composition: 15%
- brand fit / non-misleading presentation: 10%

Scores >=80 may continue to safety checks, 65–79 need revision/regeneration, <65 reject. Wrong answer, missing clue, unverified news or safety block overrides any score. Treat thresholds as pilot hypotheses.
