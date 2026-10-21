# Architecture rules

- Use a modular monolith until real scale demands otherwise.
- Suggested backend stack: Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2.x, Alembic, PostgreSQL, Redis, Celery and Celery Beat.
- API layer validates/authenticates requests and delegates to services. Business logic belongs in services/policies, not route handlers.
- Provider interfaces live in `backend/app/providers/contracts.py`; provider implementations must have timeouts, typed errors, redacted logs and retry classification.
- PostgreSQL is canonical state. Redis is not the only record of a job or publish action.
- Persist state transitions; reject invalid transitions. Use idempotency keys for commands that may be retried.
- Jobs should be safe under at-least-once execution. Make handlers idempotent or reconcile before retrying external side effects.
- Store media in object storage; use local storage only for development. Keep original and processed asset records distinct.
- All dates persisted in UTC. Convert to `Asia/Ho_Chi_Minh` only for UI and schedules.
- Every new endpoint needs schema, auth requirement, error contract, tests and docs.
- Use dependency injection for database sessions, settings and providers so tests can substitute fakes.
