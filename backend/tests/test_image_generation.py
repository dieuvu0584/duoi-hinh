import asyncio
import io
from decimal import Decimal

import httpx
import pytest
from PIL import Image
from sqlalchemy import select, text

from app.core.config import Settings
from app.core.errors import ApiError
from app.core.urlsafety import is_safe_public_https_url
from app.db.models import CostLedger, ImageAsset, ProviderBudget
from app.providers.contracts import ImageResult
from app.providers.errors import ProviderPermanent, ProviderTimeout, ProviderTransient
from app.providers.fal_image import FalImageProvider
from app.services import image_service as imgs

KEY = "fal-SECRET-ID:abcdef0123456789abcdef0123456789"
QUEUE = "https://queue.fal.run"


def run(coro):
    return asyncio.run(coro)


async def no_sleep(_):
    return None


def png_bytes(size=(64, 64), color=(10, 120, 200)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


class FakeFal:
    """Answers like fal.ai's queue API did on the test account."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.status = "COMPLETED"
        self.error = None
        self.size = (1088, 1920)
        self.nsfw = [False]
        self.image_url = "https://v3b.fal.media/files/abc/out.png"
        self.fail_at: dict[str, httpx.Response | Exception] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = f"{request.method} {request.url.path}"
        for pattern, outcome in self.fail_at.items():
            if pattern in key:
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
        path = request.url.path
        if request.method == "POST" and path.startswith("/fal-ai/"):
            return httpx.Response(200, json={
                "request_id": "req1", "status_url": f"{QUEUE}/fal-ai/flux-2/requests/req1/status",
                "response_url": f"{QUEUE}/fal-ai/flux-2/requests/req1",
                "cancel_url": f"{QUEUE}/fal-ai/flux-2/requests/req1/cancel"})
        if path.endswith("/status"):
            body = {"status": self.status}
            if self.error:
                body["error"] = self.error
            return httpx.Response(200, json=body)
        if path.endswith("/requests/req1"):
            return httpx.Response(200, json={
                "images": [{"url": self.image_url, "width": self.size[0], "height": self.size[1],
                            "content_type": "image/png"}],
                "seed": 7, "has_nsfw_concepts": self.nsfw})
        if request.url.host.endswith("fal.media"):
            return httpx.Response(200, content=png_bytes())
        return httpx.Response(404)


def adapter(fake, **kw):
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    return FalImageProvider(api_key=KEY, client=client, sleep=no_sleep, **kw)


def gen(provider, **kw):
    args = {"prompt": "a cat", "width": 1920, "height": 1920, **kw}
    return run(provider.generate(**args))


# --------------------------------------------------------------------------- adapter
def test_generate_follows_the_queue_flow_and_returns_the_real_size():
    fake = FakeFal()
    result = gen(adapter(fake))
    assert (result.width, result.height, result.seed, result.flagged) == (1088, 1920, 7, False)
    assert result.request_id == "req1" and result.image.startswith(b"\x89PNG")
    methods = [f"{r.method} {r.url.path.split('/')[-1]}" for r in fake.requests]
    assert methods[0].startswith("POST") and "GET status" in methods


def test_the_request_body_uses_the_documented_fields():
    fake = FakeFal()
    gen(adapter(fake), seed=42)
    body = __import__("json").loads(fake.requests[0].content)
    assert body == {"prompt": "a cat", "image_size": {"width": 1920, "height": 1920},
                    "num_images": 1, "output_format": "png", "enable_safety_checker": True,
                    "seed": 42}


def test_the_key_goes_only_to_fal_and_never_to_the_image_cdn():
    fake = FakeFal()
    gen(adapter(fake))
    for r in fake.requests:
        if r.url.host == "queue.fal.run":
            assert r.headers["authorization"] == f"Key {KEY}"
        else:
            assert "authorization" not in r.headers  # the CDN never sees the key
        assert KEY not in str(r.url)


def test_nsfw_flag_is_reported():
    fake = FakeFal()
    fake.nsfw = [True]
    assert gen(adapter(fake)).flagged is True


@pytest.mark.parametrize("kw", [{"prompt": " "}, {"prompt": "x" * 5000}, {"width": 100},
                                {"height": 4000}])
def test_bad_requests_are_rejected_before_any_network_call(kw):
    fake = FakeFal()
    with pytest.raises(ProviderPermanent):
        gen(adapter(fake), **kw)
    assert fake.requests == []


def test_bad_model_names_are_rejected():
    for bad in ("../etc", "fal-ai", "https://evil.example/x", "a b/c"):
        with pytest.raises(ValueError):
            FalImageProvider(api_key=KEY, model=bad)


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(401, json={"detail": f"bad key {KEY}"}), ProviderPermanent),
        (httpx.Response(422, json={"detail": "bad"}), ProviderPermanent),
        (httpx.Response(429, json={}), ProviderTransient),
        (httpx.Response(503, text="x"), ProviderTransient),
    ],
)
def test_failures_before_acceptance_are_typed_and_known_uncharged(response, expected):
    fake = FakeFal()
    fake.fail_at = {"POST /fal-ai/flux-2": response}
    with pytest.raises(expected) as exc:
        gen(adapter(fake))
    assert KEY not in str(exc.value)


@pytest.mark.parametrize("where", ["GET /fal-ai/flux-2/requests/req1/status",
                                   "GET /fal-ai/flux-2/requests/req1"])
@pytest.mark.parametrize("outcome", [httpx.Response(500), httpx.Response(404),
                                     httpx.ReadTimeout("t")])
def test_any_failure_after_acceptance_is_an_unknown_charge(where, outcome):
    fake = FakeFal()
    fake.fail_at = {where: outcome}
    with pytest.raises(ProviderTimeout):
        gen(adapter(fake))


def test_a_failed_generation_reported_in_the_status_is_unknown_cost():
    fake = FakeFal()
    fake.error = "runtime error"
    with pytest.raises(ProviderTimeout):
        gen(adapter(fake))


def test_waiting_forever_times_out_as_unknown():
    fake = FakeFal()
    fake.status = "IN_PROGRESS"
    with pytest.raises(ProviderTimeout):
        gen(adapter(fake, timeout_s=3, poll_interval=1))


@pytest.mark.parametrize("url", ["http://v3b.fal.media/x.png", "https://127.0.0.1/x.png",
                                 "https://169.254.169.254/latest", "https://localhost/x",
                                 "https://intranet/x.png", "ftp://fal.media/x"])
def test_an_unsafe_image_url_is_never_fetched(url):
    fake = FakeFal()
    fake.image_url = url
    with pytest.raises(ProviderPermanent, match="untrusted_url"):
        gen(adapter(fake))
    assert not any(str(r.url) == url for r in fake.requests)


def test_unexpected_status_urls_are_refused():
    def evil(request):
        if request.method == "POST":
            return httpx.Response(200, json={"request_id": "r", "response_url": "https://evil.example/r",
                                             "status_url": "https://evil.example/s"})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(evil))
    with pytest.raises(ProviderPermanent, match="untrusted_url"):
        gen(FalImageProvider(api_key=KEY, client=client, sleep=no_sleep))


@pytest.mark.parametrize(("url", "safe"), [
    ("https://v3b.fal.media/f.png", True), ("https://8.8.8.8/x", True),
    ("http://fal.media/x", False), ("https://10.0.0.5/x", False), ("https://192.168.1.1/x", False),
    ("https://[::1]/x", False), ("https://user:pw@fal.media/x", False),
    ("https://x.internal/a", False),
    ("not a url", False), ("https:///x", False),
])
def test_url_safety_rules(url, safe):
    assert is_safe_public_https_url(url) is safe


# --------------------------------------------------------------------------- service
class FakeImages:
    def __init__(self):
        self.calls = []
        self.mode = "ok"
        self.size = (1088, 1088)
        self.flagged = False
        self.payload = png_bytes((1088, 1088))

    async def generate(self, *, prompt, width, height, seed=None):
        self.calls.append((prompt, width, height, seed))
        if self.mode == "reject":
            raise ProviderPermanent("422", "bad")
        if self.mode == "timeout":
            raise ProviderTimeout("unknown")
        return ImageResult(image=self.payload, mime_type="image/png", width=self.size[0],
                           height=self.size[1], seed=seed, flagged=self.flagged, request_id="r1")


@pytest.fixture()
def media(tmp_path):
    return tmp_path


def cfg(media):
    return Settings(_env_file=None, media_dir=str(media))


def budget(db, monthly="4"):
    db.execute(text("UPDATE system_settings SET daily_budget_limit=50, monthly_budget_limit=50"))
    db.add(ProviderBudget(provider="fal", monthly_limit_usd=Decimal(monthly)))
    db.commit()


def make(db, provider, media, **kw):
    return run(imgs.generate_master(db, provider, cfg(media), prompt="a cat", **kw))


def test_estimates_cover_rounding_and_the_settled_cost_uses_the_real_size():
    est = imgs.estimate_usd(1920, 1920, 0.012)
    assert est == Decimal("0.045122")  # 3.6864 MP * 0.012 * 1.02
    assert imgs.actual_usd(1088, 1920, 0.012) == Decimal("0.025068")  # measured 2026-10-22
    assert imgs.actual_usd(1920, 1920, 0.012) == Decimal("0.044237")  # measured 2026-10-22
    assert imgs.estimate_usd(1080, 1920, 0.012) > imgs.actual_usd(1088, 1920, 0.012)


def test_a_master_is_generated_stored_and_costed(db, media):
    budget(db)
    provider = FakeImages()
    asset = make(db, provider, media, seed=5)
    entry = db.scalars(select(CostLedger)).one()
    assert entry.status == "settled" and entry.actual_usd == imgs.actual_usd(1088, 1088, 0.012)
    assert asset.cost_ledger_id == entry.id and asset.seed == 5
    path = media / asset.storage_ref
    assert path.is_file() and Image.open(path).size == (1088, 1088)
    assert asset.sha256 and (asset.width, asset.height) == (1088, 1088)
    assert provider.calls == [("a cat", 1920, 1920, 5)]


def test_no_budget_means_no_generation(db, media):
    provider = FakeImages()
    with pytest.raises(ApiError) as exc:
        make(db, provider, media)
    assert exc.value.code == "budget_not_set" and provider.calls == []


def test_the_four_dollar_budget_is_a_hard_stop(db, media):
    budget(db, monthly="0.05")  # room for one 1920 master (about $0.045), not two
    provider = FakeImages()
    make(db, provider, media)
    with pytest.raises(ApiError) as exc:
        make(db, provider, media)
    assert exc.value.code == "budget_provider_monthly" and len(provider.calls) == 1


def test_known_uncharged_failures_release_and_unknown_ones_keep_the_reservation(db, media):
    budget(db)
    provider = FakeImages()
    provider.mode = "reject"
    with pytest.raises(ProviderPermanent):
        make(db, provider, media)
    assert db.scalars(select(CostLedger)).one().status == "released"
    provider.mode = "timeout"
    with pytest.raises(ProviderTimeout):
        make(db, provider, media)
    assert [e.status for e in db.scalars(select(CostLedger).order_by(CostLedger.reserved_at))] == [
        "released", "reserved"]
    assert db.scalars(select(ImageAsset)).all() == []


def test_a_flagged_image_is_charged_but_never_stored(db, media):
    budget(db)
    provider = FakeImages()
    provider.flagged = True
    with pytest.raises(ApiError) as exc:
        make(db, provider, media)
    assert exc.value.code == "image_flagged"
    assert db.scalars(select(CostLedger)).one().status == "settled"
    assert db.scalars(select(ImageAsset)).all() == [] and not list(media.rglob("*.png"))


@pytest.mark.parametrize("payload", [b"not an image", b"", b"\x89PNG\r\n\x1a\ntruncated"])
def test_corrupt_image_bytes_are_rejected_and_not_stored(db, media, payload):
    budget(db)
    provider = FakeImages()
    provider.payload = payload
    with pytest.raises(ApiError) as exc:
        make(db, provider, media)
    assert exc.value.code == "bad_image" and not list(media.rglob("*.png"))


def test_the_stored_png_is_re_encoded_and_carries_no_metadata(db, media):
    from PIL.PngImagePlugin import PngInfo

    budget(db)
    provider = FakeImages()
    info = PngInfo()
    info.add_text("secret", "do-not-keep")
    buf = io.BytesIO()
    Image.new("RGB", (1088, 1088), (1, 2, 3)).save(buf, format="PNG", pnginfo=info)
    assert "secret" in Image.open(io.BytesIO(buf.getvalue())).text  # the input really has it
    provider.payload = buf.getvalue()
    asset = make(db, provider, media)
    stored = Image.open(media / asset.storage_ref)
    assert "secret" not in getattr(stored, "text", {}) and stored.format == "PNG"
