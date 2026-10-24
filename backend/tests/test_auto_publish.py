"""Approve = publish now (no time to pick), still behind every gate."""

import asyncio

import pytest
from sqlalchemy import select, text

from app.db.base import utcnow
from app.db.models import ContentItem, PublishingRecord, TokenHealth
from app.services import content_pipeline as cp
from app.services.scheduler import tick
from tests.conftest import OWNER_PASSWORD
from tests.fakes import FakeReels
from tests.test_content_pipeline import media, world  # noqa: F401  (fixtures)
from tests.test_scheduler import cfg_for


def run(coro):
    return asyncio.run(coro)


def settings(db, **kw):
    sets = ", ".join(f"{k} = {str(v).lower()}" for k, v in kw.items())
    db.execute(text(f"UPDATE system_settings SET {sets}"))
    db.commit()


def pending(world, db):
    item = cp.start(world.db, world.owner, lane="daily_life", brief="Một con mèo lười")
    run(cp.run_until_wait(db, world.deps, item.id))
    db.expire_all()
    assert db.get(ContentItem, item.id).status == "PENDING_APPROVAL"
    return item


def approve(client, headers, item):
    r = client.post(f"/api/content/{item.id}/approve", headers=headers, json={})
    assert r.status_code == 200, r.text
    return r.json()


def records(db):
    db.expire_all()
    return list(db.scalars(select(PublishingRecord).order_by(PublishingRecord.platform)))


def test_approving_queues_an_immediate_post_when_everything_allows_it(
    world, client, auth_headers, db
):
    settings(db, publish_on_approve=True, publishing_kill_switch=False, automation_enabled=True)
    item = pending(world, db)
    out = approve(client, auth_headers, item)
    assert out["publish"] == {"queued": True, "platforms": ["facebook"]}
    [record] = records(db)
    assert record.platform == "facebook" and record.status == "SCHEDULED"
    assert abs((record.scheduled_for - utcnow()).total_seconds()) < 120
    assert db.get(ContentItem, item.id).status == "SCHEDULED"


def test_youtube_is_included_when_it_is_switched_on(world, client, auth_headers, db):
    settings(db, publish_on_approve=True, publishing_kill_switch=False, automation_enabled=True,
             youtube_enabled=True)
    item = pending(world, db)
    out = approve(client, auth_headers, item)
    assert out["publish"]["platforms"] == ["facebook", "youtube"]
    assert [r.platform for r in records(db)] == ["facebook", "youtube"]


@pytest.mark.parametrize("changes,reason", [
    (dict(publishing_kill_switch=True, automation_enabled=True), "kill_switch"),
    (dict(publishing_kill_switch=False, automation_enabled=False), "automation_disabled"),
    (dict(publish_on_approve=False, publishing_kill_switch=False, automation_enabled=True),
     "disabled"),
])
def test_when_something_forbids_it_the_approval_stands_but_nothing_is_queued(
    world, client, auth_headers, db, changes, reason
):
    base = dict(publish_on_approve=True, publishing_kill_switch=False, automation_enabled=True)
    settings(db, **{**base, **changes})
    item = pending(world, db)
    out = approve(client, auth_headers, item)
    assert out["publish"] == {"queued": False, "reason": reason}
    assert records(db) == []
    assert db.get(ContentItem, item.id).status == "APPROVED"  # can still be scheduled by hand


def test_an_approved_post_is_prepared_and_published_by_the_worker_without_waiting(
    world, client, auth_headers, db
):
    settings(db, publish_on_approve=True, publishing_kill_switch=False, automation_enabled=True,
             youtube_enabled=True)
    db.add(TokenHealth(provider="meta", valid=True, checked_at=utcnow()))
    db.add(TokenHealth(provider="youtube", valid=True, checked_at=utcnow()))
    db.commit()
    item = pending(world, db)
    approve(client, auth_headers, item)
    providers = {"facebook": FakeReels(), "youtube": FakeReels()}
    for _ in range(2):  # one tick prepares the drafts, the next promotes them
        run(tick(db, providers, cfg_for(world.media)))
    assert [r.status for r in records(db)] == ["PUBLISHED", "PUBLISHED"]
    assert db.get(ContentItem, item.id).status == "PUBLISHED"


def test_the_kill_switch_still_stops_an_already_queued_post(world, client, auth_headers, db):
    settings(db, publish_on_approve=True, publishing_kill_switch=False, automation_enabled=True)
    db.add(TokenHealth(provider="meta", valid=True, checked_at=utcnow()))
    db.commit()
    item = pending(world, db)
    approve(client, auth_headers, item)
    settings(db, publishing_kill_switch=True)  # the owner hits STOP before the worker publishes
    provider = FakeReels()
    for _ in range(2):
        run(tick(db, {"facebook": provider}, cfg_for(world.media)))
    assert provider.count("publish") == 0
    assert [r.status for r in records(db)] != ["PUBLISHED"]


def test_the_setting_is_visible_and_changing_it_needs_the_password(client, owner, auth_headers):
    url = "/api/system/settings"
    assert client.get(url, headers=auth_headers).json()["publish_on_approve"] is False  # test db
    denied = client.put(url, headers=auth_headers, json={"changes": {"publish_on_approve": True}})
    assert denied.status_code == 403
    ok = client.put(url, headers=auth_headers,
                    json={"changes": {"publish_on_approve": True}, "password": OWNER_PASSWORD})
    assert ok.status_code == 200 and ok.json()["publish_on_approve"] is True
