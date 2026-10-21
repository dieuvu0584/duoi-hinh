from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.db.models import OwnerAccount, SystemSettings
from app.services.audit import record_audit

EDITABLE_FIELDS = {
    "automation_enabled": bool,
    "generation_paused": bool,
    "news_lane_enabled": bool,
    "daily_budget_limit": Decimal,
    "monthly_budget_limit": Decimal,
    "max_image_regenerations": int,
    "max_publishes_per_day": int,
    "max_lateness_minutes": int,
    "prepare_lead_minutes": int,
}
# Changes that widen what the system may do need password confirmation (re-auth).
REAUTH_FIELDS = {"news_lane_enabled", "daily_budget_limit", "monthly_budget_limit",
                 "automation_enabled"}


def get_settings_row(session: Session, *, for_update: bool = False) -> SystemSettings:
    # populate_existing: never trust a cached row for kill switch / limits.
    stmt = (
        select(SystemSettings).where(SystemSettings.id == 1)
        .execution_options(populate_existing=True)
    )
    if for_update:
        stmt = stmt.with_for_update()
    row = session.scalar(stmt)
    if row is None:
        # Missing row means a broken install: refuse rather than invent permissive defaults.
        raise ApiError(503, "settings_missing", "System settings are not initialised")
    return row


def snapshot(row: SystemSettings) -> dict:
    return {
        "automation_enabled": row.automation_enabled,
        "generation_paused": row.generation_paused,
        "publishing_kill_switch": row.publishing_kill_switch,
        "news_lane_enabled": row.news_lane_enabled,
        "daily_budget_limit": str(row.daily_budget_limit),
        "monthly_budget_limit": str(row.monthly_budget_limit),
        "budget_currency": row.budget_currency,
        "max_image_regenerations": row.max_image_regenerations,
        "max_publishes_per_day": row.max_publishes_per_day,
        "max_lateness_minutes": row.max_lateness_minutes,
        "prepare_lead_minutes": row.prepare_lead_minutes,
        "updated_at": row.updated_at.isoformat(),
    }


def stop_publishing(session: Session, owner: OwnerAccount) -> SystemSettings:
    """Turning the kill switch ON never needs re-auth: stopping must always be quick."""
    row = get_settings_row(session, for_update=True)
    changed = not row.publishing_kill_switch
    row.publishing_kill_switch = True
    record_audit(session, actor=f"owner:{owner.id}", action="settings.kill_switch.on",
                 entity_type="system_settings", entity_id="1", metadata={"changed": changed})
    session.commit()
    return row


def resume_publishing(session: Session, owner: OwnerAccount) -> SystemSettings:
    """Caller must have verified the password first (see auth_service.verify_owner_password)."""
    row = get_settings_row(session, for_update=True)
    row.publishing_kill_switch = False
    record_audit(session, actor=f"owner:{owner.id}", action="settings.kill_switch.off",
                 entity_type="system_settings", entity_id="1")
    session.commit()
    return row


def update_settings(session: Session, owner: OwnerAccount, changes: dict) -> SystemSettings:
    unknown = set(changes) - set(EDITABLE_FIELDS)
    if unknown:
        raise ApiError(422, "unknown_setting", "Unknown or read-only setting",
                       fields=[{"field": f, "issue": "not_editable"} for f in sorted(unknown)])
    row = get_settings_row(session, for_update=True)
    before = {k: getattr(row, k) for k in changes}
    for key, value in changes.items():
        setattr(row, key, EDITABLE_FIELDS[key](value) if value is not None else value)
    record_audit(session, actor=f"owner:{owner.id}", action="settings.update",
                 entity_type="system_settings", entity_id="1",
                 metadata={"before": {k: str(v) for k, v in before.items()},
                           "after": {k: str(v) for k, v in changes.items()}})
    session.commit()
    return row
