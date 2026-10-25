"""YouTube publishing through the Data API v3 (docs/publishing-youtube.md).

Draft-then-promote like Facebook: `prepare` uploads the 16:9 video as PRIVATE (resumable upload),
waits for YouTube to process it, and nothing is public. `publish` flips it to public after the
backend gate has passed. Credentials are an OAuth client plus the channel owner's refresh token;
the access token is short-lived, kept in memory only, and never logged.

Typed errors follow the contract in `app.providers.errors`: a timeout on a state-changing call is
UNKNOWN (ProviderTimeout), a definite refusal is ProviderPermanent.
"""

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.logging import redact_text
from app.providers.contracts import PreparedReel, PublishedReel, ReconcileResult
from app.providers.errors import ProviderPermanent, ProviderTimeout, ProviderTransient

TOKEN_URL = "https://oauth2.googleapis.com/token"
TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
# Uploading needs only youtube.upload, but promoting (videos.update) and cancelling
# (videos.delete) need one of these full-management scopes. Without them a draft could be
# uploaded and then never published or removed, so such a token is treated as not valid.
MANAGE_SCOPES = {"https://www.googleapis.com/auth/youtube",
                 "https://www.googleapis.com/auth/youtube.force-ssl"}
API_BASE = "https://www.googleapis.com/youtube/v3"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
MAX_FILE_BYTES = 512 * 1024 * 1024
CATEGORY_ENTERTAINMENT = "24"
_TAGS = re.compile(r"<[^>]*>")
_TITLE_BAD = re.compile(r"[<>]")


def _trusted_host(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    return parsed.scheme == "https" and (
        host == "googleapis.com" or host.endswith(".googleapis.com"))


def split_title_description(text: str) -> tuple[str, str]:
    """Title = first non-empty line (<= 100 chars, no angle brackets); description = the rest
    plus the full text so nothing the owner approved is lost."""
    text = _TAGS.sub("", text)  # YouTube rejects angle brackets; drop any markup first
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    first = lines[0] if lines else "Memes On Air"
    title = _TITLE_BAD.sub("", first)[:100].strip() or "Memes On Air"
    return title, _TITLE_BAD.sub("", text)[:4900]


class YouTubeProvider:
    def __init__(
        self, *, client_id: str, client_secret: str, refresh_token: str,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        poll_interval: float = 10.0, processing_timeout: float = 900.0,
        max_file_bytes: int = MAX_FILE_BYTES,
    ) -> None:
        if not client_id or not client_secret or not refresh_token:
            raise ValueError("client_id, client_secret and refresh_token are required")
        self._id, self._secret, self._refresh = client_id, client_secret, refresh_token
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=300.0))
        self._sleep, self._poll = sleep, poll_interval
        self._processing_timeout = processing_timeout
        self._max_bytes = max_file_bytes
        self._access: str | None = None
        self._access_until = 0.0
        self.last_problem: str | None = None  # why the last check_token said no (never a secret)

    # ------------------------------------------------------------------ plumbing
    def _safe(self, text: str) -> str:
        for secret in (self._id, self._secret, self._refresh, self._access or ""):
            if secret:
                text = text.replace(secret, "***")
        return redact_text(text)[:200]

    async def _token(self, *, force: bool = False) -> str:
        if not force and self._access and time.monotonic() < self._access_until - 60:
            return self._access
        try:
            resp = await self._client.post(TOKEN_URL, data={
                "client_id": self._id, "client_secret": self._secret,
                "refresh_token": self._refresh, "grant_type": "refresh_token"})
        except httpx.TimeoutException:
            raise ProviderTransient("token endpoint timed out") from None
        except httpx.HTTPError:
            raise ProviderTransient("token endpoint unreachable") from None
        if resp.status_code != 200:
            code = "oauth_error"
            try:
                code = str(resp.json().get("error", code))[:40]
            except ValueError:
                pass
            if resp.status_code >= 500:
                raise ProviderTransient(f"token endpoint {resp.status_code}")
            raise ProviderPermanent(code, "the refresh token or OAuth client is not valid")
        body = resp.json()
        self._access = body["access_token"]
        self._access_until = time.monotonic() + float(body.get("expires_in", 3000))
        return self._access

    def _error(self, resp: httpx.Response, *, state_changing: bool) -> Exception:
        reason = ""
        try:
            err = resp.json().get("error", {})
            reason = str((err.get("errors") or [{}])[0].get("reason", ""))[:60]
            message = self._safe(str(err.get("message", "")))
        except (ValueError, AttributeError, IndexError):
            message = ""
        status = resp.status_code
        if status == 429 or (
                status == 403 and reason in {"rateLimitExceeded", "userRateLimitExceeded"}):
            return ProviderTransient(f"rate limited ({reason or status})")
        if status >= 500:
            cls = ProviderTimeout if state_changing else ProviderTransient
            return cls(f"youtube {status}")
        return ProviderPermanent(reason or f"http_{status}", message)

    async def _call(self, method: str, url: str, *, state_changing: bool = False,
                    **kwargs: Any) -> httpx.Response:
        """One authenticated request; a 401 triggers one token refresh and one retry."""
        if not _trusted_host(url):
            raise ProviderPermanent("untrusted_host", "refusing to send credentials to this host")
        extra = kwargs.pop("headers", {})
        for attempt in (0, 1):
            token = await self._token(force=attempt == 1)
            headers = {**extra, "Authorization": f"Bearer {token}"}
            try:
                resp = await self._client.request(method, url, headers=headers, **kwargs)
            except httpx.TimeoutException:
                raise (ProviderTimeout if state_changing else ProviderTransient)(
                    "youtube request timed out") from None
            except httpx.HTTPError:
                raise (ProviderTimeout if state_changing else ProviderTransient)(
                    "youtube unreachable") from None
            if resp.status_code == 401 and attempt == 0:
                continue
            return resp
        raise ProviderPermanent("unauthorized", "YouTube rejected the access token")

    # ------------------------------------------------------------------ contract
    async def check_token(self) -> bool:
        """True when the refresh token gives an access token that may upload, publish and delete."""
        self.last_problem = None
        try:
            token = await self._token(force=True)
        except ProviderPermanent as exc:
            self.last_problem = exc.code
            return False
        except ProviderTransient:
            self.last_problem = "unreachable"
            return False
        try:
            resp = await self._client.post(TOKENINFO_URL, data={"access_token": token})
            granted = resp.json().get("scope", "") if resp.status_code == 200 else ""
            scopes = set(str(granted).split())
        except (httpx.HTTPError, ValueError):
            self.last_problem = "tokeninfo_unreachable"
            return False
        if not scopes & MANAGE_SCOPES:
            self.last_problem = "missing_scope_youtube"
            return False
        return True

    async def prepare(
        self, *, video_path: Path, description: str, is_ai_generated: bool, intent_key: str,
        on_video_id: Callable[[str], None],
    ) -> PreparedReel:
        size = video_path.stat().st_size
        if size <= 0 or size > self._max_bytes:
            raise ProviderPermanent("bad_file_size", "video file is empty or too large")
        title, body = split_title_description(description)
        metadata = {
            "snippet": {"title": title, "description": body, "categoryId": CATEGORY_ENTERTAINMENT,
                        "defaultLanguage": "vi", "defaultAudioLanguage": "vi"},
            # Always private first: nothing is public until the gate has passed.
            "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False,
                       "containsSyntheticMedia": bool(is_ai_generated), "embeddable": True,
                       "publicStatsViewable": True, "license": "youtube"},
        }
        start = await self._call(
            "POST", UPLOAD_URL, state_changing=True,
            params={"uploadType": "resumable", "part": "snippet,status"},
            headers={"X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)},
            json=metadata)
        if start.status_code != 200:
            raise self._error(start, state_changing=True)
        session_url = start.headers.get("location", "")
        if not _trusted_host(session_url):
            raise ProviderPermanent("bad_upload_url", "YouTube returned an unexpected upload URL")
        # The upload session carries its own authorisation; the bytes go in a single PUT.
        try:
            with video_path.open("rb") as fh:
                resp = await self._client.put(
                    session_url, content=fh.read(), headers={"Content-Type": "video/mp4"},
                    timeout=httpx.Timeout(60.0, read=300.0, write=600.0))
        except httpx.HTTPError:
            raise ProviderTimeout("upload interrupted: the video may or may not exist") from None
        if resp.status_code not in {200, 201}:
            raise self._error(resp, state_changing=True)
        video_id = str(resp.json().get("id", ""))
        if not video_id:
            raise ProviderPermanent("no_video_id", "YouTube returned no video id")
        on_video_id(video_id)  # persisted before we wait: the anchor for reconcile/cancel
        return await self._wait_processed(video_id)

    async def _wait_processed(self, video_id: str) -> PreparedReel:
        waited = 0.0
        while True:
            info = await self._video(video_id, "status,processingDetails")
            if info is None:
                raise ProviderPermanent("video_vanished", "the uploaded video is gone")
            status = info.get("status", {})
            upload = status.get("uploadStatus")
            if upload == "rejected":
                reason = status.get("rejectionReason", "rejected")
                return PreparedReel(video_id, "rejected", copyright_clear=reason != "copyright")
            if upload == "failed":
                raise ProviderPermanent(str(status.get("failureReason", "upload_failed"))[:40])
            processing = info.get("processingDetails", {}).get("processingStatus")
            if upload == "processed" and processing in {None, "succeeded"}:
                return PreparedReel(video_id, "ready", copyright_clear=True)
            if processing == "failed":
                detail = info["processingDetails"].get("processingFailureReason", "processing")
                raise ProviderPermanent(str(detail)[:40])
            if waited >= self._processing_timeout:
                return PreparedReel(video_id, "processing", copyright_clear=None)
            await self._sleep(self._poll)
            waited += self._poll

    async def _video(self, video_id: str, part: str) -> dict[str, Any] | None:
        resp = await self._call("GET", f"{API_BASE}/videos", params={"part": part, "id": video_id})
        if resp.status_code != 200:
            raise self._error(resp, state_changing=False)
        items = resp.json().get("items", [])
        return items[0] if items else None

    async def publish(self, *, video_id: str, intent_key: str) -> PublishedReel:
        info = await self._video(video_id, "status")
        if info is None:
            raise ProviderPermanent("video_missing", "nothing to publish")
        status = dict(info.get("status", {}))
        if status.get("privacyStatus") == "public":
            return PublishedReel(video_id, f"https://www.youtube.com/watch?v={video_id}")
        # videos.update replaces the whole `status` part: send back every mutable field we know.
        keep = {k: status[k] for k in ("embeddable", "license", "publicStatsViewable",
                                       "selfDeclaredMadeForKids", "containsSyntheticMedia")
                if k in status}
        keep["privacyStatus"] = "public"
        resp = await self._call("PUT", f"{API_BASE}/videos", state_changing=True,
                                params={"part": "status"}, json={"id": video_id, "status": keep})
        if resp.status_code != 200:
            raise self._error(resp, state_changing=True)
        after = await self._video(video_id, "status")
        if not after or after.get("status", {}).get("privacyStatus") != "public":
            # e.g. an API project that has not passed YouTube's audit keeps uploads private.
            raise ProviderPermanent(
                "stayed_private", "YouTube accepted the request but the video is still not public")
        return PublishedReel(video_id, f"https://www.youtube.com/watch?v={video_id}")

    async def delete(self, *, video_id: str) -> None:
        resp = await self._call("DELETE", f"{API_BASE}/videos", state_changing=True,
                                params={"id": video_id})
        if resp.status_code in {200, 204, 404}:
            return  # already gone is fine (idempotent)
        raise self._error(resp, state_changing=True)

    async def reconcile(self, *, video_id: str) -> ReconcileResult:
        try:
            info = await self._video(video_id, "status")
        except (ProviderTransient, ProviderTimeout):
            return ReconcileResult("error")
        if info is None:
            return ReconcileResult("missing")
        if info.get("status", {}).get("privacyStatus") == "public":
            return ReconcileResult("published", f"https://www.youtube.com/watch?v={video_id}")
        return ReconcileResult("draft_ready")
