"""
Auto-EDA: turns a raw DataFrame into a structured profile the frontend can
render directly, without the user writing any analysis themselves.

Three pieces, each independently useful:
  - `compute_column_summaries` — per-column stats, type-aware (numeric
    columns get mean/std/quartiles, categorical get top values, datetime
    get a range).
  - `compute_correlations` — Pearson correlation matrix across numeric
    columns.
  - `suggest_charts` — rule-based chart recommendations from column types
    and cardinality. Like Phase 4's cleaning suggestions, this is
    heuristics, not an LLM — Phase 9 (AI Dashboard Generator) is where an
    AI model picks widgets using this as one input among others.
"""

import pandas as pd


def compute_column_summaries(df: pd.DataFrame) -> list[dict]:
    summaries = []
    total_rows = len(df)

    for column in df.columns:
        series = df[column]
        null_count = int(series.isna().sum())
        summary: dict = {
            "column": str(column),
            "count": total_rows,
            "null_count": null_count,
            "null_pct": round(null_count / total_rows * 100, 1) if total_rows else 0,
            "unique_count": int(series.nunique(dropna=True)),
        }

        if series.dtype.kind in ("i", "u", "f"):
            non_null = series.dropna()
            summary["type"] = "numeric"
            if not non_null.empty:
                summary["stats"] = {
                    "min": _safe_float(non_null.min()),
                    "max": _safe_float(non_null.max()),
                    "mean": _safe_float(non_null.mean()),
                    "median": _safe_float(non_null.median()),
                    "std": _safe_float(non_null.std()),
                    "q1": _safe_float(non_null.quantile(0.25)),
                    "q3": _safe_float(non_null.quantile(0.75)),
                }

        elif series.dtype.kind == "M":
            non_null = series.dropna()
            summary["type"] = "datetime"
            if not non_null.empty:
                summary["stats"] = {
                    "min": non_null.min().isoformat(),
                    "max": non_null.max().isoformat(),
                }

        else:
            summary["type"] = "categorical"
            value_counts = series.value_counts(dropna=True).head(10)
            summary["stats"] = {
                "top_values": [
                    {"value": str(idx), "count": int(count)}
                    for idx, count in value_counts.items()
                ]
            }

        summaries.append(summary)

    return summaries


def compute_correlations(df: pd.DataFrame, min_abs_correlation: float = 0.0) -> dict:
    numeric_df = df.select_dtypes(include=["number"])
    if numeric_df.shape[1] < 2:
        return {"columns": list(numeric_df.columns), "pairs": []}

    corr_matrix = numeric_df.corr(numeric_only=True)
    columns = list(corr_matrix.columns)

    pairs = []
    for i, col_a in enumerate(columns):
        for col_b in columns[i + 1 :]:
            value = corr_matrix.loc[col_a, col_b]
            if pd.isna(value):
                continue
            if abs(value) >= min_abs_correlation:
                pairs.append(
                    {"column_a": col_a, "column_b": col_b, "correlation": round(float(value), 3)}
                )

    pairs.sort(key=lambda p: abs(p["correlation"]), reverse=True)
    return {"columns": columns, "pairs": pairs}


def suggest_charts(column_summaries: list[dict], max_suggestions: int = 8) -> list[dict]:
    suggestions: list[dict] = []
    numeric_cols = [c["column"] for c in column_summaries if c["type"] == "numeric"]
    categorical_cols = [
        c["column"]
        for c in column_summaries
        if c["type"] == "categorical" and c["unique_count"] <= 20
    ]
    datetime_cols = [c["column"] for c in column_summaries if c["type"] == "datetime"]

    for col in numeric_cols:
        suggestions.append(
            {
                "chart_type": "histogram",
                "title": f"Distribution of {col}",
                "x": col,
                "y": None,
                "reason": "Numeric column — histogram shows its distribution",
            }
        )

    for col in categorical_cols:
        suggestions.append(
            {
                "chart_type": "bar",
                "title": f"Count by {col}",
                "x": col,
                "y": None,
                "reason": "Categorical column with ≤20 unique values — bar chart of counts",
            }
        )

    if datetime_cols and numeric_cols:
        suggestions.append(
            {
                "chart_type": "line",
                "title": f"{numeric_cols[0]} over {datetime_cols[0]}",
                "x": datetime_cols[0],
                "y": numeric_cols[0],
                "reason": "Datetime + numeric column — trend over time",
            }
        )

    if categorical_cols and numeric_cols:
        suggestions.append(
            {
                "chart_type": "grouped_bar",
                "title": f"Average {numeric_cols[0]} by {categorical_cols[0]}",
                "x": categorical_cols[0],
                "y": numeric_cols[0],
                "reason": "Categorical + numeric column — compare averages across groups",
            }
        )

    if len(numeric_cols) >= 2:
        suggestions.append(
            {
                "chart_type": "scatter",
                "title": f"{numeric_cols[0]} vs {numeric_cols[1]}",
                "x": numeric_cols[0],
                "y": numeric_cols[1],
                "reason": "Two numeric columns — scatter plot to check relationship",
            }
        )

    return suggestions[:max_suggestions]


def _safe_float(value) -> float | None:
    try:
        f = float(value)
        return f if f == f else None  # NaN check without importing math
    except (TypeError, ValueError):
        return None
