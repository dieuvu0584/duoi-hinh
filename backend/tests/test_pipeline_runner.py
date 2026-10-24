"""Pipeline runner (worker side) and the idea/retry/schedule API."""

from datetime import timedelta

import pytest
from sqlalchemy import select, text

from app.core.config import Settings
from app.db.base import utcnow
from app.db.models import ContentItem, LlmCall, PublishingRecord
from app.providers.pipeline_factory import build_pipeline_deps, missing_pipeline_keys
from app.services import content_pipeline as cp
from app.services import content_service, pipeline_runner
from tests.test_content_pipeline import media, run, world  # noqa: F401  (fixtures)


def start_draft(world, lane="daily_life", **kw):
    return cp.start(world.db, world.owner, lane=lane, brief="Một con mèo lười", **kw)


def reload(db, item):
    db.expire_all()
    return db.get(ContentItem, item.id)


def test_the_runner_carries_a_new_idea_to_pending_approval(world, db):
    item = start_draft(world)
    notes = run(pipeline_runner.run_pending(db, world.deps))
    assert reload(db, item).status == "PENDING_APPROVAL"
    assert notes and "PENDING_APPROVAL" in notes[0]


def test_nothing_runs_while_generation_is_paused(world, db):
    item = start_draft(world)
    db.execute(text("UPDATE system_settings SET generation_paused = true"))
    db.commit()
    assert run(pipeline_runner.run_pending(db, world.deps)) == ["generation_paused"]
    assert reload(db, item).status == "DRAFT" and db.scalar(select(LlmCall)) is None


def test_a_budget_stop_backs_off_then_resumes_when_money_is_available(world, db):
    item = start_draft(world)
    db.execute(text("UPDATE system_settings SET daily_budget_limit = 0"))
    db.commit()
    now = utcnow()
    first = run(pipeline_runner.run_pending(db, world.deps, now=now))
    assert "waiting: budget" in first[0]
    assert reload(db, item).pipeline_input["retry_after"]

    db.execute(text("UPDATE system_settings SET daily_budget_limit = 50"))
    db.commit()
    soon = run(pipeline_runner.run_pending(db, world.deps, now=now + timedelta(minutes=1)))
    assert soon == [] and reload(db, item).status == "DRAFT"  # still backing off: no new attempt

    later = run(pipeline_runner.run_pending(db, world.deps, now=now + timedelta(minutes=16)))
    assert later and reload(db, item).status == "PENDING_APPROVAL"
    assert "retry_after" not in reload(db, item).pipeline_input


def test_one_failing_item_does_not_stop_the_others(world, db, monkeypatch):
    bad = start_draft(world)
    good = start_draft(world)
    real = cp.run_until_wait

    async def flaky(session, deps, item_id, **kw):
        if item_id == bad.id:
            raise RuntimeError("boom")
        return await real(session, deps, item_id, **kw)

    monkeypatch.setattr(cp, "run_until_wait", flaky)
    run(pipeline_runner.run_pending(db, world.deps, limit=5))
    assert reload(db, good).status == "PENDING_APPROVAL"
    assert reload(db, bad).pipeline_input["retry_after"]  # backed off, not retried every tick


def test_finished_items_are_left_alone(world, db):
    item = start_draft(world)
    run(pipeline_runner.run_pending(db, world.deps))
    calls = db.scalars(select(LlmCall)).all()
    assert run(pipeline_runner.run_pending(db, world.deps)) == []
    assert len(db.scalars(select(LlmCall)).all()) == len(calls)
    assert reload(db, item).status == "PENDING_APPROVAL"


def test_the_real_provider_set_needs_all_three_keys():
    cfg = Settings(_env_file=None)
    assert missing_pipeline_keys(cfg) == ["ANTHROPIC_API_KEY", "FAL_API_KEY", "ELEVENLABS_API_KEY"]
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        build_pipeline_deps(cfg)


def test_the_real_provider_set_builds_from_keys_without_calling_anything():
    cfg = Settings(_env_file=None, anthropic_api_key="a", fal_api_key="f", elevenlabs_api_key="e",
                   prompts_dir="../prompts", schemas_dir="../docs/schemas")
    deps = build_pipeline_deps(cfg)
    assert deps.llm and deps.vision and deps.images and deps.speech and deps.prompts.analyze_topic


# ------------------------------------------------------------------------------------- API
def test_the_new_routes_need_a_login(client, owner):
    fake = "00000000-0000-0000-0000-000000000000"
    assert client.post("/api/content/ideas", json={}).status_code == 401
    assert client.post(f"/api/content/{fake}/retry").status_code == 401
    assert client.post(f"/api/content/{fake}/schedule", json={}).status_code == 401


def test_an_idea_is_queued_as_a_draft(client, owner, auth_headers, db):
    r = client.post("/api/content/ideas", headers=auth_headers,
                    json={"lane": "daily_life", "brief": "Sáng thứ Hai của con mèo"})
    assert r.status_code == 201 and r.json()["status"] == "DRAFT"
    item = db.get(ContentItem, r.json()["id"])
    assert item.pipeline_input["brief"] == "Sáng thứ Hai của con mèo"


def test_a_rebus_idea_needs_the_owners_answer(client, owner, auth_headers):
    r = client.post("/api/content/ideas", headers=auth_headers,
                    json={"lane": "rebus", "brief": "Một câu đố"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "answer_required"
    ok = client.post("/api/content/ideas", headers=auth_headers,
                     json={"lane": "rebus", "brief": "Một câu đố", "answer": "đầu voi đuôi chuột"})
    assert ok.status_code == 201


def test_the_news_lane_stays_closed_until_enabled(client, owner, auth_headers):
    r = client.post("/api/content/ideas", headers=auth_headers,
                    json={"lane": "news", "brief": "Một tin"})
    assert r.status_code == 409


def test_idea_input_is_validated(client, owner, auth_headers):
    for body in ({"lane": "x", "brief": "a"}, {"lane": "daily_life", "brief": ""},
                 {"lane": "daily_life", "brief": "a", "status": "APPROVED"}):
        assert client.post("/api/content/ideas", headers=auth_headers, json=body).status_code == 422


def test_retry_clears_the_back_off_only_for_items_in_the_pipeline(world, client, auth_headers, db):
    item = start_draft(world)
    pipeline_runner._set_retry(db, item, utcnow() + timedelta(hours=1))
    r = client.post(f"/api/content/{item.id}/retry", headers=auth_headers)
    assert r.status_code == 200 and "retry_after" not in reload(db, item).pipeline_input
    done = start_draft(world)
    run(cp.run_until_wait(db, world.deps, done.id))
    assert client.post(f"/api/content/{done.id}/retry",
                       headers=auth_headers).status_code == 409


def approved(world, client, auth_headers):
    item = start_draft(world)
    run(cp.run_until_wait(world.db, world.deps, item.id))
    assert client.post(f"/api/content/{item.id}/approve", headers=auth_headers,
                       json={}).status_code == 200
    return item


def test_an_approved_item_is_scheduled_with_its_reel(world, client, auth_headers, db):
    item = approved(world, client, auth_headers)
    when = (utcnow() + timedelta(hours=2)).isoformat()
    r = client.post(f"/api/content/{item.id}/schedule", headers=auth_headers,
                    json={"scheduled_for": when})
    assert r.status_code == 201 and r.json()["status"] == "SCHEDULED"
    record = db.scalar(select(PublishingRecord))
    assert record.video_ref.startswith("video/reel_9x16") and record.video_ref
    assert reload(db, item).status == "SCHEDULED"


def test_scheduling_needs_approval_a_future_time_and_a_timezone(world, client, auth_headers, db):
    item = start_draft(world)
    run(cp.run_until_wait(world.db, world.deps, item.id))
    future = (utcnow() + timedelta(hours=2)).isoformat()
    # not approved yet -> the state machine refuses
    r = client.post(f"/api/content/{item.id}/schedule", headers=auth_headers,
                    json={"scheduled_for": future})
    assert r.status_code == 409
    client.post(f"/api/content/{item.id}/approve", headers=auth_headers, json={})
    past = (utcnow() - timedelta(hours=1)).isoformat()
    assert client.post(f"/api/content/{item.id}/schedule", headers=auth_headers,
                       json={"scheduled_for": past}).status_code == 422
    naive = (utcnow() + timedelta(hours=2)).replace(tzinfo=None).isoformat()
    assert client.post(f"/api/content/{item.id}/schedule", headers=auth_headers,
                       json={"scheduled_for": naive}).status_code == 422
    assert db.scalar(select(PublishingRecord)) is None


def test_an_edit_after_approval_cannot_be_scheduled_without_media(
    world, client, auth_headers, db
):
    item = approved(world, client, auth_headers)
    # a manual edit creates a revision with no rendered video; scheduling must refuse
    item = reload(db, item)
    item.status = "APPROVED"
    content_service.edit_content(db, world.owner, item, {"caption": "Chú thích mới hoàn toàn"})
    item = reload(db, item)
    item.status = "APPROVED"
    db.commit()
    r = client.post(f"/api/content/{item.id}/schedule", headers=auth_headers,
                    json={"scheduled_for": (utcnow() + timedelta(hours=2)).isoformat()})
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_video"


# ------------------------------------------------- findings from the first live run (2026-10-22)
def test_emoji_never_reach_the_on_screen_text(world, db):
    from tests.test_content_pipeline import concept

    world.llm.script("analyze", concept(overlay_text="Hộp nhỏ 📦 | Mèo ❤️ vừa khít"))
    item = start_draft(world)
    run(cp.run_until_wait(db, world.deps, item.id))
    overlay = content_service.current_revision(db, reload(db, item)).overlay_text
    assert overlay == "Hộp nhỏ | Mèo vừa khít"


def test_the_renderer_strips_what_the_font_cannot_draw():
    from app.services.render_service import strip_unrenderable

    assert strip_unrenderable("Xin chào 😼📦  bạn ❤️!") == "Xin chào bạn !"
    plain = "Đường Lê Lợi, “ngày” 25/10 – hết"
    assert strip_unrenderable(plain) == plain


# ------------------------------------------------------------------------- YouTube scheduling
def test_scheduling_on_both_platforms_creates_two_records(world, client, auth_headers, db):
    item = approved(world, client, auth_headers)
    db.execute(text("UPDATE system_settings SET youtube_enabled = true"))
    db.commit()
    when = (utcnow() + timedelta(hours=2)).isoformat()
    r = client.post(f"/api/content/{item.id}/schedule", headers=auth_headers,
                    json={"scheduled_for": when, "platforms": ["facebook", "youtube"]})
    assert r.status_code == 201
    assert [x["platform"] for x in r.json()["records"]] == ["facebook", "youtube"]
    records = {x.platform: x for x in db.scalars(select(PublishingRecord))}
    assert records["facebook"].video_ref.startswith("video/reel_9x16")
    assert records["youtube"].video_ref.startswith("video/youtube_16x9")  # the 16:9 render
    assert ":youtube:" in records["youtube"].intent_key


def test_youtube_cannot_be_scheduled_while_it_is_switched_off(world, client, auth_headers, db):
    item = approved(world, client, auth_headers)
    when = (utcnow() + timedelta(hours=2)).isoformat()
    r = client.post(f"/api/content/{item.id}/schedule", headers=auth_headers,
                    json={"scheduled_for": when, "platforms": ["youtube"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "youtube_disabled"
    assert db.scalar(select(PublishingRecord)) is None  # nothing half-scheduled


def test_the_default_is_facebook_only_and_unknown_platforms_are_refused(
    world, client, auth_headers, db
):
    item = approved(world, client, auth_headers)
    when = (utcnow() + timedelta(hours=2)).isoformat()
    bad = client.post(f"/api/content/{item.id}/schedule", headers=auth_headers,
                      json={"scheduled_for": when, "platforms": ["tiktok"]})
    assert bad.status_code == 422
    ok = client.post(f"/api/content/{item.id}/schedule", headers=auth_headers,
                     json={"scheduled_for": when})
    assert ok.status_code == 201 and [x["platform"] for x in ok.json()["records"]] == ["facebook"]


def test_switching_youtube_on_needs_the_password(client, owner, auth_headers):
    url = "/api/system/settings"
    denied = client.put(url, headers=auth_headers, json={"changes": {"youtube_enabled": True}})
    assert denied.status_code == 403
    from tests.conftest import OWNER_PASSWORD

    ok = client.put(url, headers=auth_headers,
                    json={"changes": {"youtube_enabled": True}, "password": OWNER_PASSWORD})
    assert ok.status_code == 200 and ok.json()["youtube_enabled"] is True


# ------------------------------------------------------------------------- resume a held item
def held_by_render_failure(world, db):
    from app.services.render_service import RenderError
    from tests.test_content_pipeline import fake_render

    good = fake_render(world.media)
    state = {"fail": True}

    def flaky(*args, **kwargs):
        if state["fail"]:
            raise RenderError("ffmpeg could not run: TimeoutExpired")
        return good(*args, **kwargs)

    world.deps.render_both = flaky
    item = start_draft(world)
    run(cp.run_until_wait(db, world.deps, item.id))
    assert reload(db, item).status == "HELD_FOR_HUMAN"
    state["fail"] = False
    return item


def test_the_ticket_says_why_an_item_is_held_and_what_was_asked(world, client, auth_headers, db):
    item = held_by_render_failure(world, db)
    t = client.get(f"/api/content/{item.id}/review", headers=auth_headers).json()
    assert t["hold"]["stage"] == "render" and t["hold"]["can_resume"] is True
    assert "ffmpeg" in t["hold"]["reason"]
    assert t["source"]["brief"] == "Một con mèo lười"


def test_a_failed_render_can_be_resumed_by_the_owner(world, client, auth_headers, db):
    item = held_by_render_failure(world, db)
    r = client.post(f"/api/content/{item.id}/resume", headers=auth_headers)
    assert r.status_code == 200 and r.json()["status"] == "RENDERING"
    calls_before = len(world.images.calls)
    run(cp.run_until_wait(db, world.deps, item.id))
    assert reload(db, item).status == "PENDING_APPROVAL"
    assert len(world.images.calls) == calls_before  # the image already bought is not bought again


def test_a_hold_from_a_review_finding_cannot_be_resumed(world, client, auth_headers, db):
    from tests.test_content_pipeline import concept

    world.llm.script("analyze", concept("rebus", answer_or_message="Bánh tét"))
    item = start_draft(world, lane="rebus", answer="Bánh chưng")
    run(cp.run_until_wait(db, world.deps, item.id))
    assert reload(db, item).status == "HELD_FOR_HUMAN"  # the model changed the frozen answer
    r = client.post(f"/api/content/{item.id}/resume", headers=auth_headers)
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_resumable"
    assert client.get(f"/api/content/{item.id}/review", headers=auth_headers).json()[
        "hold"]["can_resume"] is False


def test_only_held_items_can_be_resumed(world, client, auth_headers, db):
    item = start_draft(world)
    r = client.post(f"/api/content/{item.id}/resume", headers=auth_headers)
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_held"
    assert client.post("/api/content/00000000-0000-0000-0000-000000000000/resume").status_code == 401


def test_a_budget_wait_is_explained_on_the_ticket_and_cleared_when_it_moves(
    world, client, auth_headers, db
):
    item = start_draft(world)
    db.execute(text("UPDATE system_settings SET daily_budget_limit = 0"))
    db.commit()
    run(pipeline_runner.run_pending(db, world.deps))
    t = client.get(f"/api/content/{item.id}/review", headers=auth_headers).json()
    assert "budget_" in t["waiting"]["reason"] and t["waiting"]["retry_after"]

    db.execute(text("UPDATE system_settings SET daily_budget_limit = 50"))
    db.commit()
    client.post(f"/api/content/{item.id}/retry", headers=auth_headers)  # the owner presses retry
    run(pipeline_runner.run_pending(db, world.deps))
    t = client.get(f"/api/content/{item.id}/review", headers=auth_headers).json()
    assert t["waiting"] is None and t["status"] == "PENDING_APPROVAL"


def test_a_poor_qa_result_reaches_the_owner_with_a_warning(world, client, auth_headers, db):
    from tests.test_content_pipeline import qa

    world.llm.script("qa", qa(40, "reject", issues=["c1: nobody whispers"]))
    item = start_draft(world)
    run(cp.run_until_wait(db, world.deps, item.id))
    t = client.get(f"/api/content/{item.id}/review", headers=auth_headers).json()
    assert t["status"] == "PENDING_APPROVAL" and t["can_approve"] is True
    flag = t["explanation"]["qa_flag"]
    assert flag["score"] == 40 and "c1: nobody whispers" in flag["issues"]
    assert t["source"]["brief"]


def test_an_owner_rejection_has_no_hold_but_a_system_rejection_is_explained(
    world, client, auth_headers, db
):
    item = start_draft(world)
    run(cp.run_until_wait(db, world.deps, item.id))
    client.post(f"/api/content/{item.id}/reject", headers=auth_headers, json={"note": "no"})
    t = client.get(f"/api/content/{item.id}/review", headers=auth_headers).json()
    assert t["status"] == "REJECTED" and t["hold"]["kind"] == "rejected"
    assert t["hold"]["can_resume"] is False
    r = client.post(f"/api/content/{item.id}/resume", headers=auth_headers)
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_held"
