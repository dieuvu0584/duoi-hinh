import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import Settings
from app.db.base import utcnow

_hasher = PasswordHasher()  # argon2id with the library's current recommended parameters
# Verified against when the username does not exist, so timing does not reveal valid usernames.
_DUMMY_HASH = _hasher.hash("not-a-real-password")

JWT_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def tokens_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


def issue_access_token(
    settings: Settings, owner_id: uuid.UUID, device_id: uuid.UUID, now: datetime | None = None
) -> tuple[str, datetime]:
    now = now or utcnow()
    expires = now + timedelta(minutes=settings.access_token_ttl_minutes)
    payload = {
        "sub": str(owner_id),
        "dev": str(device_id),
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "typ": "access",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALGORITHM), expires


def decode_access_token(settings: Settings, token: str) -> dict:
    """Raises jwt.PyJWTError for any invalid, expired or wrongly-typed token."""
    payload = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[JWT_ALGORITHM],
        options={"require": ["exp", "iat", "sub", "dev"]},
    )
    if payload.get("typ") != "access":
        raise jwt.InvalidTokenError("wrong token type")
    return payload
