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
    "facebook_page_id": str,
    "youtube_channel_id": str,
    "elevenlabs_voice_id": str,
    "scan_enabled": bool,
    "youtube_enabled": bool,
    "publish_on_approve": bool,
    "scan_times": list,
}
# Text settings: "" clears the value (the environment value then applies again).
TEXT_FIELDS = {"facebook_page_id", "youtube_channel_id", "elevenlabs_voice_id"}
# Changes that widen what the system may do need password confirmation (re-auth).
# Changing the publishing target is one of them: it decides which Page/channel receives posts.
REAUTH_FIELDS = {"news_lane_enabled", "daily_budget_limit", "monthly_budget_limit",
                 "automation_enabled", "facebook_page_id", "youtube_channel_id", "youtube_enabled",
                 "publish_on_approve"}


def normalise_scan_times(values: list[str]) -> list[str]:
    """Valid, unique, sorted "HH:MM" strings (Vietnam time); at most 8 per day."""
    out: set[str] = set()
    for raw in values:
        try:
            hour, minute = (int(x) for x in str(raw).split(":"))
        except ValueError:
            hour = minute = -1
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ApiError(422, "bad_scan_time", "Scan times must look like 10:00",
                           fields=[{"field": "scan_times", "issue": "invalid_time"}])
        out.add(f"{hour:02d}:{minute:02d}")
    if len(out) > 8:
        raise ApiError(422, "too_many_scan_times", "At most 8 scan times per day")
    return sorted(out)


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


def env_defaults(cfg=None) -> dict:
    """Identifiers (never secrets) that apply while the owner has not set their own value."""
    if cfg is None:
        from app.core.config import settings as cfg
    return {"facebook_page_id": cfg.meta_page_id or None,
            "youtube_channel_id": cfg.youtube_channel_id or None,
            "elevenlabs_voice_id": cfg.elevenlabs_voice_id or None}


def snapshot(row: SystemSettings, cfg=None) -> dict:
    defaults = env_defaults(cfg)
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
        "facebook_page_id": row.facebook_page_id,
        "youtube_channel_id": row.youtube_channel_id,
        "elevenlabs_voice_id": row.elevenlabs_voice_id,
        "scan_enabled": row.scan_enabled,
        "youtube_enabled": row.youtube_enabled,
        "publish_on_approve": row.publish_on_approve,
        "scan_times": list(row.scan_times or []),
        # What is actually used: the owner's value, else the .env default.
        "env_defaults": defaults,
        "effective": {k: getattr(row, k) or defaults[k] for k in defaults},
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
        if key in TEXT_FIELDS:
            setattr(row, key, str(value).strip() or None)
        elif key == "scan_times":
            setattr(row, key, normalise_scan_times(value))
        else:
            setattr(row, key, EDITABLE_FIELDS[key](value) if value is not None else value)
    record_audit(session, actor=f"owner:{owner.id}", action="settings.update",
                 entity_type="system_settings", entity_id="1",
                 metadata={"before": {k: str(v) for k, v in before.items()},
                           "after": {k: str(v) for k, v in changes.items()}})
    session.commit()
    return row


def effective_voice_id(row: SystemSettings, cfg) -> str | None:
    """The voice for NEW narration: the owner's setting, else the environment default."""
    return row.elevenlabs_voice_id or cfg.elevenlabs_voice_id or None


def effective_page_id(row: SystemSettings, cfg) -> str | None:
    """The Facebook Page that receives posts: the owner's setting, else the environment default."""
    return row.facebook_page_id or cfg.meta_page_id or None
