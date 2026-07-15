"""
Turns a widget's config_json + its dataset's DataFrame into the shape the
frontend needs to render it. Kept separate from the API layer so it's
independently testable (same pattern as Phase 4/5's services).
"""

import pandas as pd

_AGGREGATIONS = {
    "sum": lambda s: s.sum(),
    "avg": lambda s: s.mean(),
    "count": lambda s: s.count(),
    "min": lambda s: s.min(),
    "max": lambda s: s.max(),
}


def compute_kpi(df: pd.DataFrame, column: str, aggregation: str) -> dict:
    if aggregation not in _AGGREGATIONS:
        raise ValueError(f"Unsupported aggregation: {aggregation}")
    if column not in df.columns:
        raise ValueError(f"Column '{column}' not found")

    value = _AGGREGATIONS[aggregation](df[column])
    return {"value": _safe_scalar(value)}


def compute_table(df: pd.DataFrame, columns: list[str] | None, row_limit: int = 100) -> dict:
    subset = df[columns] if columns else df
    limited = subset.head(row_limit)
    return {
        "columns": list(map(str, limited.columns)),
        "rows": limited.astype(str).values.tolist(),
        "total_rows": len(subset),
    }


def compute_chart(
    df: pd.DataFrame,
    chart_type: str,
    x_column: str,
    y_column: str | None,
    aggregation: str,
) -> dict:
    if x_column not in df.columns:
        raise ValueError(f"Column '{x_column}' not found")
    if y_column and y_column not in df.columns:
        raise ValueError(f"Column '{y_column}' not found")
    if aggregation not in _AGGREGATIONS:
        raise ValueError(f"Unsupported aggregation: {aggregation}")

    if chart_type == "histogram" or y_column is None:
        # Distribution of x_column itself (e.g. count per category, or
        # value counts for a numeric column bucketed by raw value)
        grouped = df.groupby(x_column, dropna=True).size().reset_index(name="value")
        labels = grouped[x_column].astype(str).tolist()
        values = grouped["value"].tolist()
    else:
        grouped = df.groupby(x_column, dropna=True)[y_column].agg(_AGGREGATIONS[aggregation])
        labels = grouped.index.astype(str).tolist()
        values = [_safe_scalar(v) for v in grouped.tolist()]

    # Cap series length so a high-cardinality column doesn't blow up the
    # response — the frontend chart would be unreadable past this anyway
    max_points = 50
    return {
        "chart_type": chart_type,
        "labels": labels[:max_points],
        "values": values[:max_points],
        "truncated": len(labels) > max_points,
    }


def _safe_scalar(value):
    try:
        f = float(value)
        return f if f == f else None  # NaN check
    except (TypeError, ValueError):
        return str(value)
