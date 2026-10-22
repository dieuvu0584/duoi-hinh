"""Runs the content pipeline for items that are mid-flight (called by the worker loop).

Safe under repetition: every stage is resumable and never pays twice. An item that cannot proceed
(budget, missing voice, provider error) is retried later with a back-off instead of every tick, so
a long wait does not flood the audit log. Nothing runs while generation is paused.
"""

import copy
import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.db.models import ContentItem
from app.services import content_pipeline as cp
from app.services.settings_service import get_settings_row
from app.services.state_machine import ContentState as CS

log = logging.getLogger(__name__)

ACTIVE = {CS.DRAFT, CS.ANALYZING, CS.READY_FOR_GENERATION, CS.NEEDS_REWORK, CS.GENERATING,
          CS.RENDERING, CS.QA_PENDING, CS.FINAL_SAFETY_PENDING}
BACKOFF = {"waiting: budget": timedelta(minutes=15), "waiting": timedelta(minutes=5),
           "provider_error": timedelta(minutes=5)}


def _backoff_for(note: str) -> timedelta | None:
    if note.startswith("waiting: budget"):
        return BACKOFF["waiting: budget"]
    if note.startswith("waiting"):
        return BACKOFF["waiting"]
    if note.startswith("provider_error"):
        return BACKOFF["provider_error"]
    return None


def _due(item: ContentItem, now: datetime) -> bool:
    retry_after = (item.pipeline_input or {}).get("retry_after")
    return not retry_after or datetime.fromisoformat(retry_after) <= now


def _set_retry(session: Session, item: ContentItem, when: datetime | None) -> None:
    data = copy.deepcopy(item.pipeline_input or {})
    if when is None:
        data.pop("retry_after", None)
    else:
        data["retry_after"] = when.isoformat()
    item.pipeline_input = data
    session.commit()


def clear_retry(session: Session, item: ContentItem) -> None:
    """Let the owner say 'try again now' (e.g. after raising a budget)."""
    _set_retry(session, item, None)


async def run_pending(
    session: Session, deps: cp.PipelineDeps, *, now: datetime | None = None, limit: int = 2,
) -> list[str]:
    """Advance up to `limit` active items; returns short notes for the log."""
    now = now or utcnow()
    if get_settings_row(session).generation_paused:
        session.rollback()
        return ["generation_paused"]
    session.rollback()
    ids = session.scalars(
        select(ContentItem.id).where(ContentItem.status.in_([s.value for s in ACTIVE]))
        .order_by(ContentItem.created_at).limit(50)).all()
    notes: list[str] = []
    worked = 0
    for item_id in ids:
        if worked >= limit:
            break
        item = session.get(ContentItem, item_id, populate_existing=True)
        if item is None or not _due(item, now):
            session.rollback()
            continue
        worked += 1
        try:
            outcomes = await cp.run_until_wait(session, deps, item_id)
        except Exception as exc:  # noqa: BLE001 - one bad item must not stop the others
            session.rollback()
            log.exception("pipeline: unexpected error on %s", item_id)
            notes.append(f"{item_id}:unexpected:{type(exc).__name__}")
            item = session.get(ContentItem, item_id, populate_existing=True)
            _set_retry(session, item, now + BACKOFF["provider_error"])
            continue
        last = outcomes[-1].note if outcomes else ""
        item = session.get(ContentItem, item_id, populate_existing=True)
        delay = _backoff_for(last)
        _set_retry(session, item, now + delay if delay else None)
        notes.append(f"{item_id}:{item.status}:{last or 'ok'}")
    return notes
