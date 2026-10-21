"""Package 0 spike: push a hand-made mp4 through Meta's Reels API on a TEST Page.

Throwaway script (not production code). It follows docs/publishing-meta.md:
START -> UPLOAD -> poll status -> FINISH, and records timings and raw (redacted) responses so
the VERIFY items can be answered with evidence.

Safe by default: without --live nothing is sent. Live runs need --confirm-page-id equal to
META_PAGE_ID in .env, and default to video_state=DRAFT (not public).

    python spikes/meta_reels_spike.py --video spikes/output/reel.mp4            # dry run
    python spikes/meta_reels_spike.py --video reel.mp4 --live --confirm-page-id <PAGE_ID>
    ... --state PUBLISHED   # only when you really want it public on the test Page

Requires: httpx. Never prints or stores the access token.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

DEFAULT_BASE = "https://graph.facebook.com"
TERMINAL_BAD = {"upload_failed", "error", "expired"}


class SpikeError(Exception):
    pass


def load_env(path: str = ".env") -> dict[str, str]:
    values: dict[str, str] = {}
    p = Path(path)
    if not p.exists():
        return values
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def redact(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


class Recorder:
    def __init__(self, secrets: list[str]) -> None:
        self.secrets = secrets
        self.steps: list[dict[str, Any]] = []

    def add(self, name: str, started: float, response: httpx.Response | None, note: str = "") -> None:
        body = ""
        status = None
        if response is not None:
            status = response.status_code
            body = redact(response.text[:2000], self.secrets)
        self.steps.append(
            {
                "step": name,
                "elapsed_s": round(time.monotonic() - started, 3),
                "http_status": status,
                "body": body,
                "note": note,
                "at": datetime.now(UTC).isoformat(),
            }
        )


def _call(fn, *args, secrets: list[str], **kwargs) -> httpx.Response:
    try:
        return fn(*args, **kwargs)
    except httpx.HTTPError as exc:  # message may contain a URL; redact before surfacing
        raise SpikeError(redact(f"{type(exc).__name__}: {exc}", secrets)) from None


def start(client: httpx.Client, graph: str, page_id: str, token: str, rec: Recorder) -> tuple[str, str]:
    t0 = time.monotonic()
    resp = _call(
        client.post,
        f"{graph}/{page_id}/video_reels",
        data={"upload_phase": "start", "access_token": token},
        secrets=rec.secrets,
    )
    rec.add("START", t0, resp)
    data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    if resp.status_code != 200 or "video_id" not in data or "upload_url" not in data:
        raise SpikeError(f"START failed: HTTP {resp.status_code}")
    return str(data["video_id"]), str(data["upload_url"])


def upload(client: httpx.Client, upload_url: str, token: str, video: Path, rec: Recorder) -> None:
    t0 = time.monotonic()
    payload = video.read_bytes()
    resp = _call(
        client.post,
        upload_url,
        headers={
            "Authorization": f"OAuth {token}",
            "offset": "0",
            "file_size": str(len(payload)),
            "Content-Type": "application/octet-stream",
        },
        content=payload,
        timeout=600,
        secrets=rec.secrets,
    )
    rec.add("UPLOAD", t0, resp, note=f"bytes={len(payload)}")
    if resp.status_code != 200:
        raise SpikeError(f"UPLOAD failed: HTTP {resp.status_code}")


def poll_ready(
    client: httpx.Client, graph: str, video_id: str, token: str, rec: Recorder,
    timeout_s: float, interval_s: float,
) -> str:
    """Poll until `ready`; record every observed status so processing time is measurable."""
    deadline = time.monotonic() + timeout_s
    last = "unknown"
    while time.monotonic() < deadline:
        t0 = time.monotonic()
        resp = _call(
            client.get, f"{graph}/{video_id}",
            params={"fields": "status", "access_token": token}, secrets=rec.secrets,
        )
        status = (resp.json().get("status") or {}) if resp.status_code == 200 else {}
        last = str(status.get("video_status", f"http_{resp.status_code}"))
        rec.add("POLL", t0, resp, note=f"video_status={last}")
        if last == "ready":
            return last
        if last in TERMINAL_BAD:
            raise SpikeError(f"video reached {last}")
        time.sleep(interval_s)
    raise SpikeError(f"timeout waiting for ready (last={last})")


def copyright_check(
    client: httpx.Client, graph: str, video_id: str, token: str, rec: Recorder,
    timeout_s: float, interval_s: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    info: dict[str, Any] = {}
    while time.monotonic() < deadline:
        t0 = time.monotonic()
        resp = _call(
            client.get, f"{graph}/{video_id}",
            params={"fields": "copyright_check_information", "access_token": token},
            secrets=rec.secrets,
        )
        info = resp.json().get("copyright_check_information", {}) if resp.status_code == 200 else {}
        raw_state = info.get("status")
        state = raw_state.get("status") if isinstance(raw_state, dict) else raw_state
        rec.add("COPYRIGHT", t0, resp, note=f"status={state}")
        if state == "complete" or resp.status_code != 200:
            break
        time.sleep(interval_s)
    return info


def finish(
    client: httpx.Client, graph: str, page_id: str, token: str, video_id: str,
    state: str, description: str, ai_generated: bool, rec: Recorder,
) -> dict[str, Any]:
    t0 = time.monotonic()
    resp = _call(
        client.post,
        f"{graph}/{page_id}/video_reels",
        data={
            "upload_phase": "finish",
            "video_id": video_id,
            "video_state": state,
            "description": description,
            "is_ai_generated": "true" if ai_generated else "false",
            "access_token": token,
        },
        secrets=rec.secrets,
    )
    rec.add("FINISH", t0, resp)
    if resp.status_code != 200:
        raise SpikeError(f"FINISH failed: HTTP {resp.status_code}")
    return resp.json()


def publishing_phase(
    client: httpx.Client, graph: str, video_id: str, token: str, rec: Recorder
) -> dict[str, Any]:
    t0 = time.monotonic()
    resp = _call(
        client.get, f"{graph}/{video_id}",
        params={"fields": "status", "access_token": token}, secrets=rec.secrets,
    )
    rec.add("POST_FINISH_STATUS", t0, resp)
    return resp.json().get("status", {}) if resp.status_code == 200 else {}


def plan(args: argparse.Namespace, graph: str, page_id: str) -> list[str]:
    return [
        f"POST {graph}/{page_id}/video_reels  upload_phase=start",
        "POST <upload_url from START>  headers: Authorization: OAuth ***, offset: 0, file_size: <bytes>",
        f"GET  {graph}/<video_id>?fields=status  (poll until ready)",
        f"GET  {graph}/<video_id>?fields=copyright_check_information",
        f"POST {graph}/{page_id}/video_reels  upload_phase=finish video_state={args.state}",
    ]


def run(args: argparse.Namespace, env: dict[str, str]) -> dict[str, Any]:
    token = env.get("META_ACCESS_TOKEN", "")
    page_id = env.get("META_PAGE_ID", "")
    version = env.get("META_API_VERSION", "")
    graph = f"{args.base_url.rstrip('/')}/{version}" if version else args.base_url.rstrip("/")
    video = Path(args.video)
    if not video.is_file():
        raise SpikeError(f"video not found: {video}")

    if not args.live:
        return {"mode": "dry_run", "video": str(video), "bytes": video.stat().st_size,
                "plan": plan(args, graph, page_id or "<PAGE_ID>"),
                "env_present": {k: bool(env.get(k)) for k in
                                ("META_APP_ID", "META_PAGE_ID", "META_ACCESS_TOKEN", "META_API_VERSION")}}

    missing = [k for k in ("META_PAGE_ID", "META_ACCESS_TOKEN", "META_API_VERSION") if not env.get(k)]
    if missing:
        raise SpikeError(f"missing in .env: {', '.join(missing)}")
    if args.confirm_page_id != page_id:
        raise SpikeError("--confirm-page-id must equal META_PAGE_ID (guards against the wrong Page)")

    rec = Recorder([token, env.get("META_APP_SECRET", "")])
    with httpx.Client(timeout=60) as client:
        video_id, upload_url = start(client, graph, page_id, token, rec)
        upload(client, upload_url, token, video, rec)
        poll_ready(client, graph, video_id, token, rec, args.timeout, args.interval)
        cc = copyright_check(client, graph, video_id, token, rec, args.timeout, args.interval)
        result = finish(client, graph, page_id, token, video_id, args.state, args.description,
                        args.ai_generated, rec)
        phase = publishing_phase(client, graph, video_id, token, rec)
    return {"mode": "live", "video_id": video_id, "state": args.state, "finish": result,
            "copyright": cc, "publishing_phase": phase, "steps": rec.steps}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--video", required=True)
    p.add_argument("--env", default=".env")
    p.add_argument("--live", action="store_true", help="actually call Meta (default: dry run)")
    p.add_argument("--confirm-page-id", default="")
    p.add_argument("--state", choices=["DRAFT", "PUBLISHED"], default="DRAFT")
    p.add_argument("--description", default="Package 0 test - Nhin Hinh Doan Chuyen #test")
    p.add_argument("--ai-generated", dest="ai_generated", action="store_true", default=True)
    p.add_argument("--base-url", default=DEFAULT_BASE, help="override for tests only")
    p.add_argument("--timeout", type=float, default=600)
    p.add_argument("--interval", type=float, default=5)
    p.add_argument("--out", default="spikes/output/meta_report.json")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = run(args, load_env(args.env))
    except SpikeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "steps"}, indent=2, ensure_ascii=False))
    print(f"report written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
