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
