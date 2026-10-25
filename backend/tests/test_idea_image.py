"""Meme ideas drafted from a picture and the owner's explanation."""

import base64
import io
import json
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import text

from app.api.deps import get_llm, get_settings
from app.core.config import Settings
from app.core.errors import ApiError
from app.db.models import ProviderBudget
from app.main import app
from app.services import idea_image_service as iis
from tests.test_idea_scan import FakeLLM

REPO = Path(__file__).resolve().parents[2]


def png_b64(size=(300, 200), color=(200, 60, 60), fmt="PNG"):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format=fmt)
    return base64.b64encode(buf.getvalue()).decode()


ANSWER = {"brief": "Con mèo ngồi họp online, mắt thất thần nhìn đồng hồ.",
          "joke_summary": "Họp dài như cả đời", "contains_identifiable_person": False,
          "warnings": []}


def test_a_valid_picture_is_re_encoded_small_as_jpeg():
    out = iis.prepare_image(png_b64(size=(4000, 3000)))
    img = Image.open(io.BytesIO(out))
    assert img.format == "JPEG" and max(img.size) <= iis.SEND_SIDE


@pytest.mark.parametrize("data, code", [
    ("not-base64!!!", "bad_image"),
    (base64.b64encode(b"GIF89a" + b"0" * 200).decode(), "bad_image"),
    (base64.b64encode(b"plain text pretending to be a picture " * 5).decode(), "bad_image"),
])
def test_files_that_are_not_real_pictures_are_refused(data, code):
    with pytest.raises(ApiError) as exc:
        iis.prepare_image(data)
    assert exc.value.code == code


def test_a_picture_in_an_unaccepted_format_is_refused_by_content():
    with pytest.raises(ApiError) as exc:
        iis.prepare_image(png_b64(fmt="BMP"))
    assert exc.value.code == "bad_image_type"


def test_oversized_pictures_are_refused(monkeypatch):
    monkeypatch.setattr(iis, "MAX_BYTES", 100)
    with pytest.raises(ApiError) as exc:
        iis.prepare_image(png_b64())
    assert exc.value.code == "image_too_large"


def override(llm):
    app.dependency_overrides[get_llm] = lambda: llm
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, prompts_dir=str(REPO / "prompts"),
        schemas_dir=str(REPO / "docs" / "schemas"), blacklist_words="cam tu")


def clear():
    app.dependency_overrides.pop(get_llm, None)
    app.dependency_overrides.pop(get_settings, None)


def fund(db):
    db.execute(text("UPDATE system_settings SET daily_budget_limit=5, monthly_budget_limit=5"))
    db.add(ProviderBudget(provider="anthropic", monthly_limit_usd=5))
    db.commit()


def test_the_endpoint_needs_login(client):
    r = client.post("/api/ideas/from-image", json={"image_base64": png_b64()})
    assert r.status_code == 401


def test_a_picture_and_a_note_become_a_draft_brief(client, owner, auth_headers, db):
    fund(db)
    llm = FakeLLM(ANSWER)
    override(llm)
    try:
        r = client.post("/api/ideas/from-image", headers=auth_headers,
                        json={"image_base64": png_b64(), "note": "Cảnh họp online dài lê thê"})
    finally:
        clear()
    assert r.status_code == 200 and r.json()["brief"].startswith("Con mèo")
    sent = llm.calls[0]
    assert sent[0]["type"] == "image" and sent[0]["source"]["media_type"] == "image/jpeg"
    assert json.loads(sent[1]["text"])["note"] == "Cảnh họp online dài lê thê"


def test_a_real_person_in_the_picture_adds_a_warning(client, owner, auth_headers, db):
    fund(db)
    override(FakeLLM({**ANSWER, "contains_identifiable_person": True}))
    try:
        r = client.post("/api/ideas/from-image", headers=auth_headers,
                        json={"image_base64": png_b64()})
    finally:
        clear()
    assert r.status_code == 200 and "người thật" in r.json()["warnings"][0]


def test_blacklisted_words_stop_the_draft(client, owner, auth_headers, db):
    fund(db)
    override(FakeLLM({**ANSWER, "brief": "Cảnh cấm tụ họp"}))
    try:
        r = client.post("/api/ideas/from-image", headers=auth_headers,
                        json={"image_base64": png_b64()})
    finally:
        clear()
    assert r.status_code == 422 and r.json()["error"]["code"] == "blocked_words"


def test_a_bad_file_is_refused_before_any_model_call(client, owner, auth_headers, db):
    fund(db)
    llm = FakeLLM(ANSWER)
    override(llm)
    try:
        r = client.post("/api/ideas/from-image", headers=auth_headers,
                        json={"image_base64": base64.b64encode(b"x" * 300).decode()})
    finally:
        clear()
    assert r.status_code == 422 and llm.calls == []
