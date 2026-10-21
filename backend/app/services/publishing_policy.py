from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class PublishFacts:
    """Facts re-read from the database immediately before the external publish call."""

    approved: bool
    approved_content_hash: str | None
    reviewed_content_hash: str | None
    video_content_hash: str | None
    safety_decision: str | None
    review_expires_at: datetime | None
    kill_switch_enabled: bool
    already_published: bool
    scheduled_for: datetime | None
    max_lateness_seconds: int
    publishes_in_window: int
    max_publishes_per_window: int
    budget_ok: bool
    token_valid: bool
    token_checked_at: datetime | None
    max_token_age_seconds: int
    record_status: str
    revision_is_current: bool
    topic_fresh_until: datetime | None
    media_ready: bool
    copyright_clear: bool
    human_review_required: bool = False
    human_review_complete: bool = False


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        raise ValueError("naive datetime is not allowed; use UTC-aware datetimes")
    return value


def can_publish(facts: PublishFacts, *, now: datetime | None = None) -> tuple[bool, str]:
    """Pure fail-closed policy. Call this again immediately before every external publish.

    Returns (allowed, reason_code). Any missing/mismatching/stale fact blocks. Naive datetimes
    raise ValueError (callers must treat an exception as "blocked").

    The caller must build `facts` from rows locked in the same transaction that then performs the
    FINISH call bookkeeping (docs/threat-model.md T-9); `human_review_required` must be derived by
    deterministic backend rules from risk level and categories, not copied from model output.
    """
    now = _aware(now) or datetime.now(UTC)
    expires_at = _aware(facts.review_expires_at)
    scheduled_for = _aware(facts.scheduled_for)
    token_checked_at = _aware(facts.token_checked_at)
    topic_fresh_until = _aware(facts.topic_fresh_until)

    if facts.kill_switch_enabled:
        return False, "publishing_kill_switch_enabled"
    if facts.already_published:
        return False, "already_published"
    if facts.record_status != "READY_TO_PUBLISH":
        return False, "record_not_ready"
    if not facts.revision_is_current:
        return False, "revision_superseded"
    if not facts.approved or not facts.approved_content_hash:
        return False, "content_not_approved"
    if not facts.video_content_hash or facts.approved_content_hash != facts.video_content_hash:
        return False, "approval_does_not_match_media"
    if facts.safety_decision != "allow":
        return False, "safety_review_missing_or_not_allowed"
    if facts.reviewed_content_hash != facts.video_content_hash:
        return False, "safety_review_does_not_match_media"
    if expires_at is None or expires_at <= now:
        return False, "safety_review_missing_or_stale"
    if facts.human_review_required and not facts.human_review_complete:
        return False, "human_review_required"
    if scheduled_for is None or scheduled_for > now:
        return False, "not_due"
    if (now - scheduled_for).total_seconds() > facts.max_lateness_seconds:
        return False, "skipped_late"
    if facts.publishes_in_window >= facts.max_publishes_per_window:
        return False, "publish_rate_limit"
    if not facts.budget_ok:
        return False, "budget_exceeded"
    if topic_fresh_until is not None and topic_fresh_until <= now:
        return False, "topic_stale"
    if not facts.token_valid:
        return False, "token_invalid"
    token_age = None if token_checked_at is None else (now - token_checked_at).total_seconds()
    if token_age is None or token_age > facts.max_token_age_seconds:
        return False, "token_health_stale"
    if not facts.media_ready:
        return False, "media_not_ready"
    if not facts.copyright_clear:
        return False, "copyright_check_not_clear"
    return True, "allowed"
