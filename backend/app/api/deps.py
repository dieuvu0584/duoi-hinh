from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import Settings, settings
from app.core.errors import ApiError
from app.db.models import Device, OwnerAccount
from app.db.session import get_session
from app.services import auth_service

_bearer = HTTPBearer(auto_error=False)


def get_settings() -> Settings:
    return settings


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def current_session(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: Session = Depends(get_session),
    cfg: Settings = Depends(get_settings),
) -> tuple[Session, OwnerAccount, Device]:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise ApiError(
            401, "not_authenticated", "Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    owner, device = auth_service.authenticate_access(session, cfg, credentials.credentials)
    return session, owner, device


def current_owner(
    ctx: tuple[Session, OwnerAccount, Device] = Depends(current_session),
) -> OwnerAccount:
    """Every admin route depends on this. A test asserts no /api route lacks it (except public)."""
    return ctx[1]
