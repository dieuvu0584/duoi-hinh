# Publishing to Facebook Reels (Meta Graph API)

Source: Meta "Reels Publishing API" guide and `Page Video Reels` reference (Graph API v26.0 when written). **Items marked VERIFY are not confirmed in docs and must be tested in the Package 0 spike on a test Page before relying on them.** API version is configuration (`META_API_VERSION`), never hard-coded.

## Official flow
1. **START** - `POST https://graph.facebook.com/{ver}/{page-id}/video_reels` with `upload_phase=start` -> `video_id`, `upload_url` (`https://rupload.facebook.com/video-upload/{video-id}`). Note the different upload host.
2. **UPLOAD** - `POST` the binary to `upload_url` with headers `Authorization: OAuth {token}`, `offset: 0` (or resume offset), `file_size: {bytes}`; or a hosted file via `file_url` header (host must allow `facebookexternalhit/1.1`; fbcdn URLs rejected). We upload the local file (no public URL needed).
3. **Poll** - `GET /{video-id}?fields=status`. `video_status`: `uploading`, `upload_complete`, `processing`, `ready`, `upload_failed`, `error`, `expired`. Phases: `uploading_phase`, `processing_phase`, `publishing_phase.publish_status` (`draft|scheduled|published|error`). Resume an interrupted upload using `bytes_transfered` as `offset`; `OffsetInvalidError` -> use that value.
4. **FINISH** - `POST /{page-id}/video_reels` with `video_id`, `upload_phase=finish`, `video_state` (`PUBLISHED|DRAFT|SCHEDULED`), optional `description`, `title`, `scheduled_publish_time` (>10 min and <=29 days ahead), `is_ai_generated`.
5. **Copyright check** - `copyright_check_information` on the video (needs `pages_read_engagement`).

Permissions: Page access token from a person with `CREATE_CONTENT` task; `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`. Rate limit: **30 API-published posts / rolling 24 h** on this edge.

## Design decisions
### D1. App-side scheduling, not Meta-side scheduling
Using `video_state=SCHEDULED` hands control to Meta; our kill switch, pre-publish gate and cancellation could no longer act, and the `video_reels` edge documents no read/update/delete. Therefore:
- Our backend owns the schedule. At `T - prepare_lead` (default 20 min) it runs START + UPLOAD, then waits until `video_status=upload_complete` (uploaded bytes equal the file size) **and** the copyright check is `complete` with no matches. **Measured (2026-10-22): processing does not start until FINISH** - before FINISH the status stays `upload_complete` (observed for 10 minutes), and only after FINISH does it go `processing` then `ready` (about 17 s for a 10 s, 3.8 MB video). So "prepared" means uploaded + copyright-clear, not `ready`.
- At `T` it runs the **full pre-publish gate**, then calls FINISH with `video_state=PUBLISHED`.
- If the upload `expired` or failed before `T`, restart from START (new `video_id`, new publishing attempt, same publish intent key).
- VERIFY: how long an `upload_complete` video stays valid before FINISH (`expired` semantics). Set `prepare_lead` from the measurement. Until measured, keep `prepare_lead` short (default 20 min) and handle `expired` by re-preparing.
- After FINISH(PUBLISHED) the video still has to process; confirm success by polling until `video_status=ready` and `publishing_phase.publish_status=published`. A processing `error` after FINISH means nothing went public: mark `FAILED` (not `UNKNOWN_RESULT`).
- DRAFT mode is not used for gating (VERIFY whether a draft can be promoted via API; if yes it may be a better stage).

### D2. `video_id` is the idempotency anchor
`video_id` is created at START. Persist it **before** UPLOAD. FINISH must be called at most once per intent unless reconciliation proves it did not take effect:
- On FINISH timeout/5xx/unknown: set `UNKNOWN_RESULT`, then `GET /{video-id}?fields=status` and read `publishing_phase.publish_status`:
  - `published` -> record `post_id`/permalink, `PUBLISHED`.
  - `draft`/not finished and `video_status` still `upload_complete` or `ready` -> safe to retry FINISH (VERIFY FINISH is idempotent for the same `video_id`; otherwise require owner confirmation).
  - `error`/`expired` -> mark `FAILED`, owner decides new attempt.
  - Still ambiguous after bounded polling -> stay `UNKNOWN_RESULT`, alert owner, **no automatic retry**; also check the Page's recent Reels list as a second signal.
- A blind retry of START creates a second video object; never do that for an in-flight intent.

### D3. Pre-publish gate (same code path for scheduled and publish-now)
Re-read from DB inside one transaction/critical path immediately before FINISH:
1. `publishing_kill_switch` (DB `system_settings` is the single source of truth; env value only seeds first boot).
2. Content revision approved by owner **and that approval references the same `content_hash` as the rendered video**.
3. Latest final-content safety review: `allow`, not expired, bound to the same `content_hash`; high-risk -> human review completed.
4. Not already published; schedule due and not past `max_lateness` (default 30 min; if later -> `SKIPPED_LATE`, owner decides).
5. Budget and rolling publish-count limits (local cap default 3/day; Meta hard cap 30/24 h).
6. Token health (valid, has required scopes) and video uploaded (`upload_complete`) + copyright check clean.
Any failure -> `BLOCKED` with reason code, audit event, notification; never publish "best effort".

### D4. AI disclosure
`is_ai_generated` is a Reels API parameter (self-declaration, default false). Rule: set `true` for any content whose imagery is AI-generated or AI-altered unless the owner records a justified override. Meta's labeling policy (VERIFY current text) concentrates on photorealistic video/realistic audio but may auto-label content from detected AI signals; do not try to strip metadata/watermarks. News-lane visuals are always labelled as illustrations/memes in the on-screen overlay or caption.

### D5. Token lifecycle
Use a Page access token obtained per Meta's documented process (VERIFY long-lived/Page token behavior and whether Standard access in development mode suffices for a single-owner app, which would avoid App Review). A daily `token_health` job validates the token/scopes and alerts before expiry; failure sets `publishing_blocked_reason=token_invalid`. Token stored only in the secret store; never in DB plaintext logs, never in the mobile/web client.

### D6. Caption and hashtags
`description` carries caption + hashtags (no documented limit; we cap locally e.g. 2,200 chars and a hashtag count). Rebus: **no answer in caption**. Answer reveal policy below.

### D7. Answer reveal for rebus
Public Reel never contains the answer. Reveal options (decision D-12): (a) owner posts a comment manually after N hours (MVP), (b) backend posts a comment via API if permitted (VERIFY permission `pages_manage_engagement` and whether pinning is available through API), (c) a follow-up "answer" Reel. The reveal is a first-class scheduled item with its own safety check, not an afterthought.

## Insights (analytics)
Collect only metrics the API returns for the granted permissions (Reels video insights). Snapshots at +1 h, +24 h, +72 h, +7 d; store raw returned metric names and values as returned, plus `captured_at`; do not infer metrics that are not provided. Metric availability and definitions are VERIFIED in Package D.

## Error handling table (initial)
| Condition | Classification | Action |
|---|---|---|
| 190 invalid token | permanent until fixed | block publishing, alert |
| 200 permissions | permanent | block, alert |
| 613 / rate limit | transient | backoff with jitter; do not exceed caps |
| 368 abusive/disallowed | permanent for that content | `BLOCKED`, owner review |
| 1363040/127/128/129 media spec | permanent for that file | re-render after fixing, `INVALID` |
| 6000 upload problem / `upload_failed` | transient | resume at `bytes_transfered`, bounded retries, else new START |
| network timeout on FINISH | unknown | `UNKNOWN_RESULT` reconciliation (D2) |

## Measured on the test Page (2026-10-22, Graph API v26.0) - see docs/spikes/package-0-report.md
- A 10 s, 1080x1920, 30 fps H.264/AAC, 3.8 MB mp4 made by our ffmpeg recipe was **accepted and processed** by Meta.
- START ~1 s; UPLOAD of 3.8 MB ~5 s; copyright check completed within ~55 s of START (no matches); FINISH ~3 s; processing after FINISH ~17 s.
- `FINISH` with `video_state=DRAFT` returned `{success, message: "Video is Processing...", post_id}` and the video ended with `publish_status=draft`, `published=false`. The `post_id` is not readable as a normal object (`(#12) singular statuses API is deprecated`); use the **video id** for status and deletion.
- `DELETE /{video-id}` returned `{"success": true}` and the video no longer exists: **API retraction works for a draft**; deleting a *published* Reel is still unverified.
- Page access token: scopes `pages_show_list`, `pages_read_engagement`, `pages_manage_metadata`, `pages_read_user_content`, `pages_manage_posts`, `pages_manage_engagement`; `debug_token` reports `expires_at` about 2026-12-20 (roughly 60 days after creation), so the token-health job must warn well before that.
- Still unverified: FINISH(PUBLISHED) on the live Page, FINISH idempotency, promoting a draft to public by API, `upload_complete` validity window, app mode / whether App Review is ever required for public posts.
