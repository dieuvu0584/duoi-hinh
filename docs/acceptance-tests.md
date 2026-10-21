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

## Safety/approval (FR-20..23)
- [ ] Missing, invalid, stale, blocked or hash-mismatched review prevents publishing.
- [ ] Editing a revision invalidates its approval and safety review.
- [ ] High-risk topics cannot be published without completed human review.
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
- [ ] Two workers racing on one publishing record produce one publish; kill-switch flip test.
- [ ] Dry-run mode never performs a network call to Meta; production refuses an unintended mode.

## News lane gate (D-16)
- [ ] With `news_lane_enabled=false` a news topic never reaches generation, scheduling or publishing (state-machine test).
- [ ] Enabling the flag is audited and requires re-authentication.

## Retraction/operations
- [ ] Owner can record/perform a retraction with reason; audit and state are correct.
- [ ] News topic past `fresh_until` cannot publish; review TTL respects lane.
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
