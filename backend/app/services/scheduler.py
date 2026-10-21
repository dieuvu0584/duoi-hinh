"""Scheduler tick: moves due publishing records forward (docs/workflow.md, publishing states).

One tick, run by the worker loop (app/worker.py) or on demand:
  1. SCHEDULED records whose `scheduled_for - prepare_lead` has passed  -> prepare (draft)
     (a slot already older than `max_lateness` is skipped instead)
  2. READY_TO_PUBLISH records that are due                                -> publish_due (gate)
  3. UNKNOWN_RESULT records                                               -> reconcile (bounded)
It does nothing unless `automation_enabled` is true (default false). Failures of one record never
stop the others, and no exception escapes the tick. The kill switch is enforced inside
`publish_due` (the gate), so a tick can never bypass it.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ApiError
from app.db.base import utcnow
from app.db.models import ContentRevision, PublishingRecord
from app.providers.contracts import ReelsPublishingProvider
from app.providers.errors import ProviderError
from app.services import publishing_service as ps
from app.services.audit import record_audit
from app.services.media_paths import UnsafeMediaPath, resolve_media_path
from app.services.settings_service import get_settings_row
from app.services.state_machine import PublishState

log = logging.getLogger(__name__)
MAX_RECONCILE_ATTEMPTS = 5
BATCH = 20


@dataclass
class TickReport:
    skipped: str | None = None
    prepared: int = 0
    published: int = 0
    reconciled: int = 0
    blocked: int = 0
    errors: int = 0
    notes: list[str] = field(default_factory=list)


def build_description(caption: str | None, hashtags: list[str] | None) -> str:
    parts = [caption.strip()] if caption and caption.strip() else []
    if hashtags:
        parts.append(" ".join(hashtags))
    return "\n\n".join(parts)


async def tick(
    session: Session, provider: ReelsPublishingProvider, cfg: Settings, *,
    now: datetime | None = None,
) -> TickReport:
    now = now or utcnow()
    report = TickReport()
    settings_row = get_settings_row(session)
    if not settings_row.automation_enabled:
        session.rollback()
        report.skipped = "automation_disabled"
        return report
    lead = timedelta(minutes=settings_row.prepare_lead_minutes)
    lateness = timedelta(minutes=settings_row.max_lateness_minutes)
    session.rollback()

    recovered = ps.recover_stuck(session, now=now)
    if any(recovered.values()):
        report.notes.append(f"recovered:{recovered}")

    to_prepare = session.scalars(
        select(PublishingRecord.id).where(
            PublishingRecord.status == PublishState.SCHEDULED.value,
            PublishingRecord.scheduled_for <= now + lead,
        ).order_by(PublishingRecord.scheduled_for).limit(BATCH)).all()
    for record_id in to_prepare:
        await _guard(report, _prepare_one(session, provider, cfg, record_id, now, lateness))

    to_publish = session.scalars(
        select(PublishingRecord.id).where(
            PublishingRecord.status == PublishState.READY_TO_PUBLISH.value,
            PublishingRecord.scheduled_for <= now,
        ).order_by(PublishingRecord.scheduled_for).limit(BATCH)).all()
    for record_id in to_publish:
        await _guard(report, _publish_one(session, provider, record_id, now))

    unknown = session.scalars(
        select(PublishingRecord.id).where(
            PublishingRecord.status == PublishState.UNKNOWN_RESULT.value,
            PublishingRecord.reconcile_attempts < MAX_RECONCILE_ATTEMPTS,
        ).limit(BATCH)).all()
    for record_id in unknown:
        await _guard(report, _reconcile_one(session, provider, record_id))
    return report


async def _guard(report: TickReport, work) -> None:
    """Run one record's work; a failure is logged and counted, never raised."""
    try:
        kind = await work
        if kind:
            setattr(report, kind, getattr(report, kind) + 1)
    except ApiError as exc:  # e.g. another worker already moved the record (409)
        report.notes.append(exc.code)
    except ProviderError as exc:
        report.errors += 1
        report.notes.append(type(exc).__name__)
    except Exception as exc:  # noqa: BLE001 - the loop must survive any single failure
        report.errors += 1
        report.notes.append(f"unexpected:{type(exc).__name__}")
        log.exception("scheduler: unexpected error")


async def _prepare_one(session: Session, provider, cfg, record_id, now, lateness) -> str | None:
    record = session.get(PublishingRecord, record_id, populate_existing=True)
    if record is None or record.status != PublishState.SCHEDULED.value:
        session.rollback()
        return None
    if record.scheduled_for + lateness < now:  # too late to be worth preparing
        await ps.skip_late(session, provider, record_id)
        return "blocked"
    revision = session.get(ContentRevision, record.revision_id)
    try:
        path = resolve_media_path(cfg.media_dir, record.video_ref)
    except UnsafeMediaPath as exc:
        await ps.block(session, record_id, reason=f"video:{exc}")
        return "blocked"
    done = await ps.prepare(session, provider, record_id, video_path=path,
                            description=build_description(revision.caption, revision.hashtags))
    if done.status == PublishState.READY_TO_PUBLISH.value:
        return "prepared"
    # FAILED / EXPIRED are provider problems; BLOCKED is a policy outcome (e.g. copyright)
    return "blocked" if done.status == PublishState.BLOCKED.value else "errors"


async def _publish_one(session: Session, provider, record_id, now) -> str | None:
    outcome, _ = await ps.publish_due(session, provider, record_id, now=now)
    if outcome == "published":
        return "published"
    if outcome in {"blocked", "skipped_late"}:
        return "blocked"
    return None


async def _reconcile_one(session: Session, provider, record_id) -> str | None:
    record = session.get(PublishingRecord, record_id, populate_existing=True)
    if record is None:
        session.rollback()
        return None
    record.reconcile_attempts += 1
    session.commit()
    result = await ps.reconcile(session, provider, record_id)
    if result == "ambiguous" and record.reconcile_attempts >= MAX_RECONCILE_ATTEMPTS:
        record_audit(session, actor="system", action="publish.reconcile_exhausted",
                     result="error", entity_type="publishing_record", entity_id=str(record_id),
                     metadata={"attempts": record.reconcile_attempts})
        session.commit()  # stays UNKNOWN_RESULT: the owner decides, nothing retries blindly
    return "reconciled"
