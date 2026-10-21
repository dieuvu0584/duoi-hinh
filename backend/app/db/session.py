from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

_engine = None
_factory: sessionmaker[Session] | None = None


def get_engine():
    global _engine, _factory
    if _engine is None:
        _engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
        _factory = sessionmaker(_engine, expire_on_commit=False)
    return _engine


def session_factory() -> sessionmaker[Session]:
    get_engine()
    assert _factory is not None
    return _factory


def get_session() -> Iterator[Session]:
    """FastAPI dependency. Commit is explicit in services/routes; rollback on error."""
    with session_factory()() as session:
        try:
            yield session
        except Exception:
            session.rollback()
            raise
