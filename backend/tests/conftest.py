import os

# Must be set before the app (and its settings object) is imported.
TEST_DB_URL = os.environ.get(
    "NHD_TEST_DATABASE_URL", "postgresql+psycopg://nhd:nhd_test@127.0.0.1:5433/nhd_test"
)
os.environ["NHD_DATABASE_URL"] = TEST_DB_URL
os.environ.setdefault("NHD_ENVIRONMENT", "development")
# Tests must never pick up the developer's real Meta credentials from a local .env file.
os.environ["META_PAGE_ID"] = ""
os.environ["META_ACCESS_TOKEN"] = ""
os.environ["ELEVENLABS_API_KEY"] = ""
os.environ["FAL_API_KEY"] = ""
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["ELEVENLABS_VOICE_ID"] = ""

import pathlib  # noqa: E402

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]

OWNER_USER = "owner"
OWNER_PASSWORD = "correct horse battery staple"


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(TEST_DB_URL, future=True, connect_args={"connect_timeout": 3})
    try:
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError:
        pytest.skip("PostgreSQL test database not reachable (set NHD_TEST_DATABASE_URL)")
    # Start from an empty database and apply the real migrations (acceptance: empty -> head).
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", TEST_DB_URL)
    command.upgrade(cfg, "head")
    return eng


@pytest.fixture()
def db(engine):
    """Fresh state per test: truncate data tables and restore the fail-closed settings row."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE login_attempts, devices, owner_account, audit_events, publishing_records,"
                " approvals, safety_reviews, content_revisions, content_items, token_health,"
                " cost_ledger, provider_budgets, audio_assets, video_assets, image_assets,"
                " composition_checks, llm_calls, feed_items, idea_candidates, scan_runs,"
                " feed_sources, idioms CASCADE"
            )
        )
        conn.execute(
            text(
                "UPDATE system_settings SET automation_enabled=false, generation_paused=false,"
                " publishing_kill_switch=true, news_lane_enabled=false, daily_budget_limit=0,"
                " monthly_budget_limit=0, budget_currency='USD', max_image_regenerations=3,"
                " max_publishes_per_day=3, max_lateness_minutes=30, prepare_lead_minutes=20,"
                " facebook_page_id=NULL, youtube_channel_id=NULL, elevenlabs_voice_id=NULL,"
                " scan_enabled=false, youtube_enabled=false,"
                " scan_times='[\"10:00\",\"16:00\",\"20:00\",\"00:00\"]'::jsonb"
            )
        )
    from app.db.session import session_factory

    with session_factory()() as session:
        yield session


@pytest.fixture()
def owner(db):
    from app.services import auth_service

    account = auth_service.create_owner(db, OWNER_USER, OWNER_PASSWORD)
    db.commit()
    return account


@pytest.fixture()
def client(db):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def auth_headers(client, owner):
    resp = client.post(
        "/api/auth/login",
        json={"username": OWNER_USER, "password": OWNER_PASSWORD, "device_label": "pytest"},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}
