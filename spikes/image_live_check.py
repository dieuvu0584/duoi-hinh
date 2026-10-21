"""Live check of the PRODUCTION image path (budget -> fal.ai -> sanitise -> store -> settle).

Uses NHD_DATABASE_URL (the throw-away TEST database) and the real fal.ai key from .env; one 1920x1920
master (about USD 0.044) under a USD 0.10 budget set in that test database only.

    python spikes/image_live_check.py --live
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from decimal import Decimal
from pathlib import Path

import meta_reels_spike as base  # reuse load_env

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.core.config import Settings  # noqa: E402
from app.db.models import CostLedger, ProviderBudget  # noqa: E402
from app.db.session import session_factory  # noqa: E402
from app.providers.fal_image import FalImageProvider  # noqa: E402
from app.services import image_service  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

PROMPT = (
    "A cheerful cartoon fox holding a red umbrella in light rain, humorous meme illustration, "
    "full-bleed square composition filling the whole frame edge to edge, no border, the fox compact "
    "in the exact center occupying only the middle half of the image, simple background, no text"
)


async def main_async(args: argparse.Namespace) -> int:
    env = base.load_env(args.env)
    key = env.get("FAL_API_KEY", "")
    if not args.live:
        print("dry run: would generate one 1920x1920 master via fal.ai")
        return 0
    if not key:
        print("ERROR: FAL_API_KEY missing in .env", file=sys.stderr)
        return 1
    cfg = Settings(_env_file=None, media_dir=args.media_dir)
    with session_factory()() as db:
        db.execute(text("UPDATE system_settings SET daily_budget_limit = 1, monthly_budget_limit = 1"))
        if db.get(ProviderBudget, "fal") is None:
            db.add(ProviderBudget(provider="fal", monthly_limit_usd=Decimal("0.10")))
        db.commit()
        asset = await image_service.generate_master(
            db, FalImageProvider(api_key=key, model=cfg.fal_model), cfg, prompt=PROMPT, size=1920)
        entry = db.scalars(select(CostLedger).where(CostLedger.provider == "fal")
                           .order_by(CostLedger.reserved_at.desc())).first()
        print(f"asset: {asset.storage_ref} {asset.width}x{asset.height} seed={asset.seed}")
        print(f"ledger: estimated={entry.estimated_usd} actual={entry.actual_usd} status={entry.status}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--env", default=".env")
    p.add_argument("--live", action="store_true")
    p.add_argument("--media-dir", default="spikes/output/media")
    return asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
