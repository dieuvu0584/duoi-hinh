"""Structured LLM calls with budget, validation and an audit row.

`call_json` is the only way pipeline code talks to an LLM:
  1. a conservative budget reservation is written and committed BEFORE the call;
  2. the model is asked for JSON in the endpoint-compatible (wire) form of a schema;
  3. the answer is validated against the FULL schema (ranges, enums, lengths);
  4. an invalid answer is retried once (a new reservation), then raised;
  5. the cost is settled from the token usage and an `llm_calls` row records the prompt by hash.
A refusal is never retried: it raises LlmRefused so the caller can hold the content for a human.
"""

import hashlib
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.schema_tools import InvalidModelOutput, load_schema, validate_output, wire_schema
from app.db.models import LlmCall
from app.providers.contracts import JsonLLM, JsonResult
from app.providers.errors import ProviderPermanent, ProviderTimeout, ProviderTransient
from app.services import budget_service

PROVIDER = "anthropic"


class LlmRefused(Exception):
    """The model declined to answer; the content must go to a human."""


class LlmInvalidOutput(Exception):
    """The model answered twice with JSON that breaks the schema."""


def _usd(tokens_in: int, tokens_out: int, cfg: Settings) -> Decimal:
    value = (Decimal(tokens_in) * Decimal(str(cfg.claude_usd_per_mtok_in))
             + Decimal(tokens_out) * Decimal(str(cfg.claude_usd_per_mtok_out))) / Decimal(1_000_000)
    return value.quantize(Decimal("0.000001"))


def prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def call_json(
    session: Session, llm: JsonLLM, cfg: Settings, *, purpose: str, system_prompt: str,
    content: list[dict[str, Any]], schema_name: str, entity_type: str | None = None,
    entity_id: str | None = None, max_tokens: int = 4000, est_input_tokens: int = 6000,
    schema_dir: str = "docs/schemas", attempts: int = 2,
) -> dict[str, Any]:
    full = load_schema(schema_name, schema_dir)
    wire = wire_schema(full)
    last_error = "no attempt made"
    for attempt in range(1, attempts + 1):
        reservation = budget_service.reserve(
            session, provider=PROVIDER, operation=f"llm:{purpose}",
            estimated_usd=_usd(est_input_tokens, max_tokens, cfg),
            entity_type=entity_type, entity_id=entity_id)
        try:
            result: JsonResult = await llm.complete_json(
                system=system_prompt, content=content, schema=wire, max_tokens=max_tokens)
        except (ProviderPermanent, ProviderTransient) as exc:
            budget_service.release(
                session, reservation,
                reason=f"llm request rejected before billing: {type(exc).__name__}")
            raise
        except ProviderTimeout:
            raise  # possibly billed: keep the reservation
        budget_service.settle(session, reservation,
                              actual_usd=_usd(result.input_tokens, result.output_tokens, cfg),
                              request_id=result.request_id)
        row = LlmCall(
            purpose=purpose, model=result.model, prompt_hash=prompt_hash(system_prompt),
            schema_name=schema_name, entity_type=entity_type, entity_id=entity_id,
            input_tokens=result.input_tokens, output_tokens=result.output_tokens,
            refused=result.refused, request_id=result.request_id, cost_ledger_id=reservation.id)
        session.add(row)
        if result.refused or result.payload is None:
            row.error = "refused"
            session.commit()
            raise LlmRefused(purpose)
        try:
            validate_output(full, result.payload)
        except InvalidModelOutput as exc:
            last_error = str(exc)[:290]
            row.error = last_error
            session.commit()
            continue  # one more attempt
        row.valid = True
        session.commit()
        return result.payload
    raise LlmInvalidOutput(f"{purpose}: {last_error}")
