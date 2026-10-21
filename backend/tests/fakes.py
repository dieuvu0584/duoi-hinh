"""A controllable fake Reels provider for tests (never used outside tests)."""

import itertools
from collections.abc import Callable
from pathlib import Path

from app.providers.contracts import PreparedReel, PublishedReel, ReconcileResult
from app.providers.errors import ProviderPermanent, ProviderTimeout


class FakeReels:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.drafts: dict[str, str] = {}  # video_id -> draft | published | deleted
        self._ids = itertools.count(1)
        self.prepare_mode = "ok"      # ok | expired | copyright | permanent | timeout
        self.publish_mode = "ok"      # ok | timeout_applied | timeout_not_applied | permanent
        self.delete_mode = "ok"       # ok | fail
        self.reconcile_override: str | None = None
        self.seen_in_db_before_upload: str | None = None
        self.db_probe: Callable[[str], str | None] | None = None

    def count(self, name: str) -> int:
        return sum(1 for c in self.calls if c[0] == name)

    async def prepare(self, *, video_path: Path, description: str, is_ai_generated: bool,
                      intent_key: str, on_video_id: Callable[[str], None]) -> PreparedReel:
        vid = f"v{next(self._ids)}"
        self.calls.append(("prepare", vid))
        on_video_id(vid)  # the real adapter calls this right after START, before the upload
        if self.db_probe:
            self.seen_in_db_before_upload = self.db_probe(vid)
        if self.prepare_mode == "expired":
            from app.providers.errors import ProviderExpired
            raise ProviderExpired("expired")
        if self.prepare_mode == "permanent":
            raise ProviderPermanent("1363128", "unsupported duration")
        if self.prepare_mode == "timeout":
            raise ProviderTimeout("upload timed out")
        self.drafts[vid] = "draft"
        return PreparedReel(video_id=vid, status="ready",
                            copyright_clear=self.prepare_mode != "copyright")

    async def publish(self, *, video_id: str, intent_key: str) -> PublishedReel:
        self.calls.append(("publish", video_id))
        if self.publish_mode == "permanent":
            raise ProviderPermanent("200", "permissions")
        if self.publish_mode == "timeout_applied":
            self.drafts[video_id] = "published"
            raise ProviderTimeout("no response")
        if self.publish_mode == "timeout_not_applied":
            raise ProviderTimeout("no response")
        self.drafts[video_id] = "published"
        return PublishedReel(video_id=video_id, permalink=f"/reel/{video_id}/")

    async def delete(self, *, video_id: str) -> None:
        self.calls.append(("delete", video_id))
        if self.delete_mode == "fail":
            raise ProviderPermanent("100", "cannot delete")
        self.drafts[video_id] = "deleted"

    async def reconcile(self, *, video_id: str) -> ReconcileResult:
        self.calls.append(("reconcile", video_id))
        if self.reconcile_override:
            return ReconcileResult(self.reconcile_override)  # type: ignore[arg-type]
        state = self.drafts.get(video_id)
        if state == "published":
            return ReconcileResult("published", f"/reel/{video_id}/")
        if state == "draft":
            return ReconcileResult("draft_ready")
        return ReconcileResult("missing")
