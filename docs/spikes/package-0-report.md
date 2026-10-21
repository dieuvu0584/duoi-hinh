# Package 0 report (running log)

Evidence from real runs only. Items not listed here are still unverified (see `docs/publishing-meta.md` VERIFY).

## 2026-10-22 - Meta, first live attempt on the test Page
- Test Page confirmed by name via a read-only call; the token is a **Page access token** (`GET /me` returns the Page).
- The 1080x1920 H.264/AAC 10 s mp4 passed our ffprobe gate locally (3.8 MB).
- `POST /{page-id}/video_reels upload_phase=start` -> **HTTP 403, error code 200**: "Subject does not have permission to post videos on this target, due to lack of pages_manage_posts permission."
- Conclusion: the token in use lacks `pages_manage_posts`. This matches the documented requirement; publishing cannot be tested until a Page token generated with `pages_show_list`, `pages_read_engagement` and `pages_manage_posts` is used.
- Not yet learned: upload behaviour, processing time, `ready` validity, FINISH idempotency, token lifetime, whether Standard access suffices.
