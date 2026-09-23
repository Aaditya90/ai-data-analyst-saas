"""
"Generate a dashboard for me" — turns a dataset's EDA profile (Phase 5)
into a curated set of Dashboard/DashboardWidget rows (Phase 6's models).

Same safety pattern as Phase 7/8: the AI never invents a column reference
or a widget type. It only picks indices from a pre-built candidate list
(every candidate references a column that's verified to exist) and writes
titles — the same "AI narrates/selects, code decides what's valid" split
used throughout Phases 7-8. If the AI call fails, `select_fallback` picks a
reasonable default set with zero LLM involvement, so this endpoint
degrades gracefully rather than breaking.

Grid layout (x/y/w/h) is computed by `pack_layout`, not the AI — asking an
LLM to do 12-column grid arithmetic is a waste of tokens and an
unnecessary failure mode when a simple deterministic packer does it
perfectly every time.
"""

import json

KPI_SIZE = (3, 2)     # w, h
CHART_SIZE = (6, 4)
TABLE_SIZE = (12, 4)
GRID_COLS = 12

_SYSTEM_PROMPT = """You curate a dashboard from a list of candidate widgets \
for a dataset, choosing the most useful subset and writing good titles.

Rules:
- Output ONLY valid JSON: {"dashboard_name": "...", "widgets": [{"candidate_index": N, "title": "..."}, ...]}
- Choose between 4 and 6 candidates. Prefer variety (not six KPIs) and \
prioritize candidates that reveal something informative (imbalances, \
relationships, notable distributions) over redundant ones.
- "candidate_index" must be one of the indices given in the candidate list.
- Titles should be short, plain-language, and specific (e.g. "Revenue by \
Region" not "Chart 1").
- "dashboard_name" is a short, descriptive name for the whole dashboard \
based on what the data appears to be about.
"""


class DashboardGeneratorError(RuntimeError):
    pass


def build_candidates(column_summaries: list[dict], chart_suggestions: list[dict]) -> list[dict]:
    """
    Every candidate is a fully-formed widget spec (type + config) — the AI
    (or the fallback) only ever selects among these, never constructs one
    itself. This is what makes an invalid column reference impossible
    regardless of what the AI returns.
    """
    candidates: list[dict] = []

    for col in column_summaries:
        if col["type"] == "numeric" and col.get("stats"):
            candidates.append(
                {
                    "widget_type": "kpi",
                    "title": f"Average {col['column']}",
                    "config_json": {"column": col["column"], "aggregation": "avg"},
                    "reason": "numeric column summary",
                }
            )

    for chart in chart_suggestions:
        candidates.append(
            {
                "widget_type": "chart",
                "title": chart["title"],
                "config_json": {
                    "chart_type": chart["chart_type"] if chart["chart_type"] != "histogram" else "bar",
                    "x_column": chart["x"],
                    "y_column": chart["y"],
                    "aggregation": "avg" if chart["y"] else "count",
                },
                "reason": chart["reason"],
            }
        )

    candidates.append(
        {
            "widget_type": "table",
            "title": "Data preview",
            "config_json": {"columns": []},
            "reason": "raw data view",
        }
    )

    return candidates


def select_fallback(candidates: list[dict], max_widgets: int = 6) -> dict:
    """Zero-AI default: a couple of KPIs, a few charts, and the table —
    used when the AI call fails or is unavailable."""
    kpis = [c for c in candidates if c["widget_type"] == "kpi"][:2]
    charts = [c for c in candidates if c["widget_type"] == "chart"][:3]
    tables = [c for c in candidates if c["widget_type"] == "table"][:1]
    selected = (kpis + charts + tables)[:max_widgets]
    return {"dashboard_name": "Auto-generated dashboard", "widgets": selected}


def ai_select_widgets(candidates: list[dict], dataset_name: str) -> dict:
    """
    Returns {"dashboard_name": str, "widgets": [candidate, ...]} using
    Claude to pick and title a subset. Raises DashboardGeneratorError on
    any failure — callers should fall back to select_fallback rather than
    fail the request (see app/api/dashboard_generator.py).
    """
    from app.core.config import get_settings

    settings = get_settings()
    if not settings.anthropic_api_key:
        raise DashboardGeneratorError("ANTHROPIC_API_KEY is not set")

    import anthropic

    indexed = [
        {"index": i, "widget_type": c["widget_type"], "reason": c["reason"], "default_title": c["title"]}
        for i, c in enumerate(candidates)
    ]

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    response = client.messages.create(
        model=settings.ai_model,
        max_tokens=1024,
        system=_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"Dataset: {dataset_name}\n\nCandidates:\n{json.dumps(indexed, indent=2)}",
            }
        ],
    )

    text = "".join(block.text for block in response.content if block.type == "text")
    return _parse_and_validate(text, candidates)


def _parse_and_validate(text: str, candidates: list[dict]) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise DashboardGeneratorError(f"Model did not return valid JSON: {exc}") from exc

    if "dashboard_name" not in parsed or "widgets" not in parsed:
        raise DashboardGeneratorError("Response missing 'dashboard_name' or 'widgets'")

    resolved = []
    for entry in parsed["widgets"]:
        idx = entry.get("candidate_index")
        if not isinstance(idx, int) or idx < 0 or idx >= len(candidates):
            continue  # silently skip invalid indices rather than fail the whole request
        widget = dict(candidates[idx])
        if entry.get("title"):
            widget["title"] = str(entry["title"])
        resolved.append(widget)

    if not resolved:
        raise DashboardGeneratorError("Model did not select any valid candidates")

    return {"dashboard_name": str(parsed["dashboard_name"]), "widgets": resolved[:6]}


def pack_layout(widgets: list[dict]) -> list[dict]:
    """
    Deterministic grid packer — assigns x/y/w/h to each widget in order.
    KPIs pack 4-per-row, charts 2-per-row, tables take the full row. Not
    the AI's job; see module docstring.
    """
    positioned = []
    cursor_x, cursor_y, row_height = 0, 0, 0

    for widget in widgets:
        w, h = {
            "kpi": KPI_SIZE,
            "chart": CHART_SIZE,
            "table": TABLE_SIZE,
        }.get(widget["widget_type"], CHART_SIZE)

        if cursor_x + w > GRID_COLS:
            cursor_x = 0
            cursor_y += row_height
            row_height = 0

        positioned.append({**widget, "x": cursor_x, "y": cursor_y, "w": w, "h": h})
        cursor_x += w
        row_height = max(row_height, h)

    return positioned
