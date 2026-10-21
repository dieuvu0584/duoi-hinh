"""Package 0 spike: try FLUX image generation through fal.ai under a HARD USD cap (D-18).

Facts used (fal.ai docs and pricing page, read 2026-10-22; re-check before relying):
  queue base https://queue.fal.run, header `Authorization: Key <FAL_API_KEY>`,
  POST /<endpoint> {prompt, image_size:{width,height} (512..2048), num_images, output_format,
  enable_safety_checker} -> {request_id, status_url, response_url, cancel_url};
  poll status_url until COMPLETED (a failed request sets `error` / `error_type`);
  GET response_url -> {images:[{url,width,height,...}], seed, ...}.
  Pricing is per megapixel (1024x1024 counts as 1 MP): flux-2 $0.012, flux/schnell $0.003,
  flux/dev $0.025. The API does not return the cost, so we compute it from the pixel count.

Safety: dry run by default (--live required). Cost is ESTIMATED as an upper bound and written to
the ledger BEFORE the request, then settled from the real image size. A request that could pass
--budget-usd is refused. Models with no known price are refused unless --price-per-mp is given.
The key is never printed.

    python spikes/fal_image_spike.py                      # dry run
    python spikes/fal_image_spike.py --live --max-images 4
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

QUEUE_BASE = "https://queue.fal.run"
PRICE_PER_MP = {  # USD per megapixel, fal.ai/pricing 2026-10-22
    "fal-ai/flux-2": 0.012,
    "fal-ai/flux/schnell": 0.003,
    "fal-ai/flux/dev": 0.025,
}
MP_UPPER = 1_000_000  # conservative: bill every 1,000,000 px as 1 MP (docs: 1024x1024 = 1 MP)

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


def estimate_usd(width: int, height: int, price_per_mp: float) -> float:
    # fal rounds output sizes up (1080x1920 came back 1088x1920), so callers settle on the
    # returned size; add a small margin to the pre-call estimate for that rounding.
    return round(width * height / MP_UPPER * price_per_mp, 6)


class Ledger:
    """USD spent by this spike; survives restarts. Estimates are replaced by settled amounts."""

    def __init__(self, path: Path) -> None:
        self.path = path
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        self.entries: list[dict[str, Any]] = list(data.get("entries", []))

    @property
    def usd(self) -> float:
        return round(sum(e.get("settled_usd", e["estimated_usd"]) for e in self.entries), 6)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"usd_total": self.usd, "entries": self.entries},
                                        indent=2), encoding="utf-8")

    def reserve(self, label: str, model: str, estimated_usd: float) -> dict[str, Any]:
        entry = {"at": datetime.now(UTC).isoformat(), "label": label, "model": model,
                 "estimated_usd": estimated_usd}
        self.entries.append(entry)
        self._save()
        return entry

    def settle(self, entry: dict[str, Any], request_id: str, settled_usd: float) -> None:
        entry["request_id"] = request_id
        # Never clamp to the estimate: if fal rounds the size up, the real cost is higher.
        entry["settled_usd"] = round(settled_usd, 6)
        self._save()


def can_afford(ledger: Ledger, budget_usd: float, next_usd: float) -> bool:
    return ledger.usd + next_usd <= budget_usd + 1e-9


class Fal:
    def __init__(self, key: str, queue_base: str) -> None:
        self.key, self.base = key, queue_base.rstrip("/")
        self.client = httpx.Client(timeout=60, headers={"Authorization": f"Key {key}"})

    def _safe(self, text: str) -> str:
        return base.redact(text, [self.key])

    def submit(self, endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        r = self.client.post(f"{self.base}/{endpoint}", json=body)
        if r.status_code != 200:
            raise RuntimeError(f"submit HTTP {r.status_code}: {self._safe(r.text[:300])}")
        return r.json()

    def wait(self, status_url: str, timeout_s: float = 240) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            r = self.client.get(status_url)
            last = r.json() if r.status_code in (200, 202) else {"status": f"http_{r.status_code}"}
            if last.get("status") == "COMPLETED":
                return last
            time.sleep(1.0)
        return last

    def result(self, response_url: str) -> tuple[int, dict[str, Any]]:
        r = self.client.get(response_url)
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, {"raw": self._safe(r.text[:300])}

    def download(self, url: str) -> bytes:
        # Plain client: the media CDN must never receive the API key.
        r = httpx.get(url, timeout=120, follow_redirects=True)
        r.raise_for_status()
        return r.content


def run(args: argparse.Namespace, env: dict[str, str]) -> dict[str, Any]:
    price = args.price_per_mp if args.price_per_mp is not None else PRICE_PER_MP.get(args.model)
    ledger = Ledger(Path(args.ledger))
    picks = PROMPTS[: args.max_images]
    est = estimate_usd(args.width, args.height, price) if price is not None else None
    plan = {"model": args.model, "images": [p[0] for p in picks], "budget_usd": args.budget_usd,
            "already_spent_usd": ledger.usd, "size": f"{args.width}x{args.height}",
            "price_per_mp_usd": price, "estimated_usd_per_image": est}
    key = env.get("FAL_API_KEY", "")
    if not args.live:
        return {"mode": "dry_run", "key_present": bool(key), **plan}
    if not key:
        raise SystemExit("FAL_API_KEY missing in .env")
    if price is None:
        raise SystemExit(f"no known price for {args.model}; pass --price-per-mp (see fal.ai/pricing)")

    fal = Fal(key, args.queue_base)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for label, lane, prompt in picks:
        if not can_afford(ledger, args.budget_usd, est):
            print(f"STOP before {label}: ${ledger.usd} + est ${est} would exceed "
                  f"${args.budget_usd}", file=sys.stderr)
            results.append({"label": label, "skipped": "budget"})
            break
        entry = ledger.reserve(label, args.model, est)  # reserve BEFORE the request
        t0 = time.monotonic()
        body = {"prompt": prompt, "image_size": {"width": args.width, "height": args.height},
                "num_images": 1, "output_format": "png", "enable_safety_checker": True}
        sub = fal.submit(args.model, body)
        status = fal.wait(sub["status_url"])
        code, res = (0, {})
        if status.get("status") == "COMPLETED":
            code, res = fal.result(sub["response_url"])
        images = res.get("images") or []
        out: dict[str, Any] = {"label": label, "lane": lane, "request_id": sub["request_id"],
                               "status": status.get("status"), "result_http": code,
                               "error": status.get("error") or res.get("detail"),
                               "seconds": round(time.monotonic() - t0, 1)}
        if images:
            img = images[0]
            w, h = int(img.get("width") or args.width), int(img.get("height") or args.height)
            actual = estimate_usd(w, h, price)
            ledger.settle(entry, sub["request_id"], actual)
            out.update({"width": w, "height": h, "settled_usd": actual})
            path = out_dir / f"{label}__{args.model.replace('/', '_')}.png"
            path.write_bytes(fal.download(images[0]["url"]))
            out["file"] = str(path)
        elif status.get("status") != "COMPLETED":
            fal.client.put(sub.get("cancel_url", ""))  # best effort; the reservation stays
        results.append(out)
        print(f"  [{label}] {out['status']} ${out.get('settled_usd')} ({out['seconds']}s) "
              f"total=${ledger.usd}", file=sys.stderr, flush=True)
    return {"mode": "live", **plan, "spent_usd_total": ledger.usd, "results": results}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--live", action="store_true")
    p.add_argument("--env", default=".env")
    p.add_argument("--model", default="fal-ai/flux-2", help="fal endpoint id")
    p.add_argument("--budget-usd", type=float, default=4.0)
    p.add_argument("--price-per-mp", type=float, default=None)
    p.add_argument("--max-images", type=int, default=4)
    p.add_argument("--width", type=int, default=1080)
    p.add_argument("--height", type=int, default=1920)
    p.add_argument("--queue-base", default=QUEUE_BASE)
    p.add_argument("--ledger", default="spikes/output/fal_ledger.json")
    p.add_argument("--out-dir", default="spikes/output/fal")
    p.add_argument("--report", default="spikes/output/fal_report.json")
    args = p.parse_args(argv)
    report = run(args, base.load_env(args.env))
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
