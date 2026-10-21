"""fal.ai FLUX image adapter (ImageProvider).

Verified against fal.ai docs and live on 2026-10-22 (docs/spikes/package-0-report.md):
  POST https://queue.fal.run/<endpoint>  header `Authorization: Key <key>`
  body {prompt, image_size:{width,height}, num_images:1, output_format:"png", enable_safety_checker}
  -> {request_id, status_url, response_url, cancel_url}; poll status_url until COMPLETED; GET
  response_url -> {images:[{url,width,height}], seed, has_nsfw_concepts}. fal returns a signed CDN
  URL (we download without the key) and may round the size up (1080 came back as 1088).
The key is only ever in the Authorization header and is redacted from messages. Failures before fal
accepted the job are ProviderPermanent/Transient (known uncharged); any failure after it accepted
the job is ProviderTimeout (the charge is unknown).
"""

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.core.logging import redact_text
from app.core.urlsafety import is_safe_public_https_url
from app.providers.contracts import ImageResult
from app.providers.errors import (
    ProviderError,
    ProviderPermanent,
    ProviderTimeout,
    ProviderTransient,
)

QUEUE_BASE = "https://queue.fal.run"
_ENDPOINT_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*){1,3}$")
MAX_IMAGE_BYTES = 40 * 1024 * 1024
MIN_SIDE, MAX_SIDE = 512, 2048  # documented custom size limits


class FalImageProvider:
    def __init__(
        self, *, api_key: str, model: str = "fal-ai/flux-2", queue_base: str = QUEUE_BASE,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, poll_interval: float = 1.0,
        timeout_s: float = 240.0, enable_safety_checker: bool = True,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        if not _ENDPOINT_RE.match(model):
            raise ValueError("model must look like 'owner/app' or 'owner/app/variant'")
        self._key, self._model = api_key, model
        self._queue = queue_base.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=60.0))
        self._sleep, self._poll, self._timeout = sleep, poll_interval, timeout_s
        self._safety = enable_safety_checker

    def _safe(self, text: str) -> str:
        return redact_text(text.replace(self._key, "***"))[:200]

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Key {self._key}"}

    async def _call(self, method: str, url: str, *, accepted: bool, **kw: Any) -> httpx.Response:
        """`accepted` = fal already took the job, so any failure leaves the charge unknown."""
        try:
            response = await self._client.request(method, url, headers=self._headers(), **kw)
        except httpx.TransportError as exc:
            cls = ProviderTimeout if accepted else ProviderTransient
            raise cls(f"{type(exc).__name__} talking to fal.ai") from None
        status = response.status_code
        if status < 400:
            return response
        if accepted:  # the job exists: whatever went wrong, the charge is unknown
            raise ProviderTimeout(f"HTTP {status} after the job was accepted")
        if status >= 500 or status == 429:
            raise ProviderTransient(f"HTTP {status}")
        raise ProviderPermanent(str(status), self._safe(response.text))

    async def generate(
        self, *, prompt: str, width: int, height: int, seed: int | None = None
    ) -> ImageResult:
        if not prompt.strip() or len(prompt) > 4000:
            raise ProviderPermanent("bad_prompt", "prompt is empty or too long")
        if not (MIN_SIDE <= width <= MAX_SIDE and MIN_SIDE <= height <= MAX_SIDE):
            raise ProviderPermanent("bad_size", f"sides must be {MIN_SIDE}-{MAX_SIDE}")
        body: dict[str, Any] = {
            "prompt": prompt, "image_size": {"width": width, "height": height},
            "num_images": 1, "output_format": "png", "enable_safety_checker": self._safety}
        if seed is not None:
            body["seed"] = seed

        submitted = (await self._call("POST", f"{self._queue}/{self._model}", accepted=False,
                                      json=body)).json()
        request_id = str(submitted.get("request_id", ""))
        status_url, response_url = submitted.get("status_url"), submitted.get("response_url")
        if not request_id or not status_url or not response_url:
            raise ProviderPermanent("bad_response", "fal.ai did not return a request id")
        for url in (status_url, response_url):
            if not url.startswith(self._queue + "/"):
                raise ProviderPermanent("untrusted_url", "fal.ai returned an unexpected status url")

        waited = 0.0
        while True:  # from here on the job is accepted: failures leave the charge unknown
            status = (await self._call("GET", status_url, accepted=True)).json()
            if status.get("status") == "COMPLETED":
                if status.get("error"):
                    raise ProviderTimeout(f"generation failed: {self._safe(str(status['error']))}")
                break
            if waited >= self._timeout:
                raise ProviderTimeout("timed out waiting for the image")
            await self._sleep(self._poll)
            waited += self._poll

        result = (await self._call("GET", response_url, accepted=True)).json()
        images = result.get("images") or []
        if not images or not images[0].get("url"):
            raise ProviderTimeout("fal.ai completed without an image")
        first = images[0]
        if not is_safe_public_https_url(first["url"]):
            raise ProviderPermanent("untrusted_url", "image url is not a public https url")
        flagged = any(bool(x) for x in (result.get("has_nsfw_concepts") or []))
        image = await self._download(first["url"])
        return ImageResult(
            image=image, mime_type=first.get("content_type") or "image/png",
            width=int(first.get("width") or width), height=int(first.get("height") or height),
            seed=result.get("seed"), flagged=flagged, request_id=request_id)

    async def _download(self, url: str) -> bytes:
        try:  # no Authorization header and no redirects: the key never goes to a CDN host
            response = await self._client.get(url, follow_redirects=False)
        except httpx.TransportError:
            raise ProviderTimeout("could not download the generated image") from None
        if response.status_code != 200:
            raise ProviderTimeout(f"image download answered HTTP {response.status_code}")
        if len(response.content) > MAX_IMAGE_BYTES or not response.content:
            raise ProviderError("downloaded image is empty or too large")
        return response.content
