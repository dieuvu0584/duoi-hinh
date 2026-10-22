"""Live check of the idea scan with the REAL feeds and Claude (a few cents). Scratch DB only.

Run from backend/ (see spikes/pipeline_live_check.py for the environment variables):

    python ../spikes/idea_scan_live_check.py [--propose daily_life|rebus]
"""

import argparse
import asyncio
import sys
from decimal import Decimal

from sqlalchemy import func, select, text

from app.core.config import settings
from app.db.models import CostLedger, FeedItem, FeedSource, IdeaCandidate, ProviderBudget, ScanRun
from app.db.session import session_factory
from app.providers.pipeline_factory import build_llm
from app.services import idea_service


async def main(propose_lane: str | None) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    llm = build_llm(settings)
    with session_factory()() as s:
        s.execute(text("UPDATE system_settings SET daily_budget_limit=2, monthly_budget_limit=2"))
        row = s.scalar(select(ProviderBudget).where(ProviderBudget.provider == "anthropic"))
        if row is None:
            s.add(ProviderBudget(provider="anthropic", monthly_limit_usd=Decimal("1")))
        s.commit()
        print("sources:", s.scalar(select(func.count()).select_from(FeedSource)))
        run = ScanRun(trigger="manual", status="queued")
        s.add(run)
        s.commit()
        done = await idea_service.run_scan(s, llm, settings, run.id)
        print("scan:", done.status, "new items:", done.items_new, "candidates:", done.candidates,
              "note:", done.note)
        for src in s.scalars(select(FeedSource).order_by(FeedSource.name)):
            n = s.scalar(select(func.count()).select_from(FeedItem).where(FeedItem.source_id == src.id))
            print(f"  {src.name}: {n} items, error={src.last_error}")
        cands = s.scalars(select(IdeaCandidate).order_by(IdeaCandidate.score.desc())).all()
        for c in cands:
            print(f"- [{c.score}] ({c.suggested_lane}) {c.situation}")
            print("    why:", c.why)
            print("    from:", " | ".join(x["title"] for x in c.source_items)[:200])
        if propose_lane and cands:
            pick = cands[0]
            out = await idea_service.propose(s, llm, settings, pick.id, propose_lane)
            print(f"\nProposals for the top idea as {propose_lane}:")
            for o in out.proposals:
                print(f"* {o['title']} [{o['difficulty']}]")
                print("   brief:", o["brief"])
                if o["answer"]:
                    print("   answer:", o["answer"], "| idiom:", o["idiom"], "| twist:", o["twist"])
                print("   why:", o["explanation"])
        total = s.scalar(select(func.coalesce(func.sum(CostLedger.actual_usd), 0)))
        print("spent USD:", total)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--propose", choices=["daily_life", "rebus"])
    asyncio.run(main(ap.parse_args().propose))
