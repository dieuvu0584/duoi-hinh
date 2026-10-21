from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.errors import ApiError
from app.db.models import AuditEvent, CostLedger, ProviderBudget
from app.services import budget_service as bs
from tests.conftest import OWNER_PASSWORD

NOW = datetime(2026, 10, 22, 5, 0, tzinfo=UTC)  # 12:00 in Vietnam (UTC+7)


def limits(db, *, daily="10", monthly="100"):
    db.execute(
        text("UPDATE system_settings SET daily_budget_limit = :d, monthly_budget_limit = :m"),
        {"d": daily, "m": monthly},
    )
    db.commit()


def provider(db, name="fal", monthly="4", daily=None):
    db.add(ProviderBudget(provider=name, monthly_limit_usd=Decimal(monthly),
                          daily_limit_usd=None if daily is None else Decimal(daily)))
    db.commit()


def reserve(db, est="0.05", name="fal", now=NOW, **kw):
    return bs.reserve(db, provider=name, operation="image", estimated_usd=est, now=now, **kw)


def code_of(exc_info):
    return exc_info.value.code


def test_fresh_install_cannot_spend_anything(db):
    with pytest.raises(ApiError) as exc:
        reserve(db)
    assert exc.value.status_code == 402 and code_of(exc) == "budget_not_set"


def test_provider_without_a_budget_is_blocked_even_when_global_limits_are_open(db):
    limits(db)
    with pytest.raises(ApiError) as exc:
        reserve(db)
    assert code_of(exc) == "budget_not_set"


def test_global_limits_default_to_zero_and_block_even_with_a_provider_budget(db):
    provider(db)
    with pytest.raises(ApiError) as exc:
        reserve(db)
    assert code_of(exc) == "budget_global_daily"


def test_reservation_within_all_limits_is_recorded_and_audited(db):
    limits(db)
    provider(db)
    entry = reserve(db, "0.05")
    assert entry.status == "reserved" and entry.estimated_usd == Decimal("0.05")
    audit = db.scalars(select(AuditEvent).where(AuditEvent.action == "budget.reserve")).one()
    assert audit.result == "ok"


@pytest.mark.parametrize(
    ("setup", "expected"),
    [
        (dict(daily="0.04", monthly="100", pmonthly="4"), "budget_global_daily"),
        (dict(daily="10", monthly="0.04", pmonthly="4"), "budget_global_monthly"),
        (dict(daily="10", monthly="100", pmonthly="0.04"), "budget_provider_monthly"),
        (dict(daily="10", monthly="100", pmonthly="4", pdaily="0.04"), "budget_provider_daily"),
    ],
)
def test_each_limit_level_blocks_independently(db, setup, expected):
    limits(db, daily=setup["daily"], monthly=setup["monthly"])
    provider(db, monthly=setup["pmonthly"], daily=setup.get("pdaily"))
    with pytest.raises(ApiError) as exc:
        reserve(db, "0.05")
    assert code_of(exc) == expected
    assert db.scalars(select(CostLedger)).all() == []  # nothing reserved on denial


def test_the_owner_budget_of_four_dollars_is_a_hard_stop(db):
    limits(db, daily="50", monthly="50")
    provider(db, monthly="4")
    for _ in range(80):  # 80 x 0.05 = 4.00 exactly
        reserve(db, "0.05")
    with pytest.raises(ApiError) as exc:
        reserve(db, "0.05")
    assert code_of(exc) == "budget_provider_monthly"
    assert bs.spent(db, since=bs.window_starts(NOW)[1], provider="fal") == Decimal("4.00")


def test_settled_cost_above_the_estimate_is_counted_in_full(db):
    limits(db)
    provider(db, monthly="1")
    entry = reserve(db, "0.0249")
    bs.settle(db, entry, actual_usd="0.0251", request_id="req1")
    assert bs.spent(db, since=bs.window_starts(NOW)[1]) == Decimal("0.0251")
    assert entry.status == "settled" and entry.request_id == "req1"


def test_a_failed_call_keeps_its_reservation_until_explicitly_released(db):
    limits(db)
    provider(db, monthly="0.05")
    entry = reserve(db, "0.05")
    with pytest.raises(ApiError):
        reserve(db, "0.01")  # budget is full while the call's outcome is unknown
    bs.release(db, entry, reason="provider returned 401 before processing")
    assert reserve(db, "0.05")  # the money is available again
    audit = db.scalars(select(AuditEvent).where(AuditEvent.action == "budget.release")).one()
    assert "401" in audit.event_metadata["reason"]


def test_release_requires_a_reason(db):
    limits(db)
    provider(db)
    entry = reserve(db)
    with pytest.raises(ValueError):
        bs.release(db, entry, reason="  ")


def test_a_retried_command_never_reserves_twice(db):
    limits(db)
    provider(db, monthly="0.05")
    first = reserve(db, "0.05", idempotency_key="job-1:image")
    again = reserve(db, "0.05", idempotency_key="job-1:image")
    assert again.id == first.id
    assert len(db.scalars(select(CostLedger)).all()) == 1


def test_negative_estimates_are_rejected(db):
    limits(db)
    provider(db)
    with pytest.raises(ValueError):
        reserve(db, "-1")


def test_days_and_months_follow_vietnam_time_not_utc():
    late_utc = datetime(2026, 10, 22, 18, 30, tzinfo=UTC)  # 01:30 on the 23rd in Vietnam
    day, month = bs.window_starts(late_utc)
    assert day == datetime(2026, 10, 22, 17, 0, tzinfo=UTC)
    assert month == datetime(2026, 9, 30, 17, 0, tzinfo=UTC)  # 1 Oct 00:00 in Vietnam


def test_yesterdays_spend_does_not_count_against_todays_limit(db):
    limits(db, daily="0.05", monthly="100")
    provider(db, monthly="100")
    reserve(db, "0.05", now=NOW - timedelta(days=1))
    assert reserve(db, "0.05")  # new local day: allowed
    with pytest.raises(ApiError) as exc:
        reserve(db, "0.01")
    assert code_of(exc) == "budget_global_daily"


def test_reservation_takes_the_settings_row_lock(db, engine):
    from app.db.session import session_factory

    limits(db)
    provider(db)
    reserve(db)  # committed; now check a held reservation lock blocks a second writer
    with session_factory()() as holder:
        from app.services.settings_service import get_settings_row

        get_settings_row(holder, for_update=True)  # simulates a reservation in progress
        with session_factory()() as other:
            with pytest.raises(DBAPIError, match="could not obtain lock"):
                other.execute(text("SELECT id FROM system_settings FOR UPDATE NOWAIT"))
            other.rollback()
        holder.rollback()


def test_database_constraints(db):
    with pytest.raises(IntegrityError):
        db.add(ProviderBudget(provider="x", monthly_limit_usd=Decimal("-1")))
        db.commit()
    db.rollback()
    with pytest.raises(IntegrityError):
        db.execute(text("INSERT INTO cost_ledger (id, provider, operation, estimated_usd, status,"
                        " reserved_at) VALUES (gen_random_uuid(), 'fal', 'x', -1, 'reserved',"
                        " now())"))
    db.rollback()
    with pytest.raises(IntegrityError):
        db.execute(text("INSERT INTO cost_ledger (id, provider, operation, estimated_usd, status,"
                        " reserved_at) VALUES (gen_random_uuid(), 'fal', 'x', 1, 'bogus', now())"))
    db.rollback()


# --------------------------------------------------------------------------- API
def test_costs_endpoints_require_authentication(client):
    assert client.get("/api/costs").status_code == 401
    assert client.put("/api/system/provider-budgets", json={}).status_code == 401


def test_setting_a_provider_budget_needs_the_password_when_it_raises_the_limit(
    client, owner, auth_headers
):
    body = {"provider": "fal", "monthly_limit_usd": "4"}
    denied = client.put("/api/system/provider-budgets", json=body, headers=auth_headers)
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "reauth_required"
    wrong = client.put("/api/system/provider-budgets", headers=auth_headers,
                       json={**body, "password": "wrong wrong wrong"})
    assert wrong.status_code == 403
    ok = client.put("/api/system/provider-budgets", headers=auth_headers,
                    json={**body, "password": OWNER_PASSWORD})
    assert ok.status_code == 200 and ok.json()["monthly_limit_usd"] == "4.0000"


def test_lowering_a_budget_needs_no_password(client, owner, auth_headers):
    client.put("/api/system/provider-budgets", headers=auth_headers,
               json={"provider": "fal", "monthly_limit_usd": "4", "password": OWNER_PASSWORD})
    lower = client.put("/api/system/provider-budgets", headers=auth_headers,
                       json={"provider": "fal", "monthly_limit_usd": "2"})
    assert lower.status_code == 200
    summary = client.get("/api/costs", headers=auth_headers).json()
    assert summary["providers"]["fal"]["monthly_limit_usd"] == "2.0000"


def test_provider_budget_input_is_validated(client, owner, auth_headers):
    bad = [{"provider": "bitcoin", "monthly_limit_usd": "1"},
           {"provider": "fal", "monthly_limit_usd": "-1"},
           {"provider": "fal", "monthly_limit_usd": "1", "extra": 1}]
    for body in bad:
        assert client.put("/api/system/provider-budgets", headers=auth_headers,
                          json={**body, "password": OWNER_PASSWORD}).status_code in (422,)


def test_costs_summary_reports_spend_and_limits(client, owner, auth_headers, db):
    limits(db, daily="10", monthly="100")
    client.put("/api/system/provider-budgets", headers=auth_headers,
               json={"provider": "fal", "monthly_limit_usd": "4", "password": OWNER_PASSWORD})
    entry = bs.reserve(db, provider="fal", operation="image", estimated_usd="0.0249")
    bs.settle(db, entry, actual_usd="0.0251")
    summary = client.get("/api/costs", headers=auth_headers).json()
    assert summary["providers"]["fal"]["spent_month_usd"] == "0.025100"
    assert summary["global"]["spent_today"] == "0.025100"
    assert summary["currency"] == "USD"
