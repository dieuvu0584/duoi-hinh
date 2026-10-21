import json
from pathlib import Path

import pytest

from app.core.schema_tools import InvalidModelOutput, validate_output, wire_schema

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "docs" / "schemas"
NAMES = ["analyze_topic", "image_prompt", "quality_review", "safety_review"]


def load(name):
    return json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))


def walk(node):
    yield node
    if isinstance(node, dict):
        for v in node.values():
            yield from walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v)


@pytest.mark.parametrize("name", NAMES)
def test_wire_schemas_only_use_what_the_endpoint_accepts(name):
    wire = wire_schema(load(name))
    forbidden = {"minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems", "format",
                 "pattern", "const", "$schema", "$id"}
    for node in walk(wire):
        if isinstance(node, dict):
            assert not forbidden & set(node), f"{name}: {forbidden & set(node)}"
            if node.get("type") == "object" or "properties" in node:
                assert node.get("additionalProperties") is False


def test_wire_conversion_does_not_mutate_the_original_and_keeps_const_as_enum():
    original = {"type": "object", "properties": {"c": {"const": "x"}, "n": {"type": "number",
                "minimum": 0}}}
    wire = wire_schema(original)
    assert "minimum" in original["properties"]["n"] and "const" in original["properties"]["c"]
    assert wire["properties"]["c"] == {"enum": ["x"]} and wire["additionalProperties"] is False


def good_concept():
    return {
        "decision": "proceed", "reasons": [], "answer_or_message": "Bánh chưng",
        "intended_viewer_inference": "món ăn ngày Tết",
        "clue_mapping": [{"clue_id": "c1", "visual_clue": "bánh vuông", "maps_to": "bánh",
                          "operation": "literal"}],
        "ambiguity_risks": [], "risk_level": "low", "sensitive_categories": [],
        "caption": "Đoán thử nhé", "hashtags": ["#doan"], "overlay_text": "Món gì?",
        "narration_text": "Đây là món gì nhỉ?", "reasoning_summary": "ok",
    }


def test_a_good_answer_passes_the_full_schema():
    validate_output(load("analyze_topic"), good_concept())


@pytest.mark.parametrize(
    ("mutate", "needle"),
    [
        (lambda d: d.pop("caption"), "caption"),
        (lambda d: d.update(decision="maybe"), "decision"),
        (lambda d: d.update(risk_level="severe"), "risk_level"),
        (lambda d: d.update(hashtags=["#a"] * 6), "hashtags"),
        (lambda d: d.update(caption="x" * 301), "caption"),
        (lambda d: d.update(extra="field"), "extra"),
        (lambda d: d["clue_mapping"][0].update(operation="magic"), "operation"),
        (lambda d: d.update(clue_mapping=[d["clue_mapping"][0]] * 9), "clue_mapping"),
    ],
)
def test_ranges_enums_and_lengths_are_enforced_locally(mutate, needle):
    data = good_concept()
    mutate(data)
    with pytest.raises(InvalidModelOutput) as exc:
        validate_output(load("analyze_topic"), data)
    assert needle in str(exc.value)


def test_quality_scores_outside_the_range_are_rejected_even_though_the_endpoint_cannot_say_so():
    data = {"score_total": 120, "scores": {k: 50 for k in (
        "clue_accuracy", "answer_inferability", "humor_surprise", "mobile_composition",
        "brand_fit")}, "hard_failures": [], "missing_clues": [], "ambiguous_alternatives": [],
        "crop_or_text_issues": [], "recommendation": "pass", "explanation": "ok"}
    with pytest.raises(InvalidModelOutput):
        validate_output(load("quality_review"), data)
    data["score_total"] = 80
    validate_output(load("quality_review"), data)


def test_every_schema_file_is_valid_json_schema():
    import jsonschema

    for name in NAMES:
        jsonschema.Draft202012Validator.check_schema(load(name))
