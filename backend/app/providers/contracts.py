from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class ProviderResult:
    provider: str
    model: str | None
    request_id: str | None
    payload: dict[str, Any]


@dataclass(frozen=True)
class PreparedReel:
    """Result of START + UPLOAD + processing; nothing is public yet."""

    video_id: str
    status: str  # provider status, e.g. upload_complete / processing / ready / expired
    copyright_clear: bool | None  # None while the check is still running


class TextAnalysisProvider(Protocol):
    async def analyze(
        self, *, system_prompt: str, user_payload: dict[str, Any]
    ) -> ProviderResult: ...


class SpeechProvider(Protocol):
    """Text-to-speech (ElevenLabs). Callers normalise the text first and look up the cache by
    sha256(text + voice_id + model_id + settings) before calling; every call is costed."""

    async def synthesize(
        self, *, text: str, voice_id: str, model_id: str, settings: dict[str, Any]
    ) -> ProviderResult: ...


class ImageProvider(Protocol):
    async def generate(self, *, prompt: str, aspect_ratio: str = "9:16") -> ProviderResult: ...


class ReelsPublishingProvider(Protocol):
    """Draft-then-promote publishing (docs/publishing-meta.md D1, decision D-23).

    `prepare` uploads, waits for the copyright check, finishes as a DRAFT and waits until the
    draft is ready; nothing is public. `publish` promotes that draft and is the only public
    step, so the pre-publish gate and kill switch run right before it. Implementations must
    persist the provider video id before uploading, never log tokens, and classify errors as
    transient / permanent / unknown.
    """

    async def prepare(
        self, *, video_path: Path, description: str, is_ai_generated: bool, intent_key: str
    ) -> PreparedReel: ...

    async def get_status(self, *, video_id: str) -> PreparedReel: ...

    async def publish(self, *, video_id: str, intent_key: str) -> ProviderResult: ...

    async def delete(self, *, video_id: str) -> ProviderResult: ...  # cancel a draft or retract

    async def reconcile(self, *, video_id: str) -> ProviderResult: ...


# Implement concrete adapters only after verifying current official provider documentation.
