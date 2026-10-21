# Implementation roadmap (v2)

Principle: **retire the riskiest assumptions first**, then build vertical slices. Every package has entry criteria (Definition of Ready) and exit criteria (Definition of Done). Durations are planning estimates; the gates matter, not the calendar.

## Package 0 - De-risk spikes and decisions (before any product code)
Goal: prove the three assumptions the whole product depends on, using throwaway scripts in `spikes/` (not production code).
1. **Meta publishing spike.** Create a Meta app, obtain a Page token with the documented permissions, publish a hand-made 1080x1920 mp4 to a **test Page** via START -> UPLOAD -> poll -> FINISH. Record: whether Standard access in development mode is enough (no App Review), token lifetime, how long a `ready` video stays valid before FINISH, whether FINISH is idempotent for the same `video_id`, whether a DRAFT can be promoted, comment/pin permissions, real processing time. Output: `docs/publishing-meta.md` VERIFY items resolved.
2. **Video render spike.** ffmpeg: image -> 10 s mp4 meeting the spec, ffprobe validation script, safe-zone screenshots on real phones. Output: calibrated safe zones and `video_spec` template defaults.
3. **Image/rebus quality spike.** Run ~20 concepts across the 3 lanes through the candidate image providers; owner scores usability blind. Test Vietnamese rebus decomposition with and without human verification. Output: go/no-go for each provider and for LLM-generated rebus (vs. human-authored answers + LLM assist), and the measured cost per usable asset.
4. **Decisions closed** (see `docs/decisions.md`): admin surface, notifications, hosting, audio policy, reveal policy, lane mix.
5. **Provider terms and legal check.** Record each provider's commercial-use, output-ownership and content-policy terms, and Meta's current content distribution and AI-label policies; the owner has no lawyer (D-16), so the news lane stays disabled until the owner completes the checklist in `docs/content-guidelines.md`.
6. **Retraction check.** Determine if the API can delete/unpublish a Reel.
Exit: spike reports committed; owner inputs P-1..P-5 in `docs/decisions.md` provided or consciously deferred; roadmap re-baselined.

## Package A - Backend foundation
FastAPI app, config, structured logging with redaction, health with dependency checks, PostgreSQL + SQLAlchemy 2 + Alembic baseline (identity, settings, audit, jobs, idempotency), single-owner auth (argon2id, short-lived access token + rotating refresh, device sessions, rate-limited login, revocation), error envelope + request IDs, state-machine service with table-driven tests, `system_settings` (kill switch default true), audit abstraction, the publishing policy (already written; see tests), CI (ruff, mypy/pyright, pytest, migration check on empty DB).
DoD: all `docs/acceptance-tests.md` "Backend/auth" boxes ticked by tests; no unauthenticated admin route.

**Status (2026-10-21): slice 1 done** - config, redacting logs, error envelope + request IDs, SQLAlchemy models + Alembic baseline, owner auth with rotating refresh tokens and reuse detection, rate-limited login, system settings with fail-closed defaults and re-auth for widening changes, append-only audit (DB trigger), table-driven state machines, Docker image that migrates on start. **Not done yet:** content/job/publishing tables and routes, idempotency keys, jobs/leases, FCM, TOTP, mypy/pyright, ticking the remaining acceptance boxes.

## Package B - Topics + LLM pipeline
Source registry (terms-reviewed gate), manual topic intake first, then RSS/official-source adapters one at a time, normalization/dedup/syndication groups, Claude adapter with **structured output validated against `docs/schemas/*.json`**, prompt versioning, `llm_calls` + cost ledger, intake safety gate, rebus human-verification screen.
DoD: fake-provider unit tests + a small recorded-fixture contract test per adapter; no live API in unit tests.

## Package C - Image + video pipeline
Image provider adapters (current docs only), manual upload validation, asset store, 1080x1920 normalization, symbol library + deterministic rebus composition, Pillow overlays (diacritics golden tests), **video renderer + ffprobe gate** (`docs/video-pipeline.md`), keyframe QA with vision review, explanation tickets, regeneration budget, final safety gate bound to `content_hash`.
DoD: golden-image tests, ffprobe tests on rendered output, hard-failure overrides score.

## Package D - Publishing + analytics
Meta adapter (two-phase), scheduler with `prepare_lead`, full pre-publish gate, `UNKNOWN_RESULT` reconciliation, token health job, kill switch drills, reveal handling, insights collector with snapshots.
DoD: fault-injection tests (timeouts at each step), duplicate-prevention test, kill switch tested on scheduled and publish-now; one real end-to-end publish to the test Page, then the production Page with manual approval.

## Package E - Owner admin surface
**Flutter Android app** (decision D-9) over Tailscale: secure login/pairing, dashboard, review queue with explanation ticket and video preview, jobs, schedule, kill switch (re-auth), cost, settings, metrics, FCM push (D-10), explicit offline/last-known state. Because the API is the only interface, Package A-D expose everything through typed, versioned REST endpoints so the app can be built against a stable contract; start the Flutter work once the review-queue API exists. Until the app exists, a minimal CLI or API docs page is the operator interface during Package 0-C.

## Package F - Pilot and hardening
Threat model, backup/restore drill, token revocation drill, load of 1-3 posts/day, alerting, cost caps. Pilot with **manual approval for every post**. Auto-publish of anything is a separate, evidence-based owner decision.

## Metrics policy for the pilot
12 posts in two weeks cannot rank three lanes statistically. Treat the first month as qualitative learning (clarity feedback, retention curves, comments). Compare lanes only after enough posts per lane (planning figure >= 30 each) or use effect-size-aware comparisons relative to the Page's baseline. Tag every post with experiment variables (hook, duration, audio, slot).

## Release gates
G0 spikes done -> G1 Package A green -> G2 content package reaches PENDING_APPROVAL end-to-end with fakes -> G3 real providers behind budget caps -> G4 test-Page publish -> G5 production Page, manual approval, kill switch rehearsed.
