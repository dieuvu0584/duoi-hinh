"""USD-cap tests against a LOCAL FAKE fal queue (no network, no spend)."""

from __future__ import annotations

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fal_image_spike as spike  # noqa: E402

KEY = "FAL-SECRET-ID:abcdef0123456789"


class FakeFal(BaseHTTPRequestHandler):
    submits = 0
    fail = False
    out_size = (1080, 1920)

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.headers.get("Authorization") != f"Key {KEY}":
            return self._send(401, {"detail": "bad key"})
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        FakeFal.submits += 1
        port = self.server.server_address[1]
        rid = f"req{FakeFal.submits}"
        return self._send(200, {"request_id": rid, "queue_position": 0,
                                "status_url": f"http://127.0.0.1:{port}/s/{rid}",
                                "response_url": f"http://127.0.0.1:{port}/r/{rid}",
                                "cancel_url": f"http://127.0.0.1:{port}/c/{rid}"})

    def do_PUT(self):
        return self._send(200, {"status": "CANCELLATION_REQUESTED"})

    def do_GET(self):
        port = self.server.server_address[1]
        if self.path.startswith("/s/"):
            if FakeFal.fail:
                return self._send(200, {"status": "COMPLETED", "error": "boom",
                                        "error_type": "runtime_error"})
            return self._send(200, {"status": "COMPLETED"})
        if self.path.startswith("/r/"):
            w, h = FakeFal.out_size
            return self._send(200, {"images": [{"url": f"http://127.0.0.1:{port}/img.png",
                                                "width": w, "height": h}], "seed": 1})
        if self.path == "/img.png":
            return self._send(200, b"\x89PNG-fake", "image/png")
        return self._send(404, {})


@pytest.fixture()
def server():
    FakeFal.submits, FakeFal.fail, FakeFal.out_size = 0, False, (1080, 1920)
    httpd = HTTPServer(("127.0.0.1", 0), FakeFal)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def make_args(tmp_path, base, **kw):
    ns = dict(live=False, env=".env", model="fal-ai/flux-2", budget_usd=4.0, price_per_mp=None,
              max_images=4, width=1080, height=1920, queue_base=base,
              ledger=str(tmp_path / "l.json"), out_dir=str(tmp_path / "o"),
              report=str(tmp_path / "r.json"))
    ns.update(kw)
    return argparse.Namespace(**ns)


ENV = {"FAL_API_KEY": KEY}
PER_IMAGE = spike.estimate_usd(1080, 1920, 0.012)  # about $0.0249


def test_estimate_uses_megapixels_and_a_conservative_divisor():
    assert spike.estimate_usd(1000, 1000, 0.012) == pytest.approx(0.012)
    assert PER_IMAGE == pytest.approx(0.024883, abs=1e-6)


def test_dry_run_sends_nothing_and_hides_the_key(server, tmp_path):
    report = spike.run(make_args(tmp_path, server), ENV)
    assert report["mode"] == "dry_run" and FakeFal.submits == 0
    assert report["estimated_usd_per_image"] == pytest.approx(PER_IMAGE)
    assert KEY not in json.dumps(report)


def test_live_generates_settles_and_downloads(server, tmp_path):
    report = spike.run(make_args(tmp_path, server, live=True, max_images=2), ENV)
    assert FakeFal.submits == 2 and all(Path(r["file"]).exists() for r in report["results"])
    assert spike.Ledger(tmp_path / "l.json").usd == pytest.approx(2 * PER_IMAGE)
    assert KEY not in json.dumps(report)


def test_larger_real_image_is_not_clamped_to_the_estimate(server, tmp_path):
    FakeFal.out_size = (1088, 1920)  # what fal really returned for a 1080x1920 request
    spike.run(make_args(tmp_path, server, live=True, max_images=1), ENV)
    assert spike.Ledger(tmp_path / "l.json").usd == pytest.approx(
        spike.estimate_usd(1088, 1920, 0.012))
    assert spike.Ledger(tmp_path / "l.json").usd > PER_IMAGE


def test_smaller_real_image_settles_below_the_estimate(server, tmp_path):
    FakeFal.out_size = (512, 512)
    spike.run(make_args(tmp_path, server, live=True, max_images=1), ENV)
    assert spike.Ledger(tmp_path / "l.json").usd == pytest.approx(
        spike.estimate_usd(512, 512, 0.012))


def test_budget_cap_refuses_before_any_request_that_could_overspend(server, tmp_path):
    budget = PER_IMAGE * 2 + 0.001  # room for exactly two images
    report = spike.run(make_args(tmp_path, server, live=True, max_images=6, budget_usd=budget), ENV)
    assert FakeFal.submits == 2 and report["spent_usd_total"] <= budget
    assert report["results"][-1].get("skipped") == "budget"


def test_ledger_persists_across_runs(server, tmp_path):
    args = make_args(tmp_path, server, live=True, max_images=6, budget_usd=PER_IMAGE * 2 + 0.001)
    spike.run(args, ENV)
    spike.run(args, ENV)
    assert FakeFal.submits == 2  # the second run bought nothing


def test_failed_generation_keeps_the_reservation(server, tmp_path):
    FakeFal.fail = True
    report = spike.run(make_args(tmp_path, server, live=True, max_images=1), ENV)
    assert report["results"][0]["error"] == "boom"
    assert spike.Ledger(tmp_path / "l.json").usd == pytest.approx(PER_IMAGE)  # still counted


def test_unknown_model_needs_an_explicit_price(server, tmp_path):
    with pytest.raises(SystemExit, match="no known price"):
        spike.run(make_args(tmp_path, server, live=True, model="fal-ai/mystery"), ENV)
    assert FakeFal.submits == 0
    spike.run(make_args(tmp_path, server, live=True, model="fal-ai/mystery", price_per_mp=0.01,
                        max_images=1), ENV)
    assert FakeFal.submits == 1


def test_wrong_key_is_an_error_and_costs_nothing_extra(server, tmp_path):
    with pytest.raises(RuntimeError, match="401"):
        spike.run(make_args(tmp_path, server, live=True, max_images=1), {"FAL_API_KEY": "nope"})
    assert FakeFal.submits == 0
