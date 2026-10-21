import json
import logging

from app.core.logging import REDACTED, RedactingFormatter, redact, redact_text


def test_redact_masks_sensitive_keys_recursively():
    data = {"user": "a", "password": "p", "nested": {"Authorization": "Bearer abc", "ok": 1},
            "items": [{"refresh_token": "t"}, "plain"]}
    out = redact(data)
    assert out["password"] == REDACTED
    assert out["nested"]["Authorization"] == REDACTED
    assert out["items"][0]["refresh_token"] == REDACTED
    assert out["user"] == "a" and out["nested"]["ok"] == 1


def test_redact_text_masks_secret_looking_substrings():
    text = "GET /x?access_token=SECRET123&a=1 Authorization: Bearer abc.def.ghi header OAuth ZZZ"
    out = redact_text(text)
    assert "SECRET123" not in out and "abc.def.ghi" not in out
    assert "a=1" in out


def test_formatter_redacts_message_extra_and_exception():
    handler_record = logging.LogRecord(
        "t", logging.ERROR, __file__, 1, "token refresh_token=abc failed", None, None
    )
    handler_record.data = {"password": "hunter2", "k": "v"}
    try:
        raise RuntimeError("boom access_token=LEAK")
    except RuntimeError:
        import sys

        handler_record.exc_info = sys.exc_info()
    line = RedactingFormatter().format(handler_record)
    assert "hunter2" not in line and "LEAK" not in line and "abc" not in line
    assert json.loads(line)["data"]["k"] == "v"
