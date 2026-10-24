"""The owner may approve an item whose automatic review failed to finish, but never one held for a
real finding."""

import asyncio

from sqlalchemy import select, text

from app.core.config import Settings
from app.db.models import Approval, ContentItem, SafetyReview
from app.services import content_pipeline as cp
from app.services import content_service, llm_service
from tests.test_content_pipeline import concept, media, world  # noqa: F401  (fixtures)


def run(coro):
    return asyncio.run(coro)


def held_after_invalid_safety_answer(world, db):
    """A finished video whose final safety review came back malformed twice."""
    real = llm_service.call_json
    calls = {"n": 0}

    async def flaky(*args, **kwargs):
        if kwargs.get("purpose") == "safety_review":
            calls["n"] += 1
            raise llm_service.LlmInvalidOutput("safety_review: explanation too long")
        return await real(*args, **kwargs)

    llm_service.call_json = flaky
    try:
        item = cp.start(world.db, world.owner, lane="daily_life", brief="Một con mèo lười")
        run(cp.run_until_wait(db, world.deps, item.id))
    finally:
        llm_service.call_json = real
    db.expire_all()
    row = db.get(ContentItem, item.id)
    assert row.status == "HELD_FOR_HUMAN", row.status
    return row


def test_the_ticket_says_the_hold_can_be_overridden(world, client, auth_headers, db):
    item = held_after_invalid_safety_answer(world, db)
    t = client.get(f"/api/content/{item.id}/review", headers=auth_headers).json()
    assert t["hold"]["stage"] == "safety" and t["hold"]["reason"].startswith("LlmInvalidOutput")
    assert t["hold"]["can_override"] is True and t["hold"]["can_resume"] is False


def test_the_owner_can_approve_it_and_the_owners_review_is_recorded(
    world, client, auth_headers, db
):
    item = held_after_invalid_safety_answer(world, db)
    r = client.post(f"/api/content/{item.id}/approve-held", headers=auth_headers,
                    json={"note": "Đã xem kỹ"})
    assert r.status_code == 200 and r.json()["status"] == "APPROVED"
    assert r.json()["publish"]["queued"] is False  # kill switch / automation still decide
    revision = content_service.current_revision(db, item)
    review = db.scalar(select(SafetyReview).where(
        SafetyReview.revision_id == revision.id, SafetyReview.gate == "final"))
    assert review.reviewer.startswith("owner:") and review.decision == "allow"
    approval = db.scalar(select(Approval).where(Approval.revision_id == revision.id))
    assert approval.decision == "approved" and approval.content_hash == revision.content_hash


def test_a_rebus_that_gives_its_answer_away_cannot_be_forced_through(
    world, client, auth_headers, db
):
    item = held_after_invalid_safety_answer(world, db)
    revision = content_service.current_revision(db, item)
    db.execute(text("UPDATE content_items SET lane = 'rebus' WHERE id = :i"), {"i": item.id})
    db.commit()
    # a leaked answer cannot be overridden (the text check is deterministic)
    content_service.add_revision(db, None, db.get(ContentItem, item.id), {
        "answer_text": "Bánh chưng", "caption": "Đây là bánh chưng nhé", "hashtags": [],
        "overlay_text": "x", "narration_text": "y", "voice_id": revision.voice_id,
        "media_hash": revision.media_hash, "audio_hash": revision.audio_hash,
        "is_ai_generated": True, "template_id": revision.template_id})
    db.commit()
    r = client.post(f"/api/content/{item.id}/approve-held", headers=auth_headers, json={})
    assert r.status_code in (409, 422)


def test_a_hold_for_a_real_finding_is_not_overridable(world, client, auth_headers, db):
    world.llm.script("analyze", concept("rebus", answer_or_message="Bánh tét"))
    item = cp.start(world.db, world.owner, lane="rebus", brief="câu đố", answer="Bánh chưng")
    run(cp.run_until_wait(db, world.deps, item.id))  # the model changed the frozen answer
    db.expire_all()
    assert db.get(ContentItem, item.id).status == "HELD_FOR_HUMAN"
    t = client.get(f"/api/content/{item.id}/review", headers=auth_headers).json()
    assert t["hold"]["can_override"] is False
    r = client.post(f"/api/content/{item.id}/approve-held", headers=auth_headers, json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_overridable"


def test_a_failed_render_is_retried_not_overridden(world, client, auth_headers, db):
    from app.services.render_service import RenderError

    def broken(*a, **k):
        raise RenderError("ffmpeg could not run")

    world.deps.render_both = broken
    item = cp.start(world.db, world.owner, lane="daily_life", brief="Mèo lười")
    run(cp.run_until_wait(db, world.deps, item.id))
    t = client.get(f"/api/content/{item.id}/review", headers=auth_headers).json()
    assert t["hold"]["can_resume"] is True and t["hold"]["can_override"] is False


def test_only_held_items_and_logged_in_owners(client, owner, auth_headers, world, db):
    item = cp.start(world.db, world.owner, lane="daily_life", brief="Mèo lười")
    r = client.post(f"/api/content/{item.id}/approve-held", headers=auth_headers, json={})
    assert r.status_code == 409
    assert client.post(f"/api/content/{item.id}/approve-held", json={}).status_code == 401


def test_the_blacklist_applies_to_an_override_too(world, client, auth_headers, db):
    item = held_after_invalid_safety_answer(world, db)
    from app.api import deps
    from app.main import app

    caption = content_service.current_revision(db, item).caption
    forbidden = caption.split()[0].strip(",.!?")
    cfg = Settings(_env_file=None, blacklist_words=forbidden)
    app.dependency_overrides[deps.get_settings] = lambda: cfg
    try:
        r = client.post(f"/api/content/{item.id}/approve-held", headers=auth_headers, json={})
        assert r.status_code == 409 and r.json()["error"]["code"] == "text_check_failed"
    finally:
        app.dependency_overrides.pop(deps.get_settings, None)
    assert db.get(ContentItem, item.id).status == "HELD_FOR_HUMAN"  # nothing was changed
