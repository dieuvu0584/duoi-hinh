import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.db.base import utcnow
from app.db.models import (
    Approval,
    ContentItem,
    ContentRevision,
    OwnerAccount,
    SafetyReview,
)
from app.services import safety_rules
from app.services.audit import record_audit
from app.services.content_hash import HASH_VERSION, HASHED_FIELDS, compute_content_hash
from app.services.settings_service import get_settings_row
from app.services.state_machine import ContentState, InvalidTransition, check_content_transition

REVISION_FIELDS = (*HASHED_FIELDS,)


def _actor(owner: OwnerAccount | None) -> str:
    return f"owner:{owner.id}" if owner else "system"


def transition_content(
    session: Session, item: ContentItem, target: ContentState, *, actor: str,
    reason: str | None = None,
) -> None:
    """The only place a content status changes. Raises 409 on an invalid transition."""
    source = ContentState(item.status)
    try:
        check_content_transition(source, target)
    except InvalidTransition as exc:
        record_audit(session, actor=actor, action="content.transition", result="denied",
                     entity_type="content_item", entity_id=str(item.id),
                     metadata={"from": source.value, "to": target.value})
        session.commit()
        raise exc.to_api_error() from None
    item.status = target.value
    record_audit(session, actor=actor, action="content.transition", entity_type="content_item",
                 entity_id=str(item.id),
                 metadata={"from": source.value, "to": target.value, "reason": reason})


def create_content(
    session: Session, owner: OwnerAccount | None, *, lane: str, revision: dict[str, Any],
    fresh_until: datetime | None = None,
) -> ContentItem:
    settings_row = get_settings_row(session)
    if lane == "news" and not settings_row.news_lane_enabled:
        raise ApiError(409, "news_lane_disabled", "The news lane is disabled (decision D-16)")
    if lane == "news" and fresh_until is None:
        raise ApiError(422, "fresh_until_required", "News content needs a freshness window")
    item = ContentItem(lane=lane, fresh_until=fresh_until, status=ContentState.DRAFT.value)
    session.add(item)
    session.flush()
    add_revision(session, owner, item, revision)
    record_audit(session, actor=_actor(owner), action="content.create", entity_type="content_item",
                 entity_id=str(item.id), metadata={"lane": lane})
    session.commit()
    return item


def add_revision(
    session: Session, owner: OwnerAccount | None, item: ContentItem, fields: dict[str, Any],
) -> ContentRevision:
    """Edits never mutate: they append a revision. Older approvals/reviews stop matching."""
    unknown = set(fields) - set(REVISION_FIELDS)
    if item.lane == "rebus" and safety_rules.leaks_answer(
        fields.get("answer_text"), fields.get("caption"), fields.get("overlay_text"),
        fields.get("narration_text"),
    ):
        # The answer is private admin metadata (CLAUDE.md content invariants).
        raise ApiError(422, "answer_leak",
                       "The rebus answer appears in the caption, overlay or narration")
    if unknown:
        raise ApiError(422, "unknown_field", "Unknown revision field",
                       fields=[{"field": f, "issue": "not_allowed"} for f in sorted(unknown)])
    # Lock the item row so two concurrent edits cannot take the same revision number, and read
    # the number from the database (the relationship may be stale).
    session.execute(select(ContentItem.id).where(ContentItem.id == item.id).with_for_update())
    last = session.scalar(
        select(func.coalesce(func.max(ContentRevision.revision_no), 0)).where(
            ContentRevision.content_item_id == item.id
        )
    )
    revision = ContentRevision(
        content_item_id=item.id, revision_no=last + 1, hash_version=HASH_VERSION,
        content_hash=compute_content_hash(fields), created_by=_actor(owner),
        **{name: fields.get(name) for name in REVISION_FIELDS if name != "is_ai_generated"},
        is_ai_generated=bool(fields.get("is_ai_generated", True)),
    )
    session.add(revision)
    session.flush()
    item.current_revision_id = revision.id
    record_audit(
        session, actor=_actor(owner), action="content.revision", entity_type="content_item",
        entity_id=str(item.id),
        metadata={"revision_no": revision.revision_no, "content_hash": revision.content_hash},
    )
    return revision


def edit_content(
    session: Session, owner: OwnerAccount, item: ContentItem, fields: dict[str, Any]
) -> ContentRevision:
    """New revision from an edit. A later revision supersedes approvals and reviews of earlier ones.

    Items already scheduled/published cannot be edited in place; they need cancelling first.
    """
    if ContentState(item.status) in {ContentState.SCHEDULED, ContentState.PUBLISHED,
                                     ContentState.ARCHIVED, ContentState.SUPERSEDED}:
        raise ApiError(409, "not_editable", "Content in this state cannot be edited")
    revision = add_revision(session, owner, item, fields)
    if ContentState(item.status) in {ContentState.APPROVED, ContentState.PENDING_APPROVAL,
                                     ContentState.FINAL_SAFETY_PENDING}:
        # The new bytes need a fresh final review and approval.
        item.status = ContentState.NEEDS_REWORK.value
        record_audit(session, actor=_actor(owner), action="content.edit_invalidated_approval",
                     entity_type="content_item", entity_id=str(item.id))
    session.commit()
    return revision


def current_revision(session: Session, item: ContentItem) -> ContentRevision:
    revision = session.get(ContentRevision, item.current_revision_id)
    if revision is None:
        raise ApiError(409, "no_revision", "Content has no revision")
    return revision


def record_safety_review(
    session: Session, *, item: ContentItem, revision: ContentRevision, gate: str, decision: str,
    risk_level: str, categories: list[str], reviewer: str, policy_version: str,
    findings: list[dict[str, Any]] | None = None, now: datetime | None = None,
) -> SafetyReview:
    """Persist a review. The human-review requirement is derived here, never trusted from input."""
    now = now or utcnow()
    required = safety_rules.requires_human_review(item.lane, risk_level, categories)
    review = SafetyReview(
        revision_id=revision.id, content_hash=revision.content_hash, gate=gate,
        decision=decision, risk_level=risk_level, categories=list(categories), findings=findings,
        human_review_required=required, human_review_complete=False, policy_version=policy_version,
        reviewer=reviewer, reviewed_at=now,
        expires_at=safety_rules.review_expiry(item.lane, now, item.fresh_until),
    )
    session.add(review)
    record_audit(session, actor=reviewer, action="safety.review", entity_type="content_item",
                 entity_id=str(item.id),
                 metadata={"gate": gate, "decision": decision, "risk": risk_level,
                           "human_required": required, "content_hash": revision.content_hash})
    session.flush()
    return review


def complete_human_review(
    session: Session, owner: OwnerAccount, review_id: uuid.UUID
) -> SafetyReview:
    review = session.get(SafetyReview, review_id)
    if review is None:
        raise ApiError(404, "not_found", "Review not found")
    if not review.human_review_required:
        raise ApiError(409, "not_required", "This review did not require human review")
    review.human_review_complete = True
    record_audit(session, actor=_actor(owner), action="safety.human_review_complete",
                 entity_type="safety_review", entity_id=str(review.id))
    session.commit()
    return review


def approve(
    session: Session, owner: OwnerAccount, item: ContentItem, *, note: str | None = None
) -> Approval:
    """Approve the item's CURRENT revision. Requires PENDING_APPROVAL (state machine)."""
    revision = current_revision(session, item)
    transition_content(session, item, ContentState.APPROVED, actor=_actor(owner))
    approval = Approval(revision_id=revision.id, content_hash=revision.content_hash,
                        decision="approved", actor=_actor(owner), note=note)
    session.add(approval)
    session.commit()
    return approval


def reject(
    session: Session, owner: OwnerAccount, item: ContentItem, *, note: str | None = None
) -> Approval:
    revision = current_revision(session, item)
    transition_content(session, item, ContentState.REJECTED, actor=_actor(owner), reason=note)
    approval = Approval(revision_id=revision.id, content_hash=revision.content_hash,
                        decision="rejected", actor=_actor(owner), note=note)
    session.add(approval)
    session.commit()
    return approval


def latest_review(
    session: Session, revision: ContentRevision, gate: str
) -> SafetyReview | None:
    """Latest review of this exact revision hash for a gate (older hashes never match)."""
    return session.scalar(
        select(SafetyReview)
        .where(SafetyReview.revision_id == revision.id,
               SafetyReview.content_hash == revision.content_hash,
               SafetyReview.gate == gate)
        .order_by(SafetyReview.reviewed_at.desc())
        .limit(1)
        .execution_options(populate_existing=True)
    )
