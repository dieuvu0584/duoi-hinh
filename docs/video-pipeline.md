# Video pipeline (image -> Reel mp4)

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
- Default duration by lane (configurable, pilot hypotheses): rebus 8-12 s, daily_life 6-10 s, news 8-12 s. Hard bounds 3-90 s enforced.

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
  audio: {source: "silent"|"library", asset_id|null, loudness_target_lufs: -16},
  safe_zone: {top_px, bottom_px, side_px}
}
```
Motion rules: any zoom/pan is computed so **no required clue leaves the safe area at any frame** (the renderer samples frames at 0.5 s and the QA step re-checks clue bounding boxes). Rebus clues must be stationary and readable for the whole hold; zoom limit is conservative (default <= 1.06x). Text overlays are rendered with Pillow (Vietnamese diacritics) as PNG layers; ffmpeg only composes/encodes.

## Safe zones
Reels UI covers parts of the frame (caption, buttons). Default conservative margins are configuration, not constants: top 250 px, bottom 400 px, sides 60 px at 1080x1920. **These are initial assumptions**; calibrate against Meta's current Reels guidance and real device screenshots during the Package 0 spike, then freeze in config.

## Audio
- Silent is acceptable technically but performs differently; treat audio as an experiment variable, not a default belief.
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
Voiceover/TTS, AI video generation (Veo etc.), auto-subtitles, trending-audio use, multi-scene editing. Re-evaluate after the pilot.

## Spike evidence (2026-10-20, developer machine, Windows, ffmpeg 9.0.1)
`spikes/render_reel_spike.py` rendered a Pillow-drawn Vietnamese test card to a 10 s, 1080x1920, 30 fps H.264/yuv420p + AAC 48 kHz stereo mp4 (3.8 MB) in ~13 s with a 1.06x slow zoom; the script's ffprobe gate reported no errors. This only proves the encode recipe and the validation script locally. It does **not** prove Meta accepts the file, GOP closure, safe zones on real phones, or VPS render time - those remain Package 0 items.
