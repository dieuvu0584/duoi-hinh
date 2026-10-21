import asyncio
import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select, text

from app.core.config import Settings
from app.core.errors import ApiError
from app.db.models import AudioAsset, CostLedger, ProviderBudget
from app.providers.contracts import SpeechResult
from app.providers.elevenlabs_tts import ElevenLabsSpeech
from app.providers.errors import ProviderPermanent, ProviderTimeout, ProviderTransient
from app.services import narration_service as ns

KEY = "xi-SECRET-KEY-98765"
VOICE = "voiceABC123"


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- the adapter
def adapter(handler, **kw):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ElevenLabsSpeech(api_key=KEY, client=client, **kw)


def ok_handler(request):
    return httpx.Response(200, content=b"ID3-audio-bytes",
                          headers={"character-cost": "16", "request-id": "req-1"})


def test_the_adapter_sends_the_documented_request_and_reads_the_charge():
    seen = []

    def handler(request):
        seen.append(request)
        return ok_handler(request)

    result = run(adapter(handler).synthesize(text="Xin chào", voice_id=VOICE, model_id="eleven_v3",
                                             settings={"stability": 0.5}))
    req = seen[0]
    assert req.method == "POST" and req.url.path == f"/v1/text-to-speech/{VOICE}"
    assert req.url.params["output_format"] == "mp3_44100_128"
    assert req.headers["xi-api-key"] == KEY and KEY not in str(req.url)
    body = json.loads(req.content)
    assert body == {"text": "Xin chào", "model_id": "eleven_v3",
                    "voice_settings": {"stability": 0.5}}
    assert result.audio == b"ID3-audio-bytes" and result.credits_charged == 16.0
    assert result.request_id == "req-1" and result.mime_type == "audio/mpeg"


@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, ProviderPermanent), (422, ProviderPermanent), (402, ProviderPermanent),
     (429, ProviderTransient), (500, ProviderTimeout), (503, ProviderTimeout)],
)
def test_error_statuses_map_to_typed_errors(status, expected):
    def handler(request):
        return httpx.Response(status, json={"detail": {"message": f"nope {KEY}"}})

    with pytest.raises(expected) as exc:
        run(adapter(handler).synthesize(text="x", voice_id=VOICE, model_id="m", settings={}))
    assert KEY not in str(exc.value)


@pytest.mark.parametrize("error", [httpx.ReadTimeout("t"), httpx.ConnectError("c")])
def test_transport_problems_are_unknown_outcomes(error):
    def handler(request):
        raise error

    with pytest.raises(ProviderTimeout):
        run(adapter(handler).synthesize(text="x", voice_id=VOICE, model_id="m", settings={}))


@pytest.mark.parametrize("voice", ["../x", "a b", "", "a" * 90, "voice?x=1"])
def test_unsafe_identifiers_are_rejected_before_any_request(voice):
    calls = []

    def handler(request):
        calls.append(1)
        return ok_handler(request)

    with pytest.raises(ProviderPermanent):
        run(adapter(handler).synthesize(text="x", voice_id=voice, model_id="m", settings={}))
    assert calls == []


def test_empty_text_is_rejected():
    with pytest.raises(ProviderPermanent):
        run(adapter(ok_handler).synthesize(text="  ", voice_id=VOICE, model_id="m", settings={}))


# --------------------------------------------------------------------------- the service
class FakeSpeech:
    def __init__(self):
        self.calls: list[dict] = []
        self.mode = "ok"
        self.credits = 16.0

    async def synthesize(self, *, text, voice_id, model_id, settings):
        self.calls.append({"text": text, "voice": voice_id, "model": model_id})
        if self.mode == "reject":
            raise ProviderPermanent("422", "bad")
        if self.mode == "limit":
            raise ProviderTransient("429")
        if self.mode == "timeout":
            raise ProviderTimeout("lost")
        return SpeechResult(audio=b"AUDIO-" + text.encode(), mime_type="audio/mpeg",
                            credits_charged=self.credits, request_id="r1")


@pytest.fixture()
def media(tmp_path):
    return tmp_path


def cfg(media):
    return Settings(_env_file=None, media_dir=str(media))


def budget(db, monthly="10", daily="10", global_limit="100"):
    db.execute(text("UPDATE system_settings SET daily_budget_limit=:g, monthly_budget_limit=:g"),
               {"g": global_limit})
    db.add(ProviderBudget(provider="elevenlabs", monthly_limit_usd=Decimal(monthly),
                          daily_limit_usd=Decimal(daily)))
    db.commit()


def narrate(db, provider, media, *, text="Hôm nay là ngày 25/10/2026.", voice=VOICE,
            model="eleven_v3", **kw):
    return run(ns.get_or_create_narration(db, provider, cfg(media), text=text, voice_id=voice,
                                          model_id=model, **kw))


def test_text_is_normalised_before_it_is_sent_and_stored(db, media):
    budget(db)
    speech = FakeSpeech()
    asset = narrate(db, speech, media)
    sent = speech.calls[0]["text"]
    assert not any(c.isdigit() for c in sent) and "hai mươi lăm tháng mười" in sent
    assert asset.normalized_text == sent and asset.provider == "elevenlabs"


def test_the_file_is_written_inside_the_media_dir_and_recorded(db, media):
    budget(db)
    asset = narrate(db, FakeSpeech(), media)
    path = media / asset.storage_ref
    assert path.is_file() and path.read_bytes().startswith(b"AUDIO-")
    assert media.resolve() in path.resolve().parents
    assert asset.sha256 and asset.characters == len(asset.normalized_text)
    assert not list(media.rglob("*.part"))  # atomic write leaves no temp file


def test_an_identical_request_is_a_free_cache_hit(db, media):
    budget(db)
    speech = FakeSpeech()
    first = narrate(db, speech, media)
    again = narrate(db, speech, media)
    assert again.id == first.id and len(speech.calls) == 1
    assert len(db.scalars(select(CostLedger)).all()) == 1  # no second reservation


@pytest.mark.parametrize("change", [{"voice": "otherVoice1"}, {"model": "eleven_v4"},
                                    {"text": "Một câu khác."}, {"settings": {"speed": 1.1}}])
def test_any_change_to_the_inputs_is_a_new_asset(db, media, change):
    budget(db)
    speech = FakeSpeech()
    base = narrate(db, speech, media)
    other = narrate(db, speech, media, **change)
    assert other.id != base.id and len(speech.calls) == 2


def test_different_spellings_that_normalise_alike_share_one_asset(db, media):
    budget(db)
    speech = FakeSpeech()
    a = narrate(db, speech, media, text="Giá  24.500đ")
    b = narrate(db, speech, media, text="Giá 24.500 đ")
    assert a.id == b.id and len(speech.calls) == 1


def test_cost_is_reserved_before_and_settled_from_the_providers_charge(db, media):
    budget(db)
    speech = FakeSpeech()
    speech.credits = 16.0
    asset = narrate(db, speech, media)
    entry = db.scalars(select(CostLedger)).one()
    assert entry.status == "settled" and entry.provider == "elevenlabs"
    assert entry.actual_usd == Decimal("16.0") * Decimal("0.0003")
    assert entry.estimated_usd > entry.actual_usd  # the estimate is the safe upper bound
    assert asset.cost_ledger_id == entry.id and asset.credits_charged == Decimal("16")


def test_no_budget_means_no_request_at_all(db, media):
    speech = FakeSpeech()
    with pytest.raises(ApiError) as exc:
        narrate(db, speech, media)
    assert exc.value.code == "budget_not_set" and speech.calls == []
    assert not list(media.rglob("*.mp3"))


def test_a_tiny_budget_blocks_the_call(db, media):
    budget(db, monthly="0.0001")
    speech = FakeSpeech()
    with pytest.raises(ApiError) as exc:
        narrate(db, speech, media)
    assert exc.value.code == "budget_provider_monthly" and speech.calls == []


@pytest.mark.parametrize("mode", ["reject", "limit"])
def test_a_request_rejected_before_billing_releases_its_reservation(db, media, mode):
    budget(db)
    speech = FakeSpeech()
    speech.mode = mode
    with pytest.raises((ProviderPermanent, ProviderTransient)):
        narrate(db, speech, media)
    entry = db.scalars(select(CostLedger)).one()
    assert entry.status == "released" and entry.actual_usd == 0
    assert db.scalars(select(AudioAsset)).all() == []


def test_a_timeout_keeps_the_reservation_because_the_charge_is_unknown(db, media):
    budget(db)
    speech = FakeSpeech()
    speech.mode = "timeout"
    with pytest.raises(ProviderTimeout):
        narrate(db, speech, media)
    entry = db.scalars(select(CostLedger)).one()
    assert entry.status == "reserved" and entry.estimated_usd > 0
    assert not list(media.rglob("*.mp3"))


def test_limits_on_text_length_and_emptiness(db, media):
    budget(db)
    speech = FakeSpeech()
    with pytest.raises(ApiError) as exc:
        narrate(db, speech, media, text="#chỉ #hashtag 😀")
    assert exc.value.code == "empty_narration"
    with pytest.raises(ApiError) as exc2:
        narrate(db, speech, media, text="a " * 600)
    assert exc2.value.code == "narration_too_long" and speech.calls == []


def test_the_cache_key_is_stable_and_versioned():
    a = ns.cache_key("xin chào", "v", "m", {"s": 1})
    assert a == ns.cache_key("xin chào", "v", "m", {"s": 1}) and len(a) == 64
    assert a != ns.cache_key("xin chào", "v", "m", {"s": 2})


def test_estimates_are_above_the_measured_cost():
    # measured 2026-10-22: about 0.4 credits/char on v3 and 0.2 on flash/turbo
    assert ns.CREDITS_PER_CHAR_ESTIMATE["eleven_v3"] > 0.4
    assert ns.DEFAULT_CREDITS_PER_CHAR > 0.2
    assert ns.estimate_usd(100, "eleven_v3", 0.0003) == Decimal("0.015000")


def test_license_note_is_stored_with_the_asset(db, media):
    budget(db)
    asset = narrate(db, FakeSpeech(), media, license_note="library voice, commercial plan")
    assert asset.license_note == "library voice, commercial plan"
    assert Path(media / asset.storage_ref).exists()
