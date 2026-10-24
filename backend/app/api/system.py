from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import client_ip, current_owner
from app.core.errors import ApiError
from app.db.models import OwnerAccount
from app.db.session import get_session
from app.services import auth_service, settings_service

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/health")
def health(session: Session = Depends(get_session)):
    """Public and minimal on purpose: reports dependency status, never details or secrets."""
    try:
        session.execute(text("SELECT 1"))
        database = "ok"
    except Exception:
        database = "unavailable"
    if database != "ok":
        raise ApiError(
            503, "degraded", "Service degraded", fields=[{"field": "database", "issue": database}]
        )
    return {
        "status": "ok",
        "service": "api",
        "dependencies": {"database": database, "redis": "not_checked"},
    }


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    automation_enabled: bool | None = None
    generation_paused: bool | None = None
    news_lane_enabled: bool | None = None
    daily_budget_limit: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=4)
    monthly_budget_limit: Decimal | None = Field(
        default=None, ge=0, max_digits=12, decimal_places=4
    )
    max_image_regenerations: int | None = Field(default=None, ge=0, le=20)
    max_publishes_per_day: int | None = Field(default=None, ge=0, le=30)
    max_lateness_minutes: int | None = Field(default=None, ge=1, le=1440)
    prepare_lead_minutes: int | None = Field(default=None, ge=5, le=240)
    # "" clears the value (the environment default applies again).
    facebook_page_id: str | None = Field(default=None, pattern=r"^(\d{5,30})?$")
    youtube_channel_id: str | None = Field(default=None, pattern=r"^(UC[A-Za-z0-9_-]{22})?$")
    elevenlabs_voice_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{0,80}$")
    scan_enabled: bool | None = None
    youtube_enabled: bool | None = None
    publish_on_approve: bool | None = None
    # Times of day in Vietnam time, e.g. ["10:00", "16:00"]; validated and sorted by the service.
    scan_times: list[str] | None = Field(default=None, max_length=8)


class SettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    changes: SettingsUpdate
    password: str | None = Field(default=None, max_length=256)


class PasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=256)


@router.get("/settings")
def get_settings_endpoint(
    owner: OwnerAccount = Depends(current_owner), session: Session = Depends(get_session)
):
    return settings_service.snapshot(settings_service.get_settings_row(session))


@router.put("/settings")
def put_settings(
    body: SettingsUpdateRequest,
    request: Request,
    owner: OwnerAccount = Depends(current_owner),
    session: Session = Depends(get_session),
):
    changes = body.changes.model_dump(exclude_unset=True)
    if not changes:
        raise ApiError(422, "no_changes", "No settings provided")
    if any(v is None for v in changes.values()):
        raise ApiError(422, "null_not_allowed", "Settings cannot be set to null")
    if settings_service.REAUTH_FIELDS & set(changes):
        if not body.password:
            raise ApiError(403, "reauth_required", "Password confirmation required")
        auth_service.verify_owner_password(session, owner, body.password, client_ip(request))
    row = settings_service.update_settings(session, owner, changes)
    return settings_service.snapshot(row)


@router.post("/publishing-stop")
def publishing_stop(
    owner: OwnerAccount = Depends(current_owner), session: Session = Depends(get_session)
):
    return settings_service.snapshot(settings_service.stop_publishing(session, owner))


@router.post("/publishing-resume")
def publishing_resume(
    body: PasswordRequest,
    request: Request,
    owner: OwnerAccount = Depends(current_owner),
    session: Session = Depends(get_session),
):
    auth_service.verify_owner_password(session, owner, body.password, client_ip(request))
    return settings_service.snapshot(settings_service.resume_publishing(session, owner))
