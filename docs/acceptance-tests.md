# Acceptance checklist (v2)

Each box must be backed by an automated test or a documented manual verification with date and evidence. IDs refer to `docs/requirements.md`.

## Backend/auth (FR-40..42, NFR-1..3)
- [x] Health endpoint reports service/dependency status without exposing secrets. (`test_health_is_public_and_minimal`)
- [x] Every admin route rejects unauthenticated requests (test enumerates the router). (`test_every_api_route_requires_auth_except_the_public_ones`)
- [x] Login/pairing is rate-limited and audited; device revocation invalidates refresh tokens. (`test_auth.py`)
- [x] Invalid state transitions return `409 invalid_state_transition`; transition tests are generated from `docs/workflow.md` tables. - verified at the state-machine layer (`test_state_machine.py`); not yet wired to content routes
- [x] Audit events are append-only and redact secrets. (DB trigger + `test_audit_log_is_append_only_in_the_database`, `test_audit_metadata_is_redacted`)
- [x] Migrations apply from an empty database and upgrade an existing schema. - empty -> head verified by the test fixtures; upgrade-from-older-schema untested (only one migration exists)
- [ ] A worker killed mid-job leaves the job recoverable; no job is lost or double-run with side effects.

## Topics/news (FR-1, FR-4, FR-30)
- [ ] Source URL, publisher, publication date, event date and retrieval date are separate; event date never defaults from publication date.
- [ ] Reposts of one story share a `syndication_group_id` and count as one corroboration.
- [ ] Unsupported claims are marked; sensitive topics require human review.
- [ ] No source/date/quote/statistic is fabricated; model output is schema-validated and rejected otherwise.

## Content/rebus (FR-2, FR-3, FR-11)
- [ ] Answer and clue mapping exist and are validated before image generation.
- [ ] Rebus: every syllable has >= 1 clue and every clue maps to a syllable; `human_verified=true` required.
- [ ] Rebus answer is absent from public image, overlay and caption (test scans overlay text/caption for the normalized answer).
- [ ] Duplicate answers/topics inside the window are rejected.

## Image/video (FR-5..10)
- [ ] Manual uploads receive identical validation, provenance and safety review.
- [ ] 1080x1920 processing never stretches and keeps all clue boxes inside the safe zone.
- [ ] Vietnamese overlays render all diacritics (golden images).
- [ ] Rendered mp4 passes the ffprobe gate (resolution, constant 24-60 fps, 3-90 s, yuv420p, H.264/H.265, AAC 48 kHz stereo, GOP <= 5 s).
- [ ] Motion never moves a required clue outside the safe zone at any sampled frame.
- [ ] Mismatched image is rejected/regenerated; the answer is never changed silently.
- [ ] Explanation ticket exists for every revision.
- [ ] Hard failures override numeric QA scores.

## Dual renditions and composition (FR-7, FR-7b, D-26)
- [ ] A 1920x1920 master renders to 1080x1920 and 1920x1080 by centre crop with no black bars and no stretching.
- [x] Clue boxes outside the safe region of a target are rejected for `fill` (switch to `fit`); boxes inside pass; missing boxes fail closed to `fit`; per-target geometry incl. the Reels UI margins (`backend/tests/test_composition.py`, 13 tests).
- [ ] Both renditions pass the ffprobe gate for their own size, share one narration, and have separate keyframe QA.
- [ ] Each (platform, target) has its own publishing record, gate and idempotency key; the global kill switch stops both.
- [ ] `youtube_enabled=false` blocks every YouTube action.

## Narration (FR-12..14, D-24)
- [x] Rebus answer cannot appear in caption, overlay or narration on create or edit (`test_content_api.py`, `test_content_hash_and_rules.py::test_leaks_answer_*`).
- [x] Narration text and voice id are part of `content_hash`; editing either invalidates approval (`test_content_hash_and_rules.py`, `test_narration_is_stored_and_changes_the_hash`).
- [ ] Vietnamese numerals/dates/units are expanded deterministically before synthesis (golden tests).
- [ ] Identical (text, voice, model, settings) is served from cache and records no new cost.
- [ ] A synthesis failure sends the revision to NEEDS_REWORK; nothing is published un-narrated.
- [ ] The rendered mix has an audio stream, is not silent, does not clip, ends before the hold period and is within loudness tolerance.
- [ ] The pronunciation regression list (dates, "that no", tones, names) is re-synthesised and re-listened by the owner whenever model, voice, settings or lexicon change.
- [ ] Narrated Reels always publish with `is_ai_generated=true`.
- [ ] ElevenLabs spend is recorded before any retry and respects its own budget (default 0).

## Safety/approval (FR-20..23)
- [x] Missing, invalid, stale, blocked or hash-mismatched review prevents publishing. (`test_publish_gate.py`; wired to DB data, not yet to a scheduler)
- [x] Editing a revision invalidates its approval and safety review. (`test_publish_gate.py`: edit -> `revision_superseded`; new revision -> `content_not_approved`)
- [x] High-risk topics cannot be published without completed human review. (`test_sensitive_category_requires_human_even_if_model_says_allow`)
- [ ] Kill switch (DB source of truth) blocks scheduled and publish-now paths and is rechecked immediately before FINISH.

## Publishing (FR-31..34, D1-D3)
- [ ] Prepared video that is not `ready` or whose copyright check is not clear is never finished.
- [ ] FINISH is called at most once per intent unless reconciliation proves it had no effect.
- [ ] Fault injection: timeout after START, during UPLOAD, after FINISH -> no duplicate posts; `UNKNOWN_RESULT` reconciled before any retry.
- [ ] `max_lateness` skips late posts instead of publishing them.
- [ ] Local publish cap and Meta 30/24 h limit enforced.
- [ ] `is_ai_generated` follows the rule and is audited when overridden.
- [ ] Token health job detects invalid/expiring token and blocks publishing.
- [ ] Meta tokens and raw sensitive payloads never appear in logs (log-scan test).

## Security (docs/threat-model.md)
- [ ] Prompt-injection corpus does not change lane, answer, risk decision or approval.
- [ ] Fetcher blocks private/loopback/metadata IPs, including after redirects.
- [ ] Stored XSS payloads render inert; CSP and CSRF protections present.
- [x] Used refresh token replay revokes the token family; no public registration route exists. (`test_refresh_rotates_and_old_token_reuse_revokes_the_device`, `test_no_public_registration_route`)
- [ ] Uploads: decompression bomb, wrong extension and metadata cases handled; stored images are re-encoded.
- [ ] Secret-scan and dependency audit run in CI.
- [~] Row lock proven (`test_gate_takes_a_row_lock_so_workers_cannot_race`) and kill-switch flip seen across connections; a full two-worker publish test needs the scheduler (Package D).
- [ ] Dry-run mode never performs a network call to Meta; production refuses an unintended mode.

## News lane gate (D-16)
- [x] With `news_lane_enabled=false` a news topic cannot be created (API/service test); gating of later stages follows from there being no news content.
- [ ] Enabling the flag is audited and requires re-authentication.

## Retraction/operations
- [ ] Owner can record/perform a retraction with reason; audit and state are correct.
- [x] News topic past `fresh_until` cannot publish; review TTL respects lane. (`test_news_needs_human_review_and_expires_with_the_topic`)
- [ ] Deterministic blacklist check blocks caption/overlay/answer matches.
- [ ] Alerts fire for `UNKNOWN_RESULT`, invalid token, budget threshold, stale backup (test via injected conditions).

## Analytics (FR-35)
- [ ] Only metrics returned by the API are stored; unknown metric names do not break ingestion.

## Operations (NFR-5, FR-36, FR-37)
- [ ] Closing the admin UI does not stop backend jobs.
- [ ] Offline UI is explicit and never claims success before backend confirmation.
- [ ] Budget and regeneration limits enforced server-side; cost recorded before any paid retry.
- [ ] Backup/restore and token revocation drills documented and performed.

## Pilot metrics (targets are hypotheses)
- [ ] Track QA pass rate, answer clarity in user testing, publish errors, duplicates, cost per post, owner review time.
- [ ] Initial targets: >80% QA pass, <2% publish errors, zero duplicates, >90% answer clarity.
- [ ] Do not rank lanes from < 30 posts per lane; report qualitative findings instead.
