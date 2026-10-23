"""One approved content, two platforms: separate records, providers, gates and token checks."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.errors import ApiError
from app.db.base import utcnow
from app.db.models import PublishingRecord, TokenHealth
from app.services import content_service, token_health_service
from app.services import publishing_service as ps
from app.services.scheduler import tick
from tests.fakes import FakeReels
from tests.test_publish_gate import REVISION, drive_to_pending, fresh_token, open_settings
from tests.test_scheduler import automate, cfg_for, media, status_of  # noqa: F401  (fixture)


def run(coro):
    return asyncio.run(coro)


def approved_item(db, owner):
    item = content_service.create_content(db, owner, lane="rebus", revision=dict(REVISION))
    drive_to_pending(db, owner, item)
    content_service.approve(db, owner, item)
    return item


def schedule_both(db, owner, *, minutes=-1):
    item = approved_item(db, owner)
    when = utcnow() + timedelta(minutes=minutes)
    fb = ps.schedule(db, item=item, scheduled_for=when, video_ref="reels/a.mp4", platform="facebook")
    yt = ps.schedule(db, item=item, scheduled_for=when, video_ref="reels/a.mp4", platform="youtube")
    return item, fb, yt


def youtube_token(db, *, valid=True):
    db.add(TokenHealth(provider="youtube", valid=valid, checked_at=utcnow()))
    db.commit()


def world(db, *, youtube=True):
    open_settings(db, youtube_enabled=str(youtube).lower())
    fresh_token(db)
    youtube_token(db)
    automate(db)


# --------------------------------------------------------------------------- scheduling
def test_one_approved_item_can_be_scheduled_on_both_platforms(db, owner):
    item, fb, yt = schedule_both(db, owner)
    assert fb.platform == "facebook" and yt.platform == "youtube"
    assert fb.intent_key != yt.intent_key and ":reel_9x16:" in fb.intent_key
    assert ":youtube_16x9:" in yt.intent_key
    db.expire_all()
    assert db.get(type(item), item.id).status == "SCHEDULED"


def test_the_same_platform_cannot_be_scheduled_twice(db, owner):
    item, _fb, _yt = schedule_both(db, owner)
    with pytest.raises(ApiError) as err:
        ps.schedule(db, item=item, scheduled_for=utcnow(), platform="youtube")
    assert err.value.code == "already_scheduled"


def test_an_unknown_platform_is_refused(db, owner):
    item = approved_item(db, owner)
    with pytest.raises(ApiError) as err:
        ps.schedule(db, item=item, scheduled_for=utcnow(), platform="tiktok")
    assert err.value.code == "bad_platform"


def test_cancelling_one_platform_keeps_the_item_scheduled_for_the_other(db, owner):
    item, fb, yt = schedule_both(db, owner, minutes=60)
    run(ps.cancel(db, FakeReels(), yt.id))
    db.expire_all()
    assert db.get(type(item), item.id).status == "SCHEDULED"  # Facebook is still pending
    run(ps.cancel(db, FakeReels(), fb.id))
    db.expire_all()
    assert db.get(type(item), item.id).status == "APPROVED"  # nothing left: free again


# --------------------------------------------------------------------------- the scheduler
def test_each_platform_is_prepared_and_published_by_its_own_provider(db, owner, media):
    world(db)
    fb_provider, yt_provider = FakeReels(), FakeReels()
    _, fb, yt = schedule_both(db, owner)
    providers = {"facebook": fb_provider, "youtube": yt_provider}
    run(tick(db, providers, cfg_for(media)))
    run(tick(db, providers, cfg_for(media)))
    assert status_of(db, fb) == "PUBLISHED" and status_of(db, yt) == "PUBLISHED"
    assert fb_provider.count("publish") == 1 and yt_provider.count("publish") == 1
    assert fb_provider.count("prepare") == 1 and yt_provider.count("prepare") == 1


def test_youtube_switched_off_is_never_prepared_or_published(db, owner, media):
    world(db, youtube=False)
    fb_provider, yt_provider = FakeReels(), FakeReels()
    _, fb, yt = schedule_both(db, owner)
    run(tick(db, {"facebook": fb_provider, "youtube": yt_provider}, cfg_for(media)))
    run(tick(db, {"facebook": fb_provider, "youtube": yt_provider}, cfg_for(media)))
    assert status_of(db, fb) == "PUBLISHED"
    assert status_of(db, yt) == "BLOCKED" and yt_provider.calls == []


def test_a_missing_youtube_provider_blocks_only_youtube(db, owner, media):
    world(db)
    fb_provider = FakeReels()
    _, fb, yt = schedule_both(db, owner)
    run(tick(db, {"facebook": fb_provider}, cfg_for(media)))
    run(tick(db, {"facebook": fb_provider}, cfg_for(media)))
    assert status_of(db, fb) == "PUBLISHED" and status_of(db, yt) == "BLOCKED"


def test_the_gate_uses_the_token_of_the_records_own_platform(db, owner, media):
    open_settings(db, youtube_enabled="true")
    fresh_token(db)  # only Facebook has a valid token
    automate(db)
    fb_provider, yt_provider = FakeReels(), FakeReels()
    _, fb, yt = schedule_both(db, owner)
    for _ in range(2):
        run(tick(db, {"facebook": fb_provider, "youtube": yt_provider}, cfg_for(media)))
    assert status_of(db, fb) == "PUBLISHED"
    assert status_of(db, yt) != "PUBLISHED" and yt_provider.count("publish") == 0


def test_one_post_on_two_platforms_counts_once_toward_the_daily_limit(db, owner, media):
    open_settings(db, youtube_enabled="true", max_publishes_per_day="1")
    fresh_token(db)
    youtube_token(db)
    automate(db)
    providers = {"facebook": FakeReels(), "youtube": FakeReels()}
    _, fb, yt = schedule_both(db, owner)
    for _ in range(2):
        run(tick(db, providers, cfg_for(media)))
    assert status_of(db, fb) == "PUBLISHED" and status_of(db, yt) == "PUBLISHED"


def test_a_single_provider_still_serves_every_platform(db, owner, media):
    world(db)
    provider = FakeReels()  # the old calling convention (one provider) keeps working
    _, fb, yt = schedule_both(db, owner)
    for _ in range(2):
        run(tick(db, provider, cfg_for(media)))
    assert status_of(db, fb) == "PUBLISHED" and status_of(db, yt) == "PUBLISHED"


# --------------------------------------------------------------------------- token health
class TokenProvider:
    def __init__(self, result=True, raises=False):
        self.result, self.raises, self.calls = result, raises, 0

    async def check_token(self):
        self.calls += 1
        if self.raises:
            raise RuntimeError("boom")
        return self.result


def latest(db, name):
    db.expire_all()
    return db.scalar(select(TokenHealth).where(TokenHealth.provider == name)
                     .order_by(TokenHealth.checked_at.desc()).limit(1))


def test_token_checks_are_recorded_per_provider_name(db):
    out = run(token_health_service.refresh_if_due(
        db, {"facebook": TokenProvider(True), "youtube": TokenProvider(False)}))
    assert out == {"meta": True, "youtube": False}
    assert latest(db, "meta").valid is True
    bad = latest(db, "youtube")
    assert bad.valid is False and bad.error == "token_rejected"


def test_a_fresh_valid_result_is_not_checked_again(db):
    provider = TokenProvider(True)
    run(token_health_service.refresh_if_due(db, {"facebook": provider}))
    run(token_health_service.refresh_if_due(db, {"facebook": provider}))
    assert provider.calls == 1
    run(token_health_service.refresh_if_due(db, {"facebook": provider}, force=True))
    assert provider.calls == 2


def test_an_invalid_result_is_rechecked_every_time(db):
    provider = TokenProvider(False)
    run(token_health_service.refresh_if_due(db, {"facebook": provider}))
    run(token_health_service.refresh_if_due(db, {"facebook": provider}))
    assert provider.calls == 2


def test_a_check_that_crashes_reads_as_not_valid(db):
    run(token_health_service.refresh_if_due(db, {"facebook": TokenProvider(raises=True)}))
    row = latest(db, "meta")
    assert row.valid is False and row.error == "RuntimeError"


def test_old_results_are_refreshed(db):
    provider = TokenProvider(True)
    run(token_health_service.refresh_if_due(db, {"facebook": provider}))
    later = utcnow() + token_health_service.CHECK_EVERY + timedelta(minutes=1)
    run(token_health_service.refresh_if_due(db, {"facebook": provider}, now=later))
    assert provider.calls == 2


def test_the_publish_record_table_allows_only_known_platforms(db, owner):
    item = approved_item(db, owner)
    rev = content_service.current_revision(db, item)
    from sqlalchemy.exc import IntegrityError

    db.add(PublishingRecord(revision_id=rev.id, platform="tiktok", content_hash=rev.content_hash,
                            intent_key="x", status="SCHEDULED"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_nothing_is_uploaded_while_the_platform_token_is_not_valid(db, owner, media):
    open_settings(db, youtube_enabled="true")
    fresh_token(db)  # Facebook fine, YouTube has no valid check
    automate(db)
    fb_provider, yt_provider = FakeReels(), FakeReels()
    _, fb, yt = schedule_both(db, owner, minutes=5)
    report = run(tick(db, {"facebook": fb_provider, "youtube": yt_provider}, cfg_for(media)))
    assert "token_not_valid:youtube" in report.notes
    assert yt_provider.calls == []  # no private draft that could never be published or removed
    assert status_of(db, yt) == "SCHEDULED" and status_of(db, fb) == "READY_TO_PUBLISH"


def test_the_reason_for_a_rejected_token_is_recorded(db):
    class Weak(TokenProvider):
        last_problem = "missing_scope_youtube"

    run(token_health_service.refresh_if_due(db, {"youtube": Weak(False)}))
    assert latest(db, "youtube").error == "missing_scope_youtube"
