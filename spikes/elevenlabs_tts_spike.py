"""Package 0 spike: try ElevenLabs Vietnamese narration under a HARD character cap (D-18, D-24).

Facts used (elevenlabs.io/docs, read 2026-10-22; re-check before relying):
  POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=...  header xi-api-key
  body {text, model_id, voice_settings?}; response is binary audio.
  Vietnamese is listed for eleven_flash_v2_5, eleven_v3, eleven_v4(_turbo); NOT eleven_multilingual_v2.

Safety: dry run by default (--live required). Every call's characters are added to a ledger BEFORE
the request, so a crash cannot hide spend; the run refuses a request that would pass
--max-chars. The key is never printed. Output: mp3 files, duration, syllables/second, and the
response headers (names that mention character/request/rate) so the VERIFY items can be answered.

    python spikes/elevenlabs_tts_spike.py                         # dry run
    python spikes/elevenlabs_tts_spike.py --live --models eleven_flash_v2_5,eleven_v3
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import meta_reels_spike as base  # reuse load_env and redact

DEFAULT_BASE = "https://api.elevenlabs.io"

# (label, text). The digit line and its spelled-out twin show what our own normaliser must do.
LINES: list[tuple[str, str]] = [
    ("rebus_question",
     "Nhìn hình đoán chuyện! Đây là món ăn gì nhỉ? Đáp án có hai chữ, hãy đoán thử nhé."),
    ("digits_raw",
     "Hôm nay là ngày 25/10/2026, trời mưa 3 ngày liền, giá xăng 24.500 đồng một lít."),
    ("digits_spelled",
     "Hôm nay là ngày hai mươi lăm tháng mười năm hai nghìn không trăm hai mươi sáu, "
     "trời mưa ba ngày liền, giá xăng hai mươi bốn nghìn năm trăm đồng một lít."),
    ("tones_names",
     "Anh Tuấn ở Đà Nẵng chạy xe máy qua cầu Rồng, bỗng nhiên mưa như trút nước."),
    ("daily_meme",
     "Sáng thứ Hai, chiếc đồng hồ báo thức reo, còn con mèo thì quyết định ngủ tiếp."),
    ("tone_minimal_pairs",
     "Chị Hằng đặt hàng trăm hộp bánh chưng, đủ để cả xóm ăn Tết thật no."),
]


class CharLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        self.chars: int = int(data.get("chars_requested", 0))
        self.entries: list[dict[str, Any]] = list(data.get("entries", []))

    def add(self, label: str, model: str, chars: int) -> None:
        self.chars += chars
        self.entries.append({"at": datetime.now(UTC).isoformat(), "label": label,
                             "model": model, "chars": chars})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"chars_requested": self.chars, "entries": self.entries},
                                        indent=2), encoding="utf-8")


def can_afford(ledger: CharLedger, max_chars: int, next_chars: int) -> bool:
    return ledger.chars + next_chars <= max_chars


def audio_seconds(path: Path) -> float | None:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
             str(path)], capture_output=True, text=True, check=True).stdout.strip()
        return float(out)
    except (OSError, subprocess.CalledProcessError, ValueError):
        return None


def interesting_headers(headers: httpx.Headers) -> dict[str, str]:
    keep = ("character", "request", "ratelimit", "rate-limit", "cost", "credit", "history")
    return {k: v for k, v in headers.items() if any(w in k.lower() for w in keep)}


def run(args: argparse.Namespace, env: dict[str, str]) -> dict[str, Any]:
    ledger = CharLedger(Path(args.ledger))
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    lines = LINES[: args.max_lines]
    planned = sum(len(t) for _, t in lines) * len(models)
    plan = {"models": models, "lines": [lab for lab, _ in lines], "planned_chars": planned,
            "max_chars": args.max_chars, "already_requested_chars": ledger.chars}
    key, voice = env.get("ELEVENLABS_API_KEY", ""), env.get("ELEVENLABS_VOICE_ID", "")
    present = {"ELEVENLABS_API_KEY": bool(key), "ELEVENLABS_VOICE_ID": bool(voice)}
    if not args.live:
        return {"mode": "dry_run", "env_present": present, **plan}
    if not key or not voice:
        raise SystemExit("ELEVENLABS_API_KEY / ELEVENLABS_VOICE_ID missing in .env")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    secrets = [key]
    client = httpx.Client(base_url=args.base_url.rstrip("/"), timeout=120,
                          headers={"xi-api-key": key})
    sub = client.get("/v1/user/subscription")
    quota = None
    if sub.status_code == 200:
        data = sub.json()
        quota = {k: data.get(k) for k in ("tier", "character_count", "character_limit",
                                           "next_character_count_reset_unix")}
    print("subscription:", sub.status_code, json.dumps(quota), file=sys.stderr)

    results: list[dict[str, Any]] = []
    for model in models:
        for label, text in lines:
            if not can_afford(ledger, args.max_chars, len(text)):
                print(f"STOP before {model}/{label}: cap {args.max_chars} chars", file=sys.stderr)
                results.append({"model": model, "label": label, "skipped": "char_cap"})
                continue
            ledger.add(label, model, len(text))  # record BEFORE the request
            t0 = time.monotonic()
            body = {"text": text, "model_id": model}
            r = client.post(f"/v1/text-to-speech/{voice}", params={"output_format": args.format},
                            json=body)
            entry: dict[str, Any] = {"model": model, "label": label, "chars": len(text),
                                     "http": r.status_code,
                                     "seconds": round(time.monotonic() - t0, 2),
                                     "headers": interesting_headers(r.headers)}
            if r.status_code == 200 and r.content:
                path = out_dir / f"{label}__{model}.mp3"
                path.write_bytes(r.content)
                dur = audio_seconds(path)
                entry.update({"file": str(path), "bytes": len(r.content), "audio_seconds": dur})
                if dur:
                    entry["words_per_second"] = round(len(text.split()) / dur, 2)
            else:
                entry["error"] = base.redact(r.text[:300], secrets)
            results.append(entry)
            print(f"  [{model}/{label}] http={entry['http']} dur={entry.get('audio_seconds')} "
                  f"total_chars={ledger.chars}", file=sys.stderr, flush=True)
    return {"mode": "live", "subscription_http": sub.status_code, "quota_before": quota,
            "chars_requested_total": ledger.chars, **plan, "results": results}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--live", action="store_true")
    p.add_argument("--env", default=".env")
    p.add_argument("--models", default="eleven_flash_v2_5")
    p.add_argument("--max-chars", type=int, default=1600)
    p.add_argument("--max-lines", type=int, default=len(LINES))
    p.add_argument("--format", default="mp3_44100_128")
    p.add_argument("--base-url", default=DEFAULT_BASE)
    p.add_argument("--ledger", default="spikes/output/eleven_ledger.json")
    p.add_argument("--out-dir", default="spikes/output/tts")
    p.add_argument("--report", default="spikes/output/tts_report.json")
    args = p.parse_args(argv)
    report = run(args, base.load_env(args.env))
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False)[:5000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
