"""Live check of the PRODUCTION composition check (budget -> Claude vision -> boxes -> decision).

Uses NHD_DATABASE_URL (the throw-away TEST database), the real Anthropic key from .env and the most
recent master image under spikes/output/media/images. One request (about USD 0.012) under a USD 0.05
budget set in the test database only. The key is never printed.

    python spikes/composition_live_check.py --live --clues "fox,red umbrella"
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from decimal import Decimal
from pathlib import Path

import meta_reels_spike as base  # reuse load_env

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import anthropic  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.db.models import ProviderBudget  # noqa: E402
from app.db.session import session_factory  # noqa: E402
from app.providers.claude_vision import ClaudeVision  # noqa: E402
from app.services import composition_service  # noqa: E402
from sqlalchemy import text  # noqa: E402


async def main_async(args: argparse.Namespace) -> int:
    env = base.load_env(args.env)
    key = env.get("ANTHROPIC_API_KEY", "")
    media = Path(args.media_dir)
    images = sorted((media / "images").glob("*.png"), key=lambda p: p.stat().st_mtime)
    if not args.live:
        print(f"dry run: would check {images[-1].name if images else '(no image)'}")
        return 0
    if not key or not images:
        print("ERROR: ANTHROPIC_API_KEY missing or no image found", file=sys.stderr)
        return 1
    cfg = Settings(_env_file=None, media_dir=str(media))
    client = anthropic.AsyncAnthropic(api_key=key)
    clues = [c.strip() for c in args.clues.split(",") if c.strip()]
    with session_factory()() as db:
        db.execute(text("UPDATE system_settings SET daily_budget_limit = 1, monthly_budget_limit = 1"))
        if db.get(ProviderBudget, "anthropic") is None:
            db.add(ProviderBudget(provider="anthropic", monthly_limit_usd=Decimal("0.05")))
        db.commit()
        decision, row = await composition_service.check_composition(
            db, ClaudeVision(client=client, model=cfg.claude_model), cfg,
            master_ref=f"images/{images[-1].name}", clues=clues)
        print("model:", row.model)
        for b in row.boxes:
            print(f"  box {b['clue_id']}: x {b['x0']:.3f}-{b['x1']:.3f}  y {b['y0']:.3f}-{b['y1']:.3f}")
        print("problems:", row.problems)
        print("modes:", decision.modes)
        print("reason:", decision.reason)
    return 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--env", default=".env")
    p.add_argument("--live", action="store_true")
    p.add_argument("--clues", default="fox,red umbrella")
    p.add_argument("--media-dir", default="spikes/output/media")
    return asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
