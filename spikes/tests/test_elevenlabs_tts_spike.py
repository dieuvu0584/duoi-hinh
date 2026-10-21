"""Character-cap tests against a LOCAL FAKE ElevenLabs server (no network, no spend)."""

from __future__ import annotations

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import elevenlabs_tts_spike as spike  # noqa: E402

KEY = "ELEVEN-SECRET-KEY"


class FakeEleven(BaseHTTPRequestHandler):
    calls: list[dict] = []

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json", headers=None):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.headers.get("xi-api-key") != KEY:
            return self._send(401, {"detail": "bad key"})
        return self._send(200, {"tier": "free", "character_count": 10, "character_limit": 10000})

    def do_POST(self):
        if self.headers.get("xi-api-key") != KEY:
            return self._send(401, {"detail": {"message": f"bad key {self.headers.get('xi-api-key')}"}})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        FakeEleven.calls.append({"path": self.path, "body": body})
        return self._send(200, b"ID3fake-mp3", "audio/mpeg",
                          {"x-character-count": str(len(body["text"])), "request-id": "r1"})


@pytest.fixture()
def server():
    FakeEleven.calls = []
    httpd = HTTPServer(("127.0.0.1", 0), FakeEleven)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def make_args(tmp_path, base, **kw):
    ns = dict(live=False, env=".env", models="m1", max_chars=1600, max_lines=6, format="mp3_44100_128",
              base_url=base, ledger=str(tmp_path / "l.json"), out_dir=str(tmp_path / "o"),
              report=str(tmp_path / "r.json"))
    ns.update(kw)
    return argparse.Namespace(**ns)


ENV = {"ELEVENLABS_API_KEY": KEY, "ELEVENLABS_VOICE_ID": "voice_A1"}


def test_dry_run_sends_nothing_and_reports_planned_characters(server, tmp_path):
    report = spike.run(make_args(tmp_path, server), ENV)
    assert report["mode"] == "dry_run" and FakeEleven.calls == []
    assert report["planned_chars"] == sum(len(t) for _, t in spike.LINES)
    assert KEY not in json.dumps(report)


def test_live_records_characters_before_calls_and_keeps_headers(server, tmp_path):
    report = spike.run(make_args(tmp_path, server, live=True, max_lines=2), ENV)
    assert len(FakeEleven.calls) == 2
    assert FakeEleven.calls[0]["path"].startswith("/v1/text-to-speech/voice_A1")
    assert FakeEleven.calls[0]["body"]["model_id"] == "m1"
    ledger = spike.CharLedger(tmp_path / "l.json")
    assert ledger.chars == sum(len(t) for _, t in spike.LINES[:2])
    assert report["results"][0]["headers"]["x-character-count"]
    assert KEY not in json.dumps(report)


def test_cap_is_enforced_per_request_across_models(server, tmp_path):
    cap = len(spike.LINES[0][1]) + 5
    report = spike.run(make_args(tmp_path, server, live=True, models="m1,m2", max_chars=cap), ENV)
    assert len(FakeEleven.calls) == 1  # only the first line fits under the cap
    assert any(r.get("skipped") == "char_cap" for r in report["results"])
    assert spike.CharLedger(tmp_path / "l.json").chars <= cap


def test_ledger_survives_restarts(server, tmp_path):
    first = make_args(tmp_path, server, live=True, max_chars=len(spike.LINES[0][1]) + 5)
    spike.run(first, ENV)
    calls_after_first = len(FakeEleven.calls)
    spike.run(first, ENV)
    assert len(FakeEleven.calls) == calls_after_first


def test_http_errors_are_recorded_and_redacted_but_still_counted(server, tmp_path):
    bad = dict(ENV, ELEVENLABS_API_KEY="wrong")
    report = spike.run(make_args(tmp_path, server, live=True, max_lines=1), bad)
    assert report["results"][0]["http"] == 401
    assert "wrong" not in json.dumps(report)
    assert spike.CharLedger(tmp_path / "l.json").chars > 0  # recorded before the request
