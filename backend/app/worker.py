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
from app.services.scheduler import tick

log = logging.getLogger("app.worker")


async def run(*, once: bool, interval: float) -> None:
    provider = get_reels_provider(settings)
    log.warning("worker started; publish_mode=%s provider=%s", settings.publish_mode,
                type(provider).__name__)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows event loops
            pass
    while not stop.is_set():
        with session_factory()() as session:
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
