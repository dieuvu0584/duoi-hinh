import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import jwt
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ApiError
from app.db.base import utcnow
from app.db.models import Device, LoginAttempt, OwnerAccount
from app.services import security
from app.services.audit import record_audit

MIN_PASSWORD_LENGTH = 12


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime
    device_id: uuid.UUID


def _unauthorized(code: str = "invalid_credentials") -> ApiError:
    return ApiError(401, code, "Authentication failed", headers={"WWW-Authenticate": "Bearer"})


def create_owner(session: Session, username: str, password: str) -> OwnerAccount:
    """Bootstrap the single owner (CLI only). Refuses a second account."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if not username.strip():
        raise ValueError("username is required")
    if session.scalar(select(func.count()).select_from(OwnerAccount)):
        raise ValueError("an owner account already exists")
    owner = OwnerAccount(username=username.strip(), password_hash=security.hash_password(password))
    session.add(owner)
    session.flush()
    record_audit(session, actor="system", action="owner.create", entity_type="owner",
                 entity_id=str(owner.id))
    return owner


def set_password(session: Session, username: str, new_password: str) -> None:
    """Server-side recovery (CLI). Revokes every device so old sessions cannot survive."""
    if len(new_password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    owner = session.scalar(select(OwnerAccount).where(OwnerAccount.username == username))
    if owner is None:
        raise ValueError("unknown user")
    owner.password_hash = security.hash_password(new_password)
    now = utcnow()
    for device in session.scalars(select(Device).where(Device.owner_id == owner.id,
                                                       Device.revoked_at.is_(None))):
        device.revoked_at = now
        device.revoked_reason = "password_reset"
        device.refresh_token_hash = None
    record_audit(session, actor="system", action="owner.password_reset", entity_type="owner",
                 entity_id=str(owner.id))


def _recent_failures(session: Session, settings: Settings, username: str, now: datetime) -> int:
    window_start = now - timedelta(minutes=settings.login_window_minutes)
    last_success = session.scalar(
        select(func.max(LoginAttempt.attempted_at)).where(
            LoginAttempt.username == username, LoginAttempt.success.is_(True)
        )
    )
    since = max(window_start, last_success) if last_success else window_start
    return session.scalar(
        select(func.count()).select_from(LoginAttempt).where(
            LoginAttempt.username == username,
            LoginAttempt.success.is_(False),
            LoginAttempt.attempted_at > since,
        )
    ) or 0


def _issue(settings: Settings, owner: OwnerAccount, device: Device, now: datetime) -> TokenPair:
    refresh = security.new_refresh_token()
    device.previous_refresh_token_hash = device.refresh_token_hash
    device.refresh_token_hash = security.hash_token(refresh)
    device.refresh_expires_at = now + timedelta(days=settings.refresh_token_ttl_days)
    access, access_exp = security.issue_access_token(settings, owner.id, device.id, now)
    return TokenPair(access, access_exp, refresh, device.refresh_expires_at, device.id)


def login(
    session: Session, settings: Settings, *, username: str, password: str,
    device_label: str, ip: str | None, now: datetime | None = None,
) -> TokenPair:
    now = now or utcnow()
    username = username.strip()
    if _recent_failures(session, settings, username, now) >= settings.login_max_failures:
        record_audit(session, actor="system", action="auth.login", result="denied",
                     metadata={"reason": "rate_limited", "username": username, "ip": ip})
        session.commit()
        raise ApiError(429, "rate_limited", "Too many attempts, try again later",
                       headers={"Retry-After": str(settings.login_window_minutes * 60)})

    owner = session.scalar(select(OwnerAccount).where(OwnerAccount.username == username))
    ok = security.verify_password(password, owner.password_hash if owner else None)
    ok = ok and owner is not None and owner.disabled_at is None
    session.add(LoginAttempt(username=username, ip=ip, success=ok, attempted_at=now))
    if not ok:
        record_audit(session, actor="system", action="auth.login", result="denied",
                     metadata={"reason": "bad_credentials", "username": username, "ip": ip})
        session.commit()
        raise _unauthorized()
    assert owner is not None

    if security.needs_rehash(owner.password_hash):
        owner.password_hash = security.hash_password(password)
    device = Device(owner_id=owner.id, label=(device_label or "unnamed")[:100], last_ip=ip,
                    last_seen_at=now)
    session.add(device)
    session.flush()
    pair = _issue(settings, owner, device, now)
    record_audit(session, actor=f"owner:{owner.id}", action="auth.login", entity_type="device",
                 entity_id=str(device.id), metadata={"ip": ip, "label": device.label})
    session.commit()
    return pair


def refresh(
    session: Session, settings: Settings, *, refresh_token: str, ip: str | None,
    now: datetime | None = None,
) -> TokenPair:
    now = now or utcnow()
    digest = security.hash_token(refresh_token)

    device = session.scalar(
        select(Device).where(Device.refresh_token_hash == digest).with_for_update()
    )
    if device is None:
        # A token that was already rotated away: treat as theft and kill that device's session.
        stale = session.scalar(
            select(Device).where(Device.previous_refresh_token_hash == digest).with_for_update()
        )
        if stale is not None and stale.revoked_at is None:
            stale.revoked_at = now
            stale.revoked_reason = "refresh_reuse"
            stale.refresh_token_hash = None
            record_audit(session, actor="system", action="auth.refresh_reuse", result="denied",
                         entity_type="device", entity_id=str(stale.id), metadata={"ip": ip})
            session.commit()
        raise _unauthorized("invalid_refresh_token")

    owner = session.get(OwnerAccount, device.owner_id)
    if (
        device.revoked_at is not None
        or device.refresh_expires_at is None
        or device.refresh_expires_at <= now
        or owner is None
        or owner.disabled_at is not None
    ):
        raise _unauthorized("invalid_refresh_token")

    device.last_seen_at = now
    device.last_ip = ip
    pair = _issue(settings, owner, device, now)
    record_audit(session, actor=f"owner:{owner.id}", action="auth.refresh", entity_type="device",
                 entity_id=str(device.id))
    session.commit()
    return pair


def authenticate_access(
    session: Session, settings: Settings, token: str
) -> tuple[OwnerAccount, Device]:
    try:
        payload = security.decode_access_token(settings, token)
        owner_id, device_id = uuid.UUID(payload["sub"]), uuid.UUID(payload["dev"])
    except (jwt.PyJWTError, ValueError, KeyError):
        raise _unauthorized("invalid_token") from None
    device = session.get(Device, device_id)
    owner = session.get(OwnerAccount, owner_id)
    if (
        device is None or owner is None or device.owner_id != owner.id
        or device.revoked_at is not None or owner.disabled_at is not None
    ):
        raise _unauthorized("invalid_token")
    return owner, device


def verify_owner_password(
    session: Session, owner: OwnerAccount, password: str, ip: str | None
) -> None:
    """Re-authentication for dangerous actions (e.g. resuming publishing)."""
    if not security.verify_password(password, owner.password_hash):
        record_audit(session, actor=f"owner:{owner.id}", action="auth.reauth", result="denied",
                     metadata={"ip": ip})
        session.commit()
        raise ApiError(403, "reauth_failed", "Password confirmation failed")


def revoke_device(session: Session, owner: OwnerAccount, device_id: uuid.UUID, reason: str) -> None:
    device = session.get(Device, device_id)
    if device is None or device.owner_id != owner.id:
        raise ApiError(404, "not_found", "Device not found")
    if device.revoked_at is None:
        device.revoked_at = utcnow()
        device.revoked_reason = reason
        device.refresh_token_hash = None
        record_audit(session, actor=f"owner:{owner.id}", action="device.revoke",
                     entity_type="device", entity_id=str(device.id), metadata={"reason": reason})
    session.commit()
