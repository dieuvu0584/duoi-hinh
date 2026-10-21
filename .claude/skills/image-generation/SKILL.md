---
name: image-generation
description: Implement and operate image generation adapters and deterministic post-processing for FLUX, Gemini and manual uploads.
---
# Image generation skill

- Read current official provider docs before implementing endpoints, model IDs, request shapes, output parsing, pricing or limits.
- Implement a provider-neutral interface: generate, poll if async, fetch result, report provider/model metadata, classify errors and estimate/record cost when supported.
- Never log API keys or raw secret-bearing URLs.
- Normalize image outputs to the target 9:16 canvas without stretching. Preserve source originals and record transformations.
- Use Pillow for Vietnamese text overlay and deterministic composition. Use fonts with Vietnamese glyph coverage and test diacritics.
- For rebus, compose clue assets in a deterministic layout. Run checks for missing assets, crop collisions and clue order.
- Manual uploads must pass the same validation, provenance, copyright and safety flow as generated images.
- Record provider, model, prompt version, parameters, generation timestamp, original dimensions, processed dimensions, QA findings and storage keys.
- After the still is final, render the Reel mp4 and validate it with ffprobe as specified in `docs/video-pipeline.md`; never upload an unvalidated file.
- Handle provider timeouts/rate limits with bounded retries and budget caps. Avoid duplicate charges on blind retries.
