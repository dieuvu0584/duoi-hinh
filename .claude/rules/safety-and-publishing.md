# Safety and publishing rules

## Review gates
1. **Intake gate:** source integrity, topic sensitivity, provenance, novelty and policy categories.
2. **Final-content gate:** review the exact rendered video (keyframes), overlay text, caption, hashtags, audio metadata and links together; the review is bound to the `content_hash`.
3. **Pre-publish gate:** immediately before external publish request, verify current approval, review freshness, publish state, kill switch, schedule and budget.

`human_review_required` is derived by deterministic backend rules from risk level and sensitive categories; it is never taken from model output alone. Review TTL is set per lane (news shorter) and capped by the topic's freshness window; an expired review or stale news topic is re-reviewed, not published. Any absent/invalid/stale review fails closed. Backend policy code makes final allow/block/hold decision; LLM output is evidence, not authority.

## Human review required
Always require human review for allegations about identifiable people, politics/elections, health advice, legal claims, children, private individuals, personal data, copyright concerns, war/conflict, death/injury, disasters, active emergencies or materially uncertain breaking news. Block exploitation of tragedy, harassment, doxxing, defamatory claims or misleading synthetic media.

## Retraction
The owner must be able to retract a published Reel and record the reason (`docs/operations.md`). The kill switch stops future publishes only; it does not recall a post.

## News integrity
- Record source URL, publisher, published timestamp, retrieval timestamp and event date separately.
- Do not infer event date from article publication date.
- Sensitive claims require multiple independent reliable sources or an authoritative primary source, plus human review.
- Clearly label opinion/inference separately from verified facts.
- Never invent a quote, number, event, source, date or attribution.
- AI-generated visual metaphors are not documentary evidence. Add a suitable disclosure where context could mislead.

## Publishing controls
- `publishing_kill_switch=true` blocks every publish pathway, including scheduled and manual. The database setting is authoritative.
- Re-read the control in the transaction/critical path immediately before the external call.
- Require content approval and non-stale safety review.
- Enforce per-day/month budget and configured publishing limits.
- Use an idempotency key per logical publish intent.
- On timeout/uncertain response, set `UNKNOWN_RESULT`, reconcile via the stored `video_id` and the video's `publishing_phase`, and do not blindly retry. See `docs/publishing-meta.md` D2.
- Set `is_ai_generated` per `docs/publishing-meta.md` D4; never strip provenance signals.
- Wait for the Meta copyright check; block on a match.
- Audit all control changes and publishing transitions.
