"""Composition check: measure clue boxes with a vision model, then decide crop vs fit in code.

Flow: sanitise the master (decode, downscale to <= 1024 px, strip metadata) -> reserve budget ->
ask the model for boxes -> settle from the token usage -> validate every box -> decide per target
(`composition.decide_modes`, fail closed on any gap) -> persist the evidence.
A refusal or an unusable answer never crops: the result is `fit` for both targets.
"""

import hashlib
import io
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ApiError
from app.db.models import CompositionCheck
from app.providers.contracts import VisionProvider
from app.providers.errors import ProviderPermanent, ProviderTimeout, ProviderTransient
from app.services import budget_service
from app.services.composition import SAFE_REGIONS, Box, Decision, decide_modes
from app.services.media_paths import UnsafeMediaPath, resolve_media_path

PROVIDER = "anthropic"
MAX_SIDE = 1024
# Upper bound for the reservation: an image of this size is about 1.5k tokens plus the prompt.
ESTIMATE_INPUT_TOKENS = 3000
ESTIMATE_OUTPUT_TOKENS = 1200


def _usd(tokens_in: int, tokens_out: int, cfg: Settings) -> Decimal:
    value = (Decimal(tokens_in) * Decimal(str(cfg.claude_usd_per_mtok_in))
             + Decimal(tokens_out) * Decimal(str(cfg.claude_usd_per_mtok_out))) / Decimal(1_000_000)
    return value.quantize(Decimal("0.000001"))


def prepare_image(data: bytes) -> bytes:
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            clean = img.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError):
        raise ApiError(422, "bad_image", "The master image cannot be read") from None
    clean.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    clean.save(out, format="PNG")  # re-encoded: no metadata leaves the machine
    return out.getvalue()


def _boxes(raw: list[dict]) -> tuple[list[Box], list[str]]:
    boxes: list[Box] = []
    problems: list[str] = []
    for item in raw:
        try:
            boxes.append(Box(str(item["clue_id"]), float(item["x0"]), float(item["y0"]),
                             float(item["x1"]), float(item["y1"])))
        except (KeyError, TypeError, ValueError) as exc:
            problems.append(f"{item.get('clue_id', '?') if isinstance(item, dict) else '?'}: {exc}")
    return boxes, problems


async def check_composition(
    session: Session, provider: VisionProvider, cfg: Settings, *, master_ref: str,
    clues: list[str],
) -> tuple[Decision, CompositionCheck]:
    try:
        path = resolve_media_path(cfg.media_dir, master_ref)
    except UnsafeMediaPath as exc:
        raise ApiError(422, "unsafe_media", f"Media reference rejected: {exc}") from None
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    png = prepare_image(raw)

    reservation = budget_service.reserve(
        session, provider=PROVIDER, operation="vision_composition",
        estimated_usd=_usd(ESTIMATE_INPUT_TOKENS, ESTIMATE_OUTPUT_TOKENS, cfg),
        entity_type="image", entity_id=sha[:16])
    try:
        result = await provider.measure_boxes(image_png=png, clues=clues)
    except (ProviderPermanent, ProviderTransient) as exc:
        budget_service.release(
            session, reservation,
            reason=f"vision request rejected before billing: {type(exc).__name__}")
        raise
    except ProviderTimeout:
        raise  # may have been billed: keep the reservation
    budget_service.settle(session, reservation, actual_usd=_usd(
        result.input_tokens, result.output_tokens, cfg), request_id=result.request_id)

    boxes, problems = _boxes(result.clues)
    if result.refused:
        problems.append("the model refused to measure this image")
        boxes = []
    decision = decide_modes(boxes, expected=clues)
    check = CompositionCheck(
        image_ref=master_ref, image_sha256=sha, model=result.model, expected_clues=clues,
        boxes=[b.__dict__ for b in boxes], problems=problems, modes=dict(decision.modes),
        outside={t: list(v) for t, v in decision.outside.items()}, reason=decision.reason,
        cost_ledger_id=reservation.id)
    session.add(check)
    session.commit()
    return decision, check


__all__ = ["check_composition", "prepare_image", "SAFE_REGIONS"]
