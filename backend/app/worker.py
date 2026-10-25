"""Background worker: runs the scheduler tick in a loop.

    python -m app.worker            # loop until Ctrl+C / SIGTERM
    python -m app.worker --once     # a single tick, then exit

Providers (Facebook, YouTube) come from `get_providers(settings)`: dry-run unless
NHD_PUBLISH_MODE=live.
"""

import argparse
import asyncio
import logging
import signal

from sqlalchemy import select

from app.core.config import settings
from app.core.logging import configure_logging
from app.db.models import ScanRun
from app.db.session import session_factory
from app.providers.factory import get_providers
from app.providers.pipeline_factory import build_llm, build_pipeline_deps, missing_pipeline_keys
from app.services import idea_service, token_health_service
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


async def idea_loop(*, once: bool, stop: asyncio.Event) -> None:
    """Idea scans: runs queued scans (manual or at the owner's chosen times of day)."""
    if not settings.anthropic_api_key:
        log.warning("idea scan disabled, missing: ANTHROPIC_API_KEY")
        return
    llm = build_llm(settings)
    while not stop.is_set():
        try:
            with session_factory()() as session:
                row = get_settings_row(session)
                if row.scan_enabled and not row.generation_paused:
                    idea_service.queue_due_scans(session, list(row.scan_times or []))
                session.rollback()
                queued = list(session.scalars(
                    select(ScanRun.id).where(ScanRun.status == "queued")
                    .order_by(ScanRun.requested_at)))
                for run_id in queued:
                    run = await idea_service.run_scan(session, llm, settings, run_id)
                    log.info("scan %s: %s candidates=%s note=%s", run.trigger, run.status,
                             run.candidates, run.note)
                await idea_service.auto_create(session, llm, settings)
                idea_service.expire_and_purge(session)
        except Exception:  # noqa: BLE001 - the loop must survive (db restart, feed oddities)
            log.exception("idea loop error")
        if once:
            return
        try:
            await asyncio.wait_for(stop.wait(), timeout=60)
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
    ideas = asyncio.create_task(idea_loop(once=once, stop=stop))
    try:
        await publish_loop(once=once, interval=interval, stop=stop, provider=provider,
                           provider_page=provider_page)
    finally:
        if not once:
            stop.set()
        await asyncio.gather(pipeline, ideas)


async def publish_loop(*, once, interval, stop, provider, provider_page) -> None:
    while not stop.is_set():
        with session_factory()() as session:
            # The Page is an admin setting: rebuild the provider when the owner changes it.
            page = effective_page_id(get_settings_row(session), settings)
            rebuilt = provider is None or page != provider_page
            if rebuilt:
                provider, provider_page = get_providers(settings, page), page
                log.warning("publishing providers=%s page=%s",
                            {k: type(v).__name__ for k, v in provider.items()}, page)
            # The gate refuses to publish without a recent, valid token check.
            # A freshly built provider set (start-up, Page changed, dry-run -> live) is always
            # checked at once: an older result may belong to a different provider.
            await token_health_service.refresh_if_due(session, provider, force=rebuilt)
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
