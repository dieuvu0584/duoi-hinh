"""Tests for the composition spike with a FAKE Anthropic client (no network, no spend)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import claude_composition_spike as spike  # noqa: E402


class FakeMessages:
    def __init__(self, payload, stop_reason="end_turn"):
        self.payload, self.stop_reason, self.calls = payload, stop_reason, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        block = SimpleNamespace(type="text", text=json.dumps(self.payload))
        return SimpleNamespace(content=[block], stop_reason=self.stop_reason,
                               usage=SimpleNamespace(input_tokens=2000, output_tokens=200))


def fake_client(payload, stop_reason="end_turn"):
    return SimpleNamespace(messages=FakeMessages(payload, stop_reason))


@pytest.fixture()
def image(tmp_path):
    from PIL import Image

    path = tmp_path / "m.png"
    Image.new("RGB", (64, 64), (200, 100, 50)).save(path)
    return path


def make_args(tmp_path, image, **kw):
    ns = dict(image=str(image), clues="cat,clock", model="claude-opus-5-5", max_calls=1, live=True,
              env=".env", overlay=str(tmp_path / "o.jpg"), report=str(tmp_path / "r.json"))
    ns.update(kw)
    return argparse.Namespace(**ns)


CENTRED = {"clues": [
    {"clue_id": "cat", "description": "c", "x0": 0.40, "y0": 0.40, "x1": 0.55, "y1": 0.60},
    {"clue_id": "clock", "description": "k", "x0": 0.55, "y0": 0.45, "x1": 0.65, "y1": 0.60}],
    "notes": "ok"}


def test_dry_run_makes_no_call_and_never_needs_a_client(tmp_path, image):
    report = spike.run(make_args(tmp_path, image, live=False), {"ANTHROPIC_API_KEY": "k"})
    assert report["mode"] == "dry_run"


def test_centred_clues_give_fill_for_both_targets(tmp_path, image):
    client = fake_client(CENTRED)
    report = spike.run(make_args(tmp_path, image), {}, client=client)
    run = report["runs"][0]
    assert run["decision"]["modes"] == {"reel_9x16": "fill", "youtube_16x9": "fill"}
    assert run["missing_clues"] == [] and run["estimated_usd"] == pytest.approx(0.012)
    call = client.messages.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"]["format"]["type"] == "json_schema"


def test_off_centre_clue_forces_fit_and_missing_clue_is_reported(tmp_path, image):
    payload = {"clues": [{"clue_id": "cat", "description": "c", "x0": 0.05, "y0": 0.4,
                          "x1": 0.5, "y1": 0.8}], "notes": "no clock"}
    report = spike.run(make_args(tmp_path, image), {}, client=fake_client(payload))
    run = report["runs"][0]
    assert run["decision"]["modes"]["reel_9x16"] == "fit"
    assert run["missing_clues"] == ["clock"]


def test_invalid_boxes_are_reported_not_trusted(tmp_path, image):
    payload = {"clues": [{"clue_id": "cat", "description": "c", "x0": 0.5, "y0": 0.5,
                          "x1": 1.4, "y1": 0.9}], "notes": ""}
    report = spike.run(make_args(tmp_path, image), {}, client=fake_client(payload))
    run = report["runs"][0]
    assert run["boxes"] == [] and run["problems"]
    assert run["decision"]["modes"] == {"reel_9x16": "fit", "youtube_16x9": "fit"}  # fail closed


def test_refusal_is_an_error_and_call_cap_is_respected(tmp_path, image):
    with pytest.raises(RuntimeError, match="refused"):
        spike.run(make_args(tmp_path, image), {}, client=fake_client(CENTRED, "refusal"))
    client = fake_client(CENTRED)
    spike.run(make_args(tmp_path, image, max_calls=2), {}, client=client)
    assert len(client.messages.calls) == 2
