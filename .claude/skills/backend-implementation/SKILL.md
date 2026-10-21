---
name: backend-implementation
description: Implement the Python backend in safe, tested vertical slices using this repository's architecture and acceptance criteria.
---
# Backend implementation skill

1. Inspect the existing code and identify the smallest vertical slice aligned with the roadmap.
2. Define Pydantic schemas, service interfaces and error behavior before adding routes.
3. Add DB models and Alembic migrations together when persistence is introduced.
4. Add auth dependencies to all admin endpoints by default.
5. Add tests for normal behavior, invalid state transitions, unauthorized access, duplicate requests and failure recovery.
6. Use injected provider fakes in tests; do not let unit tests call live APIs.
7. Ensure async jobs persist state in PostgreSQL and can recover after worker restart.
8. Ensure all external side effects have idempotency/reconciliation strategy.
9. Update docs, env example and acceptance checklist.
10. Run lint/type/test commands available in the repo and report exact results. Do not claim unrun tests passed.
