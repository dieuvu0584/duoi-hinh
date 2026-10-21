"""Package 0 spike: try FLUX image generation under a HARD spend cap (decision D-18).

Facts used (docs.bfl.ml / api.bfl.ai/openapi.json, read 2026-10-22; re-check before relying):
  base https://api.bfl.ai, header `x-key`, POST /v1/<model> -> {id, polling_url, cost (credits)},
  poll polling_url until status "Ready" (image at result.sample, signed URL valid 10 min),
  1 credit = $0.01, GET /v1/credits returns the balance.

Safety: dry run by default; --live required. Spend is tracked in a local ledger file and the run
refuses to start a request that could exceed --budget-usd. Charged cost is taken from the
`cost` field of the submit response and recorded BEFORE polling, so a crash cannot hide spend.
The API key is never printed.

    python spikes/flux_image_spike.py                     # dry run: shows the plan
    python spikes/flux_image_spike.py --live --max-images 4
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
import meta_reels_spike as base  # reuse load_env and redact

DEFAULT_BASE = "https://api.bfl.ai"
USD_PER_CREDIT = 0.01
TERMINAL_BAD = {"Error", "Request Moderated", "Content Moderated", "No Card Generated",
                "Task not found"}

# (label, lane, prompt). No text inside images: overlays are added later with Pillow.
PROMPTS: list[tuple[str, str, str]] = [
    ("rebus_symbol_banh", "rebus",
     "A single round steamed rice cake wrapped in green banana leaf, centered on a plain "
     "light background, clean flat illustration style, no text, no letters"),
    ("rebus_symbol_chung", "rebus",
     "A single square Vietnamese banh chung tied with bamboo string, centered on a plain "
     "light background, clean flat illustration style, no text, no letters"),
    ("daily_cat_alarm", "daily_life",
     "A tired cartoon cat in pajamas glaring at a ringing alarm clock on a bedside table, "
     "morning light, humorous meme illustration, vertical composition, no text"),
    ("daily_rain_motorbike", "daily_life",
     "A cheerful cartoon person on a motorbike in heavy rain wearing a bright raincoat, "
     "Vietnamese street background, humorous illustration, vertical composition, no text"),
    ("news_metaphor", "news",
     "An editorial illustration of a small boat steering through rising floodwater in a city "
     "street, clearly an illustration not a photograph, vertical composition, no text"),
    ("daily_cooking_chaos", "daily_life",
     "A cartoon home cook surrounded by too many pots and steam, calm smiling face, "
     "humorous illustration, bright kitchen, vertical composition, no text"),
]


class Ledger:
    """Cumulative credits spent by this spike; survives restarts."""

    def __init__(self, path: Path) -> None:
        self.path = path
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        self.credits: float = float(data.get("credits_spent", 0.0))
        self.entries: list[dict[str, Any]] = list(data.get("entries", []))

    @property
    def usd(self) -> float:
        return round(self.credits * USD_PER_CREDIT, 4)

    def add(self, label: str, request_id: str, credits: float | None, model: str) -> None:
        self.credits += float(credits or 0.0)
        self.entries.append({"at": datetime.now(UTC).isoformat(), "label": label, "id": request_id,
                             "credits": credits, "model": model})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"credits_spent": self.credits, "entries": self.entries},
                                        indent=2), encoding="utf-8")


def can_afford(ledger: Ledger, budget_usd: float, est_cost_usd: float) -> bool:
    return ledger.usd + est_cost_usd <= budget_usd + 1e-9


class Flux:
    def __init__(self, key: str, base_url: str) -> None:
        self.key, self.base_url = key, base_url.rstrip("/")
        self.client = httpx.Client(timeout=60, headers={"x-key": key, "accept": "application/json"})

    def _safe(self, text: str) -> str:
        return base.redact(text, [self.key])

    def credits(self) -> Any:
        r = self.client.get(f"{self.base_url}/v1/credits")
        return r.status_code, self._safe(r.text[:200])

    def submit(self, model: str, body: dict[str, Any]) -> dict[str, Any]:
        r = self.client.post(f"{self.base_url}/v1/{model}", json=body)
        if r.status_code != 200:
            raise RuntimeError(f"submit HTTP {r.status_code}: {self._safe(r.text[:300])}")
        return r.json()

    def wait(self, polling_url: str, request_id: str, timeout_s: float = 180) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            r = self.client.get(polling_url, params={"id": request_id})
            last = r.json() if r.status_code == 200 else {"status": f"http_{r.status_code}"}
            status = last.get("status")
            if status == "Ready" or status in TERMINAL_BAD:
                return last
            time.sleep(1.0)
        return last

    def download(self, url: str) -> bytes:
        # The signed URL needs no key; use a plain client so the key is never sent to a CDN host.
        r = httpx.get(url, timeout=120, follow_redirects=True)
        r.raise_for_status()
        return r.content


def run(args: argparse.Namespace, env: dict[str, str]) -> dict[str, Any]:
    ledger = Ledger(Path(args.ledger))
    picks = PROMPTS[: args.max_images]
    plan = {"model": args.model, "images": [p[0] for p in picks], "budget_usd": args.budget_usd,
            "already_spent_usd": ledger.usd, "assumed_cost_per_image_usd": args.est_cost_usd,
            "aspect_ratio": args.aspect_ratio, "resolution": args.resolution}
    key = env.get("FLUX_API_KEY", "")
    if not args.live:
        return {"mode": "dry_run", "key_present": bool(key), **plan}
    if not key:
        raise SystemExit("FLUX_API_KEY missing in .env")

    flux = Flux(key, args.base_url)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    print("balance check:", flux.credits(), file=sys.stderr)
    for label, lane, prompt in picks:
        if not can_afford(ledger, args.budget_usd, args.est_cost_usd):
            print(f"STOP before {label}: spent ${ledger.usd} + est ${args.est_cost_usd} "
                  f"would exceed ${args.budget_usd}", file=sys.stderr)
            results.append({"label": label, "skipped": "budget"})
            break
        body: dict[str, Any] = {"prompt": prompt, "output_format": "png"}
        if args.model.startswith("flux-3-image"):
            body.update({"aspect_ratio": args.aspect_ratio, "resolution": args.resolution})
        else:
            body.update({"width": args.width, "height": args.height})
        t0 = time.monotonic()
        sub = flux.submit(args.model, body)
        ledger.add(label, sub["id"], sub.get("cost"), args.model)  # record spend first
        final = flux.wait(sub["polling_url"], sub["id"])
        entry: dict[str, Any] = {"label": label, "lane": lane, "id": sub["id"],
                                 "submit_cost_credits": sub.get("cost"),
                                 "settled_cost_credits": final.get("cost"),
                                 "output_mp": sub.get("output_mp"), "status": final.get("status"),
                                 "seconds": round(time.monotonic() - t0, 1)}
        sample = (final.get("result") or {}).get("sample")
        if final.get("status") == "Ready" and sample:
            path = out_dir / f"{label}.png"
            path.write_bytes(flux.download(sample))
            entry["file"] = str(path)
        results.append(entry)
        print(f"  [{label}] {entry['status']} cost={entry['submit_cost_credits']} credits "
              f"({entry['seconds']}s) total=${ledger.usd}", file=sys.stderr, flush=True)
    return {"mode": "live", **plan, "spent_usd_total": ledger.usd, "results": results}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--live", action="store_true")
    p.add_argument("--env", default=".env")
    p.add_argument("--model", default="flux-3-image-klein",
                   help="path segment under /v1/ (see api.bfl.ai/openapi.json)")
    p.add_argument("--budget-usd", type=float, default=4.0)
    p.add_argument("--est-cost-usd", type=float, default=0.05,
                   help="conservative per-image estimate used BEFORE each call")
    p.add_argument("--max-images", type=int, default=4)
    p.add_argument("--aspect-ratio", default="9:16")
    p.add_argument("--resolution", default="1.5k")
    p.add_argument("--width", type=int, default=1088)
    p.add_argument("--height", type=int, default=1920)
    p.add_argument("--base-url", default=DEFAULT_BASE)
    p.add_argument("--ledger", default="spikes/output/flux_ledger.json")
    p.add_argument("--out-dir", default="spikes/output/flux")
    p.add_argument("--report", default="spikes/output/flux_report.json")
    args = p.parse_args(argv)
    report = run(args, base.load_env(args.env))
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
