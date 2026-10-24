"""Live check (a few cents, no images): do the new prompts give drawable, single-moment concepts?

Runs analyze_topic then create_image_prompt for a few briefs and prints the clue list and whether
the image-prompt step accepted the concept. Run from backend/ with the normal settings.
"""

import asyncio
import json
import sys

from app.core.config import settings
from app.core.schema_tools import load_schema, wire_schema
from app.providers.pipeline_factory import build_llm

BRIEFS = [
    ("daily_life", "Vừa ôm vàng ra khỏi tiệm thì điện thoại báo tin giá vàng giảm mạnh", None),
    ("daily_life", "Nghe nói thứ bảy được nghỉ học, cả lớp thì thầm vui như Tết", None),
    ("rebus", "Câu đố bốn hình về một người chờ đợi trong cái nóng", "Ôm quạt đợi khuya"),
]


async def call(llm, prompt_name: str, schema_name: str, payload: dict) -> dict:
    prompt = open(f"../prompts/{prompt_name}.md", encoding="utf-8").read()
    schema = wire_schema(load_schema(schema_name, "../docs/schemas"))
    r = await llm.complete_json(
        system=prompt, content=[{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        schema=schema, max_tokens=4000)
    assert r.payload is not None, "refused"
    return r.payload


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    llm = build_llm(settings)
    for lane, brief, answer in BRIEFS:
        a = await call(llm, "analyze_topic", "analyze_topic",
                       {"lane": lane, "brief": brief, "answer": answer})
        print(f"\n== {lane}: {brief}")
        print("decision:", a["decision"], "| clues:", len(a["clue_mapping"]))
        for c in a["clue_mapping"]:
            print("  -", c["visual_clue"])
        spec = {"lane": lane, "answer_or_message": a["answer_or_message"],
                "clue_mapping": a["clue_mapping"],
                "intended_viewer_inference": a["intended_viewer_inference"],
                "ambiguity_risks": a["ambiguity_risks"], "previous_qa_issues": []}
        p = await call(llm, "create_image_prompt", "image_prompt", spec)
        print("image prompt status:", p["status"], p["revision_reasons"] if p["status"] != "ok" else "")


asyncio.run(main())
