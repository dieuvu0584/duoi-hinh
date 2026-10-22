import asyncio
import json
import logging
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from app.core.config import Settings
from app.providers.errors import (
    ProviderExpired,
    ProviderPermanent,
    ProviderTimeout,
    ProviderTransient,
)
from app.providers.factory import get_reels_provider
from app.providers.meta_reels import MetaReelsProvider

TOKEN = "EAAB-SECRET-TOKEN-12345"
PAGE = "1514689985052394"


def run(coro):
    return asyncio.run(coro)


async def no_sleep(_):
    return None


class FakeGraph:
    """Records every request and answers like the Graph API behaved on the test Page."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.video_status = "upload_complete"
        self.published = False
        self.copyright = {"status": {"status": "complete", "matches_found": False}}
        self.fail: dict[str, httpx.Response | Exception] = {}
        self.deleted = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = f"{request.method} {request.url.path}"
        for pattern, outcome in self.fail.items():
            if pattern in key:
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
        body = request.content.decode() if request.content and request.method != "POST" or \
            (request.headers.get("content-type", "").startswith("application/x-www-form")) else ""
        form = {k: v[0] for k, v in parse_qs(body).items()} if body else {}
        path, fields = request.url.path, request.url.params.get("fields", "")
        if path.endswith("/video_reels") and form.get("upload_phase") == "start":
            return httpx.Response(200, json={
                "video_id": "V1", "upload_url": "https://rupload.facebook.com/video-upload/V1"})
        if "video-upload" in path:
            return httpx.Response(200, json={"success": True})
        if path.endswith("/video_reels") and form.get("upload_phase") == "finish":
            self.video_status = "ready"
            return httpx.Response(200, json={"success": True, "post_id": "P1"})
        if request.method == "DELETE":
            self.deleted = True
            return httpx.Response(200, json={"success": True})
        if request.method == "POST" and path.endswith("/V1") and form.get("published") == "true":
            self.published = True
            return httpx.Response(200, json={"success": True})
        if request.method == "GET" and path.endswith("/V1"):
            if self.deleted:
                return httpx.Response(400, json={"error": {"code": 100, "error_subcode": 33,
                                                           "message": "does not exist"}})
            out: dict = {}
            if "copyright_check_information" in fields:
                out["copyright_check_information"] = self.copyright
            if "status" in fields:
                out["status"] = {"video_status": self.video_status,
                                 "publishing_phase": {"publish_status":
                                                      "published" if self.published else "draft"}}
            if "published" in fields:
                out["published"] = self.published
                out["permalink_url"] = "/reel/V1/"
            return httpx.Response(200, json=out)
        return httpx.Response(404, json={})


def provider(graph, **kw):
    client = httpx.AsyncClient(transport=httpx.MockTransport(graph))
    return MetaReelsProvider(page_id=PAGE, access_token=TOKEN, client=client, sleep=no_sleep,
                             poll_interval=1, **kw)


@pytest.fixture()
def video(tmp_path):
    path = tmp_path / "reel.mp4"
    path.write_bytes(b"\x00" * 4096)
    return path


def prepare(p, video, seen=None):
    return run(p.prepare(video_path=video, description="d", is_ai_generated=True,
                         intent_key="k", on_video_id=(seen.append if seen is not None
                                                      else (lambda _v: None))))


def test_prepare_follows_the_measured_flow_and_persists_the_id_first(video):
    g = FakeGraph()
    seen: list[str] = []
    prepared = prepare(provider(g), video, seen)
    assert seen == ["V1"]
    assert (prepared.video_id, prepared.status, prepared.copyright_clear) == ("V1", "ready", True)
    ops = [f"{r.method} {r.url.path.split('/')[-1]}" for r in g.requests]
    assert ops[0] == "POST video_reels" and ops[1].startswith("POST V1")
    assert ops.index("POST V1") < ops.index("POST video_reels", 1)  # upload before FINISH


def test_the_finish_call_creates_a_draft_and_declares_ai_content(video):
    g = FakeGraph()
    prepare(provider(g), video)
    finish = next(r for r in g.requests if b"upload_phase=finish" in r.content)
    form = {k: v[0] for k, v in parse_qs(finish.content.decode()).items()}
    assert form["video_state"] == "DRAFT" and form["is_ai_generated"] == "true"


def test_the_token_is_only_ever_in_the_authorization_header(video):
    g = FakeGraph()
    p = provider(g)
    prepare(p, video)
    run(p.publish(video_id="V1", intent_key="k"))
    run(p.delete(video_id="V1"))
    for r in g.requests:
        assert r.headers["authorization"] == f"OAuth {TOKEN}"
        assert TOKEN not in str(r.url) and TOKEN.encode() not in r.content


def test_upload_has_the_documented_headers(video):
    g = FakeGraph()
    prepare(provider(g), video)
    up = next(r for r in g.requests if "video-upload" in r.url.path)
    assert up.headers["offset"] == "0" and up.headers["file_size"] == "4096"
    assert up.url.host == "rupload.facebook.com"


def test_credentials_are_never_sent_to_an_untrusted_upload_host(video):
    g = FakeGraph()

    def evil(request):
        if request.url.path.endswith("/video_reels"):
            return httpx.Response(200, json={"video_id": "V1",
                                             "upload_url": "https://evil.example.com/steal"})
        return g(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(evil))
    p = MetaReelsProvider(page_id=PAGE, access_token=TOKEN, client=client, sleep=no_sleep)
    with pytest.raises(ProviderPermanent, match="untrusted_host"):
        prepare(p, video)


def test_a_copyright_match_stops_before_finish_and_reports_not_clear(video):
    g = FakeGraph()
    g.copyright = {"status": {"status": "complete", "matches_found": True}}
    prepared = prepare(provider(g), video)
    assert prepared.copyright_clear is False and prepared.status == "upload_complete"
    assert not any(b"upload_phase=finish" in r.content for r in g.requests)


def test_a_copyright_check_that_never_finishes_is_not_clear(video):
    g = FakeGraph()
    g.copyright = {"status": {"status": "in_progress"}}
    prepared = prepare(provider(g, copyright_timeout=3), video)
    assert prepared.copyright_clear is None


def test_oversized_or_empty_files_are_rejected_before_any_request(tmp_path):
    g = FakeGraph()
    empty = tmp_path / "e.mp4"
    empty.write_bytes(b"")
    with pytest.raises(ProviderPermanent, match="file_size"):
        prepare(provider(g), empty)
    assert g.requests == []


def test_a_draft_that_expires_is_reported_as_expired(video):
    g = FakeGraph()
    original = g.__call__

    def expiring(request):
        resp = original(request)
        if request.method == "GET" and "status" in request.url.params.get("fields", ""):
            return httpx.Response(200, json={"status": {"video_status": "expired"}})
        return resp

    client = httpx.AsyncClient(transport=httpx.MockTransport(expiring))
    p = MetaReelsProvider(page_id=PAGE, access_token=TOKEN, client=client, sleep=no_sleep)
    with pytest.raises(ProviderExpired):
        prepare(p, video)


def test_publish_promotes_then_verifies_before_reporting_success(video):
    g = FakeGraph()
    p = provider(g)
    prepare(p, video)
    result = run(p.publish(video_id="V1", intent_key="k"))
    assert result.permalink == "/reel/V1/" and g.published


def test_a_success_response_that_did_not_publish_is_an_unknown_outcome(video):
    g = FakeGraph()
    p = provider(g, verify_timeout=3)
    prepare(p, video)

    original = g.__call__

    def lying(request):
        if request.method == "POST" and request.url.path.endswith("/V1"):
            return httpx.Response(200, json={"success": True})  # but nothing happens
        return original(request)

    p2 = MetaReelsProvider(page_id=PAGE, access_token=TOKEN, sleep=no_sleep, poll_interval=1,
                           verify_timeout=3,
                           client=httpx.AsyncClient(transport=httpx.MockTransport(lying)))
    with pytest.raises(ProviderTimeout):
        run(p2.publish(video_id="V1", intent_key="k"))


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(400, json={"error": {"code": 190, "message": "bad token"}}),
         ProviderPermanent),
        (httpx.Response(403, json={"error": {"code": 200, "message": "no permission"}}),
         ProviderPermanent),
        (httpx.Response(400, json={"error": {"code": 6000, "error_subcode": 1363130}}),
         ProviderPermanent),
        (httpx.Response(429, json={"error": {"code": 613, "message": "rate limit"}}),
         ProviderTransient),
        (httpx.Response(503, text="oops"), ProviderTimeout),  # state-changing: outcome unknown
    ],
)
def test_publish_errors_map_to_typed_errors(response, expected):
    g = FakeGraph()
    g.fail = {"POST /v26.0/V1": response}
    with pytest.raises(expected):
        run(provider(g).publish(video_id="V1", intent_key="k"))


@pytest.mark.parametrize("exc", [httpx.ReadTimeout("x"), httpx.ConnectError("x")])
def test_transport_problems_on_publish_are_unknown_not_failures(exc):
    g = FakeGraph()
    g.fail = {"POST /v26.0/V1": exc}
    with pytest.raises(ProviderTimeout):
        run(provider(g).publish(video_id="V1", intent_key="k"))


def test_error_messages_never_contain_the_token():
    g = FakeGraph()
    g.fail = {"POST /v26.0/V1": httpx.Response(
        400, json={"error": {"code": 190, "message": f"Invalid OAuth access token {TOKEN}"}})}
    with pytest.raises(ProviderPermanent) as exc:
        run(provider(g).publish(video_id="V1", intent_key="k"))
    assert TOKEN not in str(exc.value)


def test_delete_is_idempotent_when_the_video_is_already_gone(video):
    g = FakeGraph()
    p = provider(g)
    run(p.delete(video_id="V1"))
    run(p.delete(video_id="V1"))  # second delete: "does not exist" counts as done
    g.fail = {"DELETE": httpx.Response(400, json={"error": {"code": 190, "message": "token"}})}
    with pytest.raises(ProviderPermanent):
        run(p.delete(video_id="V1"))


def test_reconcile_reads_the_providers_truth(video):
    g = FakeGraph()
    p = provider(g)
    prepare(p, video)
    assert run(p.reconcile(video_id="V1")).state == "draft_ready"
    run(p.publish(video_id="V1", intent_key="k"))
    assert run(p.reconcile(video_id="V1")).state == "published"
    run(p.delete(video_id="V1"))
    assert run(p.reconcile(video_id="V1")).state == "missing"


def test_reconcile_of_a_video_still_processing_is_ambiguous():
    g = FakeGraph()
    g.video_status = "processing"
    assert run(provider(g).reconcile(video_id="V1")).state == "ambiguous"


def test_http_client_logs_do_not_carry_the_token(caplog, video):
    g = FakeGraph()
    with caplog.at_level(logging.DEBUG):
        prepare(provider(g), video)
    assert TOKEN not in caplog.text


# ---------------------------------------------------------------- factory and config
def test_dry_run_is_the_default_and_never_builds_the_live_adapter():
    cfg = Settings(_env_file=None, meta_page_id=PAGE, meta_access_token=TOKEN)
    assert cfg.publish_mode == "dry_run"
    assert get_reels_provider(cfg).__class__.__name__ == "DryRunReels"


def test_live_needs_an_explicit_mode_and_credentials():
    live = Settings(_env_file=None, publish_mode="live", meta_page_id=PAGE, meta_access_token=TOKEN)
    assert isinstance(get_reels_provider(live), MetaReelsProvider)
    with pytest.raises(RuntimeError, match="needs a Page ID"):
        get_reels_provider(Settings(_env_file=None, publish_mode="live"))


def test_the_token_is_a_secret_in_settings_repr():
    cfg = Settings(_env_file=None, meta_page_id=PAGE, meta_access_token=TOKEN)
    assert TOKEN not in repr(cfg) and TOKEN not in str(cfg)


def test_production_refuses_a_token_in_dry_run_mode():
    kwargs = dict(_env_file=None, environment="production", jwt_secret="x" * 40,
                  database_url="postgresql+psycopg://u:strong@db/x", meta_access_token=TOKEN)
    with pytest.raises(RuntimeError, match="dry_run"):
        Settings(**kwargs).validate_for_runtime()
    Settings(**kwargs, publish_mode="live", meta_page_id=PAGE).validate_for_runtime()


def test_dry_run_provider_publishes_nothing_but_follows_the_contract(video):
    from app.providers.dryrun import DryRunReels

    p = DryRunReels()
    seen: list[str] = []
    prepared = run(p.prepare(video_path=Path("x"), description="d", is_ai_generated=True,
                             intent_key="k", on_video_id=seen.append))
    assert seen == [prepared.video_id] and prepared.status == "ready"
    assert run(p.publish(video_id=prepared.video_id, intent_key="k")).permalink.startswith(
        "/dry-run/")
    assert run(p.reconcile(video_id=prepared.video_id)).state == "published"
    assert json.dumps(DryRunReels.label)  # clearly labelled
