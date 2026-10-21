import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.errors import ApiError
from app.db.base import utcnow
from app.db.models import AuditEvent, PublishingRecord, SafetyReview, TokenHealth
from app.services import content_service, publish_gate
from app.services.state_machine import ContentState as CS

REVISION = {
    "answer_text": "Bánh chưng", "caption": "Đoán thử nhé!", "hashtags": ["#doan"],
    "media_hash": "a" * 64, "is_ai_generated": True,
}
PATH = [CS.ANALYZING, CS.READY_FOR_GENERATION, CS.GENERATING, CS.RENDERING, CS.QA_PENDING,
        CS.FINAL_SAFETY_PENDING, CS.PENDING_APPROVAL]


def open_settings(db, **extra):
    sets = ["publishing_kill_switch = false"] + [f"{k} = {v}" for k, v in extra.items()]
    db.execute(text("UPDATE system_settings SET " + ", ".join(sets)))
    db.commit()


def fresh_token(db, *, valid=True, at=None):
    db.add(TokenHealth(provider="meta", valid=valid, checked_at=at or utcnow()))
    db.commit()


def new_record(db, revision, *, status="READY_TO_PUBLISH", when=None, **kw):
    record = PublishingRecord(
        revision_id=revision.id, content_hash=revision.content_hash,
        intent_key=uuid.uuid4().hex, status=status,
        scheduled_for=when or utcnow() - timedelta(minutes=1),
        media_ready=kw.pop("media_ready", True), copyright_clear=kw.pop("copyright_clear", True),
        **kw,
    )
    db.add(record)
    db.commit()
    return record


def drive_to_pending(db, owner, item, *, risk="low", categories=(), decision="allow"):
    for step in PATH:
        content_service.transition_content(db, item, step, actor="system")
    revision = content_service.current_revision(db, item)
    review = content_service.record_safety_review(
        db, item=item, revision=revision, gate="final", decision=decision, risk_level=risk,
        categories=list(categories), reviewer="llm:test", policy_version="p1",
    )
    db.commit()
    return revision, review


def ready(db, owner, *, lane="daily_life", risk="low", categories=(), fresh_until=None,
          approve=True, with_token=True, open_kill_switch=True):
    item = content_service.create_content(db, owner, lane=lane, revision=dict(REVISION),
                                          fresh_until=fresh_until)
    revision, review = drive_to_pending(db, owner, item, risk=risk, categories=categories)
    if approve:
        content_service.approve(db, owner, item)
    if open_kill_switch:
        open_settings(db)
    if with_token:
        fresh_token(db)
    return item, revision, review, new_record(db, revision)


def gate(db, record, now=None):
    result = publish_gate.evaluate_prepublish(db, record.id, now=now)
    db.commit()
    return result


def test_happy_path_is_allowed_and_audited(db, owner):
    _, _, _, record = ready(db, owner)
    assert gate(db, record) == (True, "allowed")
    event = db.scalars(select(AuditEvent).where(AuditEvent.action == "publish.gate")).one()
    assert event.result == "ok" and event.event_metadata["reason"] == "allowed"


def test_kill_switch_in_database_blocks(db, owner):
    _, _, _, record = ready(db, owner, open_kill_switch=False)
    assert gate(db, record) == (False, "publishing_kill_switch_enabled")


def test_fresh_install_defaults_cannot_publish_anything(db, owner):
    _, _, _, record = ready(db, owner, open_kill_switch=False, with_token=False)
    assert gate(db, record)[0] is False


def test_editing_after_approval_blocks_the_old_record_and_forces_rework(db, owner):
    item, revision, _, record = ready(db, owner)
    content_service.edit_content(db, owner, item, {**REVISION, "caption": "Đã sửa caption"})
    db.refresh(item)
    assert item.status == "NEEDS_REWORK"
    assert gate(db, record) == (False, "revision_superseded")


def test_new_revision_needs_its_own_review_and_approval(db, owner):
    item, _, _, _ = ready(db, owner)
    new_rev = content_service.edit_content(db, owner, item, {**REVISION, "caption": "v2"})
    record2 = new_record(db, new_rev)
    assert gate(db, record2) == (False, "content_not_approved")


def test_unapproved_content_is_blocked(db, owner):
    _, _, _, record = ready(db, owner, approve=False)
    assert gate(db, record) == (False, "content_not_approved")


def test_rejected_content_is_blocked(db, owner):
    item = content_service.create_content(db, owner, lane="rebus", revision=dict(REVISION))
    revision, _ = drive_to_pending(db, owner, item)
    content_service.reject(db, owner, item, note="no")
    open_settings(db)
    fresh_token(db)
    assert gate(db, new_record(db, revision)) == (False, "content_not_approved")


@pytest.mark.parametrize("decision", ["hold_for_human", "block"])
def test_review_that_is_not_allow_blocks(db, owner, decision):
    item = content_service.create_content(db, owner, lane="rebus", revision=dict(REVISION))
    revision, _ = drive_to_pending(db, owner, item, decision=decision)
    content_service.approve(db, owner, item)  # owner approves, but the review says no
    open_settings(db)
    fresh_token(db)
    assert gate(db, new_record(db, revision)) == (False, "safety_review_missing_or_not_allowed")


def test_stale_review_blocks(db, owner):
    _, _, review, record = ready(db, owner)
    later = review.expires_at + timedelta(seconds=1)
    fresh_token(db, at=later)  # keep the token fresh so only the review is stale
    assert gate(db, record, now=later) == (False, "safety_review_missing_or_stale")


def test_missing_review_blocks(db, owner):
    item = content_service.create_content(db, owner, lane="rebus", revision=dict(REVISION))
    for step in PATH:
        content_service.transition_content(db, item, step, actor="system")
    content_service.approve(db, owner, item)
    revision = content_service.current_revision(db, item)
    open_settings(db)
    fresh_token(db)
    assert gate(db, new_record(db, revision)) == (False, "safety_review_missing_or_not_allowed")


def test_sensitive_category_requires_human_even_if_model_says_allow(db, owner):
    _, _, review, record = ready(db, owner, categories=["children"])
    assert review.human_review_required is True
    assert gate(db, record) == (False, "human_review_required")
    content_service.complete_human_review(db, owner, review.id)
    assert gate(db, record) == (True, "allowed")


def test_news_lane_is_disabled_by_default_and_needs_freshness(db, owner):
    with pytest.raises(ApiError) as exc:
        content_service.create_content(db, owner, lane="news", revision=dict(REVISION),
                                       fresh_until=utcnow() + timedelta(hours=3))
    assert exc.value.code == "news_lane_disabled"
    db.execute(text("UPDATE system_settings SET news_lane_enabled = true"))
    db.commit()
    with pytest.raises(ApiError) as exc2:
        content_service.create_content(db, owner, lane="news", revision=dict(REVISION))
    assert exc2.value.code == "fresh_until_required"


def test_news_needs_human_review_and_expires_with_the_topic(db, owner):
    db.execute(text("UPDATE system_settings SET news_lane_enabled = true"))
    db.commit()
    fresh_until = utcnow() + timedelta(hours=2)
    _, _, review, record = ready(db, owner, lane="news", fresh_until=fresh_until)
    assert review.human_review_required is True
    assert review.expires_at <= fresh_until  # capped by the freshness window
    content_service.complete_human_review(db, owner, review.id)
    assert gate(db, record) == (True, "allowed")
    after = fresh_until + timedelta(minutes=1)
    fresh_token(db, at=after)
    # past the window the review itself is stale; the policy also refuses stale topics
    assert gate(db, record, now=after)[0] is False


def test_news_content_without_a_freshness_window_cannot_exist_in_the_database(db, owner):
    with pytest.raises(IntegrityError):
        db.execute(text("INSERT INTO content_items (id, lane, status, created_at, updated_at)"
                        " VALUES (gen_random_uuid(), 'news', 'DRAFT', now(), now())"))
    db.rollback()


def test_token_missing_invalid_or_stale_blocks(db, owner):
    _, _, _, record = ready(db, owner, with_token=False)
    assert gate(db, record) == (False, "token_invalid")
    fresh_token(db, valid=False)
    assert gate(db, record) == (False, "token_invalid")


def test_stale_valid_token_blocks(db, owner):
    _, _, _, record = ready(db, owner, with_token=False)
    fresh_token(db, at=utcnow() - timedelta(hours=3))
    assert gate(db, record) == (False, "token_health_stale")


def test_daily_publish_cap_counts_published_records(db, owner):
    _, revision, _, record = ready(db, owner)
    open_settings(db, max_publishes_per_day=2)
    for _ in range(2):
        other = content_service.create_content(db, owner, lane="rebus", revision=dict(REVISION))
        rev, _ = drive_to_pending(db, owner, other)
        rec = new_record(db, rev, status="PUBLISHED", published_at=utcnow() - timedelta(hours=1))
        rec.meta_post_id = "p" + uuid.uuid4().hex[:8]
        db.commit()
    assert gate(db, record) == (False, "publish_rate_limit")


def test_already_published_and_wrong_record_state_block(db, owner):
    _, _, _, record = ready(db, owner)
    record.status = "PREPARING"
    db.commit()
    assert gate(db, record) == (False, "record_not_ready")
    record.status = "READY_TO_PUBLISH"
    record.meta_post_id = "123"
    db.commit()
    assert gate(db, record) == (False, "already_published")


def test_media_and_copyright_must_be_clear(db, owner):
    _, revision, _, record = ready(db, owner)
    record.copyright_clear = False
    db.commit()
    assert gate(db, record) == (False, "copyright_check_not_clear")
    record.copyright_clear, record.media_ready = True, False
    db.commit()
    assert gate(db, record) == (False, "media_not_ready")


def test_not_due_and_too_late(db, owner):
    _, _, _, record = ready(db, owner)
    record.scheduled_for = utcnow() + timedelta(hours=1)
    db.commit()
    assert gate(db, record) == (False, "not_due")
    record.scheduled_for = utcnow() - timedelta(hours=2)
    db.commit()
    assert gate(db, record) == (False, "skipped_late")


def test_unknown_record_fails_closed(db, owner):
    assert publish_gate.evaluate_prepublish(db, uuid.uuid4()) == (False, "record_not_found")


def test_gate_takes_a_row_lock_so_workers_cannot_race(db, owner, engine):
    from app.db.session import session_factory

    _, _, _, record = ready(db, owner)
    publish_gate.evaluate_prepublish(db, record.id)  # lock held: transaction not committed
    with session_factory()() as other:
        with pytest.raises(DBAPIError, match="could not obtain lock"):
            other.execute(
                text("SELECT id FROM publishing_records WHERE id = :i FOR UPDATE NOWAIT"),
                {"i": record.id},
            )
        other.rollback()
    db.rollback()


def test_one_active_publishing_record_per_revision(db, owner):
    _, revision, _, record = ready(db, owner)
    with pytest.raises(IntegrityError):
        new_record(db, revision)
    db.rollback()
    db.refresh(record)
    record.status = "FAILED"
    db.commit()
    assert new_record(db, revision, status="SCHEDULED")  # allowed once the first is terminal


def test_revisions_and_approvals_are_immutable_in_the_database(db, owner):
    _, revision, review, _ = ready(db, owner)
    statements = [
        "UPDATE content_revisions SET caption = 'tampered'",
        "DELETE FROM content_revisions",
        "UPDATE approvals SET decision = 'rejected'",
        "DELETE FROM approvals",
        "UPDATE safety_reviews SET decision = 'block', risk_level = 'critical'",
        "UPDATE safety_reviews SET expires_at = now() + interval '30 days'",
        "DELETE FROM safety_reviews",
    ]
    for stmt in statements:
        with pytest.raises(DBAPIError, match="immutable"):
            db.execute(text(stmt))
        db.rollback()


def test_human_review_flag_can_be_set_once_but_not_unset(db, owner):
    _, _, review, _ = ready(db, owner, categories=["children"])
    db.execute(text("UPDATE safety_reviews SET human_review_complete = true"))
    db.commit()
    with pytest.raises(DBAPIError, match="un-completed"):
        db.execute(text("UPDATE safety_reviews SET human_review_complete = false"))
    db.rollback()
    assert db.get(SafetyReview, review.id).human_review_complete is True


def test_invalid_transition_is_409_and_audited(db, owner):
    item = content_service.create_content(db, owner, lane="rebus", revision=dict(REVISION))
    with pytest.raises(ApiError) as exc:
        content_service.transition_content(db, item, CS.PUBLISHED, actor="system")
    assert exc.value.status_code == 409 and exc.value.code == "invalid_state_transition"
    denied = db.scalars(select(AuditEvent).where(AuditEvent.action == "content.transition",
                                                 AuditEvent.result == "denied")).all()
    assert len(denied) == 1 and item.status == "DRAFT"


def test_cannot_approve_content_that_is_not_pending_approval(db, owner):
    item = content_service.create_content(db, owner, lane="rebus", revision=dict(REVISION))
    with pytest.raises(ApiError) as exc:
        content_service.approve(db, owner, item)
    assert exc.value.status_code == 409
    assert item.status == "DRAFT"


def test_approval_records_the_exact_hash(db, owner):
    _, revision, _, _ = ready(db, owner)
    approved_hash = db.execute(text("SELECT content_hash FROM approvals")).scalar_one()
    assert approved_hash == revision.content_hash


def test_now_defaults_to_utc_aware(db, owner):
    _, _, _, record = ready(db, owner)
    allowed, _ = publish_gate.evaluate_prepublish(db, record.id, now=datetime.now(UTC))
    db.commit()
    assert allowed is True


def test_gate_sees_a_kill_switch_flipped_by_another_connection(db, owner):
    """Regression: a cached settings row must never hide a freshly enabled kill switch."""
    from app.db.session import session_factory
    from app.services.settings_service import get_settings_row

    _, _, _, record = ready(db, owner)
    assert get_settings_row(db).publishing_kill_switch is False  # now cached in this session
    with session_factory()() as other:
        other.execute(text("UPDATE system_settings SET publishing_kill_switch = true"))
        other.commit()
    assert gate(db, record) == (False, "publishing_kill_switch_enabled")


def test_gate_sees_human_review_completed_by_another_connection(db, owner):
    from app.db.session import session_factory

    _, _, review, record = ready(db, owner, categories=["children"])
    assert gate(db, record) == (False, "human_review_required")  # review row now cached
    with session_factory()() as other:
        other.execute(text("UPDATE safety_reviews SET human_review_complete = true"))
        other.commit()
    assert gate(db, record) == (True, "allowed")
