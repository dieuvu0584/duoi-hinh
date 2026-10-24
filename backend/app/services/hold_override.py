"""The owner approves an item the automatic review could not finish (e.g. the model's answer was
malformed twice). The owner's own review of the video, caption and narration stands in for the
missing automatic one, recorded as a final safety review by the owner, so the normal gate and
audit trail still apply. Findings (answer leak, blacklisted words, a real safety hold) are NOT
overridable: those items must be corrected and recreated.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ApiError
from app.db.models import ContentItem, OwnerAccount, VideoAsset
from app.services import content_service
from app.services.audit import record_audit
from app.services.safety_rules import _fold, leaks_answer
from app.services.state_machine import ContentState

OVERRIDABLE_STAGES = {"qa", "safety"}
OVERRIDABLE_REASONS = ("LlmInvalidOutput",)  # the model failed to answer in a usable form


def _last_note(item: ContentItem) -> tuple[str | None, str]:
    notes = (item.pipeline_input or {}).get("notes") or []
    last = notes[-1] if notes and isinstance(notes[-1], dict) else {}
    return last.get("stage"), str(last.get("reason") or "")


def _has_videos(session: Session, item: ContentItem) -> bool:
    revision = content_service.current_revision(session, item)
    if not (revision.media_hash and revision.audio_hash):
        return False
    targets = set(session.scalars(
        select(VideoAsset.target).where(VideoAsset.revision_id == revision.id,
                                        VideoAsset.status == "valid")))
    return {"reel_9x16", "youtube_16x9"} <= targets


def can_override(session: Session, item: ContentItem) -> bool:
    if item.status != ContentState.HELD_FOR_HUMAN.value:
        return False
    stage, reason = _last_note(item)
    return (stage in OVERRIDABLE_STAGES and reason.startswith(OVERRIDABLE_REASONS)
            and _has_videos(session, item))


def _text_problems(cfg: Settings, item: ContentItem, revision) -> list[str]:
    texts = [revision.caption, revision.overlay_text, revision.narration_text,
             " ".join(revision.hashtags or [])]
    problems = []
    if item.lane == "rebus" and leaks_answer(revision.answer_text, *texts):
        problems.append("answer_leak")
    words = [_fold(w.strip()) for w in cfg.blacklist_words.split(",") if w.strip()]
    haystack = _fold(" ".join(t for t in texts if t))
    if any(w in haystack for w in words):
        problems.append("blacklisted_words")
    return problems


def approve_held(
    session: Session, cfg: Settings, owner: OwnerAccount, item: ContentItem, *, note: str | None,
):
    """Record the owner's review, move the item to approval and approve it. Returns the Approval."""
    if not can_override(session, item):
        raise ApiError(409, "not_overridable",
                       "This hold came from a review finding or has no finished video; "
                       "correct the idea and create it again, or reject it")
    revision = content_service.current_revision(session, item)
    problems = _text_problems(cfg, item, revision)
    if problems:
        raise ApiError(409, "text_check_failed", ",".join(problems))
    actor = f"owner:{owner.id}"
    content_service.record_safety_review(
        session, item=item, revision=revision, gate="final", decision="allow", risk_level="low",
        categories=[], reviewer=actor, policy_version="owner-override-1",
        findings=[{"category": "owner_review",
                   "description": "The automatic final review did not finish; the owner watched "
                                  "the video and read the caption and narration."}])
    content_service.transition_content(session, item, ContentState.PENDING_APPROVAL, actor=actor,
                                       reason="owner_override")
    record_audit(session, actor=actor, action="content.hold_override", entity_type="content_item",
                 entity_id=str(item.id), metadata={"content_hash": revision.content_hash})
    session.commit()
    return content_service.approve(session, owner, item, note=note or "Chủ duyệt tay")
