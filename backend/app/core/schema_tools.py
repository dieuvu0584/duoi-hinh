"""JSON Schema helpers for LLM structured output.

The Anthropic structured-output feature accepts only a subset of JSON Schema (verified live on
2026-10-22: every object needs `additionalProperties: false`, and `minimum`/`maximum` are rejected).
`wire_schema` derives a compatible schema from our full schemas in docs/schemas/, and every model
answer is then validated LOCALLY against the FULL schema, so ranges, enums and `const` still apply.
"""

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema

# Keywords the structured-output endpoint does not accept (or that only matter locally).
_STRIP = {"$schema", "$id", "title", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
          "minLength", "maxLength", "minItems", "maxItems", "pattern", "format", "default"}


class InvalidModelOutput(ValueError):
    """The model's answer does not satisfy the full schema."""


def wire_schema(schema: dict[str, Any]) -> dict[str, Any]:
    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if k in _STRIP:
                    continue
                # Under `properties` the keys are FIELD NAMES, not keywords: a field called
                # "title" or "pattern" must survive (it was silently dropped before).
                out[k] = ({name: walk(sub) for name, sub in v.items()}
                          if k == "properties" and isinstance(v, dict) else walk(v))
            if "const" in out:
                out["enum"] = [out.pop("const")]
            if out.get("type") == "object" or "properties" in out:
                out["additionalProperties"] = False
            return out
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(copy.deepcopy(schema))


def validate_output(schema: dict[str, Any], data: Any) -> None:
    """Raise InvalidModelOutput with a short, safe message when `data` breaks the schema."""
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        where = "/".join(str(p) for p in first.path) or "(root)"
        raise InvalidModelOutput(f"{where}: {first.message[:160]}")


@lru_cache(maxsize=16)
def load_schema(name: str, schema_dir: str = "docs/schemas") -> dict[str, Any]:
    path = Path(schema_dir) / f"{name}.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))
