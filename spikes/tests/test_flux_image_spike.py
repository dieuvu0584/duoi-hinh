"""Budget-cap tests against a LOCAL FAKE FLUX server (no network, no spend)."""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import flux_image_spike as spike  # noqa: E402

KEY = "FLUX-SECRET-KEY"


class FakeFlux(BaseHTTPRequestHandler):
    submits = 0
    cost = 1.4  # credits per image => $0.014

    def log_message(self, *a):
        pass

    def _send(self, code, body, raw=False):
        data = body if raw else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "image/png" if raw else "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.headers.get("x-key") != KEY:
            return self._send(403, {"detail": "bad key"})
        FakeFlux.submits += 1
        port = self.server.server_address[1]
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        return self._send(200, {"id": f"r{FakeFlux.submits}", "cost": FakeFlux.cost,
                                "output_mp": 2.0,
                                "polling_url": f"http://127.0.0.1:{port}/poll"})

    def do_GET(self):
        path = urlparse(self.path).path
        port = self.server.server_address[1]
        if path == "/v1/credits":
            return self._send(200, {"credits": 1000})
        if path == "/poll":
            return self._send(200, {"status": "Ready", "cost": FakeFlux.cost,
                                    "result": {"sample": f"http://127.0.0.1:{port}/img.png"}})
        if path == "/img.png":
            return self._send(200, b"\x89PNG-fake", raw=True)
        return self._send(404, {})


@pytest.fixture()
def server():
    FakeFlux.submits = 0
    FakeFlux.cost = 1.4
    httpd = HTTPServer(("127.0.0.1", 0), FakeFlux)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def args(tmp_path, base, **kw):
    argv = ["--base-url", base, "--ledger", str(tmp_path / "ledger.json"),
            "--out-dir", str(tmp_path / "img"), "--report", str(tmp_path / "r.json")]
    for k, v in kw.items():
        argv += [f"--{k.replace('_', '-')}"] + ([] if v is True else [str(v)])
    return spike.argparse.Namespace(**vars(_parse(argv)))


def _parse(argv):
    p_args = []
    import argparse

    ap = argparse.ArgumentParser()
    for name, default in [("live", False)]:
        ap.add_argument(f"--{name}", action="store_true", default=default)
    ap.add_argument("--env", default=".env")
    ap.add_argument("--model", default="flux-3-image-klein")
    ap.add_argument("--budget-usd", type=float, default=4.0)
    ap.add_argument("--est-cost-usd", type=float, default=0.05)
    ap.add_argument("--max-images", type=int, default=4)
    ap.add_argument("--aspect-ratio", default="9:16")
    ap.add_argument("--resolution", default="1.5k")
    ap.add_argument("--width", type=int, default=1088)
    ap.add_argument("--height", type=int, default=1920)
    ap.add_argument("--base-url")
    ap.add_argument("--ledger")
    ap.add_argument("--out-dir")
    ap.add_argument("--report")
    del p_args
    return ap.parse_args(argv)


def test_dry_run_makes_no_request_and_hides_the_key(server, tmp_path):
    report = spike.run(args(tmp_path, server), {"FLUX_API_KEY": KEY})
    assert report["mode"] == "dry_run" and FakeFlux.submits == 0
    assert KEY not in json.dumps(report)


def test_live_generates_records_spend_and_downloads(server, tmp_path):
    report = spike.run(args(tmp_path, server, live=True, max_images=2), {"FLUX_API_KEY": KEY})
    assert FakeFlux.submits == 2 and len(report["results"]) == 2
    assert all(Path(r["file"]).exists() for r in report["results"])
    ledger = spike.Ledger(tmp_path / "ledger.json")
    assert ledger.credits == pytest.approx(2.8) and ledger.usd == pytest.approx(0.028)
    assert KEY not in json.dumps(report)


def test_budget_cap_stops_before_overspending(server, tmp_path):
    # $0.07 budget, $0.05 pre-call estimate, real cost $0.014: image k is allowed only while
    # 0.014*(k-1) + 0.05 <= 0.07, i.e. k <= 2. The third is refused before any request.
    report = spike.run(
        args(tmp_path, server, live=True, max_images=6, budget_usd=0.07, est_cost_usd=0.05),
        {"FLUX_API_KEY": KEY},
    )
    assert FakeFlux.submits == 2 and report["spent_usd_total"] <= 0.07
    assert report["results"][-1] == {"label": report["results"][-1]["label"], "skipped": "budget"}


def test_ledger_persists_across_runs_and_blocks_a_second_run(server, tmp_path):
    first = args(tmp_path, server, live=True, max_images=6, budget_usd=0.07, est_cost_usd=0.05)
    spike.run(first, {"FLUX_API_KEY": KEY})
    submits_after_first = FakeFlux.submits
    assert submits_after_first == 2
    spike.run(first, {"FLUX_API_KEY": KEY})
    assert FakeFlux.submits == submits_after_first  # nothing more was bought


def test_spend_is_recorded_even_if_the_image_never_arrives(server, tmp_path, monkeypatch):
    monkeypatch.setattr(spike.Flux, "wait", lambda self, *a, **k: {"status": "Error"})
    report = spike.run(args(tmp_path, server, live=True, max_images=1), {"FLUX_API_KEY": KEY})
    assert report["results"][0]["status"] == "Error"
    assert spike.Ledger(tmp_path / "ledger.json").credits == pytest.approx(1.4)


def test_wrong_key_is_an_error_and_costs_nothing(server, tmp_path):
    with pytest.raises(RuntimeError, match="403"):
        spike.run(args(tmp_path, server, live=True, max_images=1), {"FLUX_API_KEY": "nope"})
    assert spike.Ledger(tmp_path / "ledger.json").credits == 0
