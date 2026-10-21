"""Package 0 spike: ask Claude for clue bounding boxes, then let OUR code decide crop vs fit.

The model only measures (normalised boxes, 0..1, origin top-left); `app.services.composition`
decides, per target, whether a centre crop keeps every essential clue (decision D-26).
Uses the official Anthropic SDK with structured JSON output. Model is configurable (default
claude-opus-5-5). Hard call cap; dry run unless --live. The key is read from .env and never
printed. An overlay image is written so the boxes can be checked by eye.

    python spikes/claude_composition_spike.py --image <master.png> --clues "cat,alarm clock"
    python spikes/claude_composition_spike.py ... --live
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
from pathlib import Path
from typing import Any

import meta_reels_spike as base  # reuse load_env

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.services.composition import SAFE_REGIONS, Box, decide_modes  # noqa: E402

PRICE_IN_PER_MTOK = 4.00  # claude-opus-5-5, USD (skill price table 2026-10-06)
PRICE_OUT_PER_MTOK = 20.00

SCHEMA = {
    "type": "object",
    "properties": {
        "clues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "clue_id": {"type": "string"},
                    "description": {"type": "string"},
                    "x0": {"type": "number"}, "y0": {"type": "number"},
                    "x1": {"type": "number"}, "y1": {"type": "number"},
                },
                "required": ["clue_id", "description", "x0", "y0", "x1", "y1"],
                "additionalProperties": False,
            },
        },
        "notes": {"type": "string"},
    },
    "required": ["clues", "notes"],
    "additionalProperties": False,
}

PROMPT = (
    "You are measuring an illustration for a video crop. For each essential subject listed below, "
    "return its tight bounding box as fractions of the image: x0,y0 is the top-left corner and "
    "x1,y1 the bottom-right corner, each between 0 and 1 (0,0 = top-left of the image, 1,1 = "
    "bottom-right). Include the WHOLE subject (for a person or animal: head to feet/paws and "
    "tail; for an object: its full outline including any base). Use the clue_id exactly as given. "
    "Do not invent subjects that are not in the image; if one is missing, omit it and say so in "
    "notes. Essential subjects: {clues}."
)


def encode_image(path: Path, max_side: int = 1024) -> tuple[str, str]:
    from PIL import Image

    img = Image.open(path).convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return base64.standard_b64encode(buf.getvalue()).decode("ascii"), "image/jpeg"


def request_boxes(client: Any, model: str, image_b64: str, media_type: str,
                  clues: list[str]) -> tuple[dict[str, Any], Any]:
    response = client.messages.create(
        model=model,
        max_tokens=4000,
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                         "data": image_b64}},
            {"type": "text", "text": PROMPT.format(clues=", ".join(clues))},
        ]}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("the model refused the request")
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text), response.usage


def to_boxes(data: dict[str, Any]) -> tuple[list[Box], list[str]]:
    boxes, problems = [], []
    for c in data.get("clues", []):
        try:
            boxes.append(Box(c["clue_id"], float(c["x0"]), float(c["y0"]),
                             float(c["x1"]), float(c["y1"])))
        except (ValueError, KeyError) as exc:
            problems.append(f"{c.get('clue_id', '?')}: {exc}")
    return boxes, problems


def usd(usage: Any) -> float:
    return round(usage.input_tokens * PRICE_IN_PER_MTOK / 1e6
                 + usage.output_tokens * PRICE_OUT_PER_MTOK / 1e6, 6)


def draw_overlay(path: Path, boxes: list[Box], out: Path) -> None:
    from PIL import Image, ImageDraw

    img = Image.open(path).convert("RGB")
    w, h = img.size
    d = ImageDraw.Draw(img)
    colors = {"reel_9x16": (0, 120, 255), "youtube_16x9": (0, 200, 0)}
    for target, (x0, y0, x1, y1) in SAFE_REGIONS.items():
        d.rectangle((x0 * w, y0 * h, x1 * w, y1 * h), outline=colors[target], width=max(4, w // 250))
    for b in boxes:
        d.rectangle((b.x0 * w, b.y0 * h, b.x1 * w, b.y1 * h), outline=(255, 0, 0),
                    width=max(5, w // 200))
    img.thumbnail((900, 900))
    img.save(out, quality=85)


def run(args: argparse.Namespace, env: dict[str, str], client: Any | None = None) -> dict[str, Any]:
    clues = [c.strip() for c in args.clues.split(",") if c.strip()]
    plan = {"model": args.model, "image": args.image, "clues": clues,
            "max_calls": args.max_calls, "key_present": bool(env.get("ANTHROPIC_API_KEY"))}
    if not args.live:
        return {"mode": "dry_run", **plan}
    if args.max_calls < 1:
        raise SystemExit("--max-calls must be at least 1")
    if client is None:
        key = env.get("ANTHROPIC_API_KEY")
        if not key:
            raise SystemExit("ANTHROPIC_API_KEY missing in .env")
        import anthropic

        client = anthropic.Anthropic(api_key=key)
    image_b64, media = encode_image(Path(args.image))
    runs: list[dict[str, Any]] = []
    total = 0.0
    for i in range(args.max_calls):
        data, usage = request_boxes(client, args.model, image_b64, media, clues)
        boxes, problems = to_boxes(data)
        cost = usd(usage)
        total += cost
        decision = decide_modes(boxes)
        missing = [c for c in clues if c not in {b.clue_id for b in boxes}]
        runs.append({"call": i + 1, "boxes": [b.__dict__ for b in boxes], "problems": problems,
                     "missing_clues": missing, "notes": data.get("notes"),
                     "decision": {"modes": decision.modes, "outside": decision.outside,
                                  "reason": decision.reason},
                     "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                     "estimated_usd": cost})
        if i == 0:
            draw_overlay(Path(args.image), boxes, Path(args.overlay))
    return {"mode": "live", **plan, "estimated_usd_total": round(total, 6), "runs": runs}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--image", required=True)
    p.add_argument("--clues", required=True, help="comma-separated essential subjects")
    p.add_argument("--model", default="claude-opus-5-5")
    p.add_argument("--max-calls", type=int, default=1)
    p.add_argument("--live", action="store_true")
    p.add_argument("--env", default=".env")
    p.add_argument("--overlay", default="spikes/output/composition_overlay.jpg")
    p.add_argument("--report", default="spikes/output/composition_report.json")
    args = p.parse_args(argv)
    env = base.load_env(args.env)
    report = run(args, env)
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    os.environ.pop("ANTHROPIC_API_KEY", None)  # only the key from .env is ever used
    raise SystemExit(main())
