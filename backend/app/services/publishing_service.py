"""Publishing orchestration: schedule -> prepare (draft) -> gate -> promote -> reconcile.

Implements docs/publishing-meta.md D1-D3 against the `ReelsPublishingProvider` interface:
- Our backend owns the schedule. Preparing creates a DRAFT at the provider (nothing public).
- The provider video id is persisted BEFORE the upload (the idempotency anchor).
- At the due time the full pre-publish gate runs (kill switch, approval, review, hashes, limits);
  only then is the draft promoted. The `PUBLISHING` state is committed BEFORE the provider call.
- A timeout is UNKNOWN_RESULT. It is reconciled against the provider; a retry goes back through
  READY_TO_PUBLISH and the whole gate again, never straight to PUBLISHING.
Soft gate reasons (not due yet, rate limit) leave the record untouched; the rest block it.
"""

import uuid
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.db.base import utcnow
from app.db.models import ContentItem, ContentRevision, PublishingRecord
from app.providers.contracts import ReelsPublishingProvider
from app.providers.errors import (
    ProviderError,
    ProviderExpired,
    ProviderPermanent,
    ProviderTimeout,
    ProviderTransient,
)
from app.services import publish_gate
from app.services.audit import record_audit
from app.services.state_machine import (
    ContentState,
    InvalidTransition,
    PublishState,
    check_publish_transition,
)

# Reasons that only mean "not yet / not applicable": the record is left exactly as it is.
SOFT_REASONS = {"not_due", "publish_rate_limit", "record_not_ready"}


def _move(session: Session, record: PublishingRecord, target: PublishState, *, actor: str,
          reason: str | None = None) -> None:
    source = PublishState(record.status)
    try:
        check_publish_transition(source, target)
    except InvalidTransition as exc:
        record_audit(session, actor=actor, action="publish.transition", result="denied",
                     entity_type="publishing_record", entity_id=str(record.id),
                     metadata={"from": source.value, "to": target.value})
        session.commit()
        raise exc.to_api_error() from None
    record.status = target.value
    record_audit(session, actor=actor, action="publish.transition",
                 entity_type="publishing_record", entity_id=str(record.id),
                 metadata={"from": source.value, "to": target.value, "reason": reason})


def _item_of(session: Session, record: PublishingRecord) -> ContentItem:
    revision = session.get(ContentRevision, record.revision_id)
    return session.get(ContentItem, revision.content_item_id)


def _set_item(session: Session, item: ContentItem, target: ContentState, actor: str) -> None:
    from app.services.content_service import transition_content

    transition_content(session, item, target, actor=actor)


def _load(session: Session, record_id: uuid.UUID) -> PublishingRecord:
    record = session.scalar(
        select(PublishingRecord).where(PublishingRecord.id == record_id).with_for_update()
        .execution_options(populate_existing=True))
    if record is None:
        raise ApiError(404, "not_found", "Publishing record not found")
    return record


def schedule(
    session: Session, *, item: ContentItem, scheduled_for: datetime, actor: str = "system",
    video_ref: str | None = None,
) -> PublishingRecord:
    """Create the publishing record for the item's CURRENT revision (item must be APPROVED)."""
    if scheduled_for.tzinfo is None:
        raise ApiError(422, "naive_datetime", "scheduled_for must include a timezone")
    revision = session.get(ContentRevision, item.current_revision_id)
    if revision is None:
        raise ApiError(409, "no_revision", "Content has no revision")
    _set_item(session, item, ContentState.SCHEDULED, actor)  # 409 unless APPROVED
    # One intent per scheduling attempt: a cancelled/failed record is terminal and a new
    # schedule is a new intent (the unique active-record index still forbids two at once).
    attempt = (session.scalar(select(func.count()).select_from(PublishingRecord).where(
        PublishingRecord.revision_id == revision.id)) or 0) + 1
    record = PublishingRecord(
        revision_id=revision.id, content_hash=revision.content_hash,
        intent_key=f"{revision.id}:facebook:reel_9x16:{attempt}",
        status=PublishState.NOT_SCHEDULED.value, scheduled_for=scheduled_for,
        video_ref=video_ref)
    session.add(record)
    session.flush()
    _move(session, record, PublishState.SCHEDULED, actor=actor)
    session.commit()  # the unique "one active record per revision" index rejects a double schedule
    return record


async def prepare(
    session: Session, provider: ReelsPublishingProvider, record_id: uuid.UUID, *,
    video_path: Path, description: str, actor: str = "system",
) -> PublishingRecord:
    """SCHEDULED/EXPIRED -> PREPARING -> READY_TO_PUBLISH (draft ready, copyright clear)."""
    record = _load(session, record_id)
    revision = session.get(ContentRevision, record.revision_id)
    _move(session, record, PublishState.PREPARING, actor=actor)
    previous_video = record.meta_video_id
    record.meta_video_id = None
    session.commit()
    if previous_video:  # re-preparing (expired/unblocked): do not leave an orphan draft behind
        await _discard_draft(session, provider, record, video_id=previous_video)

    def remember(video_id: str) -> None:  # called by the provider before the upload starts
        record.meta_video_id = video_id
        session.commit()

    try:
        prepared = await provider.prepare(
            video_path=video_path, description=description,
            is_ai_generated=revision.is_ai_generated, intent_key=record.intent_key,
            on_video_id=remember)
    except ProviderExpired:
        _move(session, record, PublishState.EXPIRED, actor=actor, reason="provider_expired")
        session.commit()
        return record
    except Exception as exc:  # noqa: BLE001 - never leave a record stuck in PREPARING
        # Nothing public exists yet, so any failure here is a clean FAILED (the draft, if any,
        # can be deleted). The owner or scheduler decides on a new attempt.
        session.rollback()
        record = _load(session, record_id)
        record.last_error_code = getattr(exc, "code", type(exc).__name__)[:60]
        _move(session, record, PublishState.FAILED, actor=actor, reason=type(exc).__name__)
        session.commit()
        if not isinstance(exc, ProviderError):
            raise  # a programming error: record is safe, but make it visible
        return record

    record.meta_video_id = prepared.video_id
    if prepared.status != "ready" or prepared.copyright_clear is not True:
        record.media_ready = prepared.status == "ready"
        record.copyright_clear = bool(prepared.copyright_clear)
        reason = "copyright_not_clear" if prepared.copyright_clear is False else "not_ready"
        _move(session, record, PublishState.BLOCKED, actor=actor, reason=reason)
    else:
        record.media_ready = True
        record.copyright_clear = True
        _move(session, record, PublishState.READY_TO_PUBLISH, actor=actor)
    session.commit()
    return record


async def publish_due(
    session: Session, provider: ReelsPublishingProvider, record_id: uuid.UUID, *,
    now: datetime | None = None, actor: str = "system",
) -> tuple[str, str]:
    """Run the gate and, only if it allows, promote the draft. Returns (outcome, reason)."""
    now = now or utcnow()
    record = _load(session, record_id)
    if record.status == PublishState.PUBLISHED.value:
        session.rollback()
        return "already_published", "already_published"  # idempotent: no provider call

    allowed, reason = publish_gate.evaluate_prepublish(session, record_id, now=now)
    if not allowed:
        if reason in SOFT_REASONS:
            session.commit()
            return "waiting", reason
        if reason == "skipped_late":
            _move(session, record, PublishState.SKIPPED_LATE, actor=actor, reason=reason)
            session.commit()
            await _discard_draft(session, provider, record)
            return "skipped_late", reason
        if record.status in {PublishState.READY_TO_PUBLISH.value, PublishState.SCHEDULED.value,
                             PublishState.PREPARING.value}:
            _move(session, record, PublishState.BLOCKED, actor=actor, reason=reason)
        session.commit()
        return "blocked", reason

    # The gate passed inside this transaction (rows locked). Persist PUBLISHING first.
    _move(session, record, PublishState.PUBLISHING, actor=actor)
    session.commit()
    video_id = record.meta_video_id
    try:
        result = await provider.publish(video_id=video_id, intent_key=record.intent_key)
    except ProviderTimeout:
        _move(session, record, PublishState.UNKNOWN_RESULT, actor=actor, reason="timeout")
        session.commit()
        return "unknown_result", "timeout"
    except (ProviderPermanent, ProviderExpired, ProviderTransient) as exc:
        # A definite provider answer that the promotion did not happen.
        record.last_error_code = getattr(exc, "code", type(exc).__name__)[:60]
        _move(session, record, PublishState.FAILED, actor=actor, reason=type(exc).__name__)
        session.commit()
        return "failed", record.last_error_code
    except Exception as exc:  # noqa: BLE001 - anything unrecognised may have gone public
        session.rollback()
        record = _load(session, record_id)
        _move(session, record, PublishState.UNKNOWN_RESULT, actor=actor,
              reason=f"unexpected:{type(exc).__name__}")
        session.commit()
        return "unknown_result", type(exc).__name__

    _finish_published(session, record, result.permalink, actor)
    return "published", "ok"


def _finish_published(session: Session, record: PublishingRecord, permalink: str | None,
                      actor: str) -> None:
    record.meta_post_id = permalink or record.meta_video_id
    record.published_at = utcnow()
    _move(session, record, PublishState.PUBLISHED, actor=actor)
    item = _item_of(session, record)
    if item.status == ContentState.SCHEDULED.value:
        _set_item(session, item, ContentState.PUBLISHED, actor)
    session.commit()


async def reconcile(
    session: Session, provider: ReelsPublishingProvider, record_id: uuid.UUID, *,
    actor: str = "system",
) -> str:
    """Resolve UNKNOWN_RESULT from the provider's truth. Never publishes anything itself."""
    record = _load(session, record_id)
    if record.status != PublishState.UNKNOWN_RESULT.value:
        session.rollback()
        raise ApiError(409, "not_unknown", "Only UNKNOWN_RESULT records are reconciled")
    result = await provider.reconcile(video_id=record.meta_video_id)
    if result.state == "published":
        _finish_published(session, record, result.permalink, actor)
        return "published"
    if result.state == "draft_ready":
        _move(session, record, PublishState.READY_TO_PUBLISH, actor=actor,
              reason="reconciled_not_published")
        session.commit()
        return "ready_to_publish"
    if result.state in {"missing", "error"}:
        record.last_error_code = f"reconciled_{result.state}"
        _move(session, record, PublishState.FAILED, actor=actor, reason=result.state)
        session.commit()
        return "failed"
    session.commit()
    return "ambiguous"  # stay UNKNOWN_RESULT, alert the owner, no automatic retry


async def _discard_draft(session: Session, provider: ReelsPublishingProvider,
                         record: PublishingRecord, *, video_id: str | None = None) -> None:
    video_id = video_id or record.meta_video_id
    if video_id:
        try:
            await provider.delete(video_id=video_id)
        except (ProviderTimeout, ProviderTransient, ProviderPermanent) as exc:
            record_audit(session, actor="system", action="publish.draft_delete_failed",
                         result="error", entity_type="publishing_record",
                         entity_id=str(record.id), metadata={"error": type(exc).__name__})
            session.commit()


async def cancel(
    session: Session, provider: ReelsPublishingProvider, record_id: uuid.UUID, *,
    actor: str = "system",
) -> PublishingRecord:
    """Cancel before going public: delete the draft and free the content to be rescheduled."""
    record = _load(session, record_id)
    _move(session, record, PublishState.CANCELLED, actor=actor)  # 409 unless cancellable
    item = _item_of(session, record)
    if item.status == ContentState.SCHEDULED.value:
        _set_item(session, item, ContentState.APPROVED, actor)
    session.commit()
    await _discard_draft(session, provider, record)
    return record


async def retract(
    session: Session, provider: ReelsPublishingProvider, record_id: uuid.UUID, *, reason: str,
    actor: str,
) -> str:
    """Take a published Reel down (docs/operations.md section 1). A reason is mandatory."""
    if not reason.strip():
        raise ApiError(422, "reason_required", "A retraction needs a reason")
    record = _load(session, record_id)
    _move(session, record, PublishState.RETRACTION_REQUESTED, actor=actor, reason=reason)
    session.commit()
    try:
        await provider.delete(video_id=record.meta_video_id)
    except (ProviderTimeout, ProviderTransient, ProviderPermanent):
        _move(session, record, PublishState.RETRACTION_FAILED, actor=actor)
        session.commit()
        return "retraction_failed"
    _move(session, record, PublishState.RETRACTED, actor=actor)
    session.commit()
    return "retracted"


async def skip_late(session: Session, provider: ReelsPublishingProvider,
                    record_id: uuid.UUID) -> None:
    """A slot that was never prepared and is now past `max_lateness`: skip it, do not publish."""
    record = _load(session, record_id)
    _move(session, record, PublishState.SKIPPED_LATE, actor="system", reason="skipped_late")
    session.commit()
    await _discard_draft(session, provider, record)


async def block(session: Session, record_id: uuid.UUID, *, reason: str,
                actor: str = "system") -> None:
    """Block a SCHEDULED record that cannot be prepared (e.g. unsafe or missing video)."""
    record = _load(session, record_id)
    _move(session, record, PublishState.BLOCKED, actor=actor, reason=reason)
    session.commit()


def recover_stuck(session: Session, *, now: datetime) -> dict[str, int]:
    """Repair records left in a transient state by a crash or a lost worker.

    PREPARING for too long -> EXPIRED (nothing public exists; it can be prepared again).
    PUBLISHING for too long -> UNKNOWN_RESULT (the promotion may or may not have happened, so
    it must be reconciled, never assumed). Both are audited.
    """
    fixed = {"preparing": 0, "publishing": 0}
    for status, target, key, max_age in (
        (PublishState.PREPARING, PublishState.EXPIRED, "preparing", timedelta(minutes=20)),
        (PublishState.PUBLISHING, PublishState.UNKNOWN_RESULT, "publishing",
         timedelta(minutes=10)),
    ):
        ids = session.scalars(
            select(PublishingRecord.id).where(
                PublishingRecord.status == status.value,
                PublishingRecord.updated_at < now - max_age)).all()
        for record_id in ids:
            record = _load(session, record_id)
            if record.status != status.value:
                session.rollback()
                continue
            _move(session, record, target, actor="system", reason=f"stuck_{key}")
            session.commit()
            fixed[key] += 1
    return fixed
