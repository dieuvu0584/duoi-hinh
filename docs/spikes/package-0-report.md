# Package 0 report (running log)

Evidence from real runs only. Items not listed here are still unverified (see `docs/publishing-meta.md` VERIFY).

## 2026-10-22 - Meta, first live attempt on the test Page
- Test Page confirmed by name via a read-only call; the token is a **Page access token** (`GET /me` returns the Page).
- The 1080x1920 H.264/AAC 10 s mp4 passed our ffprobe gate locally (3.8 MB).
- `POST /{page-id}/video_reels upload_phase=start` -> **HTTP 403, error code 200**: "Subject does not have permission to post videos on this target, due to lack of pages_manage_posts permission."
- Conclusion: the token in use lacks `pages_manage_posts`. This matches the documented requirement; publishing cannot be tested until a Page token generated with `pages_show_list`, `pages_read_engagement` and `pages_manage_posts` is used.
- Not yet learned: upload behaviour, processing time, `ready` validity, FINISH idempotency, token lifetime, whether Standard access suffices.

## 2026-10-22 - Meta, successful DRAFT run on the test Page (after fixing the token scopes)
Token: Page token with `pages_manage_posts` (valid until about 2026-12-20).

Run 1 (script waited up to 10 min for `ready` before FINISH): START and UPLOAD succeeded; `video_status` stayed `upload_complete` for the whole 10 minutes. The script aborted and, at that time, wrote no report (fixed since: partial reports are kept). That upload was never finished.

Run 2 (`--finish-anyway`, `--state DRAFT`): timeline from START (seconds): UPLOAD done +4.7; status `upload_complete` through +47.5; copyright check `complete` (no matches) at +55.8; FINISH +58.6 (HTTP 200, "Video is Processing", `post_id` returned); status `processing` +59.0 and +67.4; `ready` at +75.9 (`publish_status: draft`, `published: false`, `length: 10`).

Findings:
1. **Processing starts at FINISH, not at upload.** The design assumption "prepare until `ready`, then FINISH" was wrong. Docs updated (`publishing-meta.md` D1, `workflow.md`).
2. Meta accepts our ffmpeg mp4 (H.264 High, yuv420p, 30 fps, AAC 48 kHz stereo, closed 2 s GOP settings from `video-pipeline.md`); no media error codes.
3. Copyright check completes before FINISH, so it can gate the publish decision.
4. `DELETE /{video-id}` succeeded and removed the draft (API retraction works for drafts).
5. The `post_id` from FINISH cannot be fetched as a normal object; track the video id.
6. Token lifetime is roughly 60 days.

Still open: FINISH(PUBLISHED) behaviour and latency on the live Page; whether FINISH is idempotent for the same `video_id`; whether a DRAFT can be promoted to public by API; how long `upload_complete` stays valid; deleting a *published* Reel; whether public posting needs App Review in the app's current mode; safe zones on real phones.

## 2026-10-22 - Meta, public-publishing experiments on the test Page (`spikes/meta_publish_experiments.py`)
Authorised by the owner for the test Page. Every video created was deleted afterwards (verified: no Package 0 videos left on the Page).

| Experiment | Result |
|---|---|
| E1 FINISH with `PUBLISHED` | HTTP 200 "Video is Processing"; `upload_complete` -> `processing` -> `ready` with `publish_status=published` about 14-15 s after FINISH; `published: true`, `length: 10`. |
| E2 second FINISH, same `video_id` | HTTP 400, code 6000 / subcode 1363130 "Video Upload Is Missing"; the Page still had exactly one video, so no duplicate. |
| E3 DELETE a published Reel | `{"success": true}`; the object no longer exists. API retraction works. |
| E4 promote a DRAFT | `POST /{video-id}` with `published=true` -> `{"success": true}`; `published: true` within seconds. |

Consequence (decision D-23): the main publishing flow is now *prepare as draft, then promote at T*. This keeps the kill switch and cancellation effective until the last moment, removes processing latency at go-live, and a repeated FINISH cannot create a duplicate. `docs/publishing-meta.md` D1-D2, `docs/workflow.md`, `backend/app/providers/contracts.py` updated.

Still open: how long a `ready` draft stays valid; whether promotion is idempotent; whether App Review is ever required (public posting worked in the app's current mode); safe zones on real phones; longer videos.

## 2026-10-22 - ElevenLabs Vietnamese narration spike (`spikes/elevenlabs_tts_spike.py`)
Account: Starter plan, 81,227 characters per period, no overage. Voice: library voice "Ngoc Ngan - Vietnamese Storyteller" (category professional, language `vi`). Spend: 1,593 characters sent, **425 credits** deducted (sum of the `character-cost` response headers; the account counter agreed after a short delay).

| Model | words/s (mean) | latency per line | credits per character |
|---|---|---|---|
| eleven_flash_v2_5 | 3.3 | 0.3-0.5 s (first call 2.7 s) | about 0.2 |
| eleven_turbo_v2_5 (listed as deprecated in the docs, verified for this voice) | 3.1 | 0.3-0.4 s | about 0.2 |
| eleven_v3 | 3.2 | 2-3.8 s | about 0.4 |

Findings:
1. Vietnamese synthesis works on all three models with this voice; no error responses.
2. Planning figure corrected from 2.5 to about 3.2 spoken words per second.
3. Cost is negligible on this plan (a narrated Reel costs tens of credits against 81k), but spend must be read from `character-cost`.
4. Digits: the raw-digit line produced audio of a similar length to the hand-spelled line, so the model did read the numbers; whether it read them **correctly** needs a listening check. Our own deterministic normaliser is still required for dates, units and abbreviations.
5. Error codes (401/429/quota), per-request character limit and behaviour at the plan limit were not triggered.

Pending: the owner listens to `spikes/output/tts/*.mp3` (18 files: `<line>__<model>.mp3`) and scores pronunciation of tones, names (Da Nang, cau Rong), numbers/dates and the rebus question, which decides the model for production.

## 2026-10-22 - Image generation through fal.ai (`spikes/fal_image_spike.py`)
The owner's image key is a **fal.ai** key (not Black Forest Labs). Endpoint `fal-ai/flux-2` via the queue API (`https://queue.fal.run`, `Authorization: Key ...`), 1080x1920 requested, PNG, safety checker on. Budget cap USD 4.00; spent **about USD 0.10** (4 images). fal does not return the cost, so the spike computes it from the megapixels (price USD 0.012/MP from fal.ai/pricing) and reserves it in a ledger before each request.

| Image | Time | Result (viewed by the assistant) |
|---|---|---|
| rebus_symbol_banh (rice cake in banana leaf) | 11.0 s | Clean single object on plain background; very usable as a deterministic rebus symbol. |
| rebus_symbol_chung (square wrapped cake, bamboo string) | 10.5 s | Clean single object; recognisably a wrapped square cake (the woven pattern is more ketupat-like than banh chung). |
| daily_cat_alarm | 9.2 s | High quality, readable emotion and humour; floating "??" glyphs appeared although not requested. |
| daily_rain_motorbike | 11.7 s | High quality, Vietnamese street feel; **garbled pseudo-text on a shop sign** despite "no text". |

Findings:
1. **Size:** a 1080x1920 request came back **1088x1920** (rounded up), so the normaliser must crop 4 px per side (never stretch) and the cost uses the returned size; settled cost was USD 0.025068 per image. A bug in the spike (settled cost clamped to the estimate) was found and fixed; the ledger was corrected to USD 0.100272.
2. **Garbled text appears even when told "no text":** confirms the design rule - never rely on generated text; QA must treat unreadable/garbled lettering as a hard failure; real text is overlaid with Pillow.
3. Symbols on a plain background leave much empty canvas on a 9:16 frame; the compositor should scale and place them (and crop to the subject) instead of using the raw frame.
4. fal returns images as signed CDN URLs; we download immediately and do not send the API key to the CDN.
5. Not evaluated: how well the model keeps a **consistent style across several clue images** for one rebus, and Vietnamese-specific rebus clue accuracy; both need a larger human-scored batch.
6. Not measured: fal error shapes and the HTTP status when the balance is insufficient (not triggered), rate limits, media retention time.

## 2026-10-22 - Owner listening test of the 18 TTS files
Verdict from the owner: with `eleven_flash_v2_5` and `eleven_turbo_v2_5`, **dates were read incorrectly in both `digits_raw` and `digits_spelled`**, and **"that no" was read incorrectly in `tone_minimal_pairs`**. No problem was reported for `eleven_v3` (assumed acceptable; the owner is asked to confirm).
Decision D-25: narration uses `eleven_v3` (about twice the credits per character and 2-4 s latency per line, both negligible for offline rendering). The failing lines are kept as a pronunciation regression set (`docs/video-pipeline.md`). Open: exactly how the dates were mispronounced (not recorded); whether v3 also reads unusual dates/units correctly beyond the six test lines.

Addendum (same day): the owner also reported that **"chay" in "chay xe may" was read wrongly**, in `tones_names__eleven_flash_v2_5.mp3` (first reported against the wrong file name, then corrected by the owner: the error is Flash, not Turbo). Turbo and v3 were not reported for this word. Decision D-25 (use `eleven_v3`) is unchanged.

## 2026-10-22 - Dual renditions and the composition check
- `spikes/render_renditions_spike.py`: one 1920x1920 fal master + the v3 narration -> `reel_9x16.mp4` and `youtube_16x9.mp4` (centre-crop `fill`, no black bars). Both valid by ffprobe, 7.6 s, -15.5 LUFS / -1.5 dBTP; Vietnamese captions render with diacritics. A `fit` mode (whole square over a blurred copy) exists as the fallback.
- The image model did **not** keep the subject inside the central square even when asked (cat tail and clock edge cut by the crops).
- `spikes/claude_composition_spike.py` + `backend/app/services/composition.py`: Claude returned accurate clue boxes (verified by eye on an overlay) for about USD 0.012; our geometry code chose `fit` for both targets, correctly.
- fal spend so far about USD 0.184 of 4.00 (7 images); ElevenLabs 425 credits; Anthropic about USD 0.012.
