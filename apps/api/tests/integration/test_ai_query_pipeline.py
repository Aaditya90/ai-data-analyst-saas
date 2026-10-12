"""
Phase 17 (Testing) integration tests: the AI Query Engine pipeline
(app/services/nl_to_sql.py -> app/services/sql_guard.py), the core of
this codebase's "AI decides, code validates" split established since
Phase 7 (see ai_client.py's module docstring). Deliberately exercised at
the *service* layer rather than through the full `/query` HTTP route:
that route also touches Redis (response caching) and S3/MinIO (loading
the dataset file) — real external services this phase doesn't add fakes
for (they're not central to what Phase 17 is proving here, and both
already degrade gracefully on their own — see app/core/cache.py and
app/api/deps.py's `rate_limit`). What matters for this pipeline
specifically is: does a malicious or malformed AI response actually get
rejected by code, rather than trusted? That's fully testable without
either.

Needs: pip install -r requirements-dev.txt (anthropic in particular, for
app.services.ai_client's import to succeed even though the fake never
makes a real call).

Run with: pytest tests/integration/test_ai_query_pipeline.py -m integration
"""

import json

import pytest

from app.services.nl_to_sql import NLToSQLError, generate_sql
from app.services.sql_guard import UnsafeQueryError, validate_and_limit

pytestmark = pytest.mark.integration

_SCHEMA = [
    {"name": "region", "inferred_type": "string", "sample_values": ["East", "West"]},
    {"name": "revenue", "inferred_type": "float", "sample_values": [100.0, 250.5]},
]


def test_well_behaved_ai_response_produces_a_query_the_guard_accepts(fake_ai_client):
    fake_ai_client.default_response = (
        '{"sql": "SELECT region, SUM(revenue) FROM dataset GROUP BY region", '
        '"explanation": "Total revenue per region."}'
    )

    generated = generate_sql("What is revenue by region?", _SCHEMA)
    safe_sql = validate_and_limit(generated["sql"])

    assert "SELECT" in safe_sql.upper()
    assert "LIMIT" in safe_sql.upper()  # sql_guard enforces a row limit even when the model didn't ask for one

    # The prompt actually sent to "Claude" is inspectable via the fake —
    # confirms nl_to_sql really did call through ai_client.complete()
    # rather than, say, silently short-circuiting.
    assert len(fake_ai_client.calls) == 1
    assert fake_ai_client.calls[0]["max_tokens"] == 1024


@pytest.mark.parametrize(
    "malicious_sql",
    [
        "DROP TABLE dataset",
        "SELECT * FROM dataset; DROP TABLE dataset;",
        "DELETE FROM dataset WHERE 1=1",
        "ATTACH DATABASE '/etc/passwd' AS pwn",
        "UPDATE dataset SET revenue = 0",
    ],
)
def test_guard_rejects_every_non_select_the_ai_might_return(fake_ai_client, malicious_sql):
    # Simulates the AI being prompt-injected or simply misbehaving — the
    # system prompt tells it to only ever emit SELECT, but nothing about
    # this pipeline can rely on the model actually obeying that. sql_guard
    # is the real boundary (see its own module docstring); this test is
    # exactly what would fail if someone "simplified" nl_to_sql.py to
    # trust the model's output directly.
    fake_ai_client.default_response = json.dumps({"sql": malicious_sql, "explanation": "ignored"})

    generated = generate_sql("do something", _SCHEMA)
    with pytest.raises(UnsafeQueryError):
        validate_and_limit(generated["sql"])


def test_ai_declining_to_answer_is_surfaced_not_treated_as_an_error(fake_ai_client):
    fake_ai_client.default_response = (
        '{"sql": null, "explanation": "The schema has no date column to answer a time-based question."}'
    )

    generated = generate_sql("What was revenue last Tuesday?", _SCHEMA)

    assert generated["sql"] is None
    assert "date" in generated["explanation"].lower()


def test_non_json_ai_response_raises_nl_to_sql_error_not_a_crash(fake_ai_client):
    fake_ai_client.default_response = "Sure! Here's a query: SELECT * FROM dataset"

    with pytest.raises(NLToSQLError):
        generate_sql("anything", _SCHEMA)


def test_ai_response_wrapped_in_markdown_fence_is_still_parsed(fake_ai_client):
    fake_ai_client.default_response = (
        "```json\n"
        '{"sql": "SELECT region FROM dataset", "explanation": "Lists regions."}\n'
        "```"
    )

    generated = generate_sql("list regions", _SCHEMA)
    assert generated["sql"] == "SELECT region FROM dataset"


def test_not_configured_raises_before_any_ai_call(monkeypatch, fake_ai_client):
    # No fake_ai_client call recorded at all — confirms the missing-key
    # check happens first, not after an attempted (and failed) API call.
    from app.core.config import get_settings

    get_settings().anthropic_api_key = ""

    with pytest.raises(NLToSQLError):
        generate_sql("anything", _SCHEMA)

    assert fake_ai_client.calls == []
