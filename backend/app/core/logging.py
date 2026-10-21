import json
import logging
import re
from contextvars import ContextVar
from typing import Any

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

SENSITIVE_KEYS = {
    "password", "new_password", "token", "access_token", "refresh_token", "authorization",
    "cookie", "set-cookie", "secret", "api_key", "app_secret", "jwt_secret", "x-api-key",
}
_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*)(bearer|oauth)?\s*[^\s,;]+"),
    re.compile(r"(?i)((?:access_token|refresh_token|password|api_key|secret)=)[^&\s]+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+"),
]
REDACTED = "***"


def redact_text(text: str) -> str:
    for pattern in _PATTERNS:
        text = pattern.sub(lambda m: f"{m.group(1)}{REDACTED}", text)
    return text


def redact(value: Any) -> Any:
    """Recursively mask sensitive keys and secret-looking substrings."""
    if isinstance(value, dict):
        return {
            k: REDACTED if str(k).lower() in SENSITIVE_KEYS else redact(v) for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": request_id_var.get(),
            "message": redact_text(record.getMessage()),
        }
        extra = getattr(record, "data", None)
        if extra:
            payload["data"] = redact(extra)
        if record.exc_info:
            payload["exc"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
