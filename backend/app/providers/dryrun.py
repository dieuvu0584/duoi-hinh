"""DRY-RUN Reels provider: never touches the network (docs/operations.md section 7).

Used when NHD_PUBLISH_MODE=dry_run. It is clearly labelled and every result says so. It exists so
the whole pipeline, gates and state machine can be exercised without publishing anything.
"""

import itertools
import logging
from collections.abc import Callable
from pathlib import Path

from app.providers.contracts import PreparedReel, PublishedReel, ReconcileResult

log = logging.getLogger(__name__)


class DryRunReels:
    label = "DRY-RUN (no network, nothing is published)"

    def __init__(self) -> None:
        self._ids = itertools.count(1)
        self._state: dict[str, str] = {}

    async def prepare(self, *, video_path: Path, description: str, is_ai_generated: bool,
                      intent_key: str, on_video_id: Callable[[str], None]) -> PreparedReel:
        video_id = f"dryrun-{next(self._ids)}"
        on_video_id(video_id)
        self._state[video_id] = "draft"
        log.warning("%s prepare %s", self.label, video_id)
        return PreparedReel(video_id=video_id, status="ready", copyright_clear=True)

    async def publish(self, *, video_id: str, intent_key: str) -> PublishedReel:
        self._state[video_id] = "published"
        log.warning("%s publish %s", self.label, video_id)
        return PublishedReel(video_id=video_id, permalink=f"/dry-run/{video_id}/")

    async def delete(self, *, video_id: str) -> None:
        self._state[video_id] = "deleted"

    async def reconcile(self, *, video_id: str) -> ReconcileResult:
        state = self._state.get(video_id)
        if state == "published":
            return ReconcileResult("published", f"/dry-run/{video_id}/")
        if state == "draft":
            return ReconcileResult("draft_ready")
        return ReconcileResult("missing")
