"""Copy-ready prompts for other AI tools."""

from app.services import external_prompts as ep

DATA = {"image_prompt": {"visual_blueprint": "A cat on a fan, soft light",
                         "flux_prompt": "cat", "negative_constraints": ["crowd", "text"]},
        "owner_instructions": "make the cat bigger"}
CLUES = [{"clue_id": "c1", "visual_clue": "a cat hugging a fan", "maps_to": "x", "operation": "y"}]


def test_all_tools_get_a_prompt_with_the_clues_and_the_owners_requests():
    out = ep.build(lane="rebus", data=DATA, clue_mapping=CLUES, caption="Đố bạn đây là gì?",
                   overlay_text="Đoán xem", narration_text="Đố bạn đây là gì?")
    by = {p["tool"]: p["text"] for p in out}
    assert set(by) == {"gemini", "flow", "heygen", "text"}
    assert "a cat hugging a fan" in by["gemini"] and "make the cat bigger" in by["gemini"]
    assert "no border" in by["gemini"] and "Avoid: crowd; text." in by["gemini"]
    assert "Đố bạn đây là gì?" in by["heygen"] and "Vietnamese" in by["heygen"]


def test_nothing_is_invented_when_the_concept_is_missing():
    assert ep.build(lane="daily_life", data={}, clue_mapping=[], caption=None,
                    overlay_text=None, narration_text=None) == []
