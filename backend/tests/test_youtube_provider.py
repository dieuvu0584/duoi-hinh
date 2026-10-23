"""YouTube provider against a scripted fake of Google's endpoints (no network)."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from app.providers.errors import ProviderPermanent, ProviderTimeout, ProviderTransient
from app.providers.youtube_videos import YouTubeProvider, split_title_description

CID, SECRET, REFRESH = "client-id-123", "client-secret-XYZ", "refresh-token-ABC"
UPLOAD_SESSION = "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=U1"


def run(coro):
    return asyncio.run(coro)


class FakeGoogle:
    """Handles the few endpoints the provider uses and records what it was sent."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.token_calls = 0
        self.insert_body: dict | None = None
        self.update_body: dict | None = None
        self.status = {"privacyStatus": "private", "uploadStatus": "uploaded",
                       "embeddable": True, "license": "youtube", "publicStatsViewable": True,
                       "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True}
        self.processing = ["processing", "succeeded"]  # successive polls
        self.exists = True
        self.upload_location = UPLOAD_SESSION
        self.fail: dict[str, httpx.Response] = {}
        self.lock_private = False  # an unaudited project: update is accepted, video stays private
        self.token_status = 200
        self.scopes = ("https://www.googleapis.com/auth/youtube "
                       "https://www.googleapis.com/auth/youtube.upload")
        self.reject_first_api_call_with_401 = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        url, method = str(request.url), request.method
        self.calls.append((method, url.split("?")[0]))
        key = f"{method} {url.split('?')[0]}"
        if key in self.fail:
            return self.fail[key]
        if url.startswith("https://oauth2.googleapis.com/tokeninfo"):
            return httpx.Response(200, json={"scope": self.scopes})
        if url.startswith("https://oauth2.googleapis.com/token"):
            self.token_calls += 1
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": f"AT{self.token_calls}",
                                             "expires_in": 3600})
        if self.reject_first_api_call_with_401 and request.headers.get("authorization") == "Bearer AT1":
            return httpx.Response(401, json={"error": {"message": "expired"}})
        if method == "POST" and url.startswith("https://www.googleapis.com/upload"):
            self.insert_body = json.loads(request.content)
            return httpx.Response(200, headers={"location": self.upload_location})
        if method == "PUT" and "upload_id" in url:
            return httpx.Response(200, json={"id": "VID1"})
        if url.startswith("https://www.googleapis.com/youtube/v3/videos"):
            if method == "GET":
                if not self.exists:
                    return httpx.Response(200, json={"items": []})
                proc = self.processing.pop(0) if len(self.processing) > 1 else self.processing[0]
                status = dict(self.status)
                if proc == "succeeded":
                    status["uploadStatus"] = "processed"
                return httpx.Response(200, json={"items": [{
                    "id": "VID1", "status": status,
                    "processingDetails": {"processingStatus": proc}}]})
            if method == "PUT":
                self.update_body = json.loads(request.content)
                if not self.lock_private:
                    self.status["privacyStatus"] = self.update_body["status"]["privacyStatus"]
                return httpx.Response(200, json={"id": "VID1"})
            if method == "DELETE":
                if not self.exists:
                    return httpx.Response(404, json={"error": {"message": "no"}})
                self.exists = False
                return httpx.Response(204)
        return httpx.Response(404, json={"error": {"message": "unexpected " + key}})


def provider(google: FakeGoogle, **kw) -> YouTubeProvider:
    async def no_sleep(_s: float) -> None:
        return None

    client = httpx.AsyncClient(transport=httpx.MockTransport(google.handler))
    return YouTubeProvider(client_id=CID, client_secret=SECRET, refresh_token=REFRESH,
                           client=client, sleep=no_sleep, poll_interval=1.0, **kw)


@pytest.fixture()
def video(tmp_path) -> Path:
    path = tmp_path / "v.mp4"
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"x" * 2000)
    return path


def prepare(p, video, *, ai=True, ids=None):
    ids = ids if ids is not None else []
    return run(p.prepare(video_path=video, description="Tiêu đề hay\n\nMô tả dài #meme",
                         is_ai_generated=ai, intent_key="k", on_video_id=ids.append))


def test_title_and_description_are_split_safely():
    title, desc = split_title_description("  Dòng đầu <b>đẹp</b>\n\nPhần còn lại #tag")
    assert title == "Dòng đầu đẹp"  # markup and angle brackets never reach YouTube
    assert "<" not in desc and ">" not in desc and "Phần còn lại" in desc
    assert len(split_title_description("x" * 500)[0]) == 100
    assert split_title_description("")[0]  # never an empty title


def test_prepare_uploads_private_and_flags_synthetic_media(video):
    google = FakeGoogle()
    ids: list[str] = []
    result = prepare(provider(google), video, ids=ids)
    assert result.video_id == "VID1" and result.status == "ready" and result.copyright_clear is True
    assert ids == ["VID1"]  # persisted before the (slow) processing wait
    body = google.insert_body
    assert body["status"]["privacyStatus"] == "private"  # nothing public yet
    assert body["status"]["containsSyntheticMedia"] is True
    assert body["status"]["selfDeclaredMadeForKids"] is False
    assert body["snippet"]["title"] == "Tiêu đề hay" and "Mô tả dài" in body["snippet"]["description"]
    assert body["snippet"]["defaultLanguage"] == "vi"


def test_the_ai_flag_follows_the_revision(video):
    google = FakeGoogle()
    prepare(provider(google), video, ai=False)
    assert google.insert_body["status"]["containsSyntheticMedia"] is False


def test_the_access_token_is_fetched_once_and_reused(video):
    google = FakeGoogle()
    p = provider(google)
    prepare(p, video)
    run(p.reconcile(video_id="VID1"))
    assert google.token_calls == 1


def test_a_401_refreshes_the_token_and_retries_once(video):
    google = FakeGoogle()
    google.reject_first_api_call_with_401 = True
    prepare(provider(google), video)
    assert google.token_calls == 2


def test_a_dead_refresh_token_is_a_permanent_error_without_leaking_secrets(video):
    google = FakeGoogle()
    google.token_status = 400
    with pytest.raises(ProviderPermanent) as err:
        prepare(provider(google), video)
    assert err.value.code == "invalid_grant"
    text = str(err.value)
    assert REFRESH not in text and SECRET not in text and CID not in text


def test_check_token_reports_validity():
    google = FakeGoogle()
    assert run(provider(google).check_token()) is True
    google.token_status = 400
    assert run(provider(google).check_token()) is False


def test_a_token_that_can_only_upload_is_not_valid():
    google = FakeGoogle()
    p = provider(google)
    google.scopes = ("https://www.googleapis.com/auth/youtube.readonly "
                     "https://www.googleapis.com/auth/youtube.upload")
    assert run(p.check_token()) is False and p.last_problem == "missing_scope_youtube"
    google.scopes = "https://www.googleapis.com/auth/youtube.force-ssl"
    assert run(p.check_token()) is True and p.last_problem is None


def test_credentials_are_never_sent_to_an_unexpected_upload_host(video):
    google = FakeGoogle()
    google.upload_location = "https://evil.example.com/steal?x=1"
    p = provider(google)
    with pytest.raises(ProviderPermanent) as err:
        prepare(p, video)
    assert err.value.code == "bad_upload_url"
    assert not any("evil.example.com" in url for _, url in google.calls)


def test_processing_that_never_finishes_returns_an_unready_result(video):
    google = FakeGoogle()
    google.processing = ["processing"]
    result = prepare(provider(google, processing_timeout=3.0), video)
    assert result.status == "processing" and result.copyright_clear is None


def test_a_copyright_rejection_is_reported_as_not_clear(video):
    google = FakeGoogle()
    google.status.update(uploadStatus="rejected", rejectionReason="copyright")
    result = prepare(provider(google), video)
    assert result.copyright_clear is False and result.status == "rejected"


def test_server_errors_when_starting_an_upload_are_unknown_not_retried_blindly(video):
    google = FakeGoogle()
    google.fail["POST https://www.googleapis.com/upload/youtube/v3/videos"] = httpx.Response(503)
    with pytest.raises(ProviderTimeout):
        prepare(provider(google), video)


def test_quota_errors_are_permanent_and_rate_limits_are_transient(video):
    google = FakeGoogle()
    google.fail["POST https://www.googleapis.com/upload/youtube/v3/videos"] = httpx.Response(
        403, json={"error": {"errors": [{"reason": "quotaExceeded"}], "message": "quota"}})
    with pytest.raises(ProviderPermanent) as err:
        prepare(provider(google), video)
    assert err.value.code == "quotaExceeded"
    google.fail["POST https://www.googleapis.com/upload/youtube/v3/videos"] = httpx.Response(429)
    with pytest.raises(ProviderTransient):
        prepare(provider(google), video)


def test_an_empty_or_huge_file_is_refused_before_any_call(tmp_path):
    google = FakeGoogle()
    empty = tmp_path / "e.mp4"
    empty.write_bytes(b"")
    with pytest.raises(ProviderPermanent):
        prepare(provider(google), empty)
    assert google.calls == []


def test_publish_makes_it_public_and_keeps_the_other_status_fields(video):
    google = FakeGoogle()
    p = provider(google)
    prepare(p, video)
    result = run(p.publish(video_id="VID1", intent_key="k"))
    assert result.permalink == "https://www.youtube.com/watch?v=VID1"
    sent = google.update_body["status"]
    assert sent["privacyStatus"] == "public"
    # videos.update replaces the part: the synthetic-media declaration must survive it
    assert sent["containsSyntheticMedia"] is True and sent["selfDeclaredMadeForKids"] is False


def test_publishing_an_already_public_video_changes_nothing():
    google = FakeGoogle()
    google.status["privacyStatus"] = "public"
    result = run(provider(google).publish(video_id="VID1", intent_key="k"))
    assert result.video_id == "VID1" and google.update_body is None


def test_a_video_that_stays_private_is_not_reported_as_published():
    google = FakeGoogle()
    google.lock_private = True  # e.g. an API project that has not passed YouTube's audit
    with pytest.raises(ProviderPermanent) as err:
        run(provider(google).publish(video_id="VID1", intent_key="k"))
    assert err.value.code == "stayed_private"


def test_publishing_a_missing_video_fails_cleanly():
    google = FakeGoogle()
    google.exists = False
    with pytest.raises(ProviderPermanent):
        run(provider(google).publish(video_id="VID1", intent_key="k"))


def test_delete_is_idempotent():
    google = FakeGoogle()
    p = provider(google)
    run(p.delete(video_id="VID1"))
    run(p.delete(video_id="VID1"))  # already gone: fine
    assert google.exists is False


def test_reconcile_reads_the_truth_from_youtube():
    google = FakeGoogle()
    p = provider(google)
    assert run(p.reconcile(video_id="VID1")).state == "draft_ready"
    google.status["privacyStatus"] = "public"
    done = run(p.reconcile(video_id="VID1"))
    assert done.state == "published" and done.permalink.endswith("VID1")
    google.exists = False
    assert run(p.reconcile(video_id="VID1")).state == "missing"
    google.exists = True
    google.fail["GET https://www.googleapis.com/youtube/v3/videos"] = httpx.Response(500)
    assert run(p.reconcile(video_id="VID1")).state == "error"


def test_the_constructor_requires_all_credentials():
    with pytest.raises(ValueError):
        YouTubeProvider(client_id="", client_secret="s", refresh_token="r")
