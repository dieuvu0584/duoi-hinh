from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.db.models import AuditEvent, SystemSettings
from tests.conftest import OWNER_PASSWORD

URL = "/api/system/settings"


def put(client, headers, changes, password=None):
    body = {"changes": changes}
    if password is not None:
        body["password"] = password
    return client.put(URL, json=body, headers=headers)


def test_fresh_install_is_fail_closed(client, owner, auth_headers):
    s = client.get(URL, headers=auth_headers).json()
    assert s["publishing_kill_switch"] is True
    assert s["news_lane_enabled"] is False
    assert s["automation_enabled"] is False
    assert s["daily_budget_limit"] == "0.0000" and s["monthly_budget_limit"] == "0.0000"
    assert s["max_publishes_per_day"] == 3


def test_stop_needs_no_password_and_is_idempotent(client, owner, auth_headers, db):
    db.execute(text("UPDATE system_settings SET publishing_kill_switch = false"))
    db.commit()
    for _ in range(2):
        resp = client.post("/api/system/publishing-stop", headers=auth_headers)
        assert resp.status_code == 200 and resp.json()["publishing_kill_switch"] is True


def test_resume_requires_the_correct_password(client, owner, auth_headers, db):
    bad = client.post("/api/system/publishing-resume", json={"password": "wrong wrong wrong"},
                      headers=auth_headers)
    assert bad.status_code == 403 and bad.json()["error"]["code"] == "reauth_failed"
    assert client.get(URL, headers=auth_headers).json()["publishing_kill_switch"] is True

    ok = client.post("/api/system/publishing-resume", json={"password": OWNER_PASSWORD},
                     headers=auth_headers)
    assert ok.status_code == 200 and ok.json()["publishing_kill_switch"] is False

    actions = [e.action for e in db.scalars(select(AuditEvent).order_by(AuditEvent.occurred_at))]
    assert "auth.reauth" in actions and "settings.kill_switch.off" in actions


def test_resume_without_a_body_is_rejected(client, owner, auth_headers):
    resp = client.post("/api/system/publishing-resume", headers=auth_headers)
    assert resp.status_code == 422
    assert client.get(URL, headers=auth_headers).json()["publishing_kill_switch"] is True


@pytest.mark.parametrize("change", [{"news_lane_enabled": True}, {"daily_budget_limit": "5"},
                                    {"monthly_budget_limit": "20"}, {"automation_enabled": True}])
def test_widening_settings_require_reauth(client, owner, auth_headers, change):
    assert put(client, auth_headers, change).json()["error"]["code"] == "reauth_required"
    wrong = put(client, auth_headers, change, password="wrong wrong wrong")
    assert wrong.status_code == 403
    ok = put(client, auth_headers, change, password=OWNER_PASSWORD)
    assert ok.status_code == 200
    key, value = next(iter(change.items()))
    got = ok.json()[key]
    assert got is True if value is True else Decimal(got) == Decimal(value)


def test_ordinary_settings_do_not_need_reauth(client, owner, auth_headers):
    resp = put(client, auth_headers, {"max_publishes_per_day": 2, "generation_paused": True})
    assert resp.status_code == 200
    assert resp.json()["max_publishes_per_day"] == 2 and resp.json()["generation_paused"] is True


def test_budget_is_stored_exactly_and_audited_with_before_after(client, owner, auth_headers, db):
    put(client, auth_headers, {"daily_budget_limit": "1.5"}, password=OWNER_PASSWORD)
    assert client.get(URL, headers=auth_headers).json()["daily_budget_limit"] == "1.5000"
    event = db.scalars(select(AuditEvent).where(AuditEvent.action == "settings.update")).one()
    assert event.event_metadata["before"]["daily_budget_limit"] == "0.0000"
    assert event.event_metadata["after"]["daily_budget_limit"] == "1.5"


@pytest.mark.parametrize(
    "changes",
    [{"daily_budget_limit": "-1"}, {"max_publishes_per_day": 31}, {"max_publishes_per_day": -1},
     {"prepare_lead_minutes": 1}, {"publishing_kill_switch": False}, {"budget_currency": "VND"},
     {"unknown": 1}, {}],
)
def test_invalid_or_forbidden_settings_are_rejected(client, owner, auth_headers, changes):
    resp = put(client, auth_headers, changes, password=OWNER_PASSWORD)
    assert resp.status_code == 422


def test_null_values_are_rejected(client, owner, auth_headers):
    resp = put(client, auth_headers, {"max_lateness_minutes": None})
    assert resp.status_code == 422


def test_kill_switch_cannot_be_turned_off_through_the_generic_settings_endpoint(
    client, owner, auth_headers
):
    resp = put(client, auth_headers, {"publishing_kill_switch": False}, password=OWNER_PASSWORD)
    assert resp.status_code == 422
    assert client.get(URL, headers=auth_headers).json()["publishing_kill_switch"] is True


def test_database_constraints_back_up_the_api(db):
    with pytest.raises(IntegrityError):
        db.execute(text("INSERT INTO system_settings (id) VALUES (2)"))
    db.rollback()
    with pytest.raises(IntegrityError):
        db.execute(text("UPDATE system_settings SET daily_budget_limit = -1"))
    db.rollback()
    with pytest.raises(IntegrityError):
        db.execute(text("UPDATE system_settings SET max_publishes_per_day = 99"))
    db.rollback()
    assert db.scalar(select(SystemSettings.publishing_kill_switch)) is True


def test_audit_metadata_is_redacted(db):
    from app.services.audit import record_audit

    record_audit(db, actor="system", action="x", metadata={"password": "hunter2", "ok": "fine"},
                 note="refresh_token=abc123")
    db.commit()
    event = db.scalars(select(AuditEvent).where(AuditEvent.action == "x")).one()
    assert event.event_metadata == {"password": "***", "ok": "fine"}
    assert "abc123" not in (event.note or "")
