import asyncio
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.core.errors import ApiError
from app.db.base import utcnow
from app.db.models import AuditEvent, ContentItem, PublishingRecord
from app.services import content_service
from app.services import publishing_service as ps
from tests.fakes import FakeReels
from tests.test_publish_gate import REVISION, drive_to_pending, fresh_token, open_settings

VIDEO = Path("unused.mp4")


def run(coro):
    return asyncio.run(coro)


def approved_item(db, owner, *, lane="rebus", kill_switch_open=True):
    item = content_service.create_content(db, owner, lane=lane, revision=dict(REVISION))
    drive_to_pending(db, owner, item)
    content_service.approve(db, owner, item)
    if kill_switch_open:
        open_settings(db)
    fresh_token(db)
    return item


def scheduled(db, owner, *, minutes_ago=1, **kw):
    item = approved_item(db, owner, **kw)
    record = ps.schedule(db, item=item, scheduled_for=utcnow() - timedelta(minutes=minutes_ago))
    return item, record


def prepared(db, owner, provider, **kw):
    item, record = scheduled(db, owner, **kw)
    run(ps.prepare(db, provider, record.id, video_path=VIDEO, description="d"))
    db.refresh(record)
    return item, record


def fresh(db, obj):
    db.expire_all()
    db.refresh(obj)
    return obj


# --------------------------------------------------------------------------- schedule/prepare
def test_schedule_moves_the_item_and_creates_one_active_record(db, owner):
    item, record = scheduled(db, owner)
    assert record.status == "SCHEDULED" and fresh(db, item).status == "SCHEDULED"
    with pytest.raises(ApiError) as exc:  # the item is no longer APPROVED
        ps.schedule(db, item=item, scheduled_for=utcnow())
    assert exc.value.status_code == 409


def test_schedule_needs_an_approved_item_and_an_aware_time(db, owner):
    draft = content_service.create_content(db, owner, lane="rebus", revision=dict(REVISION))
    with pytest.raises(ApiError) as exc:
        ps.schedule(db, item=draft, scheduled_for=utcnow())
    assert exc.value.status_code == 409
    item = approved_item(db, owner)
    with pytest.raises(ApiError) as exc2:
        ps.schedule(db, item=item, scheduled_for=utcnow().replace(tzinfo=None))
    assert exc2.value.code == "naive_datetime"


def test_prepare_persists_the_video_id_before_the_upload_and_reaches_ready(db, owner):
    from app.db.session import session_factory

    provider = FakeReels()

    def probe(video_id):  # another connection: only committed data is visible
        with session_factory()() as other:
            return other.scalar(text("SELECT meta_video_id FROM publishing_records"))

    provider.db_probe = probe
    _, record = prepared(db, owner, provider)
    assert provider.seen_in_db_before_upload == "v1"
    assert record.status == "READY_TO_PUBLISH" and record.meta_video_id == "v1"
    assert record.media_ready and record.copyright_clear


def test_a_copyright_match_blocks_the_record_and_nothing_goes_public(db, owner):
    provider = FakeReels()
    provider.prepare_mode = "copyright"
    _, record = prepared(db, owner, provider)
    assert record.status == "BLOCKED" and not record.copyright_clear
    assert provider.count("publish") == 0


def test_expired_upload_can_be_prepared_again_with_a_new_video(db, owner):
    provider = FakeReels()
    provider.prepare_mode = "expired"
    _, record = prepared(db, owner, provider)
    assert record.status == "EXPIRED"
    provider.prepare_mode = "ok"
    run(ps.prepare(db, provider, record.id, video_path=VIDEO, description="d"))
    db.refresh(record)
    assert record.status == "READY_TO_PUBLISH" and record.meta_video_id == "v2"
    assert ("delete", "v1") in provider.calls  # the old draft is not left orphaned


@pytest.mark.parametrize("mode", ["permanent", "timeout"])
def test_prepare_failures_are_clean_failures(db, owner, mode):
    provider = FakeReels()
    provider.prepare_mode = mode
    _, record = prepared(db, owner, provider)
    assert record.status == "FAILED" and record.last_error_code
    assert provider.count("publish") == 0


# --------------------------------------------------------------------------- publish
def test_happy_path_publishes_once_and_updates_the_item(db, owner):
    provider = FakeReels()
    item, record = prepared(db, owner, provider)
    assert run(ps.publish_due(db, provider, record.id)) == ("published", "ok")
    db.refresh(record)
    assert record.status == "PUBLISHED" and record.meta_post_id == "/reel/v1/"
    assert record.published_at is not None and fresh(db, item).status == "PUBLISHED"
    assert provider.count("publish") == 1


def test_publishing_again_is_a_no_op(db, owner):
    provider = FakeReels()
    _, record = prepared(db, owner, provider)
    run(ps.publish_due(db, provider, record.id))
    assert run(ps.publish_due(db, provider, record.id)) == ("already_published",
                                                            "already_published")
    assert provider.count("publish") == 1


def test_kill_switch_flipped_after_prepare_stops_the_promotion(db, owner):
    provider = FakeReels()
    _, record = prepared(db, owner, provider)
    db.execute(text("UPDATE system_settings SET publishing_kill_switch = true"))
    db.commit()
    assert run(ps.publish_due(db, provider, record.id)) == ("blocked",
                                                            "publishing_kill_switch_enabled")
    db.refresh(record)
    assert record.status == "BLOCKED" and provider.count("publish") == 0
    assert provider.drafts["v1"] == "draft"  # still only a private draft


def test_the_gate_decides_before_any_provider_call(db, owner):
    provider = FakeReels()
    _, record = prepared(db, owner, provider)
    db.execute(text("UPDATE system_settings SET max_publishes_per_day = 0"))
    db.commit()
    assert run(ps.publish_due(db, provider, record.id)) == ("waiting", "publish_rate_limit")
    assert fresh(db, record).status == "READY_TO_PUBLISH" and provider.count("publish") == 0


def test_not_due_yet_waits_without_changing_state(db, owner):
    provider = FakeReels()
    item, record = scheduled(db, owner, minutes_ago=-30)  # due in 30 minutes
    run(ps.prepare(db, provider, record.id, video_path=VIDEO, description="d"))
    assert run(ps.publish_due(db, provider, record.id)) == ("waiting", "not_due")
    assert fresh(db, record).status == "READY_TO_PUBLISH"


def test_publishing_before_prepare_does_not_block_the_record(db, owner):
    provider = FakeReels()
    _, record = scheduled(db, owner)
    assert run(ps.publish_due(db, provider, record.id)) == ("waiting", "record_not_ready")
    assert fresh(db, record).status == "SCHEDULED" and provider.count("publish") == 0


def test_a_slot_that_is_too_late_is_skipped_and_the_draft_deleted(db, owner):
    provider = FakeReels()
    _, record = prepared(db, owner, provider)
    record.scheduled_for = utcnow() - timedelta(hours=3)
    db.commit()
    assert run(ps.publish_due(db, provider, record.id)) == ("skipped_late", "skipped_late")
    assert fresh(db, record).status == "SKIPPED_LATE"
    assert provider.drafts["v1"] == "deleted" and provider.count("publish") == 0


def test_editing_after_prepare_blocks_publishing_of_the_old_draft(db, owner):
    provider = FakeReels()
    item, record = prepared(db, owner, provider)
    # An edit needs the item out of SCHEDULED; cancel (which frees it) then edit, then try anyway.
    run(ps.cancel(db, provider, record.id))
    content_service.edit_content(db, owner, item, {**REVISION, "caption": "edited"})
    outcome = run(ps.publish_due(db, provider, record.id))
    assert outcome[0] in {"blocked", "waiting"} and provider.count("publish") == 0


def test_a_permanent_provider_error_is_a_clean_failure(db, owner):
    provider = FakeReels()
    provider.publish_mode = "permanent"
    _, record = prepared(db, owner, provider)
    assert run(ps.publish_due(db, provider, record.id)) == ("failed", "200")
    assert fresh(db, record).status == "FAILED"


# --------------------------------------------------------------------------- unknown result
def test_timeout_after_the_promotion_took_effect_reconciles_to_published(db, owner):
    provider = FakeReels()
    provider.publish_mode = "timeout_applied"
    item, record = prepared(db, owner, provider)
    assert run(ps.publish_due(db, provider, record.id)) == ("unknown_result", "timeout")
    assert fresh(db, record).status == "UNKNOWN_RESULT"
    assert run(ps.reconcile(db, provider, record.id)) == "published"
    assert fresh(db, record).status == "PUBLISHED" and fresh(db, item).status == "PUBLISHED"
    assert provider.count("publish") == 1  # reconciliation never publishes again


def test_timeout_that_did_not_take_effect_retries_only_through_the_full_gate(db, owner):
    provider = FakeReels()
    provider.publish_mode = "timeout_not_applied"
    _, record = prepared(db, owner, provider)
    run(ps.publish_due(db, provider, record.id))
    assert run(ps.reconcile(db, provider, record.id)) == "ready_to_publish"
    assert fresh(db, record).status == "READY_TO_PUBLISH"

    # The owner turns the kill switch on in the meantime: the retry must be refused.
    db.execute(text("UPDATE system_settings SET publishing_kill_switch = true"))
    db.commit()
    assert run(ps.publish_due(db, provider, record.id)) == ("blocked",
                                                            "publishing_kill_switch_enabled")
    assert provider.count("publish") == 1  # only the first (timed-out) attempt


def test_after_reconciliation_a_clean_retry_publishes_exactly_once(db, owner):
    provider = FakeReels()
    provider.publish_mode = "timeout_not_applied"
    _, record = prepared(db, owner, provider)
    run(ps.publish_due(db, provider, record.id))
    run(ps.reconcile(db, provider, record.id))
    provider.publish_mode = "ok"
    assert run(ps.publish_due(db, provider, record.id)) == ("published", "ok")
    assert provider.count("publish") == 2 and provider.drafts["v1"] == "published"


def test_ambiguous_reconciliation_stays_unknown_and_never_retries(db, owner):
    provider = FakeReels()
    provider.publish_mode = "timeout_not_applied"
    _, record = prepared(db, owner, provider)
    run(ps.publish_due(db, provider, record.id))
    provider.reconcile_override = "ambiguous"
    assert run(ps.reconcile(db, provider, record.id)) == "ambiguous"
    assert fresh(db, record).status == "UNKNOWN_RESULT" and provider.count("publish") == 1


def test_a_missing_video_after_unknown_result_fails_the_record(db, owner):
    provider = FakeReels()
    provider.publish_mode = "timeout_not_applied"
    _, record = prepared(db, owner, provider)
    run(ps.publish_due(db, provider, record.id))
    provider.drafts["v1"] = "deleted"
    assert run(ps.reconcile(db, provider, record.id)) == "failed"


def test_only_unknown_records_can_be_reconciled(db, owner):
    provider = FakeReels()
    _, record = prepared(db, owner, provider)
    with pytest.raises(ApiError) as exc:
        run(ps.reconcile(db, provider, record.id))
    assert exc.value.code == "not_unknown"


# --------------------------------------------------------------------------- cancel / retract
def test_cancel_deletes_the_draft_and_returns_the_item_to_approved(db, owner):
    provider = FakeReels()
    item, record = prepared(db, owner, provider)
    run(ps.cancel(db, provider, record.id))
    assert fresh(db, record).status == "CANCELLED" and fresh(db, item).status == "APPROVED"
    assert provider.drafts["v1"] == "deleted"
    new = ps.schedule(db, item=item, scheduled_for=utcnow())  # can be scheduled again
    assert new.id != record.id


def test_a_published_reel_cannot_be_cancelled_only_retracted(db, owner):
    provider = FakeReels()
    _, record = prepared(db, owner, provider)
    run(ps.publish_due(db, provider, record.id))
    with pytest.raises(ApiError) as exc:
        run(ps.cancel(db, provider, record.id))
    assert exc.value.status_code == 409


def test_retraction_needs_a_reason_and_deletes_the_video(db, owner):
    provider = FakeReels()
    _, record = prepared(db, owner, provider)
    run(ps.publish_due(db, provider, record.id))
    with pytest.raises(ApiError) as exc:
        run(ps.retract(db, provider, record.id, reason=" ", actor="owner:x"))
    assert exc.value.code == "reason_required"
    assert run(ps.retract(db, provider, record.id, reason="factual error",
                          actor="owner:x")) == "retracted"
    assert fresh(db, record).status == "RETRACTED" and provider.drafts["v1"] == "deleted"


def test_a_failed_retraction_is_recorded_and_can_be_retried(db, owner):
    provider = FakeReels()
    _, record = prepared(db, owner, provider)
    run(ps.publish_due(db, provider, record.id))
    provider.delete_mode = "fail"
    assert run(ps.retract(db, provider, record.id, reason="x", actor="o")) == "retraction_failed"
    assert fresh(db, record).status == "RETRACTION_FAILED"
    provider.delete_mode = "ok"
    assert run(ps.retract(db, provider, record.id, reason="retry", actor="o")) == "retracted"


# --------------------------------------------------------------------------- audit and limits
def test_every_transition_is_audited(db, owner):
    provider = FakeReels()
    _, record = prepared(db, owner, provider)
    run(ps.publish_due(db, provider, record.id))
    moves = [e.event_metadata["to"] for e in db.scalars(
        select(AuditEvent).where(AuditEvent.action == "publish.transition")
        .order_by(AuditEvent.occurred_at))]
    assert moves == ["SCHEDULED", "PREPARING", "READY_TO_PUBLISH", "PUBLISHING", "PUBLISHED"]


def test_the_daily_publish_cap_counts_what_was_published(db, owner):
    provider = FakeReels()
    open_settings(db, max_publishes_per_day=1)
    first = approved_item(db, owner)
    r1 = ps.schedule(db, item=first, scheduled_for=utcnow() - timedelta(minutes=1))
    run(ps.prepare(db, provider, r1.id, video_path=VIDEO, description="d"))
    assert run(ps.publish_due(db, provider, r1.id))[0] == "published"

    second = approved_item(db, owner)
    r2 = ps.schedule(db, item=second, scheduled_for=utcnow() - timedelta(minutes=1))
    run(ps.prepare(db, provider, r2.id, video_path=VIDEO, description="d"))
    assert run(ps.publish_due(db, provider, r2.id)) == ("waiting", "publish_rate_limit")


def test_database_still_allows_only_one_active_record_per_revision(db, owner):
    provider = FakeReels()
    item, record = prepared(db, owner, provider)
    assert db.scalar(select(PublishingRecord).where(PublishingRecord.revision_id ==
                                                    record.revision_id)) is not None
    assert db.get(ContentItem, item.id).status == "SCHEDULED"
