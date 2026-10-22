"""Live end-to-end check of the content pipeline with the REAL providers (spends a little money).

Run from backend/ against a scratch database (never the real one):

    NHD_DATABASE_URL=postgresql+psycopg://nhd:nhd_test@127.0.0.1:5433/nhd_live \
    NHD_MEDIA_DIR=<scratch dir> NHD_PROMPTS_DIR=../prompts NHD_SCHEMAS_DIR=../docs/schemas \
    python ../spikes/pipeline_live_check.py "<brief>" [--lane daily_life]

Keys come from the normal settings (.env). Per-provider caps below keep the spend small.
"""

import argparse
import asyncio
import sys
from decimal import Decimal

from sqlalchemy import func, select, text

from app.core.config import settings
from app.db.models import CostLedger, LlmCall, ProviderBudget, VideoAsset
from app.db.session import session_factory
from app.providers.pipeline_factory import build_pipeline_deps
from app.services import content_pipeline as cp
from app.services import content_service

CAPS = {"anthropic": "1.5", "fal": "0.5", "elevenlabs": "1.0"}


async def main(brief: str, lane: str, answer: str | None) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    deps = build_pipeline_deps(settings)
    with session_factory()() as s:
        s.execute(text("UPDATE system_settings SET daily_budget_limit=3, monthly_budget_limit=3"))
        for name, cap in CAPS.items():
            row = s.scalar(select(ProviderBudget).where(ProviderBudget.provider == name))
            if row is None:
                s.add(ProviderBudget(provider=name, monthly_limit_usd=Decimal(cap)))
            else:
                row.monthly_limit_usd = Decimal(cap)
        s.commit()
        item = cp.start(s, None, lane=lane, brief=brief, answer=answer)
        print("item", item.id)
        outcomes = await cp.run_until_wait(s, deps, item.id)
        for o in outcomes:
            print(f"  {o.stage:9} -> {o.status:22} {o.note} {o.details or ''}")
        s.expire_all()
        item = s.get(type(item), item.id)
        print("final status:", item.status)
        if item.current_revision_id:
            rev = content_service.current_revision(s, item)
            print("caption:", rev.caption)
            print("overlay:", rev.overlay_text)
            print("narration:", rev.narration_text)
            print("answer:", rev.answer_text)
        for v in s.scalars(select(VideoAsset)):
            print("video", v.target, v.mode, f"{v.width}x{v.height}", v.duration_s, v.storage_ref)
        print("notes:", (item.pipeline_input or {}).get("notes"))
        print("qa issues:", (item.pipeline_input or {}).get("last_qa_issues"))
        total = s.scalar(select(func.coalesce(func.sum(CostLedger.actual_usd), 0)))
        print("spent USD:", total)
        for provider, usd in s.execute(
            select(CostLedger.provider, func.sum(CostLedger.actual_usd)).group_by(
                CostLedger.provider)):
            print("  ", provider, usd)
        for c in s.scalars(select(LlmCall)):
            print("  llm", c.purpose, c.input_tokens, c.output_tokens, "valid" if c.valid else "INVALID")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("brief")
    ap.add_argument("--lane", default="daily_life")
    ap.add_argument("--answer")
    a = ap.parse_args()
    asyncio.run(main(a.brief, a.lane, a.answer))
