"""Live integration check of the PRODUCTION adapter (app.providers.meta_reels) on the TEST Page.

One full cycle: prepare (draft) -> reconcile -> publish (public!) -> reconcile -> delete.
The video is ALWAYS deleted at the end. Needs --live and --confirm-page-id == META_PAGE_ID.
The token is read from .env and never printed.

    python spikes/meta_adapter_live_check.py --video spikes/output/reel.mp4 --live --confirm-page-id <ID>
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

import meta_reels_spike as base  # reuse load_env

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.providers.errors import ProviderError  # noqa: E402
from app.providers.meta_reels import MetaReelsProvider  # noqa: E402


async def main_async(args: argparse.Namespace) -> int:
    env = base.load_env(args.env)
    page, token = env.get("META_PAGE_ID", ""), env.get("META_ACCESS_TOKEN", "")
    if not args.live:
        print("dry run: would run prepare -> reconcile -> publish -> reconcile -> delete")
        return 0
    if not page or not token or args.confirm_page_id != page:
        print("ERROR: need credentials and --confirm-page-id == META_PAGE_ID", file=sys.stderr)
        return 1
    provider = MetaReelsProvider(page_id=page, access_token=token,
                                 api_version=env.get("META_API_VERSION") or "v26.0",
                                 poll_interval=4.0)
    t0 = time.monotonic()
    ids: list[str] = []

    def log(msg: str) -> None:
        print(f"  +{time.monotonic() - t0:6.1f}s {msg}", flush=True)

    try:
        prepared = await provider.prepare(video_path=Path(args.video),
                                          description="Adapter check #test", is_ai_generated=True,
                                          intent_key="live-check", on_video_id=ids.append)
        log(f"prepared video_id_saved_first={ids[0] == prepared.video_id} status={prepared.status} "
            f"copyright_clear={prepared.copyright_clear}")
        log(f"reconcile (draft)    -> {(await provider.reconcile(video_id=prepared.video_id)).state}")
        published = await provider.publish(video_id=prepared.video_id, intent_key="live-check")
        log(f"published            -> permalink={published.permalink}")
        log(f"reconcile (public)   -> {(await provider.reconcile(video_id=prepared.video_id)).state}")
        return 0
    except ProviderError as exc:
        print(f"ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        for vid in ids:
            try:
                await provider.delete(video_id=vid)
                log(f"deleted {vid}")
                log(f"reconcile (deleted)  -> {(await provider.reconcile(video_id=vid)).state}")
            except ProviderError as exc:
                print(f"CLEANUP FAILED for {vid}: {type(exc).__name__}: {exc}", file=sys.stderr)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--video", required=True)
    p.add_argument("--env", default=".env")
    p.add_argument("--live", action="store_true")
    p.add_argument("--confirm-page-id", default="")
    return asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
