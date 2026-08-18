"""
Turns a natural-language question + a dataset's schema into a SQL query.

The model only ever sees column names, inferred types, and a few sample
values (from Phase 3's schema_json) — never the full dataset. This keeps
token usage bounded regardless of dataset size and avoids sending
potentially sensitive row-level data to the LLM provider just to generate
a query shape.

The returned SQL is untrusted input from here on — the caller MUST run it
through app.services.sql_guard before execution. This module's only job is
generating a candidate query and a plain-language explanation of it.
"""

import json

from app.core.config import get_settings

_SYSTEM_PROMPT = """You translate a natural-language question into a single \
SQLite SELECT query against one table named `dataset`.

Rules:
- Output ONLY valid JSON: {"sql": "...", "explanation": "..."}
- The SQL must be a single SELECT statement (or WITH ... SELECT). Never \
INSERT, UPDATE, DELETE, DROP, ALTER, or any other statement type.
- Only reference columns that exist in the provided schema.
- Use standard SQLite syntax.
- "explanation" is one plain-language sentence describing what the query \
computes, written for someone who doesn't read SQL.
- If the question cannot be answered with the given schema, return \
{"sql": null, "explanation": "<why not, in plain language>"}.
"""


class NLToSQLError(RuntimeError):
    pass


def generate_sql(question: str, schema: list[dict]) -> dict:
    """
    Returns {"sql": str | None, "explanation": str}. `sql` is None when the
    model determined the question can't be answered from this schema.
    """
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise NLToSQLError(
            "ANTHROPIC_API_KEY is not set — add it to .env to use the AI Query Engine."
        )

    import anthropic

    schema_summary = [
        {
            "column": col["name"],
            "type": col["inferred_type"],
            "sample_values": col.get("sample_values", [])[:3],
        }
        for col in schema
    ]

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    response = client.messages.create(
        model=settings.ai_model,
        max_tokens=1024,
        system=_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Schema (table name: dataset):\n{json.dumps(schema_summary, indent=2)}\n\n"
                    f"Question: {question}"
                ),
            }
        ],
    )

    text = "".join(block.text for block in response.content if block.type == "text")
    return _parse_response(text)


def _parse_response(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise NLToSQLError(f"Model did not return valid JSON: {exc}") from exc

    if "sql" not in parsed or "explanation" not in parsed:
        raise NLToSQLError("Model response is missing 'sql' or 'explanation'")

    return parsed
