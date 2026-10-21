import re
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core.config import settings
from app.db.base import utcnow
from app.db.models import AuditEvent, Device
from app.services import auth_service
from tests.conftest import OWNER_PASSWORD, OWNER_USER


def login(client, password=OWNER_PASSWORD, username=OWNER_USER):
    return client.post(
        "/api/auth/login",
        json={"username": username, "password": password, "device_label": "t"},
    )


def test_login_success_returns_tokens(client, owner):
    resp = login(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "Bearer" and body["access_token"] and body["refresh_token"]
    assert resp.headers["X-Request-ID"]


def test_wrong_password_and_unknown_user_look_identical(client, owner):
    a = login(client, password="wrong password!!")
    b = login(client, username="nobody")
    assert a.status_code == b.status_code == 401
    assert a.json()["error"]["code"] == b.json()["error"]["code"] == "invalid_credentials"
    assert a.json()["error"]["message"] == b.json()["error"]["message"]


def test_rate_limit_blocks_even_the_correct_password(client, owner):
    for _ in range(settings.login_max_failures):
        assert login(client, password="bad bad bad bad").status_code == 401
    locked = login(client)  # correct password, but locked out
    assert locked.status_code == 429
    assert locked.headers["Retry-After"]


def test_lockout_expires_after_the_window(db, owner):
    now = utcnow()
    for _ in range(settings.login_max_failures):
        with pytest.raises(Exception):  # noqa: B017 - ApiError 401
            auth_service.login(db, settings, username=OWNER_USER, password="nope nope nope",
                               device_label="t", ip=None, now=now)
    later = now + timedelta(minutes=settings.login_window_minutes + 1)
    pair = auth_service.login(db, settings, username=OWNER_USER, password=OWNER_PASSWORD,
                              device_label="t", ip=None, now=later)
    assert pair.access_token


def test_failed_logins_are_audited_without_the_password(client, owner, db):
    login(client, password="supersecret-wrong")
    events = db.scalars(select(AuditEvent).where(AuditEvent.action == "auth.login")).all()
    assert events and events[-1].result == "denied"
    assert "supersecret-wrong" not in str(events[-1].event_metadata)


def test_refresh_rotates_and_old_token_reuse_revokes_the_device(client, owner, db):
    first = login(client).json()
    second = client.post("/api/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert second.status_code == 200
    new = second.json()
    assert new["refresh_token"] != first["refresh_token"]

    # Replaying the already-used token is treated as theft: the whole device session dies.
    replay = client.post("/api/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert replay.status_code == 401
    after = client.post("/api/auth/refresh", json={"refresh_token": new["refresh_token"]})
    assert after.status_code == 401
    device = db.get(Device, first["device_id"])
    db.refresh(device)
    assert device.revoked_at is not None and device.revoked_reason == "refresh_reuse"


def test_expired_refresh_token_is_rejected(db, owner):
    pair = auth_service.login(db, settings, username=OWNER_USER, password=OWNER_PASSWORD,
                              device_label="t", ip=None)
    future = utcnow() + timedelta(days=settings.refresh_token_ttl_days + 1)
    with pytest.raises(Exception) as exc:  # noqa: B017
        auth_service.refresh(db, settings, refresh_token=pair.refresh_token, ip=None, now=future)
    assert exc.value.status_code == 401


def test_expired_access_token_is_rejected(client, db, owner):
    past = utcnow() - timedelta(hours=2)
    pair = auth_service.login(db, settings, username=OWNER_USER, password=OWNER_PASSWORD,
                              device_label="t", ip=None, now=past)
    headers = {"Authorization": f"Bearer {pair.access_token}"}
    resp = client.get("/api/system/settings", headers=headers)
    assert resp.status_code == 401


def test_tampered_or_garbage_tokens_are_rejected(client, owner, auth_headers):
    token = auth_headers["Authorization"].split()[1]
    for bad in (token[:-3] + "abc", "garbage", ""):
        resp = client.get("/api/system/settings", headers={"Authorization": f"Bearer {bad}"})
        assert resp.status_code == 401


def test_logout_and_device_revocation_kill_the_access_token(client, owner):
    pair = login(client).json()
    headers = {"Authorization": f"Bearer {pair['access_token']}"}
    assert client.get("/api/system/settings", headers=headers).status_code == 200
    assert client.post("/api/auth/logout", headers=headers).status_code == 204
    assert client.get("/api/system/settings", headers=headers).status_code == 401


def test_owner_can_revoke_another_device(client, owner):
    a = login(client).json()
    b = login(client).json()
    ha = {"Authorization": f"Bearer {a['access_token']}"}
    hb = {"Authorization": f"Bearer {b['access_token']}"}
    assert client.delete(f"/api/auth/devices/{b['device_id']}", headers=ha).status_code == 204
    assert client.get("/api/system/settings", headers=hb).status_code == 401
    assert client.get("/api/system/settings", headers=ha).status_code == 200
    listed = client.get("/api/auth/devices", headers=ha).json()
    assert {d["id"] for d in listed} == {a["device_id"], b["device_id"]}


def test_unknown_device_revocation_is_404(client, owner, auth_headers):
    nil = "00000000-0000-0000-0000-000000000000"
    resp = client.delete(f"/api/auth/devices/{nil}", headers=auth_headers)
    assert resp.status_code == 404


def test_reset_password_revokes_all_sessions(client, owner, db, auth_headers):
    auth_service.set_password(db, OWNER_USER, "a brand new long password")
    db.commit()
    assert client.get("/api/system/settings", headers=auth_headers).status_code == 401
    assert login(client, password="a brand new long password").status_code == 200


def test_only_one_owner_and_strong_password_required(db, owner):
    with pytest.raises(ValueError, match="already exists"):
        auth_service.create_owner(db, "second", "another long password")
    db.rollback()
    with pytest.raises(ValueError, match="at least"):
        auth_service.set_password(db, OWNER_USER, "short")


def test_every_api_route_requires_auth_except_the_public_ones(client):
    public = {("POST", "/api/auth/login"), ("POST", "/api/auth/refresh"),
              ("GET", "/api/system/health")}
    spec = client.get("/openapi.json").json()
    seen = set()
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            key = (method.upper(), path)
            if not path.startswith("/api"):
                continue
            seen.add(key)
            if key in public:
                continue
            assert op.get("security"), f"{key} is not protected"
            # And behaviourally: no token -> 401, never 200/422/500.
            call = getattr(client, method)
            concrete = re.sub(r"\{[^}]+\}", "00000000-0000-0000-0000-000000000000", path)
            resp = call(concrete)
            assert resp.status_code == 401, f"{key} returned {resp.status_code} without auth"
    assert public <= seen


def test_no_public_registration_route(client):
    paths = " ".join(client.get("/openapi.json").json()["paths"]).lower()
    for word in ("register", "signup", "sign-up"):
        assert word not in paths


def test_audit_log_is_append_only_in_the_database(db, owner):
    event = db.scalars(select(AuditEvent)).first()
    assert event is not None
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("UPDATE audit_events SET action = 'tampered'"))
    db.rollback()
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("DELETE FROM audit_events"))
    db.rollback()


def test_health_is_public_and_minimal(client):
    resp = client.get("/api/system/health")
    assert resp.status_code == 200
    assert resp.json()["dependencies"]["database"] == "ok"
    assert "url" not in resp.text.lower() and "password" not in resp.text.lower()


def test_error_envelope_has_request_id_and_validation_fields(client):
    resp = client.post("/api/auth/login", json={"username": "", "unexpected": 1})
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "validation_error" and error["request_id"] != "-"
    assert error["fields"]


def test_runtime_validation_rejects_insecure_defaults_outside_development():
    from app.core.config import Settings

    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        Settings(environment="production").validate_for_runtime()
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        Settings(
            environment="production", jwt_secret="x" * 40,
            database_url="postgresql+psycopg://nhd:nhd_dev_only@db/nhd",
        ).validate_for_runtime()
    Settings(environment="development").validate_for_runtime()
    Settings(environment="production", jwt_secret="x" * 40,
             database_url="postgresql+psycopg://u:strong@db/x").validate_for_runtime()
