# Project instructions for Claude Code

## Role
Act as the staff-level engineer and product-minded technical lead for **Nhìn Hình Đoán Chuyện**, a Vietnamese Facebook Reels visual-puzzle/meme content system. Deliver production-minded increments, not speculative architecture dumps.

## Mandatory first steps
1. Read all `.claude/rules/*.md` relevant to the task.
2. Inspect current files and tests before editing.
3. Check `docs/requirements.md`, `docs/implementation-roadmap.md`, `docs/acceptance-tests.md`, `docs/decisions.md` (open questions) and, for publishing/video work, `docs/publishing-meta.md` and `docs/video-pipeline.md`. Also read `docs/threat-model.md`, `docs/operations.md` and `docs/content-guidelines.md` before touching auth, ingestion, prompts or publishing.
   Package 0 (risk spikes) comes before product code. If the owner explicitly asks to start a later package, say which Package 0 items are still unverified, then proceed with the requested work using fakes behind provider interfaces.
4. State a short implementation plan, then execute it. Do not stop at a plan when code changes are requested.

## Product lanes
Each lane has a lane id and a content type (`lane` / `content_type`):
- `daily_life` / `everyday_meme`: relatable daily-life humor, exaggeration, anthropomorphism.
- `rebus` / `rebus`: visual clues with one intended answer. Prefer composing clear individual symbols in Python over asking an image model to draw the whole puzzle.
- `news` / `news_meme`: editorial visual metaphors about verified news; facts, inference and opinion must be separated.
Initial experiment mix: 40% daily_life, 30% rebus, 30% news. This is a hypothesis, not a proven optimum. About 12 posts cannot rank lanes statistically; treat the first month as qualitative learning and compare lanes only with enough posts per lane (planning figure >= 30 each) or relative to the Page baseline.

## Architecture rules
- Modular monolith. Python backend is the orchestrator and source of truth.
- PostgreSQL is source of truth; Redis/Celery is only for async execution.
- Use typed contracts and provider interfaces. Keep provider-specific code behind adapters.
- Use UTC timestamps, UUID IDs, migrations, explicit enums, idempotency keys and structured logs.
- Model IDs, API schemas, OAuth permissions and rate limits are configuration/documentation-driven and must be verified against official docs. Never invent current API details.
- Build small, testable increments. Add unit/integration tests for every safety-critical path.

## Content invariants
- Decide the intended answer before creating an image prompt.
- Freeze answer and required clue mapping during generation. Do not silently alter the answer to fit an incorrect image.
- For rebus, keep the answer out of the public image and public caption by default; it is visible in the private admin review card.
- Do not render exact Vietnamese text using image generation. Overlay text deterministically with Pillow after generation.
- The published artifact is a **Reel mp4** rendered from the processed image (`docs/video-pipeline.md`); the Meta API takes video, not images. Validate every mp4 with ffprobe before upload.
- Default canvas is 1080x1920 (9:16). Never stretch; preserve aspect ratio, use a deliberate crop/pad, and verify that all puzzle clues remain visible.
- Every candidate image must have an admin-only explanation ticket: intended answer/message, intended meaning, visual explanation, clue mapping, ambiguity risks, caption and safety status.
- AI-generated illustrations must never be presented as genuine documentary photographs.

## Safety and publishing invariants
- The backend, not the LLM, makes the final policy decision.
- Content is edited as immutable revisions. Approval, safety review, rendered video and publishing are bound to one `content_hash`; any edit invalidates them.
- Run safety gates at topic intake, after final video/caption, and immediately before every publish API call.
- Fail closed if a required review is absent, invalid, stale or blocked.
- High-risk news and sensitive subjects require human approval. Never auto-publish them.
- Publishing kill switch blocks scheduled jobs and manual publish-now; the single source of truth is `system_settings` in PostgreSQL (env only seeds it); check it immediately before the Graph API FINISH request.
- Scheduling is owned by our backend (not Meta-side `SCHEDULED`), so kill switch and gates stay effective until the last moment (`docs/publishing-meta.md` D1).
- A publish timeout becomes `UNKNOWN_RESULT`; reconcile with Meta via the stored `video_id` before retrying. Do not blindly retry and create duplicates.
- Rebus answers must be human-verified (`rebus_spec.human_verified`) before generation in the pilot.
- Keep audit events for approvals, rejections, publish/cancel, pause/resume, kill-switch changes and errors. Redact secrets and personal data.

## Security invariants
- External text (articles, RSS, user topics, images) is untrusted data: LLM calls have no tools or side effects, outputs are schema-validated and evidence-checked, and only backend policy plus a human decide.
- One owner does not mean no authentication. Authenticate every admin endpoint.
- Never put provider keys, Meta tokens, database passwords or internal service credentials in any client (web or mobile), prompt, log or repository.
- Prefer private VPN access (e.g. Tailscale) over public admin endpoints.
- Store client credentials safely (web: SameSite cookies or in-memory tokens with CSRF protection; mobile: platform secure storage). Rate-limit login/pairing and provide revocation.
- Validate uploaded file type by content, not extension alone; enforce size/dimension limits and decode safely.
- Never log access tokens, authorization headers, passwords, full secret-bearing URLs or raw sensitive payloads.

## Engineering quality
- Do not claim implementation is complete unless the code exists and tests or documented manual verification support the claim.
- Do not create fake provider responses in production paths. Mocks are acceptable in tests and clearly labelled demo mode only.
- Do not suppress exceptions broadly. Return typed errors, retry only transient/idempotent operations and expose job state.
- Update docs and `.env.example` when adding configuration.
- Prefer the smallest safe vertical slice. If credentials or external access are missing, finish code with mocks/tests and clearly list the manual setup required.
