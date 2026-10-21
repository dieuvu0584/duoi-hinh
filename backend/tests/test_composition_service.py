import asyncio
import io
import json
from decimal import Decimal
from types import SimpleNamespace

import anthropic
import httpx
import pytest
from PIL import Image
from sqlalchemy import select, text

from app.core.config import Settings
from app.core.errors import ApiError
from app.db.models import CompositionCheck, CostLedger, ProviderBudget
from app.providers.claude_vision import SCHEMA, ClaudeVision
from app.providers.contracts import VisionBoxes
from app.providers.errors import (
    ProviderError,
    ProviderPermanent,
    ProviderTimeout,
    ProviderTransient,
)
from app.services import composition_service as cs
from app.services.composition import Box, decide_modes


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- decide_modes(expected)
def test_a_missing_expected_clue_fails_closed():
    boxes = [Box("cat", 0.4, 0.4, 0.55, 0.6)]
    d = decide_modes(boxes, expected=["cat", "clock"])
    assert d.modes == {"reel_9x16": "fit", "youtube_16x9": "fit"}
    assert "clock" in d.reason and d.outside["reel_9x16"] == ["clock"]


def test_all_expected_clues_present_and_inside_allows_fill():
    boxes = [Box("cat", 0.4, 0.4, 0.55, 0.6), Box("clock", 0.5, 0.45, 0.6, 0.6)]
    assert decide_modes(boxes, expected=["cat", "clock"]).all_fill


# --------------------------------------------------------------------------- the adapter
class FakeMessages:
    def __init__(self, payload=None, stop_reason="end_turn", error=None):
        self.payload, self.stop_reason, self.error, self.calls = payload, stop_reason, error, []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        block = SimpleNamespace(type="text", text=json.dumps(self.payload))
        return SimpleNamespace(content=[block], stop_reason=self.stop_reason, id="msg_1",
                               model="claude-opus-5-5",
                               usage=SimpleNamespace(input_tokens=2000, output_tokens=200))


def client_with(**kw):
    return SimpleNamespace(messages=FakeMessages(**kw))


GOOD = {"clues": [
    {"clue_id": "cat", "description": "c", "x0": 0.40, "y0": 0.40, "x1": 0.55, "y1": 0.60},
    {"clue_id": "clock", "description": "k", "x0": 0.55, "y0": 0.45, "x1": 0.65, "y1": 0.60}],
    "notes": "ok"}


def png():
    buf = io.BytesIO()
    Image.new("RGB", (2000, 2000), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


def api_error(cls, status):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom", response=httpx.Response(status, request=req), body=None)


def test_the_request_uses_structured_output_and_the_default_model():
    c = client_with(payload=GOOD)
    out = run(ClaudeVision(client=c).measure_boxes(image_png=png(), clues=["cat", "clock"]))
    call = c.messages.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"]["format"] == {"type": "json_schema", "schema": SCHEMA}
    content = call["messages"][0]["content"]
    assert content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/png"
    assert '["cat", "clock"]' in content[1]["text"]  # the clue list is passed as JSON data
    assert not out.refused and out.input_tokens == 2000 and out.request_id == "msg_1"


def test_a_refusal_is_returned_as_data_with_its_token_usage():
    c = client_with(payload={}, stop_reason="refusal")
    out = run(ClaudeVision(client=c).measure_boxes(image_png=png(), clues=["cat"]))
    assert out.refused and out.clues == [] and out.input_tokens == 2000


@pytest.mark.parametrize("clues", [[], [""], ["x" * 100], ["a"] * 13])
def test_bad_clue_lists_are_rejected_before_any_call(clues):
    c = client_with(payload=GOOD)
    with pytest.raises(ProviderPermanent):
        run(ClaudeVision(client=c).measure_boxes(image_png=png(), clues=clues))
    assert c.messages.calls == []


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (api_error(anthropic.AuthenticationError, 401), ProviderPermanent),
        (api_error(anthropic.BadRequestError, 400), ProviderPermanent),
        (api_error(anthropic.RateLimitError, 429), ProviderTransient),
        (api_error(anthropic.InternalServerError, 500), ProviderTimeout),
        (anthropic.APITimeoutError(httpx.Request("POST", "https://x")), ProviderTimeout),
        (anthropic.APIConnectionError(request=httpx.Request("POST", "https://x")), ProviderTimeout),
    ],
)
def test_api_errors_are_mapped_to_typed_errors(error, expected):
    with pytest.raises(expected):
        run(ClaudeVision(client=client_with(error=error)).measure_boxes(
            image_png=png(), clues=["cat"]))


@pytest.mark.parametrize("text_out", ["not json", '{"nope": 1}', ""])
def test_unusable_answers_are_provider_errors(text_out):
    c = SimpleNamespace(messages=FakeMessages())

    async def create(**kwargs):
        block = SimpleNamespace(type="text", text=text_out)
        return SimpleNamespace(content=[block], stop_reason="end_turn", id="m", model="x",
                               usage=SimpleNamespace(input_tokens=1, output_tokens=1))

    c.messages.create = create
    with pytest.raises(ProviderError):
        run(ClaudeVision(client=c).measure_boxes(image_png=png(), clues=["cat"]))


# --------------------------------------------------------------------------- the service
class FakeVision:
    def __init__(self, clues=None, refused=False, error=None):
        self.clues = GOOD["clues"] if clues is None else clues
        self.refused, self.error, self.calls = refused, error, []

    async def measure_boxes(self, *, image_png, clues):
        self.last_png = image_png
        self.calls.append((len(image_png), clues))
        if self.error:
            raise self.error
        return VisionBoxes(clues=self.clues, notes="", refused=self.refused, input_tokens=2000,
                           output_tokens=200, model="claude-opus-5-5", request_id="r1")


@pytest.fixture()
def media(tmp_path):
    (tmp_path / "images").mkdir()
    Image.new("RGB", (1920, 1920), (5, 5, 5)).save(tmp_path / "images" / "m.png")
    return tmp_path


def cfg(media):
    return Settings(_env_file=None, media_dir=str(media))


def budget(db, monthly="1"):
    db.execute(text("UPDATE system_settings SET daily_budget_limit=10, monthly_budget_limit=10"))
    db.add(ProviderBudget(provider="anthropic", monthly_limit_usd=Decimal(monthly)))
    db.commit()


def check(db, provider, media, clues=("cat", "clock")):
    return run(cs.check_composition(db, provider, cfg(media), master_ref="images/m.png",
                                    clues=list(clues)))


def test_centred_clues_give_fill_and_the_evidence_is_stored(db, media):
    budget(db)
    decision, row = check(db, FakeVision(), media)
    assert decision.all_fill and row.modes == {"reel_9x16": "fill", "youtube_16x9": "fill"}
    assert row.expected_clues == ["cat", "clock"] and len(row.boxes) == 2 and row.problems == []
    assert row.image_sha256 and row.model == "claude-opus-5-5"


def test_the_cost_is_reserved_then_settled_from_token_usage(db, media):
    budget(db)
    check(db, FakeVision(), media)
    entry = db.scalars(select(CostLedger)).one()
    assert entry.status == "settled" and entry.provider == "anthropic"
    assert entry.actual_usd == Decimal("0.012000")  # 2000 in x $4/M + 200 out x $20/M
    assert entry.estimated_usd > entry.actual_usd


def test_only_a_downscaled_reencoded_image_is_sent(db, media):
    from PIL.PngImagePlugin import PngInfo

    info = PngInfo()
    info.add_text("secret", "private metadata")
    Image.new("RGB", (1920, 1920), (5, 5, 5)).save(media / "images" / "m.png", pnginfo=info)
    budget(db)
    vision = FakeVision()
    check(db, vision, media)
    sent = Image.open(io.BytesIO(vision.last_png))
    assert max(sent.size) == 1024 and sent.format == "PNG"
    assert "secret" not in getattr(sent, "text", {})  # metadata never leaves the machine


def test_a_clue_outside_the_crop_forces_fit(db, media):
    budget(db)
    off = [{"clue_id": "cat", "description": "c", "x0": 0.05, "y0": 0.4, "x1": 0.5, "y1": 0.8},
           GOOD["clues"][1]]
    decision, row = check(db, FakeVision(clues=off), media)
    assert decision.modes["reel_9x16"] == "fit" and row.outside["reel_9x16"] == ["cat"]


def test_a_missing_clue_in_the_answer_fails_closed(db, media):
    budget(db)
    decision, row = check(db, FakeVision(clues=GOOD["clues"][:1]), media)
    assert decision.modes == {"reel_9x16": "fit", "youtube_16x9": "fit"}
    assert "clock" in row.reason


def test_invalid_boxes_are_recorded_and_never_trusted(db, media):
    budget(db)
    bad = [{"clue_id": "cat", "description": "c", "x0": 0.5, "y0": 0.5, "x1": 1.7, "y1": 0.9},
           {"clue_id": "clock"}]
    decision, row = check(db, FakeVision(clues=bad), media)
    assert row.boxes == [] and len(row.problems) == 2
    assert decision.modes == {"reel_9x16": "fit", "youtube_16x9": "fit"}


def test_a_refusal_never_crops_but_is_still_billed(db, media):
    budget(db)
    decision, row = check(db, FakeVision(refused=True), media)
    assert decision.modes == {"reel_9x16": "fit", "youtube_16x9": "fit"}
    assert any("refused" in p for p in row.problems)
    assert db.scalars(select(CostLedger)).one().status == "settled"


def test_no_budget_means_no_request(db, media):
    vision = FakeVision()
    with pytest.raises(ApiError) as exc:
        check(db, vision, media)
    assert exc.value.code == "budget_not_set" and vision.calls == []


def test_known_uncharged_failure_releases_and_a_timeout_keeps_the_reservation(db, media):
    budget(db)
    with pytest.raises(ProviderPermanent):
        check(db, FakeVision(error=ProviderPermanent("401", "bad key")), media)
    with pytest.raises(ProviderTimeout):
        check(db, FakeVision(error=ProviderTimeout("lost")), media)
    statuses = [e.status for e in db.scalars(select(CostLedger).order_by(CostLedger.reserved_at))]
    assert statuses == ["released", "reserved"] and db.scalars(select(CompositionCheck)).all() == []


@pytest.mark.parametrize("ref", ["../x.png", "/etc/passwd", "images/missing.png", ""])
def test_unsafe_master_references_are_refused_before_any_cost(db, media, ref):
    budget(db)
    with pytest.raises(ApiError) as exc:
        run(cs.check_composition(db, FakeVision(), cfg(media), master_ref=ref, clues=["cat"]))
    assert exc.value.code == "unsafe_media" and db.scalars(select(CostLedger)).all() == []


def test_an_unreadable_master_is_rejected(db, media):
    (media / "images" / "junk.png").write_bytes(b"not an image")
    budget(db)
    with pytest.raises(ApiError) as exc:
        run(cs.check_composition(db, FakeVision(), cfg(media), master_ref="images/junk.png",
                                 clues=["cat"]))
    assert exc.value.code == "bad_image" and db.scalars(select(CostLedger)).all() == []
