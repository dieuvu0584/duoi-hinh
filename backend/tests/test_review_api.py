"""Review API (queue, explanation ticket, media) and admin-editable targets/voice."""

import pytest
from sqlalchemy import select, text

from app.api.deps import get_settings
from app.core.config import Settings
from app.db.models import AudioAsset, AuditEvent, LlmCall
from app.main import app
from app.providers.factory import get_reels_provider
from app.services import content_pipeline as cp
from app.services import content_service
from tests.conftest import OWNER_PASSWORD
from tests.test_content_pipeline import go, media, run, world  # noqa: F401, F811 (fixtures)

SETTINGS_URL = "/api/system/settings"


@pytest.fixture()
def served(world, client, auth_headers):  # noqa: F811
    """The app serves media from the same directory the fake pipeline wrote to."""
    app.dependency_overrides[get_settings] = lambda: world.cfg
    yield world
    app.dependency_overrides.pop(get_settings, None)


def pending(world):
    item, _ = go(world)
    assert item.status == "PENDING_APPROVAL"
    return item


def test_every_review_route_needs_a_login(client, owner):
    fake = "00000000-0000-0000-0000-000000000000"
    for path in ("/api/content", f"/api/content/{fake}/review",
                 f"/api/content/{fake}/media/reel_9x16", f"/api/content/{fake}/audio"):
        assert client.get(path).status_code == 401
    assert client.post(f"/api/content/{fake}/human-review/complete").status_code == 401


def test_the_queue_lists_what_needs_the_owner(served, client, auth_headers):
    item = pending(served)
    rows = client.get("/api/content", headers=auth_headers).json()["items"]
    assert [r["id"] for r in rows] == [str(item.id)]
    assert client.get("/api/content?status=APPROVED", headers=auth_headers).json()["items"] == []
    assert client.get("/api/content?status=NOPE", headers=auth_headers).status_code == 422
    both = client.get("/api/content?status=APPROVED,PENDING_APPROVAL", headers=auth_headers)
    assert [r["id"] for r in both.json()["items"]] == [str(item.id)]
    assert client.get("/api/content?status=APPROVED,NOPE", headers=auth_headers).status_code == 422


def test_the_ticket_has_everything_needed_to_decide(served, client, auth_headers):
    item = pending(served)
    t = client.get(f"/api/content/{item.id}/review", headers=auth_headers).json()
    assert t["status"] == "PENDING_APPROVAL" and t["can_approve"] is True
    assert t["revision"]["narration_text"] and t["revision"]["content_hash"]
    assert t["explanation"]["visual_explanation"]
    assert t["safety"]["final"]["decision"] == "allow" and t["safety"]["intake"]
    assert {v["target"] for v in t["videos"]} == {"reel_9x16", "youtube_16x9"}
    assert t["audio_url"] and t["needs_human_review_completion"] is False


def test_videos_and_audio_stream_for_the_current_revision(served, client, auth_headers):
    item = pending(served)
    t = client.get(f"/api/content/{item.id}/review", headers=auth_headers).json()
    for v in t["videos"]:
        r = client.get(v["url"], headers=auth_headers)
        assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"
        assert r.content == b"fake-video" and r.headers["cache-control"] == "private, no-store"
    assert client.get(t["audio_url"], headers=auth_headers).status_code == 200


def test_media_of_an_older_revision_is_not_served(served, client, auth_headers, db, owner):
    item = pending(served)
    content_service.edit_content(db, owner, item, {"caption": "Một chú thích khác hẳn"})
    t = client.get(f"/api/content/{item.id}/review", headers=auth_headers).json()
    assert t["videos"] == [] and t["can_approve"] is False  # the edit invalidated everything
    r = client.get(f"/api/content/{item.id}/media/reel_9x16", headers=auth_headers)
    assert r.status_code == 404


def test_a_media_reference_cannot_escape_the_media_directory(served, client, auth_headers, db):
    item = pending(served)
    (served.media.parent / "secret.txt").write_text("nope")
    db.execute(text("UPDATE video_assets SET storage_ref = '../secret.txt'"))
    db.commit()
    r = client.get(f"/api/content/{item.id}/media/reel_9x16", headers=auth_headers)
    assert r.status_code == 404 and b"nope" not in r.content


def test_a_human_review_must_be_completed_before_the_ticket_says_approve(
    served, client, auth_headers, db
):
    item = pending(served)
    revision = content_service.current_revision(db, item)
    content_service.record_safety_review(
        db, item=item, revision=revision, gate="final", decision="hold_for_human",
        risk_level="high", categories=["politics"], reviewer="llm:test", policy_version="t")
    db.commit()
    url = f"/api/content/{item.id}"
    t = client.get(f"{url}/review", headers=auth_headers).json()
    assert t["needs_human_review_completion"] is True and t["can_approve"] is False
    assert client.post(f"{url}/human-review/complete", headers=auth_headers).status_code == 200
    t = client.get(f"{url}/review", headers=auth_headers).json()
    assert t["needs_human_review_completion"] is False and t["can_approve"] is True
    assert db.scalar(select(AuditEvent).where(
        AuditEvent.action == "safety.human_review_complete"))


def test_a_blocked_review_cannot_be_cleared_by_a_human(served, client, auth_headers, db):
    item = pending(served)
    revision = content_service.current_revision(db, item)
    content_service.record_safety_review(
        db, item=item, revision=revision, gate="final", decision="block",
        risk_level="critical", categories=["politics"], reviewer="llm:test", policy_version="t")
    db.commit()
    r = client.post(f"/api/content/{item.id}/human-review/complete", headers=auth_headers)
    assert r.status_code == 409 and r.json()["error"]["code"] == "review_blocked"


# ----------------------------------------------------------------------- admin targets / voice
def put(client, headers, changes, password=None):
    body = {"changes": changes}
    if password is not None:
        body["password"] = password
    return client.put(SETTINGS_URL, json=body, headers=headers)


def test_the_voice_can_be_changed_without_a_password(client, owner, auth_headers):
    r = put(client, auth_headers, {"elevenlabs_voice_id": "NewVoice_123"})
    assert r.status_code == 200 and r.json()["elevenlabs_voice_id"] == "NewVoice_123"


def test_changing_the_page_needs_the_password(client, owner, auth_headers):
    assert put(client, auth_headers, {"facebook_page_id": "123456789012"}).status_code == 403
    bad = put(client, auth_headers, {"facebook_page_id": "123456789012"}, "wrong wrong wrong")
    assert bad.status_code == 403
    ok = put(client, auth_headers, {"facebook_page_id": "123456789012"}, OWNER_PASSWORD)
    assert ok.status_code == 200 and ok.json()["facebook_page_id"] == "123456789012"
    assert "meta_access_token" not in ok.json()  # the snapshot never carries secrets


def test_the_youtube_channel_needs_the_password_and_a_valid_id(client, owner, auth_headers):
    channel = "UC" + "a" * 22
    assert put(client, auth_headers, {"youtube_channel_id": channel}).status_code == 403
    assert put(client, auth_headers, {"youtube_channel_id": "not-a-channel"},
               OWNER_PASSWORD).status_code == 422
    ok = put(client, auth_headers, {"youtube_channel_id": channel}, OWNER_PASSWORD)
    assert ok.json()["youtube_channel_id"] == channel


@pytest.mark.parametrize("field,value", [
    ("facebook_page_id", "abc"), ("facebook_page_id", "12 34 56 78"),
    ("elevenlabs_voice_id", "has space"), ("elevenlabs_voice_id", "x" * 81),
])
def test_bad_identifiers_are_rejected(client, owner, auth_headers, field, value):
    assert put(client, auth_headers, {field: value}, OWNER_PASSWORD).status_code == 422


def test_an_empty_string_clears_the_setting(client, owner, auth_headers):
    put(client, auth_headers, {"elevenlabs_voice_id": "SomeVoice1"})
    r = put(client, auth_headers, {"elevenlabs_voice_id": ""})
    assert r.status_code == 200 and r.json()["elevenlabs_voice_id"] is None


def test_the_change_is_audited(client, owner, auth_headers, db):
    put(client, auth_headers, {"facebook_page_id": "123456789012"}, OWNER_PASSWORD)
    event = db.scalars(select(AuditEvent).where(AuditEvent.action == "settings.update")).one()
    assert event.event_metadata["after"] == {"facebook_page_id": "123456789012"}


def test_the_pipeline_uses_the_admin_voice_before_the_environment(world, db):
    db.execute(text("UPDATE system_settings SET elevenlabs_voice_id = 'AdminVoice99'"))
    db.commit()
    item, _ = go(world)
    assert content_service.current_revision(db, item).voice_id == "AdminVoice99"
    assert db.scalar(select(AudioAsset.voice_id)) == "AdminVoice99"  # the audio really used it


def test_without_any_voice_the_pipeline_waits_and_spends_nothing(world, db):
    cfg = world.cfg.model_copy(update={"elevenlabs_voice_id": None})
    world.deps.cfg = cfg
    item = cp.start(db, world.owner, lane="daily_life", brief="Mèo lười")
    outcome = run(cp.run_stage(db, world.deps, item.id))
    assert outcome.note == "waiting: voice_not_configured"
    assert db.scalar(select(LlmCall)) is None and not world.images.calls
    db.expire_all()
    assert db.get(type(item), item.id).status == "DRAFT"


def test_the_publishing_provider_uses_the_admin_page(monkeypatch):
    cfg = Settings(_env_file=None, publish_mode="live", meta_page_id="111111111111",
                   meta_access_token="tok_test_value")
    assert get_reels_provider(cfg)._page_id == "111111111111"
    assert get_reels_provider(cfg, "222222222222")._page_id == "222222222222"
    no_env = Settings(_env_file=None, publish_mode="live", meta_access_token="tok_test_value")
    assert get_reels_provider(no_env, "333333333333")._page_id == "333333333333"
    with pytest.raises(RuntimeError):
        get_reels_provider(no_env)
