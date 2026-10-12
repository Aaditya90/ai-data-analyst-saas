"""
Finds candidate insights in a DataFrame using plain statistics, and ranks
them by a significance score — the AI's job (see ai_narration.py) is only
to phrase these in plain language, never to decide what's significant.
This split is deliberate: "which findings matter" should be reproducible
and auditable, not dependent on an LLM's mood that day.

Four insight types, each with its own significance score in [0, 1]-ish
range so they can be sorted together:
  - outlier:     numeric column has values far from the mean (z-score)
  - correlation: two numeric columns move together
  - imbalance:   a categorical column is dominated by one value
  - trend:       a numeric column trends up/down over a datetime column
"""

import pandas as pd


def detect_insights(df: pd.DataFrame, max_insights: int = 8) -> list[dict]:
    candidates: list[dict] = []
    candidates.extend(_detect_outliers(df))
    candidates.extend(_detect_correlations(df))
    candidates.extend(_detect_imbalances(df))
    candidates.extend(_detect_trends(df))

    candidates.sort(key=lambda c: c["significance"], reverse=True)
    return candidates[:max_insights]


def _detect_outliers(df: pd.DataFrame) -> list[dict]:
    insights = []
    for col in df.select_dtypes(include="number").columns:
        series = df[col].dropna()
        if len(series) < 5:
            continue
        mean, std = series.mean(), series.std()
        if std == 0 or pd.isna(std):
            continue

        z_scores = (series - mean) / std
        outliers = series[z_scores.abs() > 2]
        if outliers.empty:
            continue

        outlier_pct = len(outliers) / len(series)
        worst_idx = z_scores.abs().idxmax()
        insights.append(
            {
                "insight_type": "outlier",
                "column": col,
                "significance": min(1.0, outlier_pct * 5 + 0.3),
                "stats": {
                    "outlier_count": int(len(outliers)),
                    "outlier_pct": round(outlier_pct * 100, 1),
                    "mean": round(float(mean), 2),
                    "std": round(float(std), 2),
                    "most_extreme_value": round(float(series.loc[worst_idx]), 2),
                    "most_extreme_z_score": round(float(z_scores.loc[worst_idx]), 2),
                },
            }
        )
    return insights


def _detect_correlations(df: pd.DataFrame) -> list[dict]:
    numeric = df.select_dtypes(include="number")
    if numeric.shape[1] < 2:
        return []

    corr = numeric.corr(numeric_only=True)
    insights = []
    seen = set()
    for col_a in corr.columns:
        for col_b in corr.columns:
            if col_a == col_b or (col_b, col_a) in seen:
                continue
            seen.add((col_a, col_b))
            value = corr.loc[col_a, col_b]
            if pd.isna(value) or abs(value) < 0.5:
                continue
            insights.append(
                {
                    "insight_type": "correlation",
                    "column": f"{col_a}, {col_b}",
                    "significance": abs(float(value)),
                    "stats": {
                        "column_a": col_a,
                        "column_b": col_b,
                        "correlation": round(float(value), 3),
                        "direction": "positive" if value > 0 else "negative",
                    },
                }
            )
    return insights


def _detect_imbalances(df: pd.DataFrame) -> list[dict]:
    insights = []
    for col in df.select_dtypes(include="object").columns:
        series = df[col].dropna()
        if series.empty or series.nunique() < 2:
            continue
        counts = series.value_counts(normalize=True)
        top_value, top_pct = counts.index[0], counts.iloc[0]
        if top_pct < 0.6:
            continue
        insights.append(
            {
                "insight_type": "imbalance",
                "column": col,
                "significance": min(1.0, float(top_pct)),
                "stats": {
                    "dominant_value": str(top_value),
                    "dominant_pct": round(float(top_pct) * 100, 1),
                    "unique_values": int(series.nunique()),
                },
            }
        )
    return insights


def _detect_trends(df: pd.DataFrame) -> list[dict]:
    datetime_cols = df.select_dtypes(include="datetime").columns
    numeric_cols = df.select_dtypes(include="number").columns
    if len(datetime_cols) == 0 or len(numeric_cols) == 0:
        return []

    insights = []
    date_col = datetime_cols[0]
    ordered = df[[date_col]].join(df[numeric_cols]).dropna(subset=[date_col]).sort_values(date_col)
    if len(ordered) < 5:
        return []

    x = (ordered[date_col] - ordered[date_col].min()).dt.days.astype(float)
    if x.max() == 0:
        return []

    for col in numeric_cols:
        y = ordered[col]
        valid = y.notna()
        if valid.sum() < 5:
            continue
        # Simple linear trend via least squares — no scipy dependency needed
        x_valid, y_valid = x[valid], y[valid]
        slope, intercept = _linear_fit(x_valid, y_valid)
        y_pred = slope * x_valid + intercept
        ss_res = ((y_valid - y_pred) ** 2).sum()
        ss_tot = ((y_valid - y_valid.mean()) ** 2).sum()
        r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0

        if r_squared < 0.3:
            continue  # trend isn't clean enough to be worth surfacing

        pct_change = (
            (y_pred.iloc[-1] - y_pred.iloc[0]) / abs(y_pred.iloc[0]) * 100
            if y_pred.iloc[0] != 0
            else 0
        )
        insights.append(
            {
                "insight_type": "trend",
                "column": col,
                "significance": float(r_squared),
                "stats": {
                    "direction": "increasing" if slope > 0 else "decreasing",
                    "r_squared": round(float(r_squared), 2),
                    "pct_change_over_period": round(float(pct_change), 1),
                    "date_column": date_col,
                },
            }
        )
    return insights


def _linear_fit(x: pd.Series, y: pd.Series) -> tuple[float, float]:
    x_mean, y_mean = x.mean(), y.mean()
    numerator = ((x - x_mean) * (y - y_mean)).sum()
    denominator = ((x - x_mean) ** 2).sum()
    if denominator == 0:
        return 0.0, y_mean
    slope = numerator / denominator
    intercept = y_mean - slope * x_mean
    return float(slope), float(intercept)
