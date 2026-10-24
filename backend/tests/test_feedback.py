"""Owner verdicts on videos and the lessons distilled from them."""

import json
from pathlib import Path

from sqlalchemy import select

from app.api.deps import get_llm, get_settings
from app.core.config import Settings
from app.db.models import ContentItem, Lesson
from app.main import app
from tests.test_content_pipeline import go, media, world  # noqa: F401  (fixtures)
from tests.test_idea_scan import FakeLLM

REPO = Path(__file__).resolve().parents[2]


def make_items(world, n=3):
    items = []
    for _ in range(n):
        item, _ = go(world)
        items.append(item)
    return items


def test_every_endpoint_requires_login(client):
    fake = "00000000-0000-0000-0000-000000000000"
    for method, path in (("post", f"/api/content/{fake}/feedback"), ("get", "/api/lessons"),
                         ("get", "/api/feedback/summary"), ("post", "/api/lessons"),
                         ("post", "/api/lessons/distill"), ("patch", f"/api/lessons/{fake}"),
                         ("delete", f"/api/lessons/{fake}")):
        assert getattr(client, method)(path).status_code == 401, path


def test_feedback_is_saved_replaced_and_shown_on_the_ticket(world, client, auth_headers):
    item, _ = go(world)
    url = f"/api/content/{item.id}/feedback"
    r = client.post(url, headers=auth_headers,
                    json={"rating": 2, "labels": ["bad_text", "confusing_image"], "note": "kho hieu"})
    assert r.status_code == 200 and r.json()["labels"] == ["bad_text", "confusing_image"]
    client.post(url, headers=auth_headers, json={"rating": 5, "labels": ["great"]})
    ticket = client.get(f"/api/content/{item.id}/review", headers=auth_headers).json()
    assert ticket["feedback"]["rating"] == 5 and ticket["feedback"]["labels"] == ["great"]
    summary = client.get("/api/feedback/summary", headers=auth_headers).json()
    assert summary["count"] == 1 and summary["by_lane"]["daily_life"]["average"] == 5.0


def test_bad_feedback_is_rejected(world, client, auth_headers):
    item, _ = go(world)
    url = f"/api/content/{item.id}/feedback"
    assert client.post(url, headers=auth_headers, json={"rating": 6}).status_code == 422
    assert client.post(url, headers=auth_headers,
                       json={"rating": 3, "labels": ["nope"]}).status_code == 422
    assert client.post(url, headers=auth_headers, json={"rating": 3, "note": "x" * 501}
                       ).status_code == 422


def test_only_approved_lessons_reach_the_prompts(world, db):
    db.add_all([Lesson(text="Dat kinh lup sat net nut", lane="all", status="approved"),
                Lesson(text="Chua duyet", lane="all", status="proposed"),
                Lesson(text="Bi loai", lane="all", status="rejected"),
                Lesson(text="Chi cho rebus", lane="rebus", status="approved")])
    db.commit()
    go(world)  # a daily_life item
    sent = [json.loads(c[0]["text"]) for k, c in world.llm.calls if k in {"analyze", "image"}]
    assert sent and all(s["channel_lessons"] == ["Dat kinh lup sat net nut"] for s in sent)


def test_without_lessons_nothing_is_added(world):
    go(world)
    sent = [json.loads(c[0]["text"]) for k, c in world.llm.calls if k in {"analyze", "image"}]
    assert sent and all("channel_lessons" not in s for s in sent)


def test_lessons_can_be_added_approved_edited_and_deleted(client, owner, auth_headers, db):
    made = client.post("/api/lessons", headers=auth_headers,
                       json={"text": "Chu toi da 4 tu", "lane": "all"})
    assert made.status_code == 201 and made.json()["status"] == "approved"
    row = Lesson(text="De xuat", lane="rebus", status="proposed", source="auto")
    db.add(row)
    db.commit()
    r = client.patch(f"/api/lessons/{row.id}", headers=auth_headers,
                     json={"status": "approved", "text": "De xuat da sua"})
    assert r.json()["status"] == "approved" and r.json()["text"] == "De xuat da sua"
    assert client.get("/api/lessons", headers=auth_headers).json()["lessons"]
    assert client.delete(f"/api/lessons/{row.id}", headers=auth_headers).status_code == 204
    assert client.patch(f"/api/lessons/{row.id}", headers=auth_headers,
                        json={"status": "approved"}).status_code == 404


def test_distill_needs_enough_feedback_then_only_proposes(world, client, auth_headers, db):
    answer = {"lessons": [
        {"text": "Dat kinh lup sat net nut", "lane": "all", "reason": "3 video"},
        {"text": "Dat kinh lup sat net nut", "lane": "all", "reason": "trung"},
    ]}
    llm = FakeLLM(answer)
    app.dependency_overrides[get_llm] = lambda: llm
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, prompts_dir=str(REPO / "prompts"),
        schemas_dir=str(REPO / "docs" / "schemas"))
    try:
        assert client.post("/api/lessons/distill", headers=auth_headers).status_code == 409
        for item in make_items(world, 3):
            client.post(f"/api/content/{item.id}/feedback", headers=auth_headers,
                        json={"rating": 2, "labels": ["bad_text"], "note": "chu xau"})
        r = client.post("/api/lessons/distill", headers=auth_headers)
        assert r.status_code == 200 and len(r.json()["proposed"]) == 1  # duplicate dropped
        rows = db.scalars(select(Lesson)).all()
        assert [x.status for x in rows] == ["proposed"]  # nothing is applied automatically
        sent = json.loads(llm.calls[0][0]["text"])
        assert len(sent["feedback"]) == 3 and sent["feedback"][0]["note"] == "chu xau"
    finally:
        app.dependency_overrides.pop(get_llm, None)
        app.dependency_overrides.pop(get_settings, None)


def test_feedback_survives_in_the_table_per_item(world, client, auth_headers, db):
    item, _ = go(world)
    client.post(f"/api/content/{item.id}/feedback", headers=auth_headers, json={"rating": 4})
    assert db.scalar(select(ContentItem.id).where(ContentItem.id == item.id))
