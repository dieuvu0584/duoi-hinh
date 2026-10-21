import asyncio
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.db.base import utcnow
from app.db.models import PublishingRecord
from app.providers.errors import ProviderError
from app.services import content_service
from app.services import publishing_service as ps
from app.services.media_paths import UnsafeMediaPath, resolve_media_path
from app.services.scheduler import MAX_RECONCILE_ATTEMPTS, build_description, tick
from tests.fakes import FakeReels
from tests.test_publish_gate import REVISION, drive_to_pending, fresh_token, open_settings


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def media(tmp_path):
    (tmp_path / "reels").mkdir()
    (tmp_path / "reels" / "a.mp4").write_bytes(b"\x00" * 128)
    return tmp_path


def cfg_for(media):
    return Settings(_env_file=None, media_dir=str(media))


def automate(db, on=True):
    db.execute(text(f"UPDATE system_settings SET automation_enabled = {str(on).lower()}"))
    db.commit()


def new_slot(db, owner, *, minutes_from_now, video_ref="reels/a.mp4", lane="rebus", caption=None):
    rev = dict(REVISION)
    if caption:
        rev["caption"] = caption
    item = content_service.create_content(db, owner, lane=lane, revision=rev)
    drive_to_pending(db, owner, item)
    content_service.approve(db, owner, item)
    return item, ps.schedule(db, item=item, video_ref=video_ref,
                             scheduled_for=utcnow() + timedelta(minutes=minutes_from_now))


def status_of(db, record):
    db.expire_all()
    return db.get(PublishingRecord, record.id).status


def ready_world(db):
    open_settings(db)
    fresh_token(db)
    automate(db)


# ----------------------------------------------------------------------------- guards
def test_nothing_happens_while_automation_is_disabled(db, owner, media):
    open_settings(db)
    fresh_token(db)
    provider = FakeReels()
    _, record = new_slot(db, owner, minutes_from_now=-1)
    report = run(tick(db, provider, cfg_for(media)))
    assert report.skipped == "automation_disabled" and provider.calls == []
    assert status_of(db, record) == "SCHEDULED"


def test_a_slot_outside_the_prepare_window_is_left_alone(db, owner, media):
    ready_world(db)
    provider = FakeReels()
    _, record = new_slot(db, owner, minutes_from_now=120)  # lead is 20 minutes
    run(tick(db, provider, cfg_for(media)))
    assert provider.calls == [] and status_of(db, record) == "SCHEDULED"


# ----------------------------------------------------------------------------- the happy path
def test_a_slot_is_prepared_inside_the_lead_window_then_published_when_due(db, owner, media):
    ready_world(db)
    provider = FakeReels()
    item, record = new_slot(db, owner, minutes_from_now=10)

    first = run(tick(db, provider, cfg_for(media)))
    assert first.prepared == 1 and status_of(db, record) == "READY_TO_PUBLISH"
    assert provider.count("publish") == 0  # not due yet: only a private draft exists

    db.execute(text("UPDATE publishing_records SET scheduled_for = now() - interval '1 minute'"))
    db.commit()
    second = run(tick(db, provider, cfg_for(media)))
    assert second.published == 1 and status_of(db, record) == "PUBLISHED"
    assert provider.count("publish") == 1 and provider.count("prepare") == 1


def test_the_description_sent_to_the_provider_is_caption_plus_hashtags():
    assert build_description("Hello", ["#a", "#b"]) == "Hello\n\n#a #b"
    assert build_description(None, ["#a"]) == "#a"
    assert build_description("  ", None) == ""


def test_running_the_tick_twice_never_publishes_twice(db, owner, media):
    ready_world(db)
    provider = FakeReels()
    _, record = new_slot(db, owner, minutes_from_now=-1)
    run(tick(db, provider, cfg_for(media)))
    run(tick(db, provider, cfg_for(media)))
    run(tick(db, provider, cfg_for(media)))
    assert provider.count("publish") == 1 and provider.count("prepare") == 1
    assert status_of(db, record) == "PUBLISHED"


# ----------------------------------------------------------------------------- safety
def test_kill_switch_stops_the_scheduler_at_the_gate(db, owner, media):
    ready_world(db)
    provider = FakeReels()
    _, record = new_slot(db, owner, minutes_from_now=-1)
    run(tick(db, provider, cfg_for(media)))  # prepares and (being due) publishes in one pass
    assert provider.count("publish") == 1

    provider2 = FakeReels()
    _, record2 = new_slot(db, owner, minutes_from_now=-1)
    db.execute(text("UPDATE system_settings SET publishing_kill_switch = true"))
    db.commit()
    report = run(tick(db, provider2, cfg_for(media)))
    assert provider2.count("publish") == 0 and report.published == 0
    assert status_of(db, record2) == "BLOCKED"


def test_a_late_slot_is_skipped_never_published(db, owner, media):
    ready_world(db)
    provider = FakeReels()
    _, record = new_slot(db, owner, minutes_from_now=-180)  # 3 h late; limit is 30 min
    report = run(tick(db, provider, cfg_for(media)))
    assert status_of(db, record) == "SKIPPED_LATE" and report.blocked == 1
    assert provider.count("publish") == 0 and provider.count("prepare") == 0


@pytest.mark.parametrize("ref", ["../secret.txt", "reels/../../secret.txt", "/etc/passwd",
                                 "C:/Windows/win.ini", "reels/missing.mp4", "", None])
def test_unsafe_or_missing_videos_are_blocked_and_never_uploaded(db, owner, media, ref):
    (media.parent / "secret.txt").write_text("secret")
    ready_world(db)
    provider = FakeReels()
    _, record = new_slot(db, owner, minutes_from_now=5, video_ref=ref)
    run(tick(db, provider, cfg_for(media)))
    assert status_of(db, record) == "BLOCKED" and provider.calls == []


def test_media_paths_cannot_escape_the_media_directory(media):
    assert resolve_media_path(media, "reels/a.mp4").name == "a.mp4"
    for bad in ("../x", "reels/../../x", "/abs/path", "reels", "nul\0.mp4"):
        with pytest.raises(UnsafeMediaPath):
            resolve_media_path(media, bad)


def test_a_symlink_that_points_outside_is_refused(media, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "evil.mp4"
    outside.write_bytes(b"x")
    link = media / "reels" / "link.mp4"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available on this system")
    with pytest.raises(UnsafeMediaPath):
        resolve_media_path(media, "reels/link.mp4")


# ----------------------------------------------------------------------------- resilience
def test_one_failing_record_does_not_stop_the_others(db, owner, media):
    ready_world(db)
    provider = FakeReels()
    _, bad = new_slot(db, owner, minutes_from_now=-1, video_ref="reels/missing.mp4")
    _, good = new_slot(db, owner, minutes_from_now=-1)
    report = run(tick(db, provider, cfg_for(media)))
    assert status_of(db, good) == "PUBLISHED" and status_of(db, bad) == "BLOCKED"
    assert report.published == 1 and report.blocked >= 1


def test_provider_exceptions_never_escape_the_tick(db, owner, media):
    ready_world(db)
    provider = FakeReels()

    async def boom(**_):
        raise ProviderError("down")

    provider.prepare = boom
    _, record = new_slot(db, owner, minutes_from_now=-1)
    report = run(tick(db, provider, cfg_for(media)))
    assert report.errors == 1
    assert status_of(db, record) == "FAILED"  # never stuck in PREPARING


def test_unexpected_exceptions_are_contained(db, owner, media):
    ready_world(db)
    provider = FakeReels()

    async def boom(**_):
        raise RuntimeError("bug")

    provider.prepare = boom
    _, record = new_slot(db, owner, minutes_from_now=-1)
    report = run(tick(db, provider, cfg_for(media)))
    assert report.errors == 1 and "unexpected:RuntimeError" in report.notes


def test_unknown_results_are_reconciled_and_a_bounded_number_of_times(db, owner, media):
    ready_world(db)
    provider = FakeReels()
    provider.publish_mode = "timeout_applied"
    _, record = new_slot(db, owner, minutes_from_now=-1)
    first = run(tick(db, provider, cfg_for(media)))  # prepare, timed-out promote, reconcile
    assert first.reconciled == 1 and status_of(db, record) == "PUBLISHED"
    assert provider.count("publish") == 1  # the reconciliation never published again


def test_an_ambiguous_record_is_retried_only_up_to_the_limit(db, owner, media):
    ready_world(db)
    provider = FakeReels()
    provider.publish_mode = "timeout_not_applied"
    provider.reconcile_override = "ambiguous"
    _, record = new_slot(db, owner, minutes_from_now=-1)
    for _ in range(MAX_RECONCILE_ATTEMPTS + 3):
        run(tick(db, provider, cfg_for(media)))
    assert status_of(db, record) == "UNKNOWN_RESULT"
    assert provider.count("reconcile") == MAX_RECONCILE_ATTEMPTS
    assert provider.count("publish") == 1  # nothing was ever retried blindly


def test_after_reconciliation_the_retry_goes_through_the_gate_again(db, owner, media):
    ready_world(db)
    provider = FakeReels()
    provider.publish_mode = "timeout_not_applied"
    _, record = new_slot(db, owner, minutes_from_now=-1)
    run(tick(db, provider, cfg_for(media)))            # prepare + timed-out publish
    db.execute(text("UPDATE system_settings SET publishing_kill_switch = true"))
    db.commit()
    run(tick(db, provider, cfg_for(media)))            # reconcile -> READY_TO_PUBLISH, gate blocks
    run(tick(db, provider, cfg_for(media)))
    assert provider.count("publish") == 1
    assert status_of(db, record) in {"BLOCKED", "READY_TO_PUBLISH"}


def test_two_workers_racing_on_one_record_publish_it_once(db, owner, media):
    from app.db.session import session_factory

    ready_world(db)
    provider = FakeReels()
    _, record = new_slot(db, owner, minutes_from_now=-1)

    async def two_workers():
        async def worker():
            with session_factory()() as session:
                return await tick(session, provider, cfg_for(media))
        return await asyncio.gather(worker(), worker())

    run(two_workers())
    assert provider.count("publish") == 1
    assert status_of(db, record) in {"PUBLISHED", "UNKNOWN_RESULT"}
    assert Path(media).exists()


# ----------------------------------------------------------------------------- crash recovery
def age(db, record, minutes):
    db.execute(text("UPDATE publishing_records SET updated_at = now() - make_interval(mins => :m)"),
               {"m": minutes})
    db.commit()


def test_a_record_stuck_in_preparing_is_expired_so_it_can_be_prepared_again(db, owner, media):
    ready_world(db)
    provider = FakeReels()
    _, record = new_slot(db, owner, minutes_from_now=5)
    db.execute(text("UPDATE publishing_records SET status = 'PREPARING'"))
    db.commit()
    age(db, record, 30)  # a worker died half an hour ago
    run(tick(db, provider, cfg_for(media)))
    # recovered to EXPIRED; EXPIRED is not picked up automatically, the owner or a later pass
    # re-prepares it (no half-open record is left behind)
    assert status_of(db, record) == "EXPIRED"


def test_a_record_stuck_in_publishing_becomes_unknown_and_is_reconciled_not_republished(
    db, owner, media
):
    ready_world(db)
    provider = FakeReels()
    _, record = new_slot(db, owner, minutes_from_now=-1)
    run(tick(db, provider, cfg_for(media)))             # published normally
    db.execute(text("UPDATE publishing_records SET status = 'PUBLISHING', published_at = NULL,"
                    " meta_post_id = NULL"))
    db.commit()
    age(db, record, 15)  # the worker crashed after committing PUBLISHING
    before = provider.count("publish")
    report = run(tick(db, provider, cfg_for(media)))
    assert provider.count("publish") == before  # never promoted again
    assert status_of(db, record) == "PUBLISHED"   # reconciliation found it public
    assert any(n.startswith("recovered") for n in report.notes)


def test_recent_transient_states_are_not_touched(db, owner, media):
    ready_world(db)
    provider = FakeReels()
    _, record = new_slot(db, owner, minutes_from_now=5)
    db.execute(text("UPDATE publishing_records SET status = 'PREPARING'"))
    db.commit()
    age(db, record, 2)
    run(tick(db, provider, cfg_for(media)))
    assert status_of(db, record) == "PREPARING"


def test_an_unrecognised_error_during_promotion_is_unknown_not_a_clean_failure(
    db, owner, media
):
    ready_world(db)
    provider = FakeReels()

    attempts = []

    async def weird(**_):
        attempts.append(1)
        raise RuntimeError("socket exploded")

    provider.publish = weird
    _, record = new_slot(db, owner, minutes_from_now=-1)
    run(tick(db, provider, cfg_for(media)))
    # UNKNOWN_RESULT, then reconciled in the same tick: the draft was never promoted, so the
    # record returns to READY_TO_PUBLISH (and will pass the gate again on the next tick).
    assert status_of(db, record) in {"UNKNOWN_RESULT", "READY_TO_PUBLISH"}
    assert len(attempts) == 1 and provider.count("reconcile") == 1
