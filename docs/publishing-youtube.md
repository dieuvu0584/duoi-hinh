# Publishing to YouTube (design, not yet verified by a live run)

Source: YouTube Data API reference for `videos.insert` and the quota/compliance page (read 2026-10-22). **Items marked VERIFY must be tested with a real upload before relying on them.**

## What the docs say
- `POST https://www.googleapis.com/upload/youtube/v3/videos` with OAuth scope `https://www.googleapis.com/auth/youtube.upload` (or a broader YouTube scope). The page says the call has a quota cost of 1 unit in the *Video Uploads* bucket with **100 calls per day** by default; other endpoints share 10,000 units per day. Our volume (1-3 uploads/day) is far below this.
- Body fields we use: `snippet.title`, `snippet.description`, `snippet.tags[]`, `snippet.categoryId`, `snippet.defaultLanguage`, `status.privacyStatus`, `status.publishAt` (scheduling), `status.selfDeclaredMadeForKids`, **`status.containsSyntheticMedia`** (declaration for altered/synthetic content; the reference lists it without a description). If `privacyStatus` is omitted the video defaults to **public**, so we always set it explicitly.
- Maximum file size 256 GB; duration limits are not stated on the page.
- The page states that videos uploaded from unverified API projects are **not** restricted to private viewing. This contradicts what the assistant remembered about a past restriction; **VERIFY with a test upload** (set `privacyStatus` to `private` first) before assuming public uploads work without an audit.
- Quota extensions require a compliance audit form; not needed at our volume.

## Design
- Same draft-then-publish principle as Facebook (`docs/publishing-meta.md` D1): our backend owns the schedule, uploads the 16:9 file as **private**, waits until YouTube has processed it, and at `T` runs the same pre-publish gate and flips it to public (`videos.update` of `status.privacyStatus`, or `publishAt`; VERIFY which is more reliable and whether processing/HD finishes before publishing). Cancelling = delete the video.
- `YouTubePublishingProvider` mirrors `ReelsPublishingProvider` (`prepare`, `publish`, `delete`, `get_status`, `reconcile`). Each (platform, target) has its own `publishing_records` row, state machine and idempotency key; the global kill switch and the per-platform enable flag (`youtube_enabled`, default **false**) both must allow it.
- Idempotency: persist the YouTube `videoId` returned by the resumable upload before anything public happens; reconcile by reading that video.
- AI disclosure: always set `status.containsSyntheticMedia=true` for AI imagery/synthetic voice unless the owner records a justified override (check YouTube's current policy text; VERIFY).
- Metadata: title and description derived from the reviewed caption (the same safety gate applies); the rebus answer never appears in them.
- Credentials: Google Cloud project OAuth client + a refresh token for the channel's owner account, kept only in the secret store (`YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN`, `YOUTUBE_CHANNEL_ID`). A token-health job checks it like Meta's. Never expose in logs/clients.
- Copyright: audio is owner-generated narration (+ optional licensed music); YouTube may run Content ID on uploads; record any claim.

## Open items (Package 0 YouTube spike, needs owner inputs P-7)
Test upload to a **test channel**: resumable upload, processing time, private -> public promotion, `publishAt`, `containsSyntheticMedia`, delete, error codes and quota behaviour, whether an unaudited API project can publish publicly.
