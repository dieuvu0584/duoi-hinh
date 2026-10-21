# Development quickstart

Windows note: use `127.0.0.1`, not `localhost`, in database URLs (IPv6 fallback makes `localhost` very slow on Windows).

## Backend tests (needs a PostgreSQL test database)
```
docker run -d --name nhd-test-pg -e POSTGRES_USER=nhd -e POSTGRES_PASSWORD=nhd_test \
  -e POSTGRES_DB=nhd_test -p 127.0.0.1:5433:5432 postgres:16
cd backend
pip install -e ".[dev]"
pytest -q          # drops and recreates the schema of NHD_TEST_DATABASE_URL, then runs migrations
ruff check .
```
`NHD_TEST_DATABASE_URL` defaults to `postgresql+psycopg://nhd:nhd_test@127.0.0.1:5433/nhd_test`. Tests that need the database are skipped when it is unreachable. **Never point it at a database you care about.**

## Running the stack (Docker)
```
cp .env.example .env     # then edit secrets; .env is gitignored
cd deploy
docker compose --env-file ../.env up -d --build
```
Always pass `--env-file ../.env`; compose does not read the root `.env` for interpolation otherwise, and missing `POSTGRES_*` values now fail loudly. Set `NHD_API_PORT` in `.env` if port 8000 is taken. The API container applies migrations on start (`alembic upgrade head`).

## Worker (scheduler)
```
python -m app.worker --once     # one tick
python -m app.worker            # loop every 30 s (--interval to change)
```
It does nothing unless `automation_enabled` is true (default false), uses the dry-run provider unless `NHD_PUBLISH_MODE=live`, and in Docker runs as the `worker` service with `../media` mounted read-only at `/app/media`.

## Create the owner account (once, on the server)
```
docker compose --env-file ../.env exec api python -m app.cli create-owner --username owner
docker compose --env-file ../.env exec api python -m app.cli reset-password --username owner
```
The password is read from a hidden prompt (minimum 12 characters). There is no registration endpoint. Resetting the password revokes every device session.

## API quick reference (Package A)
`POST /api/auth/login` | `POST /api/auth/refresh` | `POST /api/auth/logout` | `GET/DELETE /api/auth/devices` | `GET /api/system/health` (public) | `GET/PUT /api/system/settings` | `POST /api/system/publishing-stop` | `POST /api/system/publishing-resume` (password required).
Settings that widen what the system may do (budgets, news lane, automation) require the password in the request body. The kill switch can only be turned off through `publishing-resume`.
