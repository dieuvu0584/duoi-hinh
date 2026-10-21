# Architecture

## Logical components
- **Owner admin UI** (default: responsive web admin served by the backend behind a private network; Flutter only if decision O-1 changes): dashboard, review queue, job monitoring, schedule, analytics, settings. It is a client only.
- **FastAPI backend**: auth, API contracts, workflow state transitions, policy checks, audit and orchestration.
- **PostgreSQL**: canonical business state, source provenance, reviews, jobs, publishing records, metrics and audit events.
- **Redis + Celery**: async queue and scheduled execution; not canonical state.
- **Providers**: Claude structured analysis, FLUX/Gemini image generation, Meta Graph API publishing, owner notifications (channel per decision O-2).
- **Media pipeline**: validation, storage, deterministic composition/text overlay, ffmpeg Reel rendering with ffprobe gate, QA and provenance (`docs/video-pipeline.md`).
- **Cross-cutting docs**: `docs/threat-model.md`, `docs/operations.md`, `docs/content-guidelines.md`, `docs/publishing-meta.md`.

## Key principles
- Modular monolith first; no premature microservices.
- Provider adapters isolate vendor changes.
- Fail closed on safety, approval, auth and kill-switch checks.
- Use idempotency for repeatable commands and reconciliation for uncertain external outcomes.
- Private network/VPN preferred for admin access.
- All times UTC in storage; local schedule display uses `Asia/Ho_Chi_Minh`.

## Deployment
Docker Compose for local development and initial VPS. Use managed PostgreSQL/object storage where practical for production. Add TLS, firewall rules, private network access, backups, restore drills, health checks and resource limits before production.
