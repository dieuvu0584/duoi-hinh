from tests.conftest import OWNER_PASSWORD

REVISION = {"answer_text": "Bánh chưng", "caption": "Đoán thử nhé!", "hashtags": ["#doan"],
            "media_hash": "a" * 64}


def create(client, headers, lane="rebus", **extra):
    return client.post("/api/content", headers=headers,
                       json={"lane": lane, "revision": REVISION, **extra})


def test_create_and_read_content(client, owner, auth_headers):
    resp = create(client, auth_headers)
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "DRAFT" and body["current_revision"]["revision_no"] == 1
    assert len(body["current_revision"]["content_hash"]) == 64
    got = client.get(f"/api/content/{body['id']}", headers=auth_headers)
    assert got.status_code == 200 and got.json()["id"] == body["id"]


def test_unknown_content_is_404(client, owner, auth_headers):
    resp = client.get("/api/content/00000000-0000-0000-0000-000000000000", headers=auth_headers)
    assert resp.status_code == 404 and resp.json()["error"]["code"] == "not_found"


def test_edit_creates_a_new_revision_with_a_new_hash(client, owner, auth_headers):
    first = create(client, auth_headers).json()
    edited = client.post(f"/api/content/{first['id']}/revisions", headers=auth_headers,
                         json={**REVISION, "caption": "Caption mới"})
    assert edited.status_code == 201
    second = edited.json()["current_revision"]
    assert second["revision_no"] == 2
    assert second["content_hash"] != first["current_revision"]["content_hash"]
    third = client.post(f"/api/content/{first['id']}/revisions", headers=auth_headers,
                        json={**REVISION, "caption": "Lần ba"}).json()["current_revision"]
    assert third["revision_no"] == 3


def test_approve_requires_pending_approval(client, owner, auth_headers):
    item = create(client, auth_headers).json()
    resp = client.post(f"/api/content/{item['id']}/approve", headers=auth_headers, json={})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_state_transition"
    status = client.get(f"/api/content/{item['id']}", headers=auth_headers).json()["status"]
    assert status == "DRAFT"


def test_news_is_refused_until_the_owner_enables_the_lane(client, owner, auth_headers):
    resp = create(client, auth_headers, lane="news", fresh_until="2030-01-01T00:00:00Z")
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "news_lane_disabled"
    on = client.put("/api/system/settings", headers=auth_headers,
                    json={"changes": {"news_lane_enabled": True}, "password": OWNER_PASSWORD})
    assert on.status_code == 200
    assert create(client, auth_headers, lane="news",
                  fresh_until="2030-01-01T00:00:00Z").status_code == 201
    assert create(client, auth_headers, lane="news").status_code == 422


def test_input_is_validated(client, owner, auth_headers):
    bad_hash = client.post("/api/content", headers=auth_headers,
                           json={"lane": "rebus", "revision": {"media_hash": "not-a-hash"}})
    assert bad_hash.status_code == 422
    bad_lane = client.post("/api/content", headers=auth_headers,
                           json={"lane": "sports", "revision": REVISION})
    assert bad_lane.status_code == 422
    extra = client.post("/api/content", headers=auth_headers,
                        json={"lane": "rebus", "revision": {**REVISION, "status": "APPROVED"}})
    assert extra.status_code == 422


def test_cannot_set_status_or_hash_through_the_api(client, owner, auth_headers):
    item = create(client, auth_headers).json()
    forged = client.post(f"/api/content/{item['id']}/revisions", headers=auth_headers,
                         json={**REVISION, "content_hash": "f" * 64})
    assert forged.status_code == 422


def test_rebus_answer_must_not_appear_in_public_text(client, owner, auth_headers):
    for field in ("caption", "overlay_text", "narration_text"):
        bad = {**REVISION, field: "Đây chính là bánh chưng nhé"}
        resp = client.post("/api/content", headers=auth_headers,
                           json={"lane": "rebus", "revision": bad})
        assert resp.status_code == 422, field
        assert resp.json()["error"]["code"] == "answer_leak"


def test_answer_leak_is_also_blocked_when_editing(client, owner, auth_headers):
    item = create(client, auth_headers).json()
    resp = client.post(f"/api/content/{item['id']}/revisions", headers=auth_headers,
                       json={**REVISION, "narration_text": "Đáp án là Banh Chung đấy"})
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "answer_leak"
    assert client.get(f"/api/content/{item['id']}", headers=auth_headers
                      ).json()["current_revision"]["revision_no"] == 1


def test_the_leak_rule_applies_only_to_rebus(client, owner, auth_headers):
    resp = create(client, auth_headers, lane="daily_life")
    assert resp.status_code == 201
    same = client.post("/api/content", headers=auth_headers, json={
        "lane": "daily_life", "revision": {**REVISION, "caption": "Món bánh chưng ngày Tết"}})
    assert same.status_code == 201


def test_narration_is_stored_and_changes_the_hash(client, owner, auth_headers):
    plain = create(client, auth_headers).json()
    narrated = client.post(f"/api/content/{plain['id']}/revisions", headers=auth_headers,
                           json={**REVISION, "voice_id": "voice_A1",
                                 "narration_text": "Nhìn hình đoán chuyện: đây là gì nhỉ?"}).json()
    assert narrated["current_revision"]["content_hash"] != plain["current_revision"]["content_hash"]


def test_voice_id_is_validated(client, owner, auth_headers):
    bad = client.post("/api/content", headers=auth_headers, json={
        "lane": "rebus", "revision": {**REVISION, "voice_id": "../etc/passwd"}})
    assert bad.status_code == 422
