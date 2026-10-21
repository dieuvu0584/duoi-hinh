# Architecture

## Logical components
- **Flutter owner app** (Android-first, decision D-9): dashboard, review queue, job monitoring, schedule, analytics, settings. It is a client only.
- **FastAPI backend**: auth, API contracts, workflow state transitions, policy checks, audit and orchestration.
- **PostgreSQL**: canonical business state, source provenance, reviews, jobs, publishing records, metrics and audit events.
- **Redis + Celery**: async queue and scheduled execution; not canonical state.
- **Providers**: Claude structured analysis, FLUX/Gemini image generation, Meta Graph API publishing, FCM push notifications (decision D-10).
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

## Hosting (decision D-11)
The backend stack (API, worker, scheduler, PostgreSQL, Redis, ffmpeg) runs in Docker on the owner's own PC. The phone reaches the API only through Tailscale; no port is opened to the public internet. Meta publishing uses outbound calls only (no inbound webhooks), which suits this setup. Consequences (PC availability, sleep, updates, off-machine backups) are in `docs/operations.md` section 8.
