"""Master image generation: reserve budget -> generate -> verify -> store -> settle.

- Cost is estimated from the requested pixels with a small rounding margin (fal rounds sizes up),
  reserved and committed BEFORE the call, and settled from the size really returned.
- The returned bytes are decoded with Pillow under a pixel cap, re-encoded to PNG (strips any
  embedded metadata, rejects corrupt or polyglot files) and written atomically inside NHD_MEDIA_DIR.
- Images flagged by the provider's safety checker are not stored (the charge is still settled).
- A known-uncharged failure releases the reservation; an unknown one keeps it.
"""

import hashlib
import io
import uuid
from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ApiError
from app.db.models import ImageAsset
from app.providers.contracts import ImageProvider
from app.providers.errors import ProviderPermanent, ProviderTimeout, ProviderTransient
from app.services import budget_service

PROVIDER = "fal"
MAX_PIXELS = 4096 * 4096
ROUNDING_MARGIN = Decimal("1.02")  # fal returned 1088 for a 1080 request (+0.7%)


def megapixels(width: int, height: int) -> Decimal:
    return Decimal(width * height) / Decimal(1_000_000)  # conservative: 1,000,000 px = 1 MP


def estimate_usd(width: int, height: int, usd_per_mp: float) -> Decimal:
    return (megapixels(width, height) * Decimal(str(usd_per_mp)) * ROUNDING_MARGIN).quantize(
        Decimal("0.000001"))


def actual_usd(width: int, height: int, usd_per_mp: float) -> Decimal:
    return (megapixels(width, height) * Decimal(str(usd_per_mp))).quantize(Decimal("0.000001"))


def sanitize_png(data: bytes) -> tuple[bytes, int, int]:
    """Decode under a pixel cap and re-encode as PNG; raises ApiError for anything unusable."""
    from PIL import Image, UnidentifiedImageError

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            clean = img.convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, ValueError):
        raise ApiError(422, "bad_image", "The provider returned an unreadable image") from None
    out = io.BytesIO()
    clean.save(out, format="PNG")
    return out.getvalue(), clean.width, clean.height


async def generate_master(
    session: Session, provider: ImageProvider, cfg: Settings, *, prompt: str, size: int = 1920,
    seed: int | None = None, content_item_id: uuid.UUID | None = None,
) -> ImageAsset:
    estimate = estimate_usd(size, size, cfg.fal_usd_per_megapixel)
    reservation = budget_service.reserve(
        session, provider=PROVIDER, operation="image", estimated_usd=estimate,
        entity_type="image", entity_id=hashlib.sha256(prompt.encode()).hexdigest()[:16])
    try:
        result = await provider.generate(prompt=prompt, width=size, height=size, seed=seed)
    except (ProviderPermanent, ProviderTransient) as exc:
        budget_service.release(
            session, reservation,
            reason=f"image request rejected before billing: {type(exc).__name__}")
        raise
    except ProviderTimeout:
        raise  # the job may have been charged: keep the reservation

    # fal does not return the cost: compute it from what was really produced.
    budget_service.settle(session, reservation,
                          actual_usd=actual_usd(result.width, result.height,
                                                cfg.fal_usd_per_megapixel),
                          request_id=result.request_id)
    if result.flagged:
        raise ApiError(422, "image_flagged", "The image was flagged by the provider safety checker")

    png, width, height = sanitize_png(result.image)
    digest = hashlib.sha256(png).hexdigest()
    ref = f"images/{digest}.png"
    path = Path(cfg.media_dir) / ref
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".png.part")
    tmp.write_bytes(png)
    tmp.replace(path)

    asset = ImageAsset(
        provider=PROVIDER, model_id=cfg.fal_model, prompt=prompt,
        prompt_hash=hashlib.sha256(prompt.encode()).hexdigest(), seed=result.seed, width=width,
        height=height, sha256=digest, storage_ref=ref, cost_ledger_id=reservation.id,
        content_item_id=content_item_id)
    session.add(asset)
    session.commit()
    return asset
