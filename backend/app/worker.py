"""Background worker: runs the scheduler tick in a loop.

    python -m app.worker            # loop until Ctrl+C / SIGTERM
    python -m app.worker --once     # a single tick, then exit

The provider comes from `get_reels_provider(settings)`: dry-run unless NHD_PUBLISH_MODE=live.
"""

import argparse
import asyncio
import logging
import signal

from app.core.config import settings
from app.core.logging import configure_logging
from app.db.session import session_factory
from app.providers.factory import get_reels_provider
from app.providers.pipeline_factory import build_pipeline_deps, missing_pipeline_keys
from app.services.pipeline_runner import run_pending
from app.services.scheduler import tick
from app.services.settings_service import effective_page_id, get_settings_row

log = logging.getLogger("app.worker")


async def pipeline_loop(*, once: bool, interval: float, stop: asyncio.Event) -> None:
    """Content generation runs beside publishing so a long render never delays a publish."""
    missing = missing_pipeline_keys(settings)
    if missing:
        log.warning("content pipeline disabled, missing: %s", ", ".join(missing))
        return
    deps = build_pipeline_deps(settings)
    while not stop.is_set():
        try:
            with session_factory()() as session:
                notes = await run_pending(session, deps)
        except Exception:  # noqa: BLE001 - the loop must survive (db restart, etc.)
            log.exception("pipeline loop error")
            notes = []
        if notes:
            log.info("pipeline: %s", notes)
        if once:
            return
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass


async def run(*, once: bool, interval: float) -> None:
    provider = None
    provider_page: str | None = None
    log.warning("worker started; publish_mode=%s", settings.publish_mode)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows event loops
            pass
    pipeline = asyncio.create_task(pipeline_loop(once=once, interval=interval, stop=stop))
    try:
        await publish_loop(once=once, interval=interval, stop=stop, provider=provider,
                           provider_page=provider_page)
    finally:
        if not once:
            stop.set()
        await pipeline


async def publish_loop(*, once, interval, stop, provider, provider_page) -> None:
    while not stop.is_set():
        with session_factory()() as session:
            # The Page is an admin setting: rebuild the provider when the owner changes it.
            page = effective_page_id(get_settings_row(session), settings)
            if provider is None or page != provider_page:
                provider, provider_page = get_reels_provider(settings, page), page
                log.warning("publishing provider=%s page=%s", type(provider).__name__, page)
            report = await tick(session, provider, settings)
        log.info("tick: %s", report)
        if once:
            return
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.worker")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=30.0)
    args = parser.parse_args()
    settings.validate_for_runtime()
    configure_logging(settings.log_level)
    asyncio.run(run(once=args.once, interval=args.interval))


if __name__ == "__main__":
    main()
