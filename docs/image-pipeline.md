# Image pipeline

1. Freeze content answer/message, clues and visual blueprint.
2. Generate one or more candidates using configured FLUX/Gemini adapter, or accept a manual upload.
3. Validate file content/MIME, byte size, decoded dimensions and format. Reject corrupt or unsupported files.
4. Preserve original asset; record provider, model, prompt/version, parameters and provenance.
5. Normalize to 1080x1920 (9:16) with aspect-preserving crop/pad. Never stretch. Verify clue safe areas.
6. Compose rebus symbols deterministically when possible; overlay Vietnamese text with Pillow and a font supporting diacritics.
7. Run visual QA against frozen answer/clue mapping and check for missing clues, ambiguous alternate readings, clipping, text legibility and artifacts.
8. Create private explanation ticket for admin review.
9. Run final safety review on the exact processed image plus overlay/caption/hashtags.
10. Render the Reel mp4 from the approved image and `video_spec`, validate with ffprobe, extract keyframes, repeat QA and safety review on the exact video (`docs/video-pipeline.md`).
11. Store review status and approval bound to `content_hash`. Regeneration must be budgeted and bounded.

## Required explanation ticket
- answer/message;
- intended meaning;
- visual explanation;
- clue mapping (each visual clue -> intended interpretation);
- ambiguity risks and plausible alternate answers;
- caption and answer reveal policy;
- safety status and required human review.

The ticket is private admin metadata. For rebus, do not put the answer into the public image/caption by default.
