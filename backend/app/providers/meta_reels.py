"""Facebook Reels adapter (Graph API) implementing the draft-then-promote contract.

Flow (measured against the test Page on 2026-10-22, see docs/publishing-meta.md):
  prepare : START -> UPLOAD -> wait for the copyright check -> FINISH as DRAFT -> wait `ready`
  publish : POST /{video-id} published=true, then verify `published`
  delete  : DELETE /{video-id} (cancels a draft or retracts a published Reel)
  reconcile: GET /{video-id}?fields=published,status

Security: the token travels only in the `Authorization: OAuth` header (never in a URL or body),
is sent only to graph.facebook.com / *.facebook.com hosts, and is redacted from every message.
Errors are typed (`app.providers.errors`): a transport timeout or a 5xx on a state-changing call
is a ProviderTimeout (outcome UNKNOWN), never silently retried here.
"""

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.logging import redact_text
from app.providers.contracts import PreparedReel, PublishedReel, ReconcileResult
from app.providers.errors import (
    ProviderError,
    ProviderExpired,
    ProviderPermanent,
    ProviderTimeout,
    ProviderTransient,
)

GRAPH_BASE = "https://graph.facebook.com"
MAX_FILE_BYTES = 100 * 1024 * 1024
_TRANSIENT_CODES = {1, 2, 4, 17, 32, 341, 613}
_PERMANENT_CODES = {10, 100, 102, 190, 200, 368}  # token, permission, parameter, policy


def _trusted_host(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    return parsed.scheme == "https" and (host == "facebook.com" or host.endswith(".facebook.com"))


class MetaReelsProvider:
    def __init__(
        self, *, page_id: str, access_token: str, api_version: str = "v26.0",
        graph_base: str = GRAPH_BASE, client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        poll_interval: float = 5.0, copyright_timeout: float = 180.0,
        ready_timeout: float = 300.0, verify_timeout: float = 60.0,
        max_file_bytes: int = MAX_FILE_BYTES,
    ) -> None:
        if not page_id or not access_token:
            raise ValueError("page_id and access_token are required")
        self._page_id = page_id
        self._token = access_token
        self._graph = f"{graph_base.rstrip('/')}/{api_version}"
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=120.0))
        self._sleep = sleep
        self._poll, self._cc_timeout = poll_interval, copyright_timeout
        self._ready_timeout, self._verify_timeout = ready_timeout, verify_timeout
        self._max_bytes = max_file_bytes

    # ------------------------------------------------------------------ plumbing
    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        return {"Authorization": f"OAuth {self._token}", **(extra or {})}

    def _safe(self, text: str) -> str:
        return redact_text(text.replace(self._token, "***"))[:200]

    def _error(self, response: httpx.Response, *, state_changing: bool) -> ProviderError:
        try:
            err = response.json().get("error", {})
        except ValueError:
            err = {}
        code = int(err.get("code", 0) or 0)
        sub = err.get("error_subcode")
        label = f"{code}/{sub}" if sub else str(code or response.status_code)
        message = self._safe(str(err.get("message", "")))
        if response.status_code >= 500:
            cls = ProviderTimeout if state_changing else ProviderTransient
            return cls(f"HTTP {response.status_code} {label}")
        if code in _TRANSIENT_CODES:
            return ProviderTransient(f"{label} {message}")
        if code == 6000 or (isinstance(sub, int) and 1363000 <= sub < 1364000) \
                or code in _PERMANENT_CODES or 400 <= response.status_code < 500:
            return ProviderPermanent(label, message)
        return ProviderPermanent(label, message)

    async def _request(
        self, method: str, url: str, *, state_changing: bool, **kwargs: Any
    ) -> httpx.Response:
        if not _trusted_host(url):
            raise ProviderPermanent("untrusted_host", "refusing to send credentials to this host")
        kwargs["headers"] = self._headers(kwargs.get("headers"))
        try:
            response = await self._client.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(f"{type(exc).__name__} on {method}") from None
        except httpx.TransportError as exc:
            cls = ProviderTimeout if state_changing else ProviderTransient
            raise cls(f"{type(exc).__name__} on {method}") from None
        if response.status_code >= 400:
            raise self._error(response, state_changing=state_changing)
        return response

    def _json(self, response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError:
            raise ProviderPermanent("bad_response", "response was not JSON") from None
        return data if isinstance(data, dict) else {}

    # ------------------------------------------------------------------ contract
    async def check_token(self) -> bool:
        """True when the Page token can read the Page (a cheap, side-effect-free call)."""
        try:
            resp = await self._client.get(
                f"{self._graph}/{self._page_id}", params={"fields": "id"},
                headers=self._headers())
        except httpx.HTTPError:
            return False
        return resp.status_code == 200 and resp.json().get("id") == self._page_id

    async def prepare(
        self, *, video_path: Path, description: str, is_ai_generated: bool, intent_key: str,
        on_video_id: Callable[[str], None],
    ) -> PreparedReel:
        size = video_path.stat().st_size
        if size <= 0 or size > self._max_bytes:
            raise ProviderPermanent("file_size", f"{size} bytes is outside the allowed range")
        data = self._json(await self._request(
            "POST", f"{self._graph}/{self._page_id}/video_reels", state_changing=True,
            data={"upload_phase": "start"}))
        video_id, upload_url = str(data.get("video_id", "")), str(data.get("upload_url", ""))
        if not video_id or not upload_url:
            raise ProviderPermanent("bad_response", "START did not return an id and upload url")
        on_video_id(video_id)  # persisted by the caller BEFORE any bytes are uploaded

        await self._request(
            "POST", upload_url, state_changing=True, content=video_path.read_bytes(),
            headers={"offset": "0", "file_size": str(size),
                     "Content-Type": "application/octet-stream"})

        clear = await self._wait_copyright(video_id)
        if clear is not True:
            return PreparedReel(video_id=video_id, status="upload_complete", copyright_clear=clear)

        await self._request(
            "POST", f"{self._graph}/{self._page_id}/video_reels", state_changing=True,
            data={"upload_phase": "finish", "video_id": video_id, "video_state": "DRAFT",
                  "description": description,
                  "is_ai_generated": "true" if is_ai_generated else "false"})
        await self._wait_ready(video_id)
        return PreparedReel(video_id=video_id, status="ready", copyright_clear=True)

    async def _wait_copyright(self, video_id: str) -> bool | None:
        waited = 0.0
        while True:
            data = self._json(await self._request(
                "GET", f"{self._graph}/{video_id}", state_changing=False,
                params={"fields": "copyright_check_information"}))
            info = data.get("copyright_check_information") or {}
            status = info.get("status")
            state = status.get("status") if isinstance(status, dict) else status
            if state == "complete":
                matches = info.get("matches_found")
                if matches is None and isinstance(status, dict):
                    matches = status.get("matches_found")  # measured shape: nested in `status`
                return not bool(matches)
            if waited >= self._cc_timeout:
                return None  # still running: not clear, so the record is blocked, not published
            await self._sleep(self._poll)
            waited += self._poll

    async def _wait_ready(self, video_id: str) -> None:
        waited = 0.0
        while True:
            status = await self._status(video_id)
            video_status = status.get("video_status")
            if video_status == "ready":
                return
            if video_status == "expired":
                raise ProviderExpired("video expired before it was ready")
            if video_status in {"error", "upload_failed"}:
                raise ProviderPermanent("processing_failed", str(video_status))
            if waited >= self._ready_timeout:
                raise ProviderTimeout("timed out waiting for the draft to become ready")
            await self._sleep(self._poll)
            waited += self._poll

    async def _status(self, video_id: str) -> dict[str, Any]:
        data = self._json(await self._request(
            "GET", f"{self._graph}/{video_id}", state_changing=False,
            params={"fields": "status"}))
        status = data.get("status")
        return status if isinstance(status, dict) else {}

    async def publish(self, *, video_id: str, intent_key: str) -> PublishedReel:
        data = self._json(await self._request(
            "POST", f"{self._graph}/{video_id}", state_changing=True,
            data={"published": "true"}))
        if data.get("success") is not True:
            raise ProviderTimeout("promotion answered without success: outcome unknown")
        waited = 0.0
        while True:  # verify so a "success" that did nothing is never reported as published
            obj = self._json(await self._request(
                "GET", f"{self._graph}/{video_id}", state_changing=False,
                params={"fields": "published,permalink_url"}))
            if obj.get("published") is True:
                return PublishedReel(video_id=video_id, permalink=obj.get("permalink_url"))
            if waited >= self._verify_timeout:
                raise ProviderTimeout("promotion not confirmed in time: outcome unknown")
            await self._sleep(min(self._poll, 2.0))
            waited += min(self._poll, 2.0)

    async def delete(self, *, video_id: str) -> None:
        try:
            await self._request("DELETE", f"{self._graph}/{video_id}", state_changing=True)
        except ProviderPermanent as exc:
            if getattr(exc, "code", "").startswith("100/33"):
                return  # already gone: the goal state is reached
            raise

    async def reconcile(self, *, video_id: str) -> ReconcileResult:
        try:
            obj = self._json(await self._request(
                "GET", f"{self._graph}/{video_id}", state_changing=False,
                params={"fields": "published,permalink_url,status"}))
        except ProviderPermanent as exc:
            if getattr(exc, "code", "").startswith("100/33"):
                return ReconcileResult("missing")
            raise
        if obj.get("published") is True:
            return ReconcileResult("published", obj.get("permalink_url"))
        status = obj.get("status") if isinstance(obj.get("status"), dict) else {}
        video_status = status.get("video_status")
        if video_status in {"error", "expired", "upload_failed"}:
            return ReconcileResult("error")
        if video_status == "ready":
            return ReconcileResult("draft_ready")
        return ReconcileResult("ambiguous")
