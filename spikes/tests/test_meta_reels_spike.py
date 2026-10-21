"""Tests the spike's request flow against a LOCAL FAKE of the Meta API.

This verifies our script's logic only (order, headers, redaction, safety guards). It says nothing
about how the real Meta API behaves; that is what the live Package 0 run is for.
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import meta_reels_spike as spike  # noqa: E402

TOKEN = "SECRET-TOKEN-123"
PAGE = "1234567890"


class FakeMeta(BaseHTTPRequestHandler):
    calls: list[dict] = []
    status_polls = 0
    fail_start = False
    never_ready = False

    def log_message(self, *args):  # silence
        pass

    def _send(self, code: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        path = urlparse(self.path).path
        if path.endswith("/video_reels"):
            form = {k: v[0] for k, v in parse_qs(body.decode()).items()}
            FakeMeta.calls.append({"op": form["upload_phase"], "form": form})
            if form["upload_phase"] == "start":
                if FakeMeta.fail_start:
                    return self._send(400, {"error": {"message": f"bad token {TOKEN}"}})
                port = self.server.server_address[1]
                return self._send(200, {"video_id": "V1",
                                        "upload_url": f"http://127.0.0.1:{port}/video-upload/V1"})
            return self._send(200, {"success": True})
        if path.startswith("/video-upload/"):
            FakeMeta.calls.append({"op": "upload", "headers": dict(self.headers), "size": len(body)})
            return self._send(200, {"success": True})
        return self._send(404, {})

    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        fields = q.get("fields", [""])[0]
        FakeMeta.calls.append({"op": f"get:{fields}"})
        if fields == "status":
            FakeMeta.status_polls += 1
            state = "uploading" if FakeMeta.status_polls == 1 or FakeMeta.never_ready else "ready"
            return self._send(200, {"status": {"video_status": state,
                                               "publishing_phase": {"publish_status": "draft"}}})
        return self._send(200, {"copyright_check_information": {"status": {"status": "complete"},
                                                                 "matches_found": False}})


@pytest.fixture()
def server():
    FakeMeta.calls = []
    FakeMeta.status_polls = 0
    FakeMeta.fail_start = False
    httpd = HTTPServer(("127.0.0.1", 0), FakeMeta)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture()
def video(tmp_path):
    path = tmp_path / "reel.mp4"
    path.write_bytes(b"\x00" * 2048)
    return path


def env(**extra):
    base = {"META_PAGE_ID": PAGE, "META_ACCESS_TOKEN": TOKEN, "META_API_VERSION": "v26.0"}
    base.update(extra)
    return base


def args(video, base, **kw):
    argv = ["--video", str(video), "--base-url", base, "--interval", "0.01", "--timeout", "2",
            "--ready-wait", "2"]
    for k, v in kw.items():
        argv += [f"--{k.replace('_', '-')}"] + ([] if v is True else [str(v)])
    return spike.parse_args(argv)


def test_dry_run_sends_nothing(server, video):
    report = spike.run(args(video, server), env())
    assert report["mode"] == "dry_run"
    assert FakeMeta.calls == []
    assert TOKEN not in json.dumps(report)


def test_live_requires_matching_page_confirmation(server, video):
    with pytest.raises(spike.SpikeError, match="confirm-page-id"):
        spike.run(args(video, server, live=True), env())
    with pytest.raises(spike.SpikeError, match="confirm-page-id"):
        spike.run(args(video, server, live=True, confirm_page_id="999"), env())
    assert FakeMeta.calls == []


def test_live_requires_credentials(server, video):
    with pytest.raises(spike.SpikeError, match="missing"):
        spike.run(args(video, server, live=True, confirm_page_id=PAGE), {"META_PAGE_ID": PAGE})


def test_live_flow_order_state_and_redaction(server, video):
    report = spike.run(args(video, server, live=True, confirm_page_id=PAGE), env())
    ops = [c["op"] for c in FakeMeta.calls]
    assert ops == ["start", "upload", "get:status", "get:status",
                   "get:copyright_check_information", "finish", "get:status", "get:status"]
    upload = next(c for c in FakeMeta.calls if c["op"] == "upload")
    assert upload["headers"]["offset"] == "0"
    assert upload["headers"]["file_size"] == "2048" == str(upload["size"])
    assert upload["headers"]["Authorization"] == f"OAuth {TOKEN}"
    finish = next(c for c in FakeMeta.calls if c["op"] == "finish")
    assert finish["form"]["video_state"] == "DRAFT"  # safe default
    assert finish["form"]["video_id"] == "V1"
    assert finish["form"]["is_ai_generated"] == "true"
    assert report["video_id"] == "V1"
    assert TOKEN not in json.dumps(report)


def test_published_state_is_explicit(server, video):
    spike.run(args(video, server, live=True, confirm_page_id=PAGE, state="PUBLISHED"), env())
    finish = next(c for c in FakeMeta.calls if c["op"] == "finish")
    assert finish["form"]["video_state"] == "PUBLISHED"


def test_start_failure_stops_before_upload_and_error_is_redacted(server, video):
    FakeMeta.fail_start = True
    with pytest.raises(spike.SpikeError) as exc:
        spike.run(args(video, server, live=True, confirm_page_id=PAGE), env())
    assert [c["op"] for c in FakeMeta.calls] == ["start"]
    assert TOKEN not in str(exc.value)


def test_connection_error_message_is_redacted(video):
    a = args(video, "http://127.0.0.1:9", live=True, confirm_page_id=PAGE)
    with pytest.raises(spike.SpikeError) as exc:
        spike.run(a, env())
    assert TOKEN not in str(exc.value)


def test_api_version_defaults_when_not_set(server, video):
    report = spike.run(args(video, server), {"META_PAGE_ID": PAGE, "META_ACCESS_TOKEN": TOKEN})
    assert report["mode"] == "dry_run"
    assert any("/v26.0/" in step for step in report["plan"])


def test_finish_anyway_calls_finish_even_when_never_ready(server, video):
    FakeMeta.never_ready = True
    try:
        with pytest.raises(spike.SpikeError, match="ready") as exc:
            spike.run(args(video, server, live=True, confirm_page_id=PAGE), env())
        assert [c["op"] for c in FakeMeta.calls].count("finish") == 0
        assert any(s["step"] == "UPLOAD" for s in exc.value.steps)  # partial steps are kept
        FakeMeta.calls.clear()
        report = spike.run(args(video, server, live=True, confirm_page_id=PAGE,
                                finish_anyway=True), env())
        assert [c["op"] for c in FakeMeta.calls].count("finish") == 1
        assert report["status_before_finish"] == "uploading"
    finally:
        FakeMeta.never_ready = False


def test_load_env_parses_without_exposing(tmp_path):
    p = tmp_path / ".env"
    p.write_text('# c\nMETA_PAGE_ID="42"\nMETA_ACCESS_TOKEN=abc\nEMPTY=\n', encoding="utf-8")
    values = spike.load_env(str(p))
    assert values["META_PAGE_ID"] == "42" and values["META_ACCESS_TOKEN"] == "abc"
    assert spike.load_env(str(tmp_path / "missing")) == {}
