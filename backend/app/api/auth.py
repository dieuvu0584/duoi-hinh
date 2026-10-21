import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, current_owner, current_session, get_settings
from app.core.config import Settings
from app.db.models import Device, OwnerAccount
from app.db.session import get_session
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    device_label: str = Field(default="unnamed", max_length=100)


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh_token: str = Field(min_length=20, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime
    device_id: uuid.UUID
    token_type: str = "Bearer"


class DeviceOut(BaseModel):
    id: uuid.UUID
    label: str
    created_at: datetime
    last_seen_at: datetime | None
    revoked_at: datetime | None


def _tokens(pair: auth_service.TokenPair) -> TokenResponse:
    return TokenResponse(**pair.__dict__)


@router.post("/login", response_model=TokenResponse)
def login(
    body: LoginRequest,
    request: Request,
    session: Session = Depends(get_session),
    cfg: Settings = Depends(get_settings),
):
    pair = auth_service.login(
        session, cfg, username=body.username, password=body.password,
        device_label=body.device_label, ip=client_ip(request),
    )
    return _tokens(pair)


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    body: RefreshRequest,
    request: Request,
    session: Session = Depends(get_session),
    cfg: Settings = Depends(get_settings),
):
    pair = auth_service.refresh(
        session, cfg, refresh_token=body.refresh_token, ip=client_ip(request)
    )
    return _tokens(pair)


@router.post("/logout", status_code=204)
def logout(ctx=Depends(current_session)):
    session, owner, device = ctx
    auth_service.revoke_device(session, owner, device.id, "logout")


@router.get("/devices", response_model=list[DeviceOut])
def devices(
    owner: OwnerAccount = Depends(current_owner), session: Session = Depends(get_session)
):
    rows = session.scalars(
        select(Device).where(Device.owner_id == owner.id).order_by(Device.created_at.desc())
    )
    return [
        DeviceOut(
            id=d.id, label=d.label, created_at=d.created_at, last_seen_at=d.last_seen_at,
            revoked_at=d.revoked_at,
        )
        for d in rows
    ]


@router.delete("/devices/{device_id}", status_code=204)
def delete_device(
    device_id: uuid.UUID,
    owner: OwnerAccount = Depends(current_owner),
    session: Session = Depends(get_session),
):
    auth_service.revoke_device(session, owner, device_id, "revoked_by_owner")
