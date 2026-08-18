"""
Takes the statistically-ranked insights from insights.py and asks Claude to
phrase each one as a plain-language sentence.

Critical design point: the model is given the exact numbers already
computed (mean, z-score, correlation coefficient, % change, etc.) and told
to phrase *those specific numbers*, not to compute or estimate anything
itself. This is what keeps the AI from hallucinating statistics — its only
job here is natural-language phrasing, the same "AI narrates, doesn't
decide" split used by insights.py's docstring.
"""

import json

_SYSTEM_PROMPT = """You write one plain-language sentence per data insight, \
for a business audience with no statistics background.

Rules:
- Output ONLY valid JSON: a list of strings, one per insight, in the same \
order as the input.
- Use ONLY the numbers given to you in each insight's "stats" object. \
Never estimate, round differently than given, or invent any number not \
present in the input.
- Keep each sentence under 30 words. No jargon like "z-score" or \
"r-squared" — describe what it means in plain terms instead.
- Do not hedge or add disclaimers — state the finding directly.
"""


class NarrationError(RuntimeError):
    pass


def narrate_insights(insights: list[dict]) -> list[str]:
    """
    Returns one narration string per insight, same order as input. Raises
    NarrationError if the model can't be reached or returns something
    unusable — callers should fall back to a template-based description
    rather than fail the whole request (see app/api/insights.py).
    """
    if not insights:
        return []

    from app.core.config import get_settings

    settings = get_settings()
    if not settings.anthropic_api_key:
        raise NarrationError("ANTHROPIC_API_KEY is not set")

    import anthropic

    payload = [
        {"type": i["insight_type"], "column": i["column"], "stats": i["stats"]}
        for i in insights
    ]

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    response = client.messages.create(
        model=settings.ai_model,
        max_tokens=1024,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(payload, indent=2)}],
    )

    text = "".join(block.text for block in response.content if block.type == "text")
    return _parse_narrations(text, expected_count=len(insights))


def _parse_narrations(text: str, expected_count: int) -> list[str]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise NarrationError(f"Model did not return valid JSON: {exc}") from exc

    if not isinstance(parsed, list) or len(parsed) != expected_count:
        raise NarrationError(
            f"Expected a list of {expected_count} strings, got: {type(parsed).__name__}"
        )

    return [str(s) for s in parsed]


def fallback_narration(insight: dict) -> str:
    """
    Template-based description used when the AI call fails or is
    unavailable — every insight always has *some* description, degraded
    but never broken.
    """
    t, col, s = insight["insight_type"], insight["column"], insight["stats"]

    if t == "outlier":
        return (
            f"{col} has {s['outlier_count']} unusual value(s) "
            f"({s['outlier_pct']}% of rows), including one far from the "
            f"typical range of about {s['mean']}."
        )
    if t == "correlation":
        direction_phrase = (
            f"{s['column_a']} and {s['column_b']} tend to move in the same direction"
            if s["direction"] == "positive"
            else f"{s['column_a']} and {s['column_b']} tend to move in opposite directions"
        )
        return f"{direction_phrase} (correlation {s['correlation']})."
    if t == "imbalance":
        return f"{s['dominant_pct']}% of {col} values are '{s['dominant_value']}'."
    if t == "trend":
        return (
            f"{col} has been {s['direction']} over time, changing about "
            f"{s['pct_change_over_period']}% over the period covered."
        )
    return f"Notable pattern found in {col}."
