"""
Standalone unit tests for Phase 16 (Observability).

app.core.request_context and app.core.logging_config.JSONFormatter are
deliberately dependency-free (stdlib only — see both modules' docstrings),
so this file's tests for them import the real modules and actually run,
unlike most of this codebase's tests in a from-scratch sandbox with no
network access to install FastAPI/SQLAlchemy/etc. The ai_client.py and
error_tracking.py pieces (which do need `anthropic`/`sentry_sdk`/
pydantic-settings) are covered by equivalent logic, verified the same way
as other phases — see README.md "Testing Phase 16".

Run with: python tests/test_observability.py
"""

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.logging_config import JSONFormatter
from app.core.request_context import get_request_id, new_request_id, reset_request_id, set_request_id


def _make_record(msg="hello", level=logging.INFO, extra=None, exc_info=None) -> logging.LogRecord:
    record = logging.LogRecord(
        name="app.test",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=exc_info,
    )
    for key, value in (extra or {}).items():
        setattr(record, key, value)
    return record


def test_new_request_id_is_a_nonempty_hex_string():
    rid = new_request_id()
    assert isinstance(rid, str)
    assert len(rid) == 32  # uuid4().hex
    int(rid, 16)  # raises if not valid hex


def test_request_id_context_set_get_reset_round_trip():
    assert get_request_id() is None  # nothing set yet in this test's context
    token = set_request_id("abc123")
    try:
        assert get_request_id() == "abc123"
    finally:
        reset_request_id(token)
    assert get_request_id() is None


def test_json_formatter_produces_valid_json_with_core_fields():
    formatter = JSONFormatter()
    line = formatter.format(_make_record(msg="hello world"))
    payload = json.loads(line)  # raises if not valid JSON

    assert payload["message"] == "hello world"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert "timestamp" in payload


def test_json_formatter_includes_request_id_when_set():
    token = set_request_id("req-42")
    try:
        formatter = JSONFormatter()
        payload = json.loads(formatter.format(_make_record()))
        assert payload["request_id"] == "req-42"
    finally:
        reset_request_id(token)


def test_json_formatter_omits_request_id_when_unset():
    assert get_request_id() is None
    formatter = JSONFormatter()
    payload = json.loads(formatter.format(_make_record()))
    assert "request_id" not in payload


def test_json_formatter_includes_extra_fields():
    formatter = JSONFormatter()
    record = _make_record(extra={"http_status": 200, "duration_ms": 12.5})
    payload = json.loads(formatter.format(record))
    assert payload["http_status"] == 200
    assert payload["duration_ms"] == 12.5


def test_json_formatter_does_not_duplicate_standard_fields():
    # `module`, `funcName`, etc. are standard LogRecord attributes that
    # must NOT be re-emitted as if they were caller-supplied `extra` data
    # — only genuinely custom fields should show up beyond the fixed set.
    formatter = JSONFormatter()
    payload = json.loads(formatter.format(_make_record()))
    expected_keys = {"timestamp", "level", "logger", "message"}
    assert set(payload.keys()) == expected_keys


def test_json_formatter_captures_exception_info():
    try:
        raise ValueError("boom")
    except ValueError:
        record = _make_record(msg="something failed", level=logging.ERROR, exc_info=sys.exc_info())

    formatter = JSONFormatter()
    payload = json.loads(formatter.format(record))
    assert "exception" in payload
    assert "ValueError" in payload["exception"]
    assert "boom" in payload["exception"]


if __name__ == "__main__":
    test_new_request_id_is_a_nonempty_hex_string()
    test_request_id_context_set_get_reset_round_trip()
    test_json_formatter_produces_valid_json_with_core_fields()
    test_json_formatter_includes_request_id_when_set()
    test_json_formatter_omits_request_id_when_unset()
    test_json_formatter_includes_extra_fields()
    test_json_formatter_does_not_duplicate_standard_fields()
    test_json_formatter_captures_exception_info()
    print("All Phase 16 unit tests passed.")
