# Data model proposal (v2)

UUID primary keys, UTC `timestamptz`, explicit enums (PostgreSQL enum or checked text), SQLAlchemy 2.x typed mappings, Alembic migrations. JSONB for provider metadata and specs only; queryable core fields are real columns. Every table has `created_at`, `updated_at`.

## Identity and access
- `owner_account`: single owner; `password_hash` (argon2id via an established library), optional TOTP secret reference, `disabled_at`.
- `devices` / `sessions`: device label, refresh-token hash (rotating, one-time use), `last_seen_at`, `revoked_at`, IP/user-agent (redacted as needed). Supports pairing and revocation.
- `login_attempts`: for rate limiting/lockout and audit.

## Sources and topics
- `source_registry`: name, publisher, type (`official|news|wire|science_tech|rss_api|user_submitted|social_permitted`), canonical domain, access method, terms-reviewed flag + date + note, reliability, is_primary_evidence, polling schedule, enabled, last success/error, rate-limit policy, secret reference.
- `source_documents`: canonical URL (unique), title, publisher, `published_at`, `event_date` (nullable, never defaulted from published_at), `retrieved_at`, content hash, permitted excerpt, provenance JSONB, `syndication_group_id` (reposts of one wire story share a group; corroboration counts groups, not documents).
- `topics`: title, lane, `fresh_until` (required for news), status, verification status, novelty/relevance/visualizability scores, risk level, `review_required`, links to `source_documents` (M:N `topic_sources` with role `primary|corroborating|context`), `facts` / `unverified_claims` / `opinion_seed` as child rows or JSONB with schema.

## Content
- `content_items`: topic FK nullable (daily_life/rebus may have none), lane, `current_revision_id`, workflow state, `series_id` nullable, dedupe key.
- `content_revisions` (immutable): `content_item_id`, `revision_no`, `answer_text`, `narration_text`, `voice_id`, `clue_mapping` JSONB, `rebus_spec` JSONB (below), `visual_blueprint`, caption, hashtags, overlay_text, `expected_viewer_interpretation`, `ambiguity_risks`, `selected_image_asset_id`, `video_asset_id`, `audio_asset_id`, `is_ai_generated`, `content_hash`, `created_by` (`system|owner`), `parent_revision_id`.
- `explanation_tickets`: one per revision; private admin metadata (answer, intended meaning, visual explanation, clue mapping, ambiguity risks, reveal policy, safety status).
- `answer_reveals`: revision FK, mode (`manual_comment|api_comment|follow_up_reel`), scheduled_at, state, posted_ref.

### Rebus spec (Vietnamese)
Vietnamese rebus relies on syllables, tones and diacritics; LLMs are unreliable here. The answer is stored decomposed:
```
rebus_spec = {
  answer_text: "...",
  answer_normalized: "...",          // NFC, lowercased, plus toneless ASCII form
  syllables: [ {text, tone, clue_ids:[..]} ],
  clues: [ {id, symbol_id, depicts, operation: "literal|homophone|drop_tone|add|remove|sound_alike|position",
            maps_to_syllable_index, note} ],
  difficulty: 1..5, alternate_answers: [...], human_verified: bool
}
```
Rules: every syllable is covered by >= 1 clue; every clue maps to a syllable; `human_verified=true` is mandatory before generation in the pilot; answer uniqueness is enforced (no repeat of a normalized answer within a configurable window).

## Assets
- `clue_symbols`: reusable rebus symbol library (id, label, tags, source `generated|manual|licensed`, license note, storage key, hash, approved flag).
- `image_assets`: content_revision FK nullable, provider (`flux|gemini|manual|library`), model ID (nullable for manual), prompt + `prompt_version_id`, params, storage keys for **original** and **processed** (separate immutable keys), MIME, bytes, original/processed dimensions, processing metadata, provenance (who uploaded, license/copyright declaration), QA JSON, cost, `content_hash`.
- `video_assets`: revision FK, `video_spec` JSONB, `video_spec_hash`, storage key, `content_hash`, ffprobe JSON, encoder version, duration, status (`RENDERING|VALID|INVALID`), keyframe keys.
- `audio_assets`: kind (`narration|music|mix`), provider (`elevenlabs|library`), model_id, `voice_id`, synthesis settings JSONB, `text_hash` (sha256 of text + voice + model + settings, the cache key), duration, `sha256`, storage key, cost, license/plan note. Narration assets are content-addressed, so an identical request is a free cache hit.
- `prompt_versions`: name, version, body hash, schema version; every LLM call and image prompt records the version used.

## Review and approval
- `llm_calls`: provider, model, `prompt_version_id`, entity, request hash, redacted response ref, tokens/cost, latency, request id, status (for debugging and cost; never raw secrets).
- `safety_reviews`: entity type/id, **`revision_id` + `content_hash` reviewed**, gate (`intake|final|prepublish`), decision, risk level, categories, findings, required changes, policy version, `reviewed_at`, `expires_at`, reviewer (`llm:<model>|owner`), `human_review_required/complete`.
- `qa_reviews`: asset/revision ref, rubric version, scores, hard failures, recommendation, reviewer.
- `approvals`: revision FK, `content_hash`, decision, actor, timestamp, note. Approval is invalid if `content_hash` differs from the revision being published.

## Execution
- `jobs`: type, entity ref, status, attempts, `retry_after`, lease owner/expiry, redacted error, `idempotency_key` (unique per scope), timestamps.
- `idempotency_keys`: key, scope, request hash, response ref, expiry (for API commands).
- `cost_ledger`: provider, operation, units, `cost_amount`, currency, entity, `recorded_at`, estimate-vs-actual flag. Budgets are enforced by summing this table; cost is recorded **before** a blind retry is allowed.
- `publishing_records`: content_revision FK, `content_hash`, schedule, status, `intent_key` (unique per logical publish intent), `meta_video_id`, `meta_post_id`, permalink, attempts (child rows `publishing_attempts` with START/UPLOAD/FINISH timestamps, redacted request/response, error code), reconciliation timestamps.
- `system_settings`: single row, see `docs/workflow.md`; `system_setting_changes` via `audit_events`.
- `token_health`: provider, checked_at, valid, scopes, expires_at (if known), error.
- `audit_events`: actor (`system|owner|device:<id>`), action, entity, result, redacted metadata, `request_id`, timestamp. Append-only (no update/delete from the app role).

## Metrics
- `post_metrics`: publishing FK, `captured_at`, `window` (`1h|24h|72h|7d`), `metric_name`, `value`, `period` as returned by the API (long/EAV form so unsupported/renamed metrics never break the schema), `raw_ref`.
- `experiments` (light): hypothesis, lane/variant tags on content (hook style, duration, audio yes/no, posting slot) so the pilot can compare something real.

## Canonical content hash
`content_hash = SHA-256` over a canonical serialization: UTF-8 NFC strings, JSON with sorted keys and no insignificant whitespace, fields in a fixed documented order (answer, clue mapping, rebus spec, caption, hashtags, overlay text, image asset hash, video asset hash, audio asset hash, `is_ai_generated`, template/version ids). The function is versioned (`hash_version`) and covered by golden tests; media are included by content hash, not by key.

## Retraction, review TTL, freshness
- `retractions`: publishing FK, reason, requested_by, requested_at, mode (`api|manual`), result, evidence ref.
- `safety_reviews.expires_at` is computed as `min(lane_ttl, topic_fresh_until)`; `topics.fresh_until` is required for `news`.
- `approvals` expire together with the safety review they depend on.

## Integrity requirements
- State transitions only through the service; DB `CHECK` constraints for impossible combinations where feasible.
- Unique: `source_documents.canonical_url`, `publishing_records.intent_key`, `jobs(scope, idempotency_key)`, one non-terminal `publishing_record` per revision.
- `content_revisions` and `safety_reviews` rows are immutable after insert (except review staleness marker).
- Answer/clue mapping is immutable per revision; editing creates a new revision and requires re-verification + re-review + re-approval.
- Originals and processed assets are distinct immutable objects (content-addressed keys).
- DB roles: app role cannot UPDATE/DELETE `audit_events`.
