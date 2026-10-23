"""Provider token health: the publish gate refuses to publish unless a recent check passed.

`refresh_if_due` is called by the worker; it asks each provider whether its credential still works
and records the answer in `token_health` (`meta` for Facebook, `youtube` for YouTube). A failed or
missing check keeps publishing blocked (fail closed). Nothing here ever stores a token.
"""

import logging
from collections.abc import Mapping
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.db.models import TokenHealth
from app.services.publish_gate import TOKEN_PROVIDER

log = logging.getLogger(__name__)
CHECK_EVERY = timedelta(minutes=10)  # the gate accepts a result up to one hour old


def has_valid_token(
    session: Session, platform: str, *, now: datetime | None = None
) -> bool:
    """True when the latest check for this platform passed less than an hour ago."""
    now = now or utcnow()
    row = session.scalar(
        select(TokenHealth).where(TokenHealth.provider == TOKEN_PROVIDER.get(platform, platform))
        .order_by(TokenHealth.checked_at.desc()).limit(1)
        .execution_options(populate_existing=True))
    return bool(row and row.valid and now - row.checked_at < timedelta(hours=1))


async def refresh_if_due(
    session: Session, providers: Mapping[str, object], *, now: datetime | None = None,
    force: bool = False,
) -> dict[str, bool]:
    """Check every provider whose last result is older than `CHECK_EVERY`; returns the results."""
    now = now or utcnow()
    results: dict[str, bool] = {}
    for platform, provider in providers.items():
        name = TOKEN_PROVIDER.get(platform, platform)
        last = session.scalar(
            select(TokenHealth).where(TokenHealth.provider == name)
            .order_by(TokenHealth.checked_at.desc()).limit(1)
            .execution_options(populate_existing=True))
        if not force and last is not None and last.valid and now - last.checked_at < CHECK_EVERY:
            continue
        try:
            valid = bool(await provider.check_token())
            reason = getattr(provider, "last_problem", None) or "token_rejected"
            error = None if valid else reason[:60]
        except Exception as exc:  # noqa: BLE001 - a broken check must read as "not valid"
            valid, error = False, type(exc).__name__[:60]
        session.add(TokenHealth(provider=name, valid=valid, checked_at=now, error=error))
        session.commit()
        results[name] = valid
        if not valid:
            log.warning("token health: %s is NOT valid (%s)", name, error)
    return results
