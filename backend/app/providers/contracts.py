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


class ImageProvider(Protocol):
    async def generate(self, *, prompt: str, aspect_ratio: str = "9:16") -> ProviderResult: ...


class ReelsPublishingProvider(Protocol):
    """Two-phase publishing so the app can gate and kill-switch right before going public.

    See docs/publishing-meta.md. Implementations must persist the provider video id before
    uploading, never log tokens, and classify errors as transient / permanent / unknown.
    """

    async def prepare(self, *, video_path: Path, intent_key: str) -> PreparedReel: ...

    async def get_status(self, *, video_id: str) -> PreparedReel: ...

    async def finish(
        self, *, video_id: str, description: str, is_ai_generated: bool, intent_key: str
    ) -> ProviderResult: ...

    async def reconcile(self, *, video_id: str) -> ProviderResult: ...


# Implement concrete adapters only after verifying current official provider documentation.
