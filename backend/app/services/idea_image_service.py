"""Turn a picture the owner captured (plus their own explanation) into a meme idea.

The picture is untrusted input: its type is decided by its content, its size and dimensions are
limited, and it is re-encoded before the model sees it. It is processed in memory and never stored.
The model has no tools; its answer is only a draft brief that the owner reads and edits.
"""

import base64
import binascii
import io
import json
from typing import Any

from PIL import Image, UnidentifiedImageError

from app.core.config import Settings
from app.core.errors import ApiError
from app.providers.contracts import JsonLLM
from app.services import feedback_service, llm_service
from app.services.idea_service import _blacklisted, _read_prompt

PROMPT = "idea_from_image"
MAX_BYTES = 6 * 1024 * 1024
MAX_SIDE = 10_000
MAX_PIXELS = 40_000_000
SEND_SIDE = 1568  # the model does not need more detail than this
ALLOWED = {"JPEG", "PNG", "WEBP"}


def prepare_image(data_b64: str) -> bytes:
    """Decode, validate by content, re-encode as a bounded JPEG. Raises ApiError on a problem."""
    try:
        raw = base64.b64decode(data_b64, validate=True)
    except (binascii.Error, ValueError):
        raise ApiError(422, "bad_image", "The picture could not be read") from None
    if not raw or len(raw) > MAX_BYTES:
        raise ApiError(422, "image_too_large", "The picture must be under 6 MB")
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            fmt, size = probe.format, probe.size
        if fmt not in ALLOWED:
            raise ApiError(422, "bad_image_type", "Only JPEG, PNG or WEBP pictures are accepted")
        if max(size) > MAX_SIDE or size[0] * size[1] > MAX_PIXELS:
            raise ApiError(422, "image_too_large", "The picture is too large in pixels")
        with Image.open(io.BytesIO(raw)) as img:
            img.load()
            rgb = img.convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ApiError(422, "bad_image", "The picture could not be read") from None
    rgb.thumbnail((SEND_SIDE, SEND_SIDE))
    out = io.BytesIO()
    rgb.save(out, format="JPEG", quality=85)
    return out.getvalue()


async def describe(
    session, llm: JsonLLM, cfg: Settings, *, image_b64: str, note: str | None,
) -> dict[str, Any]:
    jpeg = prepare_image(image_b64)
    block = {"type": "image", "source": {
        "type": "base64", "media_type": "image/jpeg",
        "data": base64.standard_b64encode(jpeg).decode("ascii")}}
    lessons = feedback_service.approved_lessons(session, "daily_life")
    payload = {"note": (note or "").strip()[:1500],
               **({"channel_lessons": lessons} if lessons else {})}
    try:
        out = await llm_service.call_json(
            session, llm, cfg, purpose="idea_from_image",
            system_prompt=_read_prompt(cfg, PROMPT),
            content=[block, {"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
            schema_name="idea_from_image", entity_type="idea_image", entity_id="upload",
            schema_dir=cfg.schemas_dir, est_input_tokens=3000, max_tokens=2000)
    except llm_service.LlmRefused as exc:
        raise ApiError(422, "llm_refused", "The model declined this picture") from exc
    except llm_service.LlmInvalidOutput as exc:
        raise ApiError(502, "llm_invalid_output", "The model's answer could not be used") from exc
    if not out["brief"].strip():
        raise ApiError(422, "no_idea", "No idea could be made from this picture")
    if _blacklisted(cfg, out["brief"], out["joke_summary"]):
        raise ApiError(422, "blocked_words", "The idea contains words that are not allowed")
    warnings = list(out["warnings"])
    if out["contains_identifiable_person"]:
        warnings.insert(0, "Ảnh có người thật nhìn thấy rõ mặt: ý tưởng đã được viết chung chung, "
                           "đừng dùng hình hay tên người đó.")
    return {"brief": out["brief"].strip(), "joke_summary": out["joke_summary"].strip(),
            "warnings": warnings}
