"""Canonical content hash (docs/data-model.md, "Canonical content hash").

Approval, safety review, the rendered video and publishing are all bound to this hash, so the
function must be stable: UTF-8 NFC strings, JSON with sorted keys and no insignificant
whitespace, a fixed field list, and a version number. Changing anything here is a breaking
change and must bump HASH_VERSION (golden tests will fail first).
"""

import hashlib
import json
import unicodedata
from typing import Any

HASH_VERSION = 1

HASHED_FIELDS = (
    "answer_text",
    "clue_mapping",
    "rebus_spec",
    "caption",
    "hashtags",
    "overlay_text",
    "media_hash",
    "audio_hash",
    "is_ai_generated",
    "template_id",
)


def _canonical(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, dict):
        return {unicodedata.normalize("NFC", str(k)): _canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    return value


def compute_content_hash(fields: dict[str, Any]) -> str:
    unknown = set(fields) - set(HASHED_FIELDS)
    if unknown:
        raise ValueError(f"fields not part of the content hash: {sorted(unknown)}")
    payload = {"v": HASH_VERSION, **{name: _canonical(fields.get(name)) for name in HASHED_FIELDS}}
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
