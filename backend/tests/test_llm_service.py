import asyncio
import json
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.core.config import Settings
from app.core.errors import ApiError
from app.db.models import CostLedger, LlmCall, ProviderBudget
from app.providers.contracts import JsonResult
from app.providers.errors import ProviderPermanent, ProviderTimeout
from app.services import llm_service as ls

SCHEMA_DIR = str(Path(__file__).resolve().parents[2] / "docs" / "schemas")


def run(coro):
    return asyncio.run(coro)


def concept(**over):
    base = {
        "decision": "proceed", "reasons": [], "answer_or_message": "Bánh chưng",
        "intended_viewer_inference": "món Tết",
        "clue_mapping": [{"clue_id": "c1", "visual_clue": "bánh vuông", "maps_to": "bánh",
                          "operation": "literal"}],
        "ambiguity_risks": [], "risk_level": "low", "sensitive_categories": [],
        "caption": "Đoán thử nhé", "hashtags": ["#doan"], "overlay_text": "Món gì?",
        "narration_text": "Đây là món gì nhỉ?", "reasoning_summary": "ok",
    }
    base.update(over)
    return base


class FakeLLM:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    async def complete_json(self, *, system, content, schema, max_tokens=4000):
        self.calls.append({"system": system, "schema": schema, "max_tokens": max_tokens})
        item = self.answers.pop(0)
        if isinstance(item, Exception):
            raise item
        refused = item == "refuse"
        return JsonResult(payload=None if refused else item, refused=refused, input_tokens=1000,
                          output_tokens=200, model="claude-opus-5-5", request_id="r1")


def cfg():
    return Settings(_env_file=None)


def budget(db, monthly="1"):
    db.execute(text("UPDATE system_settings SET daily_budget_limit=10, monthly_budget_limit=10"))
    db.add(ProviderBudget(provider="anthropic", monthly_limit_usd=Decimal(monthly)))
    db.commit()


def call(db, llm, **kw):
    args = dict(purpose="analyze", system_prompt="SYSTEM PROMPT", content=[{"type": "text",
                "text": "{}"}], schema_name="analyze_topic", schema_dir=SCHEMA_DIR)
    args.update(kw)
    return run(ls.call_json(db, llm, cfg(), **args))


def test_a_valid_answer_is_returned_and_recorded(db):
    budget(db)
    llm = FakeLLM([concept()])
    out = call(db, llm, entity_type="content_item", entity_id="x")
    assert out["decision"] == "proceed"
    row = db.scalars(select(LlmCall)).one()
    assert row.valid and row.purpose == "analyze" and row.input_tokens == 1000
    assert row.prompt_hash == ls.prompt_hash("SYSTEM PROMPT") and row.cost_ledger_id
    entry = db.scalars(select(CostLedger)).one()
    assert entry.status == "settled" and entry.actual_usd == Decimal("0.008000")


def test_the_wire_schema_not_the_full_one_is_sent_to_the_model(db):
    budget(db)
    llm = FakeLLM([concept()])
    call(db, llm)
    sent = json.dumps(llm.calls[0]["schema"])
    assert "maxLength" not in sent and "maxItems" not in sent
    assert '"additionalProperties": false' in sent


def test_an_invalid_answer_is_retried_once_then_accepted(db):
    budget(db)
    llm = FakeLLM([concept(caption="x" * 400), concept()])
    assert call(db, llm)["caption"] == "Đoán thử nhé"
    rows = db.scalars(select(LlmCall).order_by(LlmCall.created_at)).all()
    assert [r.valid for r in rows] == [False, True] and "caption" in rows[0].error
    assert len(db.scalars(select(CostLedger)).all()) == 2  # both attempts were paid and recorded


def test_two_invalid_answers_raise(db):
    budget(db)
    llm = FakeLLM([concept(decision="maybe"), concept(risk_level="severe")])
    with pytest.raises(ls.LlmInvalidOutput):
        call(db, llm)
    assert len(llm.calls) == 2


def test_a_refusal_is_not_retried_and_is_still_billed(db):
    budget(db)
    llm = FakeLLM(["refuse", concept()])
    with pytest.raises(ls.LlmRefused):
        call(db, llm)
    assert len(llm.calls) == 1
    assert db.scalars(select(CostLedger)).one().status == "settled"
    assert db.scalars(select(LlmCall)).one().refused


def test_no_budget_means_no_call(db):
    llm = FakeLLM([concept()])
    with pytest.raises(ApiError) as exc:
        call(db, llm)
    assert exc.value.code == "budget_not_set" and llm.calls == []


def test_known_uncharged_failure_releases_and_timeout_keeps_the_reservation(db):
    budget(db)
    with pytest.raises(ProviderPermanent):
        call(db, FakeLLM([ProviderPermanent("401", "bad key")]))
    with pytest.raises(ProviderTimeout):
        call(db, FakeLLM([ProviderTimeout("lost")]))
    statuses = [e.status for e in db.scalars(select(CostLedger).order_by(CostLedger.reserved_at))]
    assert statuses == ["released", "reserved"] and db.scalars(select(LlmCall)).all() == []


def test_the_prompt_hash_changes_with_the_prompt(db):
    assert ls.prompt_hash("a") != ls.prompt_hash("b") and len(ls.prompt_hash("a")) == 64


def test_the_estimate_reserved_before_the_call_is_an_upper_bound(db):
    budget(db)
    call(db, FakeLLM([concept()]), est_input_tokens=6000, max_tokens=4000)
    entry = db.scalars(select(CostLedger)).one()
    assert entry.estimated_usd == Decimal("0.104000") and entry.actual_usd < entry.estimated_usd
