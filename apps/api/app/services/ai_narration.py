"""
Takes the statistically-ranked insights from insights.py and asks Claude to
phrase each one as a plain-language sentence.

Critical design point: the model is given the exact numbers already
computed (mean, z-score, correlation coefficient, % change, etc.) and told
to phrase *those specific numbers*, not to compute or estimate anything
itself. This is what keeps the AI from hallucinating statistics — its only
job here is natural-language phrasing, the same "AI narrates, doesn't
decide" split used by insights.py's docstring.

Phase 10 (ML & Forecasting) extends this file with two more narrators —
narrate_model_result() and narrate_forecast() — rather than starting a new
service file, since the exact same contract applies: hand Claude numbers
that were already computed by automl.py/forecasting.py, ask it only to
phrase them, and fall back to a template if the call fails or no API key
is set.
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


_MODEL_RESULT_SYSTEM_PROMPT = """You write a short plain-language summary of a \
trained machine-learning model's results, for a business audience with no \
statistics or ML background.

Rules:
- Output ONLY valid JSON: an object with keys "summary" (2-4 sentences) and \
"top_drivers" (1-2 sentences about the most important input columns).
- Use ONLY the numbers given to you (task type, algorithm, metrics, \
feature importances). Never estimate, round differently than given, or \
invent any number not present in the input.
- No jargon like "R-squared", "F1 score", or "macro-averaged" — describe \
what the numbers mean in plain terms instead (e.g. "explains about 80% of \
the variation", "correctly classified about 92% of cases").
- Be honest about model quality: if the metrics are weak, say so plainly \
rather than overselling the result.
"""

_FORECAST_SYSTEM_PROMPT = """You write a short plain-language summary of a \
time-series forecast, for a business audience with no statistics background.

Rules:
- Output ONLY valid JSON: an object with keys "summary" (2-4 sentences \
describing the trend/seasonality and where the forecast is headed) and \
"confidence_note" (1 sentence about how reliable the forecast is, based on \
the backtest accuracy given).
- Use ONLY the numbers given to you (frequency, method, forecast points, \
backtest metrics). Never estimate, round differently than given, or invent \
any number not present in the input.
- No jargon like "MAPE" or "RMSE" — describe accuracy in plain terms \
instead (e.g. "predictions were typically off by about 8%").
- If backtest metrics are missing (not enough history), say the forecast \
is unvalidated rather than claiming an accuracy figure.
"""


def narrate_model_result(model_summary: dict) -> dict:
    """
    `model_summary` should contain task_type, algorithm, metrics,
    feature_importance (top few entries only — see app/api/ml_models.py).
    Returns {"summary": str, "top_drivers": str}. Raises NarrationError if
    the model can't be reached or returns something unusable.
    """
    from app.core.config import get_settings

    settings = get_settings()
    if not settings.anthropic_api_key:
        raise NarrationError("ANTHROPIC_API_KEY is not set")

    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    response = client.messages.create(
        model=settings.ai_model,
        max_tokens=512,
        system=_MODEL_RESULT_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(model_summary, indent=2)}],
    )
    text = "".join(block.text for block in response.content if block.type == "text")
    return _parse_narration_object(text, required_keys=("summary", "top_drivers"))


def fallback_model_narration(model_summary: dict) -> dict:
    """Template-based summary used when the AI call fails or is
    unavailable — a model result is always described, degraded but never
    broken."""
    task_type = model_summary["task_type"]
    algorithm = model_summary["algorithm"].replace("_", " ")
    metrics = model_summary["metrics"]
    top_features = model_summary.get("feature_importance", [])[:3]

    if task_type == "regression":
        summary = (
            f"A {algorithm} model was trained to predict {model_summary['target_column']}. "
            f"It explains about {round(metrics['r2'] * 100)}% of the variation in the data, "
            f"with an average prediction error of about {metrics['mae']} "
            f"({model_summary['target_column']} units)."
        )
    else:
        summary = (
            f"A {algorithm} model was trained to predict {model_summary['target_column']}. "
            f"It correctly classified about {round(metrics['accuracy'] * 100)}% of cases "
            f"in testing."
        )

    if top_features:
        names = ", ".join(f["column"] for f in top_features)
        top_drivers = f"The columns that mattered most to the model were: {names}."
    else:
        top_drivers = "No single column stood out as a dominant driver."

    return {"summary": summary, "top_drivers": top_drivers}


def narrate_forecast(forecast_summary: dict) -> dict:
    """
    `forecast_summary` should contain frequency, method, a few representative
    forecast points, and metrics (or None). Returns {"summary": str,
    "confidence_note": str}. Raises NarrationError if the model can't be
    reached or returns something unusable.
    """
    from app.core.config import get_settings

    settings = get_settings()
    if not settings.anthropic_api_key:
        raise NarrationError("ANTHROPIC_API_KEY is not set")

    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    response = client.messages.create(
        model=settings.ai_model,
        max_tokens=512,
        system=_FORECAST_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(forecast_summary, indent=2)}],
    )
    text = "".join(block.text for block in response.content if block.type == "text")
    return _parse_narration_object(text, required_keys=("summary", "confidence_note"))


def fallback_forecast_narration(forecast_summary: dict) -> dict:
    """Template-based summary used when the AI call fails or is
    unavailable."""
    points = forecast_summary["forecast"]
    first, last = points[0], points[-1]
    direction = "rising" if last["forecast"] > first["forecast"] else "falling"
    if abs(last["forecast"] - first["forecast"]) < 1e-9:
        direction = "flat"

    summary = (
        f"The forecast projects {len(points)} {forecast_summary['frequency']} period(s) ahead, "
        f"starting around {first['forecast']} and {direction} to about {last['forecast']} "
        f"by the end of the forecast window."
    )

    metrics = forecast_summary.get("metrics")
    if metrics and metrics.get("mape") is not None:
        confidence_note = (
            f"In backtesting on recent history, forecasts were typically off by about "
            f"{metrics['mape']}%."
        )
    elif metrics:
        confidence_note = (
            f"In backtesting on recent history, forecasts were off by about {metrics['mae']} "
            f"on average."
        )
    else:
        confidence_note = "There wasn't enough history to backtest this forecast's accuracy."

    return {"summary": summary, "confidence_note": confidence_note}


def _parse_narration_object(text: str, required_keys: tuple[str, ...]) -> dict:
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

    if not isinstance(parsed, dict) or not all(k in parsed for k in required_keys):
        raise NarrationError(f"Expected an object with keys {required_keys}, got: {parsed!r}")

    return {k: str(parsed[k]) for k in required_keys}
