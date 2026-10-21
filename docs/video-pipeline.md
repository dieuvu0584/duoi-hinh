# Video pipeline (square master -> Reel 9:16 and YouTube 16:9)

Facebook Reels are published as **video**. Verified against Meta's Reels Publishing API guide (Graph API v26.0 at time of writing): the Page `video_reels` edge accepts an uploaded video file, not an image. The still 1080x1920 image produced by the image pipeline is therefore an *intermediate*; the publishable artifact is an `.mp4` rendered deterministically from it.

> Re-verify every number below against the current official guide before Package D. Meta changes specs and limits.

## Meta Reel technical constraints (from official guide, to re-verify)
| Property | Constraint |
|---|---|
| Container/codec | `.mp4` recommended; H.264/H.265 (VP9, AV1 also supported) |
| Aspect ratio | 9:16 |
| Resolution | 1080x1920 recommended, minimum 540x960 |
| Frame rate | 24-60 fps, **fixed** frame rate |
| Duration | 3-90 s (60 s max if also shared as a Page story) |
| Video settings | 4:2:0 chroma subsampling, closed GOP of 2-5 s, progressive scan |
| Audio | 128 kbps+, stereo, AAC-LC, 48 kHz |
| Audio required | Not specified by Meta. We always mux an audio track (see Audio) |
| File size | Not specified by Meta; we cap locally (default 100 MB) |
| Daily limit | 30 API-published Reels per rolling 24 h (we enforce a much lower local cap) |

Known API error codes for bad media: `1363040` (aspect ratio), `1363127` (resolution), `1363128` (duration), `1363129` (frame rate). Our pre-upload validation must make these unreachable.

## Render spec (our defaults)
- 1080x1920, **30 fps constant**, H.264 High profile, `yuv420p`, progressive.
- Closed GOP of 2 s: `-g 60 -keyint_min 60 -sc_threshold 0 -flags +cgop`.
- Audio: AAC-LC, 48 kHz, stereo, 128 kbps (silent track if no licensed audio is chosen).
- `-movflags +faststart`. Target bitrate configurable; start at ~8 Mbps video.
- Default duration by lane (configurable, pilot hypotheses): rebus 8-12 s, daily_life 6-10 s, news 8-12 s. Hard bounds 3-90 s enforced. **When there is narration the duration is derived, not fixed**: `duration = lead_in + narration_duration + think_time`, clamped to the bounds (see Narration).

## Timeline templates (data, not code)
A render is described by a `video_spec` JSON stored on the `video_assets` row, so a video is reproducible from its inputs.

```
video_spec = {
  template: "rebus_hold" | "daily_kenburns" | "news_card",
  fps: 30, duration_s: 10,
  layers: [ {type: "image", asset_id, motion: "none"|"slow_zoom"|"pan", zoom_max: 1.06},
            {type: "overlay_text", text, font_id, box, appear_s, disappear_s},
            {type: "countdown", from: 5, appear_s: 3}   // optional "guess now" cue
          ],
  audio: {narration: {asset_id, start_s, duration_s}|null,
          music: {asset_id, duck_db: -18}|null,   // optional, owner-licensed only
          loudness_target_lufs: -16},
  safe_zone: {top_px, bottom_px, side_px}
}
```
Motion rules: any zoom/pan is computed so **no required clue leaves the safe area at any frame** (the renderer samples frames at 0.5 s and the QA step re-checks clue bounding boxes). Rebus clues must be stationary and readable for the whole hold; zoom limit is conservative (default <= 1.06x). Text overlays are rendered with Pillow (Vietnamese diacritics) as PNG layers; ffmpeg only composes/encodes.

## Safe zones
Reels UI covers parts of the frame (caption, buttons). Default conservative margins are configuration, not constants: top 250 px, bottom 400 px, sides 60 px at 1080x1920. **These are initial assumptions**; calibrate against Meta's current Reels guidance and real device screenshots during the Package 0 spike, then freeze in config.

## Narration (voiceover) - decision D-24
Each Reel is narrated in Vietnamese: the voice reads the question/setup for the image so the viewer understands without reading. Narration is a first-class, reviewed part of the content, not decoration.

### What is spoken, by lane
- **rebus:** introduces the puzzle and the question ("Nhin hinh doan chuyen: day la mon gi?"), may state neutral facts such as the number of words, and invites the viewer to guess. It **never says or spells the answer, and never gives a hint that equals it**. A deterministic check (`leaks_answer`, tones and punctuation ignored) rejects any revision whose narration, caption or overlay contains the answer.
- **daily_life:** a short setup or punchline for the scene (about 10-25 spoken words).
- **news (only if the lane is enabled):** neutral, sourced statements; states that the image is an illustration; always human reviewed.

### Script
- The narration text is produced with the concept (same LLM step, schema `narration_text`), is a field of the content revision, **is part of `content_hash`** (together with `voice_id`) and is reviewed by the safety gate exactly like the caption. Editing it creates a new revision and invalidates approval.
- Target length: short enough that the whole Reel stays inside the lane duration (**measured 2026-10-22: about 3.1-3.3 spoken Vietnamese words/syllables per second** with the chosen voice, so a 10-25 word line is roughly 3-8 s).
- Deterministic text normalisation before synthesis: expand numerals, dates, units, abbreviations and symbols into Vietnamese words ourselves, and fix known mispronunciations with a replacement list. (ElevenLabs documents that Flash models do not normalise numbers by default; do not rely on the model for this.)

### Synthesis (ElevenLabs)
- Provider adapter `SpeechProvider` (contracts). `POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}`, header `xi-api-key`, body `text`, `model_id`, optional `voice_settings` (`stability`, `similarity_boost`, `style`, `speed`), `seed`, `apply_text_normalization`; `output_format` query. The response is binary audio. Verified against the official reference on 2026-10-22; response headers, per-request limits, 401/429/quota errors and pricing are **not** documented there and must be measured in the spike.
- Model IDs are configuration (`ELEVENLABS_MODEL_ID`), never hard-coded. Per the official models page: `eleven_multilingual_v2` does **not** list Vietnamese; Vietnamese is listed for `eleven_flash_v2_5` (cheaper, 50% lower price per character) and for `eleven_v3` / `eleven_v4` / `eleven_v4_turbo`. **Chosen (decision D-25, owner listening test 2026-10-22): `eleven_v3`.** `eleven_flash_v2_5` and `eleven_turbo_v2_5` mispronounced dates and the phrase "that no" and are not used for narration; they could only serve non-critical previews. `eleven_v3` costs about twice as much per character (about 0.4 vs 0.2 credits) and takes 2-4 s per line, which is irrelevant for offline rendering.
- Voice: a single owner-chosen `ELEVENLABS_VOICE_ID`, stored as `voice_id` on the revision. **No cloning of real people**; use a voice the owner is licensed to use commercially under the account plan (record the plan/licence note on the audio asset).
- Output: request a lossless or high-bitrate format the current plan allows, then re-encode with the video (AAC 48 kHz stereo). Content-address the result by `sha256(text + voice_id + model_id + settings)`; **cache hits cost nothing and make rendering reproducible**.
- Cost: characters are billed by the provider; every call is written to `cost_ledger` (provider `elevenlabs`) and counted against its own budget (default 0 until the owner sets it, decision D-18). Regeneration is bounded by `max_image_regenerations`-style limits. No blind retries after a timeout: record the attempt first.
- Failure: if synthesis fails the revision goes to `NEEDS_REWORK` with a reason. The pipeline never silently publishes an un-narrated Reel for a lane that expects narration.

### Timeline and mixing
- `lead_in` 0.5-1.0 s (image visible, no speech), then narration, then `think_time` (rebus 4-6 s, others 1-2 s) with the puzzle held still, and an optional countdown cue. Total clamped to 3-90 s (target under about 25 s for retention; to be tested).
- Mix with ffmpeg: narration is the primary track; optional music is ducked (about -18 dB under speech) and must be owner-licensed; normalise the final mix to the target loudness (`loudnorm`, about -16 LUFS integrated, true peak <= -1.5 dBTP; values to be tuned on a phone); final AAC-LC, 48 kHz, stereo, 128 kbps or more.
- Overlay text shows the key words of the question while they are spoken (readable by viewers watching muted). Full auto-subtitles are out of scope.

### QA and review
- Validation gate additions: an audio stream exists and is not silent (RMS above a floor), no clipping, narration ends before the hold period, duration within bounds, measured loudness within tolerance, answer-leak check passed.
- The owner's review card has an **audio player** and the exact narration script next to the video. Pronunciation of Vietnamese tones and proper nouns is the main quality risk; the owner listens before approving during the pilot.
- The safety review receives the narration text and the audio metadata (`voice_id`, model, settings, text hash). The audio is deterministic from the reviewed text, so the text review plus the owner's listening covers it.

### AI disclosure
A synthetic voice is AI-generated audio. `is_ai_generated` must be `true` for any narrated Reel, and the Page should not present the voice as a real person. Meta's labelling policy mentions realistic-sounding audio created with AI; see `docs/publishing-meta.md` D4.

## Audio (music and other tracks)
- With narration the Reel is never silent. Background music is an optional experiment (decision D-13) and must be quieter than speech; silent-only is a fallback for lanes without narration.
- Only use audio the owner is licensed to use (own/generated/royalty-free with a recorded license). Store `audio_license` metadata per track. No trending-audio scraping.
- After upload Meta runs a copyright check (`copyright_check_information`: `in_progress`/`complete`, `matches_found`). The publish gate waits for `complete` and blocks on `matches_found=true` unless the owner overrides with audit.

## Validation gate (must pass before upload)
Run `ffprobe` on the rendered file and assert: container mp4, resolution == 1080x1920 (>=540x960 absolute floor), constant fps in [24,60], duration in [3,90] and within lane bounds, `pix_fmt=yuv420p`, video codec H.264/H.265, audio AAC 48 kHz stereo, GOP/keyframe interval <= 5 s, file size <= cap. Store the ffprobe JSON in `video_assets.probe`. A failed gate sets the video `INVALID` (never uploaded).

## QA on video
- Extract keyframes (first, middle, last, and every motion extreme). Run the same clue-visibility and text-legibility checks as for the still.
- The vision QA (`prompts/quality_review.md`) receives the keyframes plus the frozen answer/clue mapping.
- The final safety review (`safety_review.md`) must see the **exact rendered video's keyframes, overlay text, caption, hashtags, audio metadata** and is bound to the video's content hash (see `docs/publishing-meta.md`).

## Tooling and determinism
- `ffmpeg`/`ffprobe` are system dependencies (installed in the worker image; pinned version recorded in `video_assets.encoder_version`).
- Rendering is a Celery job, idempotent by `(content_revision_id, video_spec_hash)`. Same inputs => same output hash is a goal (verify; encoder output may differ across versions - record version).
- Temp files in a per-job directory; cleaned on success/failure; disk quota guarded.

## Out of scope for MVP
AI video generation (Veo etc.), voice cloning, auto-subtitles (the on-screen overlay text carries the key words), trending-audio use, multi-scene editing. Re-evaluate after the pilot.

## Spike evidence (2026-10-20, developer machine, Windows, ffmpeg 9.0.1)
`spikes/render_reel_spike.py` rendered a Pillow-drawn Vietnamese test card to a 10 s, 1080x1920, 30 fps H.264/yuv420p + AAC 48 kHz stereo mp4 (3.8 MB) in ~13 s with a 1.06x slow zoom; the script's ffprobe gate reported no errors. This only proves the encode recipe and the validation script locally. It does **not** prove Meta accepts the file, GOP closure, safe zones on real phones, or VPS render time - those remain Package 0 items.

## Narration measurements (2026-10-22, ElevenLabs, Starter plan, voice "Ngoc Ngan - Vietnamese Storyteller", `spikes/elevenlabs_tts_spike.py`)
6 Vietnamese lines x 3 models, 18 calls, 1,593 characters sent. All returned HTTP 200 mp3 (44.1 kHz/128 kbps).
- **Speed:** mean 3.3 (Flash v2.5), 3.1 (Turbo v2.5), 3.2 (v3) words per second; audio for the 6 lines ran 36-39 s per model.
- **Latency:** Flash/Turbo about 0.3-0.5 s per line (the first call was 2.7 s, a cold start); v3 about 2-3.8 s.
- **Billing:** the response header `character-cost` is authoritative and the account counter moved by exactly its sum (425 for 1,593 characters sent): about **0.2 per character on Flash and Turbo and 0.4 per character on v3** on this plan. The counter updates with a delay of up to a minute. Track spend from `character-cost`, not from request length; keep the request length as the pre-call upper bound.
- **Other headers:** `request-id`, `history-item-id`, and concurrency headers (`maximum-concurrent-requests: 6`).
- **Quota:** Starter, 81,227 characters per period and no overage (`can_extend_character_limit: false`), so a narrated 25-word Reel (about 130 characters) uses roughly 26-52 credits; the plan allows well over a thousand Reels a month.
- **Not measured:** pronunciation quality (needs a human to listen; the files are in `spikes/output/tts/`, gitignored), number/date reading correctness, error codes for 401/429/quota.

### Pronunciation regression set (owner-verified)
Failures found by the owner's listening test (2026-10-22, voice "Ngoc Ngan - Vietnamese Storyteller"):
| Line | flash_v2_5 | turbo_v2_5 | v3 |
|---|---|---|---|
| `digits_raw` ("ngay 25/10/2026, ... 3 ngay lien, ... 24.500 dong") | date read wrongly | date read wrongly | no problem reported |
| `digits_spelled` (same, fully spelled out) | date read wrongly | date read wrongly | no problem reported |
| `tone_minimal_pairs` ("... an Tet that no") | "that no" read wrongly | "that no" read wrongly | no problem reported |
| `tones_names` ("Anh Tuan o Da Nang **chay** xe may qua cau Rong ...") | the word "chay" read wrongly | no problem reported | no problem reported |
Because the hand-spelled date was also read wrongly by Flash/Turbo, the cause is the model, not missing normalisation. Rules that follow:
- These lines (and any future mispronunciation) live in a **regression list** kept with the TTS code; whenever the model, voice, settings or pronunciation lexicon change, re-synthesise them and have the owner re-listen before the change ships.
- A **pronunciation lexicon** (owner-maintained data: written form -> respelling, versioned) is applied before synthesis and its version is stored on the audio asset. ElevenLabs also supports pronunciation dictionaries (`pronunciation_dictionary_locators`); whether they work for Vietnamese is unverified.
- During the pilot the owner listens to every narration before approving; a mispronunciation sends the revision back to rework (change the wording or the lexicon).

## Two renditions from one master (decision D-26)
The same square master, the same narration and the same caption produce two videos:

| Target | Size | Used for |
|---|---|---|
| `reel_9x16` | 1080x1920, 30 fps | Facebook Reel (specs above) |
| `youtube_16x9` | 1920x1080, 30 fps, H.264 High, yuv420p, AAC 48 kHz stereo, `faststart` | YouTube channel (see `docs/publishing-youtube.md`) |

- **Master:** 1:1, generated at **1920x1920** so both crops are native resolution (no upscaling). With fal.ai FLUX.2 this costs about USD 0.044 per image (megapixel pricing, 3.69 MP at USD 0.012/MP; measured USD 0.0442). A 1280x1280 master (about USD 0.020) is possible but needs a 1.5x upscale of each crop.
- **Mode `fill` (default):** centre-crop with no black bars and no extra backdrop (`scale=W:H:force_original_aspect_ratio=increase,crop=W:H`). Cropping a square to 9:16 keeps 56.25% of the width; cropping to 16:9 keeps 56.25% of the height. The only area both crops share is the **central safe square** (x and y from 0.21875 to 0.78125, 1080x1080 on a 1920 master). Essential subject and clues must be inside it. This square also sits inside the Reels usable band (rows 420-1500 of 1920, between the 250 px top and 400 px bottom UI margins).
- **Mode `fit` (fallback):** the whole square at the target's size over a blurred, dimmed copy of itself. Used when clues do not fit in the safe square, or by owner choice. Nothing is cropped.
- **Composition check (mandatory before `fill`):** a vision model returns normalised bounding boxes for every essential clue/subject (`composition.clue_boxes`); the backend (not the model) tests them in `app/services/composition.py`, **per target**: the 9:16 crop keeps the central 56.25% of the width and avoids the Reels UI margins (y from 0.130 to 0.792), the 16:9 crop keeps the central 56.25% of the height (y from 0.21875 to 0.78125). A clue outside a target's region -> that target uses `fit` (or regenerate); no boxes -> `fit` (fail closed). A tall subject can therefore be `fill` for the Reel and `fit` for YouTube. Prompts asking for a centred subject help but do **not** guarantee it (measured, below), so this check cannot be skipped.
- **Captions per target:** 9:16 caption in the top band (y 255-395, above the central square and below the Reels top margin); 16:9 caption in the right side band (x 1520-1880). Rendered with Pillow (Vietnamese diacritics verified).
- **Shared timeline and audio:** lead-in 0.8 s, narration, 2 s hold; one narration file is mixed into both; loudness-normalised (`loudnorm` -16 LUFS / -1.5 dBTP; measured -15.5 LUFS and -1.5 dBTP on both outputs).
- Both renditions go through the ffprobe gate for their own size and the keyframe QA/safety review; the content hash covers the master and the template version, so a re-render with a different template needs a new review.
- YouTube has no 250/400 px UI margins, but the same safe square keeps the two crops consistent.

### Measured (2026-10-22, `spikes/render_renditions_spike.py`)
Master 1920x1920 (cat and alarm clock, prompt asking for a compact centred subject) -> `reel_9x16.mp4` and `youtube_16x9.mp4`, both 7.6 s (0.8 + 4.8 narration + 2.0), 4.5 MB, validated by ffprobe with no errors, rendered in about 25 s each. Findings: no black bars in either; the picture fills the frame; Vietnamese caption renders with all diacritics. **The model did not keep the subject inside the central square**: the cat's tail and the right edge of the clock fell outside it, so the 9:16 crop cut the clock's right edge and the 16:9 crop cut the bottom of the clock and the cat's paws. Acceptable for a meme, a failure for a rebus clue; hence the mandatory composition check and the `fit` fallback.

### Composition check measured (2026-10-22, `spikes/claude_composition_spike.py`)
`claude-opus-5-5` (official Anthropic SDK, structured JSON output) was given the 1920x1920 master (downscaled to 1024 px) and asked for the boxes of "cat" and "alarm clock". It returned both within one call: cat (0.072, 0.385) to (0.548, 0.795), clock (0.553, 0.505) to (0.812, 0.830). Drawn over the image, the boxes matched the real outlines closely (the cat's tail and the clock's feet are inside them). Our code decided `fit` for both targets because the cat sticks out of the left crop edge and both boxes cross the lower limit, which agrees with the cut frames seen earlier. Cost: 1,989 input + 222 output tokens, about USD 0.012 per check. Not yet evaluated: accuracy on many images, small clues, several similar clues, and cheaper models (the default stays `claude-opus-5-5`; a cheaper model is the owner's decision). When this becomes an adapter, follow the SDK guidance to handle `stop_reason == "refusal"` and to enable the server-side refusal fallback for this model.
