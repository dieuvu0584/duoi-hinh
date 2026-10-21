# API contract outline

All `/api/*` admin routes require authentication. Define a consistent error envelope, pagination and request IDs. Commands that may be retried should accept `Idempotency-Key`. A `202 Accepted` response means the backend accepted work, not that it completed.

## Proposed endpoints
- `GET /api/dashboard`
- `GET /api/topics`, `POST /api/topics`
- `GET /api/content/pending`, `GET /api/content/{id}`, `PATCH /api/content/{id}`
- `POST /api/content/{id}/approve`, `POST /api/content/{id}/reject`
- `POST /api/content/{id}/regenerate`
- `POST /api/content/{id}/images/upload`, `POST /api/images/{id}/select`
- `GET /api/jobs`, `POST /api/jobs/{id}/cancel`
- `GET /api/publishing/schedule`, `POST /api/publishing/{id}/publish-now`, `POST /api/publishing/{id}/cancel`
- `POST /api/system/pause`, `POST /api/system/resume`
- `POST /api/system/publishing-stop`, `POST /api/system/publishing-resume`
- `GET /api/system/health`
- `POST /api/auth/login`, `POST /api/auth/refresh`, `POST /api/auth/logout`, `GET/DELETE /api/auth/devices`
- `GET /api/content/{id}/revisions`, `POST /api/content/{id}/rebus/verify`
- `GET /api/content/{id}/video`, `POST /api/content/{id}/render`
- `GET /api/costs`, `GET/PUT /api/system/settings`
- `GET /api/metrics`
- `POST /api/publishing/{id}/retract` (reason required, re-auth), `GET /api/publishing/{id}/retraction`

## Contract rules
- Validate state transitions and return conflict errors for invalid transitions.
- Separate synchronous validation errors from accepted background jobs.
- Upload responses include validation results and generated asset ID, never a public arbitrary filesystem path.
- Error payload should include stable `code`, safe `message`, `request_id` and optional field errors; never include secrets or stack traces.
- Use pagination for list endpoints and explicit filters/sort order.
- Do not expose internal object-storage credentials or raw provider payloads.
- WebSocket/SSE updates are optional; polling can be the MVP fallback.
