"""Live check of the PRODUCTION narration path (normaliser -> cache -> budget -> ElevenLabs -> ledger).

Uses the database in NHD_DATABASE_URL (point it at the throw-away TEST database, migrations applied)
and the real ElevenLabs account from .env. One short line, then the same line again to prove the
cache hit costs nothing. The key is never printed.

    set NHD_DATABASE_URL=postgresql+psycopg://nhd:nhd_test@127.0.0.1:5433/nhd_test
    python spikes/narration_live_check.py --live
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from decimal import Decimal
from pathlib import Path

import meta_reels_spike as base  # reuse load_env

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from sqlalchemy import select, text  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.db.models import CostLedger, ProviderBudget  # noqa: E402
from app.db.session import session_factory  # noqa: E402
from app.providers.elevenlabs_tts import ElevenLabsSpeech  # noqa: E402
from app.services import narration_service as ns  # noqa: E402

LINE = "Sáng thứ Hai, 25/10, chiếc đồng hồ báo thức reo lúc 6h30, còn con mèo thì ngủ tiếp."


async def main_async(args: argparse.Namespace) -> int:
    env = base.load_env(args.env)
    key, voice = env.get("ELEVENLABS_API_KEY", ""), env.get("ELEVENLABS_VOICE_ID", "")
    model = env.get("ELEVENLABS_MODEL_ID") or "eleven_v3"
    if not args.live:
        print(f"dry run: would synthesise {len(LINE)} characters with {model}")
        return 0
    if not key or not voice:
        print("ERROR: ELEVENLABS_API_KEY / ELEVENLABS_VOICE_ID missing in .env", file=sys.stderr)
        return 1
    cfg = Settings(_env_file=None, media_dir=args.media_dir)
    provider = ElevenLabsSpeech(api_key=key)
    with session_factory()() as db:
        db.execute(text("UPDATE system_settings SET daily_budget_limit = 1, monthly_budget_limit = 1"))
        if db.get(ProviderBudget, "elevenlabs") is None:
            db.add(ProviderBudget(provider="elevenlabs", monthly_limit_usd=Decimal("1")))
        db.commit()
        first = await ns.get_or_create_narration(db, provider, cfg, text=LINE, voice_id=voice,
                                                 model_id=model)
        print(f"normalised: {first.normalized_text}")
        print(f"asset: {first.storage_ref}  duration_s={first.duration_s}  chars={first.characters} "
              f"credits_charged={first.credits_charged}")
        again = await ns.get_or_create_narration(db, provider, cfg, text=LINE, voice_id=voice,
                                                 model_id=model)
        entries = db.scalars(select(CostLedger).where(CostLedger.provider == "elevenlabs")).all()
        print(f"second call is a cache hit: {again.id == first.id}; ledger rows: {len(entries)}; "
              f"settled usd={[str(e.actual_usd) for e in entries]}")
    return 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # Vietnamese text on a cp1252 Windows console
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--env", default=".env")
    p.add_argument("--live", action="store_true")
    p.add_argument("--media-dir", default="spikes/output/media")
    return asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
