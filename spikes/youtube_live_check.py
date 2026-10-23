"""Live check of the YouTube provider with the REAL channel: private upload, status, delete.

Nothing is ever made public. Run from backend/ (credentials come from the normal settings):

    python ../spikes/youtube_live_check.py [--promote]

`--promote` additionally flips the test video to public, reads it back and deletes it at once
(only run it when the channel owner agrees to a very short public moment).
"""

import argparse
import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path

from app.core.config import settings
from app.providers.errors import ProviderError
from app.providers.factory import get_youtube_provider
from app.providers.youtube_videos import YouTubeProvider


async def main(promote: bool) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    live = settings.model_copy(update={"publish_mode": "live"})
    provider = get_youtube_provider(live)
    assert isinstance(provider, YouTubeProvider)
    print("token valid:", await provider.check_token())

    with tempfile.TemporaryDirectory() as tmp:
        video = Path(tmp) / "nhd_live_check.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30:duration=4",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-c:v", "libx264",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video)],
            check=True, capture_output=True)
        ids: list[str] = []
        try:
            result = await provider.prepare(
                video_path=video, is_ai_generated=True, intent_key="live-check",
                description="Kiem tra ket noi (se bi xoa ngay)\n\nVideo thu cua cong cu, rieng tu.",
                on_video_id=ids.append)
            print("prepared:", result)
            info = await provider._video(result.video_id, "status,processingDetails")
            status = (info or {}).get("status", {})
            print("privacy:", status.get("privacyStatus"), "| synthetic:",
                  status.get("containsSyntheticMedia"), "| kids:",
                  status.get("selfDeclaredMadeForKids"), "| upload:", status.get("uploadStatus"))
            if promote:
                try:
                    published = await provider.publish(video_id=result.video_id, intent_key="x")
                    print("PUBLIC:", published.permalink)
                except ProviderError as exc:
                    print("publish refused:", type(exc).__name__, exc)
                print("reconcile:", await provider.reconcile(video_id=result.video_id))
        finally:
            for video_id in ids:
                await provider.delete(video_id=video_id)
                print("deleted", video_id, "->", await provider.reconcile(video_id=video_id))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--promote", action="store_true")
    asyncio.run(main(ap.parse_args().promote))
