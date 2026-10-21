# Operations runbook (design-level)

Procedures to be written out and rehearsed in Package F. This file fixes the requirements so the code supports them.

## 1. Retraction and correction (post already public)
Required capability: the owner can retract a published Reel quickly and record why.
- State: `PUBLISHED -> RETRACTION_REQUESTED -> RETRACTED | RETRACTION_FAILED` on the publishing record; audited.
- Mechanism: **VERIFY in Package 0** whether the Graph API allows deleting/unpublishing a Page Reel (`DELETE /{video-id}` or `/{post-id}`) with the granted permissions. If not, the admin UI links to the post and instructs manual deletion in Facebook, and records the manual retraction.
- Triggers: factual error, defamation or privacy complaint, copyright claim, platform warning, owner decision.
- News corrections: prefer a correction comment/follow-up plus (if needed) retraction; keep the original record and evidence immutable.
- Kill switch is **not** a recall: it stops future publishes only (see threat T-9 residual window). Alerts go to the owner when a publish succeeds after a recent kill-switch change.

## 2. Incident response (publishing/safety)
1. Flip the kill switch (re-auth required). 2. Retract affected posts. 3. Revoke Meta token / rotate secrets if exposure is suspected. 4. Preserve audit events and logs (redacted). 5. Write a short post-mortem and add a regression test or policy rule.

## 3. Owner bootstrap and recovery
First owner is created by a one-time CLI command on the server; there is no public registration route. Password/TOTP recovery is done on the server by the same CLI and audited. Device revocation is available in the admin UI and CLI.

## 4. Backups, retention, recovery
- PostgreSQL: scheduled backups with point-in-time recovery if the provider supports it; media in object storage with versioning; secrets are **not** in backups (documented re-provisioning steps).
- Initial targets (to be agreed by the owner, not facts): RPO <= 24 h, RTO <= 4 h; restore drill before production.
- Retention (defaults, configurable): published media and records kept; unpublished/rejected candidates and intermediate frames purged after 30 days; `llm_calls` bodies purged after 30 days (keep hashes, costs); audit events kept >= 12 months; source excerpts purged when source terms require. Deletion jobs are audited.
- Personal data: the system should not store personal data about third parties beyond what appears in source metadata; flagged content with private individuals is held for human review.

## 5. Monitoring and alerts
Metrics: job queue depth/age, worker heartbeat, provider error rates and latency, daily cost vs. budget, publish attempts/outcomes, `UNKNOWN_RESULT` count, token health, disk usage, render durations, backup age.
Alerts to the owner (channel per O-2): publish failure or `UNKNOWN_RESULT`, token invalid or near expiry, budget at 80%/100%, worker down, backup stale, Meta rate-limit response, a safety `block` on scheduled content, copyright match.

## 6. Change management
Prompts and models are versioned (`prompt_versions`). A prompt or model change requires re-running the evaluation set before enabling in production. Meta API version bumps are tested against the test Page first.

## 7. Dry-run mode
`NHD_PUBLISH_MODE=dry_run` (default outside production) runs the entire pipeline and gate but replaces the Meta adapter with a clearly labelled fake that never performs network calls. Production refuses to start in `dry_run` with real tokens present, and `live` requires an explicit setting.
