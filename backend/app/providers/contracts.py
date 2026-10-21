from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class ProviderResult:
    provider: str
    model: str | None
    request_id: str | None
    payload: dict[str, Any]


@dataclass(frozen=True)
class PreparedReel:
    """Result of START + UPLOAD + FINISH(DRAFT) + waiting: a ready draft; nothing is public."""

    video_id: str
    status: str  # provider status, e.g. ready / processing / expired
    copyright_clear: bool | None  # None while the check is still running


@dataclass(frozen=True)
class PublishedReel:
    video_id: str
    permalink: str | None


@dataclass(frozen=True)
class ReconcileResult:
    """What the provider says about a video after an unknown outcome."""

    state: Literal["published", "draft_ready", "missing", "error", "ambiguous"]
    permalink: str | None = None


@dataclass(frozen=True)
class VisionBoxes:
    """What a vision model measured. The backend, not the model, decides what to do with it."""

    clues: list[dict[str, Any]]  # {"clue_id", "description", "x0", "y0", "x1", "y1"} as returned
    notes: str
    refused: bool
    input_tokens: int
    output_tokens: int
    model: str
    request_id: str | None


class VisionProvider(Protocol):
    async def measure_boxes(self, *, image_png: bytes, clues: list[str]) -> VisionBoxes: ...


@dataclass(frozen=True)
class JsonResult:
    payload: dict[str, Any] | None  # None when the model refused
    refused: bool
    input_tokens: int
    output_tokens: int
    model: str
    request_id: str | None


class JsonLLM(Protocol):
    """One structured-output call. `schema` must already be in the endpoint-compatible (wire) form;
    the caller validates the answer against the full schema."""

    async def complete_json(
        self, *, system: str, content: list[dict[str, Any]], schema: dict[str, Any],
        max_tokens: int = 4000,
    ) -> JsonResult: ...


class TextAnalysisProvider(Protocol):
    async def analyze(
        self, *, system_prompt: str, user_payload: dict[str, Any]
    ) -> ProviderResult: ...


@dataclass(frozen=True)
class SpeechResult:
    audio: bytes
    mime_type: str
    credits_charged: float | None  # what the provider billed (ElevenLabs: character-cost header)
    request_id: str | None


class SpeechProvider(Protocol):
    """Text-to-speech (ElevenLabs). Callers normalise the text first and look up the cache by
    sha256(text + voice_id + model_id + settings) before calling; every call is costed."""

    async def synthesize(
        self, *, text: str, voice_id: str, model_id: str, settings: dict[str, Any]
    ) -> SpeechResult: ...


@dataclass(frozen=True)
class ImageResult:
    image: bytes
    mime_type: str
    width: int  # the size the provider really returned (it may round the request up)
    height: int
    seed: int | None
    flagged: bool  # the provider's own safety checker flagged the image
    request_id: str | None


class ImageProvider(Protocol):
    """Text-to-image. Errors are typed: a failure BEFORE the provider accepted the job is
    ProviderPermanent/Transient (known uncharged); a failure after it accepted is ProviderTimeout
    (the charge is unknown)."""

    async def generate(
        self, *, prompt: str, width: int, height: int, seed: int | None = None
    ) -> ImageResult: ...


class ReelsPublishingProvider(Protocol):
    """Draft-then-promote publishing (docs/publishing-meta.md D1, decision D-26 flow).

    `prepare` uploads, waits for the copyright check, finishes as a DRAFT and waits until the
    draft is ready; nothing is public. It must call `on_video_id` as soon as the provider id is
    known (before the upload) so the caller can persist it. `publish` promotes that draft and is
    the only public step; the pre-publish gate and kill switch run right before it. Errors are
    typed (`app.providers.errors`): a timeout is UNKNOWN, never retried blindly. Implementations
    never log tokens.
    """

    async def prepare(
        self, *, video_path: Path, description: str, is_ai_generated: bool, intent_key: str,
        on_video_id: Callable[[str], None],
    ) -> PreparedReel: ...

    async def publish(self, *, video_id: str, intent_key: str) -> PublishedReel: ...

    async def delete(self, *, video_id: str) -> None: ...  # cancel a draft or retract a Reel

    async def reconcile(self, *, video_id: str) -> ReconcileResult: ...


# Implement concrete adapters only after verifying current official provider documentation.
