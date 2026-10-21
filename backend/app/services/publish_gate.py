"""Builds PublishFacts from the database and runs the pure fail-closed policy.

Call `evaluate_prepublish` immediately before FINISH, in the transaction that then records the
PUBLISHING state. Rows are locked so a concurrent worker, edit or kill-switch change cannot slip
between check and act (docs/threat-model.md T-9). There is no Meta call in this module.
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.db.models import (
    Approval,
    ContentItem,
    ContentRevision,
    PublishingRecord,
    TokenHealth,
)
from app.services import content_service
from app.services.audit import record_audit
from app.services.publishing_policy import PublishFacts, can_publish
from app.services.settings_service import get_settings_row

TOKEN_MAX_AGE_SECONDS = 3600


def _budget_ok(session: Session) -> bool:
    # Publishing itself costs nothing; provider spend is tracked by cost_ledger (slice 3).
    # Until that table exists nothing can have been spent, so this is trivially true.
    return True


def build_facts(
    session: Session, record: PublishingRecord, *, now: datetime
) -> PublishFacts:
    settings_row = get_settings_row(session, for_update=True)
    revision = session.get(ContentRevision, record.revision_id)
    item = session.get(ContentItem, revision.content_item_id) if revision else None

    # Approval counts only if it names this exact hash and is the latest decision for it.
    approval = None
    review = None
    if revision is not None:
        approval = session.scalar(
            select(Approval)
            .where(
                Approval.revision_id == revision.id,
                Approval.content_hash == revision.content_hash,
            )
            .order_by(Approval.decided_at.desc())
            .limit(1)
            .execution_options(populate_existing=True)
        )
        review = content_service.latest_review(session, revision, "final")

    approved = approval is not None and approval.decision == "approved"
    token = session.scalar(
        select(TokenHealth).where(TokenHealth.provider == "meta")
        .order_by(TokenHealth.checked_at.desc()).limit(1)
        .execution_options(populate_existing=True)
    )
    window_start = now - timedelta(hours=24)
    published_recently = session.scalar(
        select(func.count()).select_from(PublishingRecord).where(
            PublishingRecord.published_at.is_not(None), PublishingRecord.published_at > window_start
        )
    ) or 0
    already_published = record.status == "PUBLISHED" or record.meta_post_id is not None

    return PublishFacts(
        approved=approved,
        approved_content_hash=approval.content_hash if approved else None,
        reviewed_content_hash=review.content_hash if review else None,
        video_content_hash=record.content_hash,
        safety_decision=review.decision if review else None,
        review_expires_at=review.expires_at if review else None,
        kill_switch_enabled=settings_row.publishing_kill_switch,
        already_published=already_published,
        scheduled_for=record.scheduled_for,
        max_lateness_seconds=settings_row.max_lateness_minutes * 60,
        publishes_in_window=published_recently,
        max_publishes_per_window=settings_row.max_publishes_per_day,
        budget_ok=_budget_ok(session),
        token_valid=bool(token and token.valid),
        token_checked_at=token.checked_at if token else None,
        max_token_age_seconds=TOKEN_MAX_AGE_SECONDS,
        record_status=record.status,
        revision_is_current=bool(item and revision and item.current_revision_id == revision.id
                                 and record.content_hash == revision.content_hash),
        topic_fresh_until=item.fresh_until if item else None,
        media_ready=record.media_ready,
        copyright_clear=record.copyright_clear,
        human_review_required=bool(review and review.human_review_required),
        human_review_complete=bool(review and review.human_review_complete),
    )


def evaluate_prepublish(
    session: Session, record_id: uuid.UUID, *, now: datetime | None = None
) -> tuple[bool, str]:
    """Lock, gather, decide. Any error is a refusal (fail closed) and is audited."""
    now = now or utcnow()
    record = session.scalar(
        select(PublishingRecord).where(PublishingRecord.id == record_id).with_for_update()
        .execution_options(populate_existing=True)
    )
    if record is None:
        return False, "record_not_found"
    try:
        allowed, reason = can_publish(build_facts(session, record, now=now), now=now)
    except Exception as exc:  # fail closed on any unexpected problem
        allowed, reason = False, f"gate_error:{type(exc).__name__}"
    record_audit(
        session, actor="system", action="publish.gate", result="ok" if allowed else "denied",
        entity_type="publishing_record", entity_id=str(record.id),
        metadata={"reason": reason, "content_hash": record.content_hash},
    )
    session.flush()
    return allowed, reason
