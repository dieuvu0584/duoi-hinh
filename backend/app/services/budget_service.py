"""Spend control: reserve before a paid call, settle after (decision D-18).

Rules (all fail closed):
- A provider needs a `provider_budgets` row with a monthly limit > 0, otherwise it cannot spend.
- The global daily/monthly limits in `system_settings` (default 0) also apply to the sum of all
  providers. Every limit must allow the reservation.
- The reservation is written and COMMITTED before the external call, so a crash cannot hide
  spend. It is counted at its estimate until settled; a settled amount larger than the estimate is
  counted in full (never clamped). `release` is only for calls known to be uncharged and needs a
  reason.
- Reservations serialise on the settings row lock so two workers cannot overspend together.
Day and month boundaries use the owner's timezone (UTC+7, no DST).
"""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.db.base import utcnow
from app.db.models import CostLedger, ProviderBudget
from app.services.audit import record_audit
from app.services.settings_service import get_settings_row

LOCAL_TZ = timezone(timedelta(hours=7))  # Asia/Ho_Chi_Minh
_ZERO = Decimal("0")


def window_starts(now: datetime) -> tuple[datetime, datetime]:
    """(start of local day, start of local month) as UTC-aware datetimes."""
    local = now.astimezone(LOCAL_TZ)
    day = local.replace(hour=0, minute=0, second=0, microsecond=0)
    month = day.replace(day=1)
    return day.astimezone(UTC), month.astimezone(UTC)


def spent(session: Session, *, since: datetime, provider: str | None = None) -> Decimal:
    stmt = select(func.coalesce(func.sum(func.coalesce(CostLedger.actual_usd,
                                                       CostLedger.estimated_usd)), 0)).where(
        CostLedger.reserved_at >= since, CostLedger.status != "released")
    if provider is not None:
        stmt = stmt.where(CostLedger.provider == provider)
    return Decimal(session.scalar(stmt) or 0)


def _deny(session: Session, code: str, message: str, *, provider: str, meta: dict) -> ApiError:
    record_audit(session, actor="system", action="budget.reserve", result="denied",
                 entity_type="provider", entity_id=provider, metadata={"reason": code, **meta})
    session.commit()
    return ApiError(402, code, message)


def reserve(
    session: Session, *, provider: str, operation: str, estimated_usd: Decimal | float | str,
    entity_type: str | None = None, entity_id: str | None = None,
    idempotency_key: str | None = None, now: datetime | None = None,
) -> CostLedger:
    """Reserve spend and commit it. Raises 402 `budget_*` when any limit would be exceeded."""
    now = now or utcnow()
    estimate = Decimal(str(estimated_usd))
    if estimate < _ZERO:
        raise ValueError("estimated_usd must not be negative")

    # Serialise concurrent reservations; also gives us fresh settings values.
    settings_row = get_settings_row(session, for_update=True)

    if idempotency_key:
        existing = session.scalar(
            select(CostLedger).where(CostLedger.idempotency_key == idempotency_key))
        if existing is not None:
            session.commit()
            return existing  # a retried command never reserves twice

    day, month = window_starts(now)
    budget = session.get(ProviderBudget, provider)
    if budget is None or budget.monthly_limit_usd <= _ZERO:
        raise _deny(session, "budget_not_set", "No spending budget is set for this provider",
                    provider=provider, meta={"operation": operation})

    checks = [
        ("budget_global_daily", settings_row.daily_budget_limit, spent(session, since=day)),
        ("budget_global_monthly", settings_row.monthly_budget_limit, spent(session, since=month)),
        ("budget_provider_monthly", budget.monthly_limit_usd,
         spent(session, since=month, provider=provider)),
    ]
    if budget.daily_limit_usd is not None:
        checks.append(("budget_provider_daily", budget.daily_limit_usd,
                       spent(session, since=day, provider=provider)))
    for code, limit, already in checks:
        if already + estimate > limit:
            raise _deny(session, code, "Spending limit reached", provider=provider,
                        meta={"operation": operation, "limit": str(limit),
                              "already": str(already), "estimate": str(estimate)})

    entry = CostLedger(provider=provider, operation=operation, entity_type=entity_type,
                       entity_id=entity_id, idempotency_key=idempotency_key,
                       estimated_usd=estimate, status="reserved", reserved_at=now)
    session.add(entry)
    record_audit(session, actor="system", action="budget.reserve", entity_type="provider",
                 entity_id=provider, metadata={"operation": operation, "estimate": str(estimate)})
    session.commit()
    return entry


def settle(
    session: Session, entry: CostLedger, *, actual_usd: Decimal | float | str,
    request_id: str | None = None, now: datetime | None = None,
) -> CostLedger:
    """Record the real cost (taken from the provider's response). Larger-than-estimate is kept."""
    actual = Decimal(str(actual_usd))
    if actual < _ZERO:
        raise ValueError("actual_usd must not be negative")
    entry.actual_usd = actual
    entry.status = "settled"
    entry.settled_at = now or utcnow()
    if request_id:
        entry.request_id = request_id
    session.commit()
    return entry


def release(session: Session, entry: CostLedger, *, reason: str) -> CostLedger:
    """Give back a reservation for a call that is KNOWN not to have been charged."""
    if not reason.strip():
        raise ValueError("a reason is required to release a reservation")
    entry.actual_usd = _ZERO
    entry.status = "released"
    entry.note = reason
    record_audit(session, actor="system", action="budget.release", entity_type="provider",
                 entity_id=entry.provider, metadata={"reason": reason, "ledger_id": str(entry.id)})
    session.commit()
    return entry


def summary(session: Session, *, now: datetime | None = None) -> dict:
    now = now or utcnow()
    day, month = window_starts(now)
    settings_row = get_settings_row(session)
    providers = {}
    for b in session.scalars(select(ProviderBudget).order_by(ProviderBudget.provider)):
        providers[b.provider] = {
            "monthly_limit_usd": str(b.monthly_limit_usd),
            "daily_limit_usd": None if b.daily_limit_usd is None else str(b.daily_limit_usd),
            "spent_today_usd": str(spent(session, since=day, provider=b.provider)),
            "spent_month_usd": str(spent(session, since=month, provider=b.provider)),
        }
    return {
        "currency": "USD",
        "global": {
            "daily_limit": str(settings_row.daily_budget_limit),
            "monthly_limit": str(settings_row.monthly_budget_limit),
            "spent_today": str(spent(session, since=day)),
            "spent_month": str(spent(session, since=month)),
        },
        "providers": providers,
    }


def set_provider_budget(
    session: Session, *, provider: str, monthly_limit_usd: Decimal,
    daily_limit_usd: Decimal | None, actor: str,
) -> ProviderBudget:
    row = session.get(ProviderBudget, provider)
    before = None if row is None else (str(row.monthly_limit_usd),
                                       None if row.daily_limit_usd is None
                                       else str(row.daily_limit_usd))
    if row is None:
        row = ProviderBudget(provider=provider)
        session.add(row)
    row.monthly_limit_usd = monthly_limit_usd
    row.daily_limit_usd = daily_limit_usd
    record_audit(session, actor=actor, action="budget.provider.set", entity_type="provider",
                 entity_id=provider,
                 metadata={"before": before, "after": [str(monthly_limit_usd),
                                                       None if daily_limit_usd is None
                                                       else str(daily_limit_usd)]})
    session.commit()
    session.refresh(row)  # return what the database stored (fixed-scale decimals)
    return row


def raises_limit(session: Session, provider: str, monthly: Decimal, daily: Decimal | None) -> bool:
    """True if the change increases what can be spent (needs password confirmation)."""
    row = session.get(ProviderBudget, provider)
    if row is None:
        return monthly > _ZERO
    if monthly > row.monthly_limit_usd:
        return True
    if daily is None and row.daily_limit_usd is not None:
        return True  # removing a daily cap widens spending
    return daily is not None and row.daily_limit_usd is not None and daily > row.daily_limit_usd
