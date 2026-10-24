"""The owner can take an approved post back (before it is public) and take a published one down."""

import asyncio

import pytest
from sqlalchemy import select, text

from app.api.deps import get_publish_providers
from app.db.base import utcnow
from app.db.models import ContentItem, PublishingRecord, TokenHealth
from app.main import app
from app.services import content_pipeline as cp
from app.services.scheduler import tick
from tests.conftest import OWNER_PASSWORD
from tests.fakes import FakeReels
from tests.test_content_pipeline import media, world  # noqa: F401  (fixtures)
from tests.test_scheduler import cfg_for


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def providers():
    fakes = {"facebook": FakeReels(), "youtube": FakeReels()}
    app.dependency_overrides[get_publish_providers] = lambda: fakes
    yield fakes
    app.dependency_overrides.pop(get_publish_providers, None)


def settings(db, **kw):
    sets = ", ".join(f"{k} = {str(v).lower()}" for k, v in kw.items())
    db.execute(text(f"UPDATE system_settings SET {sets}"))
    db.commit()


def approved_and_queued(world, client, headers, db, *, youtube=False):
    settings(db, publish_on_approve=True, publishing_kill_switch=False, automation_enabled=True,
             youtube_enabled=youtube)
    db.add(TokenHealth(provider="meta", valid=True, checked_at=utcnow()))
    db.add(TokenHealth(provider="youtube", valid=True, checked_at=utcnow()))
    db.commit()
    item = cp.start(world.db, world.owner, lane="daily_life", brief="Một con mèo lười")
    run(cp.run_until_wait(db, world.deps, item.id))
    r = client.post(f"/api/content/{item.id}/approve", headers=headers, json={})
    assert r.status_code == 200 and r.json()["publish"]["queued"] is True
    return item


def statuses(db):
    db.expire_all()
    return {r.platform: r.status for r in db.scalars(select(PublishingRecord))}


def test_a_queued_post_can_be_cancelled_and_the_item_returns_to_approved(
    world, client, auth_headers, db, providers
):
    item = approved_and_queued(world, client, auth_headers, db)
    r = client.post(f"/api/content/{item.id}/cancel-schedule", headers=auth_headers)
    assert r.status_code == 200 and r.json() == {"cancelled": ["facebook"], "status": "APPROVED"}
    assert statuses(db) == {"facebook": "CANCELLED"}
    # it can be scheduled again afterwards, for a time of the owner's choosing
    from datetime import timedelta

    when = (utcnow() + timedelta(hours=1)).isoformat()
    again = client.post(f"/api/content/{item.id}/schedule", headers=auth_headers,
                        json={"scheduled_for": when})
    assert again.status_code == 201 and again.json()["status"] == "SCHEDULED"


def test_cancelling_after_the_draft_was_uploaded_deletes_the_draft(
    world, client, auth_headers, db, providers
):
    item = approved_and_queued(world, client, auth_headers, db)
    # the worker prepares the private draft but the publish tick has not run yet
    db.execute(text("UPDATE publishing_records SET scheduled_for = now() + interval '10 minutes'"))
    db.commit()
    run(tick(db, providers, cfg_for(world.media)))
    assert statuses(db) == {"facebook": "READY_TO_PUBLISH"}
    draft = next(iter(providers["facebook"].drafts))
    r = client.post(f"/api/content/{item.id}/cancel-schedule", headers=auth_headers)
    assert r.status_code == 200
    assert providers["facebook"].drafts[draft] == "deleted"  # nothing is left at the platform
    assert statuses(db) == {"facebook": "CANCELLED"}
    assert db.get(ContentItem, item.id).status == "APPROVED"


def test_both_platforms_are_cancelled_together(world, client, auth_headers, db, providers):
    item = approved_and_queued(world, client, auth_headers, db, youtube=True)
    r = client.post(f"/api/content/{item.id}/cancel-schedule", headers=auth_headers)
    assert r.json()["cancelled"] == ["facebook", "youtube"]
    assert statuses(db) == {"facebook": "CANCELLED", "youtube": "CANCELLED"}


def test_an_upload_in_flight_cannot_be_cancelled_and_nothing_changes(
    world, client, auth_headers, db, providers
):
    item = approved_and_queued(world, client, auth_headers, db)
    db.execute(text("UPDATE publishing_records SET status = 'PREPARING'"))
    db.commit()
    r = client.post(f"/api/content/{item.id}/cancel-schedule", headers=auth_headers)
    assert r.status_code == 409 and r.json()["error"]["code"] == "busy"
    assert statuses(db) == {"facebook": "PREPARING"}


def test_there_is_nothing_to_cancel_for_an_item_that_was_never_queued(
    world, client, auth_headers, db, providers
):
    settings(db, publish_on_approve=False)
    item = cp.start(world.db, world.owner, lane="daily_life", brief="x y z")
    run(cp.run_until_wait(db, world.deps, item.id))
    client.post(f"/api/content/{item.id}/approve", headers=auth_headers, json={})
    r = client.post(f"/api/content/{item.id}/cancel-schedule", headers=auth_headers)
    assert r.status_code == 409 and r.json()["error"]["code"] == "nothing_to_cancel"


def test_an_approval_can_be_withdrawn_but_a_scheduled_one_must_be_cancelled_first(
    world, client, auth_headers, db, providers
):
    item = approved_and_queued(world, client, auth_headers, db)
    blocked = client.post(f"/api/content/{item.id}/reject", headers=auth_headers,
                          json={"note": "changed my mind"})
    assert blocked.status_code == 409  # still queued: cancel the schedule first
    client.post(f"/api/content/{item.id}/cancel-schedule", headers=auth_headers)
    ok = client.post(f"/api/content/{item.id}/reject", headers=auth_headers,
                     json={"note": "changed my mind"})
    assert ok.status_code == 200 and ok.json()["status"] == "REJECTED"


def test_retracting_a_published_post_needs_the_password_and_a_reason(
    world, client, auth_headers, db, providers
):
    item = approved_and_queued(world, client, auth_headers, db)
    for _ in range(2):
        run(tick(db, providers, cfg_for(world.media)))
    assert statuses(db) == {"facebook": "PUBLISHED"}
    url = f"/api/content/{item.id}/retract"
    wrong = client.post(url, headers=auth_headers, json={"reason": "sai nội dung", "password": "x" * 12})
    assert wrong.status_code == 403 and statuses(db) == {"facebook": "PUBLISHED"}
    short = client.post(url, headers=auth_headers, json={"reason": "", "password": OWNER_PASSWORD})
    assert short.status_code == 422
    ok = client.post(url, headers=auth_headers,
                     json={"reason": "sai nội dung", "password": OWNER_PASSWORD})
    assert ok.status_code == 200 and ok.json()["results"] == {"facebook": "retracted"}
    assert statuses(db) == {"facebook": "RETRACTED"}
    assert "deleted" in providers["facebook"].drafts.values()


def test_there_is_nothing_to_retract_before_publishing(world, client, auth_headers, db, providers):
    item = approved_and_queued(world, client, auth_headers, db)
    r = client.post(f"/api/content/{item.id}/retract", headers=auth_headers,
                    json={"reason": "thử", "password": OWNER_PASSWORD})
    assert r.status_code == 409 and r.json()["error"]["code"] == "nothing_to_retract"


def test_the_new_routes_need_a_login(client, owner):
    fake = "00000000-0000-0000-0000-000000000000"
    assert client.post(f"/api/content/{fake}/cancel-schedule").status_code == 401
    assert client.post(f"/api/content/{fake}/retract", json={}).status_code == 401
