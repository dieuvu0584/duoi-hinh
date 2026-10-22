# Workflow and state machines

Every state change goes through one service function that checks the transition table below, writes an `audit_events` row, and is the only writer of the status column. Invalid transitions return a stable `409 invalid_state_transition`. Tests are table-driven from these tables.

## Content revision states
Content is edited as immutable **revisions** (`content_revisions`). Approval, safety review, rendered video and publishing all reference one `revision_id` + `content_hash` (hash over answer, clue mapping, caption, hashtags, overlay text, selected image/video asset hashes, audio id). Any edit creates a new revision and invalidates approvals/reviews of older ones.

| From | To | Trigger |
|---|---|---|
| DRAFT | ANALYZING | analysis job started |
| ANALYZING | READY_FOR_GENERATION | analysis valid, intake safety `allow` (or human-cleared) |
| ANALYZING | HELD_FOR_HUMAN | intake gate `hold_for_human` or `needs_revision` |
| ANALYZING | REJECTED | `reject`/`block` |
| HELD_FOR_HUMAN | READY_FOR_GENERATION / REJECTED | owner decision after an **intake-gate** hold (audited) |
| HELD_FOR_HUMAN | PENDING_APPROVAL | owner clears a **final-gate** hold (service checks which gate held it) |
| READY_FOR_GENERATION | GENERATING | image/clue assets job started (budget ok) |
| GENERATING | RENDERING | image, composition check and narration are ready (revision now binds the media by hash) |
| GENERATING / RENDERING / QA_PENDING | HELD_FOR_HUMAN | a stage cannot continue without a person (image prompt needs revision, render or QA could not run, assets missing) |
| GENERATING | NEEDS_REWORK | all candidates fail QA; regeneration budget remains |
| RENDERING | QA_PENDING | video rendered and ffprobe gate passed |
| RENDERING | NEEDS_REWORK | render/gate failure |
| QA_PENDING | FINAL_SAFETY_PENDING | QA recommendation `pass` |
| QA_PENDING | NEEDS_REWORK / REJECTED | QA `revise` / `reject` |
| NEEDS_REWORK | GENERATING | within `max_image_regenerations` and budget; else -> REJECTED |
| FINAL_SAFETY_PENDING | PENDING_APPROVAL | final safety `allow` (human-reviewed first when required) |
| FINAL_SAFETY_PENDING | HELD_FOR_HUMAN / BLOCKED | `hold_for_human` / `block` |
| PENDING_APPROVAL | APPROVED | owner approves **this revision** |
| PENDING_APPROVAL | REJECTED / NEEDS_REWORK | owner rejects / requests changes |
| APPROVED | SCHEDULED | schedule slot assigned |
| SCHEDULED | APPROVED | schedule cancelled |
| SCHEDULED | PUBLISHED | publishing record reached `PUBLISHED` |
| PUBLISHED | ARCHIVED | retention policy |
| any non-terminal | SUPERSEDED | a newer revision replaces it |
Terminal until an authorized edit creates a new revision: REJECTED, BLOCKED, SUPERSEDED, ARCHIVED.

## Job states
`QUEUED -> RUNNING -> SUCCEEDED`; `RUNNING -> RETRY_WAIT -> QUEUED` (transient errors, bounded attempts, `retry_after`); `RUNNING -> FAILED`; `QUEUED|RUNNING|RETRY_WAIT -> CANCEL_REQUESTED -> CANCELLED`; `QUEUED -> SKIPPED` (precondition no longer true). Jobs persist in PostgreSQL; a worker crash returns `RUNNING` jobs older than their lease to `QUEUED` (handlers must be idempotent or reconcile first).

## Publishing record states
| From | To | Trigger |
|---|---|---|
| NOT_SCHEDULED | SCHEDULED | slot assigned |
| SCHEDULED | PREPARING | `T - prepare_lead`: START + UPLOAD begun |
| PREPARING | READY_TO_PUBLISH | draft is `ready` (`publish_status=draft`) and the copyright check is complete and clean (START, UPLOAD, FINISH as DRAFT all done; nothing public) |
| PREPARING | FAILED / EXPIRED | upload/processing error, or `expired` |
| EXPIRED | PREPARING | re-prepare with new `video_id` (same intent key) |
| READY_TO_PUBLISH | PUBLISHING | pre-publish gate passed at `T`; `PUBLISHING` persisted before the promotion call (`POST /{video-id} published=true`) |
| PUBLISHING | PUBLISHED | FINISH success confirmed (post id / `publish_status=published`) |
| PUBLISHING | UNKNOWN_RESULT | timeout/ambiguous response |
| PUBLISHING | FAILED | definite provider rejection of FINISH (never for ambiguous results) |
| READY_TO_PUBLISH | EXPIRED | provider reports `expired` before FINISH |
| RETRACTION_FAILED | RETRACTION_REQUESTED / RETRACTED | owner retries, or records a manual retraction |
| UNKNOWN_RESULT | PUBLISHED / FAILED | reconciliation result |
| UNKNOWN_RESULT | READY_TO_PUBLISH | reconciliation proves the promotion had no effect; the retry then passes the **full gate again** (kill switch, approval, review) - there is no direct UNKNOWN_RESULT -> PUBLISHING edge |
| READY_TO_PUBLISH | SKIPPED_LATE | the slot is older than `max_lateness` when the gate runs (the draft is deleted) |
| SCHEDULED / PREPARING / READY_TO_PUBLISH | BLOCKED | any gate failure (reason code) |
| BLOCKED | SCHEDULED | cause cleared + owner re-confirms |
| SCHEDULED... | CANCELLED | owner cancels |
| SCHEDULED | SKIPPED_LATE | past `max_lateness` |
| PUBLISHED | RETRACTION_REQUESTED | owner retracts (reason required) |
| RETRACTION_REQUESTED | RETRACTED / RETRACTION_FAILED | API deletion confirmed / failed (then manual retraction recorded) |

## Independent system controls (single source of truth: `system_settings` in PostgreSQL)
`automation_enabled`, `generation_paused`, `publishing_kill_switch`, `daily_budget_limit`, `monthly_budget_limit`, `max_image_regenerations`, `max_publishes_per_day`, `default_posting_schedule`, `max_lateness_minutes`, `prepare_lead_minutes`, `news_lane_enabled` (default **false**; budget limits default to **0** so paid provider calls are blocked until the owner sets them in the app (D-18); while false, news topics cannot leave `DRAFT`/`HELD_FOR_HUMAN`). Environment variables only seed initial values; changing a control writes an audit event. `publishing_kill_switch` defaults to **true** on a fresh database.

## Mandatory publish sequence
See `docs/publishing-meta.md` D1-D3. Scheduled and publish-now share one service; publish-now simply uses `T = now` and still needs a `READY_TO_PUBLISH` video, the full gate and an owner action.

## Reveal items (rebus)
`answer_reveals` are optional (owner may choose not to reveal, D-19) and have their own schedule/state (`PENDING -> SCHEDULED -> POSTED | SKIPPED | FAILED`) and pass the same kill switch and safety checks.

## Idea scan (D-28)

```
feed_sources (RSS) --fetch--> feed_items --Claude shortlist--> idea_candidates (new)
   owner picks + lane --Claude propose--> idea_candidates (proposed, 3 options)
   owner edits/verifies --create--> content_items (DRAFT) --> normal pipeline
```
- Scheduled by `system_settings.scan_times` (Vietnam time, `scan_enabled` default false). One `scan_runs` row per slot (unique `slot_key`), run within 3 h of the slot or skipped; manual scans via `POST /api/ideas/scan`.
- Headlines are untrusted: parsed with defusedxml, cut to plain text, sent to Claude only as data. The backend drops candidates with any sensitive category, high/critical risk, blacklisted words, score < 50, invented ids, or items already on the list; keeps at most 10 per scan; candidates expire after 48 h and headlines are purged after 7 days.
- Rebus proposals start from the owner's idiom bank; an option whose clues spell its own answer is dropped; the owner verifies the answer before creating.
