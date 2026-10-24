"""Narration: normalise -> cache lookup -> reserve budget -> synthesise -> store -> settle.

- The text sent to the provider is the NORMALISED text (numbers, dates, units spelled out).
- The cache key covers normaliser version, normalised text, voice, model and settings, so an
  identical request is a free cache hit and the audio is reproducible.
- Money: a reservation is written and committed BEFORE the paid call (budget_service). The
  settled amount comes from the provider's own `character-cost` header. A known-uncharged failure
  (bad request, rate limit) releases the reservation with a reason; a timeout keeps it (unknown).
- Files are written atomically inside NHD_MEDIA_DIR; nothing outside it is ever touched.
"""

import hashlib
import json
import subprocess
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ApiError
from app.db.models import AudioAsset
from app.providers.contracts import SpeechProvider
from app.providers.errors import ProviderPermanent, ProviderTimeout, ProviderTransient
from app.services import budget_service
from app.services.vi_normalizer import NORMALIZER_VERSION, normalize

MAX_NARRATION_CHARS = 1000
PROVIDER = "elevenlabs"
# Conservative credits-per-character estimates, above what was measured (0.2 / 0.4, 2026-10-22).
CREDITS_PER_CHAR_ESTIMATE = {"eleven_v3": 0.5, "eleven_v4": 0.5}
DEFAULT_CREDITS_PER_CHAR = 0.25


def cache_key(normalized_text: str, voice_id: str, model_id: str, settings: dict) -> str:
    payload = json.dumps(
        {"v": NORMALIZER_VERSION, "text": normalized_text, "voice": voice_id, "model": model_id,
         "settings": settings or {}}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def estimate_usd(chars: int, model_id: str, usd_per_credit: float) -> Decimal:
    credits = chars * CREDITS_PER_CHAR_ESTIMATE.get(model_id, DEFAULT_CREDITS_PER_CHAR)
    return Decimal(str(round(credits * usd_per_credit, 6)))


def probe_duration(path: Path) -> Decimal | None:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
             str(path)], capture_output=True, text=True, check=True, timeout=30).stdout.strip()
        return Decimal(out).quantize(Decimal("0.001"))
    except (OSError, subprocess.SubprocessError, ArithmeticError, ValueError):
        return None


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)


async def get_or_create_narration(
    session: Session, provider: SpeechProvider, cfg: Settings, *, text: str, voice_id: str,
    model_id: str, settings: dict | None = None, lexicon: dict[str, str] | None = None,
    license_note: str | None = None,
) -> AudioAsset:
    settings = settings or {}
    normalized = normalize(text, lexicon)
    if not normalized:
        raise ApiError(422, "empty_narration", "Narration text is empty after normalisation")
    if len(normalized) > MAX_NARRATION_CHARS:
        raise ApiError(422, "narration_too_long",
                       f"Narration is longer than {MAX_NARRATION_CHARS} characters")
    key = cache_key(normalized, voice_id, model_id, settings)

    hit = session.scalar(select(AudioAsset).where(AudioAsset.text_hash == key))
    if hit is not None:
        if hit.duration_s is None:
            # An earlier probe failed (ffprobe timed out on a busy machine); without a duration the
            # video can never be rendered, so measure the stored file again instead of reusing null.
            hit.duration_s = probe_duration(Path(cfg.media_dir) / hit.storage_ref)
            session.commit()
        return hit  # free: same text, voice, model and settings

    reservation = budget_service.reserve(
        session, provider=PROVIDER, operation="tts",
        estimated_usd=estimate_usd(len(normalized), model_id, cfg.elevenlabs_usd_per_credit),
        entity_type="audio_asset", entity_id=key)
    try:
        result = await provider.synthesize(text=normalized, voice_id=voice_id, model_id=model_id,
                                           settings=settings)
    except (ProviderPermanent, ProviderTransient) as exc:
        budget_service.release(session, reservation,
                               reason=f"synthesis rejected before billing: {type(exc).__name__}")
        raise
    except ProviderTimeout:
        raise  # the reservation stays: the provider may have charged

    ref = f"audio/{key}.mp3"
    path = Path(cfg.media_dir) / ref
    _write_atomic(path, result.audio)
    credits = result.credits_charged
    actual_usd = (Decimal(str(credits)) * Decimal(str(cfg.elevenlabs_usd_per_credit))
                  if credits is not None else reservation.estimated_usd)
    budget_service.settle(session, reservation, actual_usd=actual_usd, request_id=result.request_id)

    asset = AudioAsset(
        kind="narration", provider=PROVIDER, model_id=model_id, voice_id=voice_id,
        synth_settings=settings, text_hash=key, normalized_text=normalized,
        normalizer_version=NORMALIZER_VERSION, sha256=hashlib.sha256(result.audio).hexdigest(),
        storage_ref=ref, duration_s=probe_duration(path), characters=len(normalized),
        credits_charged=None if credits is None else Decimal(str(credits)),
        cost_ledger_id=reservation.id, license_note=license_note)
    session.add(asset)
    session.commit()
    return asset
