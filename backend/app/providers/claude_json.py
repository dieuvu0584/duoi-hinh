"""Claude JSON adapter: one structured-output call (JsonLLM).

The caller supplies the system prompt, the user content blocks and a WIRE schema (see
app.core.schema_tools). The adapter never validates business meaning; `llm_service` validates the
answer against the full schema. A refusal is returned as data (it still bills tokens).
"""

import json
from typing import Any

from app.providers.claude_common import call_messages
from app.providers.contracts import JsonResult
from app.providers.errors import ProviderError


class ClaudeJson:
    def __init__(self, *, client: Any, model: str = "claude-opus-5-5") -> None:
        self._client, self._model = client, model

    async def complete_json(
        self, *, system: str, content: list[dict[str, Any]], schema: dict[str, Any],
        max_tokens: int = 4000,
    ) -> JsonResult:
        response = await call_messages(
            self._client, model=self._model, max_tokens=max_tokens, system=system,
            output_config={"format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": content}])
        usage = response.usage
        base = dict(input_tokens=int(usage.input_tokens), output_tokens=int(usage.output_tokens),
                    model=str(getattr(response, "model", self._model)),
                    request_id=getattr(response, "id", None))
        if response.stop_reason == "refusal":
            return JsonResult(payload=None, refused=True, **base)
        if response.stop_reason == "max_tokens":
            raise ProviderError("the answer was cut off by max_tokens")
        text = next((b.text for b in response.content if getattr(b, "type", "") == "text"), None)
        if text is None:
            raise ProviderError("the model returned no text block")
        try:
            payload = json.loads(text)
        except ValueError:
            raise ProviderError("the model did not return JSON") from None
        return JsonResult(payload=payload, refused=False, **base)
