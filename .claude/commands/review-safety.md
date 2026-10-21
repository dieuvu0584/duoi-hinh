Perform a safety/security review of the changed code and relevant content/publishing flows. Inspect:
- all publish paths, including scheduled and manual publish-now;
- kill switch checked immediately before external calls;
- stale/missing approvals and safety reviews fail closed;
- idempotency and `UNKNOWN_RESULT` reconciliation;
- auth on all admin endpoints and secret redaction;
- uploaded file validation and budget caps;
- source provenance and distinction between event date and publication date;
- approval, safety review and rendered video all bound to the same `content_hash`; edits invalidate them;
- untrusted source text handling (prompt injection, SSRF, XSS) per `docs/threat-model.md`;
- retraction path, news freshness (`fresh_until`) and review TTL;
- audit events and recovery behavior.
Report findings by severity with file/line references and tests needed. Fix straightforward defects in scope; do not weaken safety controls to make tests pass.
