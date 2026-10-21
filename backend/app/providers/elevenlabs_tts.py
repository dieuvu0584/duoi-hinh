"""ElevenLabs text-to-speech adapter (SpeechProvider).

Verified against the official reference and live on 2026-10-22 (docs/video-pipeline.md):
  POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=...
  header `xi-api-key`, JSON body {text, model_id, voice_settings?}; the response is binary audio and
  the header `character-cost` is what the account was actually charged.
The key travels only in the header and is redacted from every message. Errors are typed; a
transport timeout is UNKNOWN (the provider may have charged), never silently retried here.
"""

import re
from typing import Any

import httpx

from app.core.logging import redact_text
from app.providers.contracts import SpeechResult
from app.providers.errors import (
    ProviderError,
    ProviderPermanent,
    ProviderTimeout,
    ProviderTransient,
)

BASE_URL = "https://api.elevenlabs.io"
_VOICE_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_MODEL_RE = re.compile(r"^[a-z0-9_]{1,60}$")
_FORMAT_MIME = {"mp3": "audio/mpeg", "wav": "audio/wav", "pcm": "audio/pcm", "opus": "audio/ogg"}


class ElevenLabsSpeech:
    def __init__(
        self, *, api_key: str, base_url: str = BASE_URL, output_format: str = "mp3_44100_128",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self._key = api_key
        self._base = base_url.rstrip("/")
        self._format = output_format
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=120.0))

    def _safe(self, text: str) -> str:
        return redact_text(text.replace(self._key, "***"))[:200]

    async def synthesize(
        self, *, text: str, voice_id: str, model_id: str, settings: dict[str, Any]
    ) -> SpeechResult:
        if not text.strip():
            raise ProviderPermanent("empty_text", "nothing to synthesise")
        if not _VOICE_RE.match(voice_id) or not _MODEL_RE.match(model_id):
            raise ProviderPermanent("bad_identifier", "voice or model id has unsafe characters")
        body: dict[str, Any] = {"text": text, "model_id": model_id}
        if settings:
            body["voice_settings"] = settings
        try:
            response = await self._client.post(
                f"{self._base}/v1/text-to-speech/{voice_id}",
                params={"output_format": self._format}, json=body,
                headers={"xi-api-key": self._key})
        except httpx.TimeoutException:
            raise ProviderTimeout("timeout while synthesising: the charge is unknown") from None
        except httpx.TransportError as exc:
            raise ProviderTimeout(f"{type(exc).__name__} while synthesising") from None

        if response.status_code >= 500:
            raise ProviderTimeout(f"HTTP {response.status_code}: the charge is unknown")
        if response.status_code == 429:
            raise ProviderTransient("rate or concurrency limit reached")
        if response.status_code >= 400:
            detail = self._safe(response.text)
            raise ProviderPermanent(str(response.status_code), detail)
        if not response.content:
            raise ProviderError("empty audio response")

        cost = response.headers.get("character-cost")
        try:
            credits = float(cost) if cost is not None else None
        except ValueError:
            credits = None
        mime = _FORMAT_MIME.get(self._format.split("_")[0], "application/octet-stream")
        return SpeechResult(audio=response.content, mime_type=mime, credits_charged=credits,
                            request_id=response.headers.get("request-id"))
