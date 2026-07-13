"""
Two responsibilities, kept separate on purpose:

  1. `detect_issues(df)` — profiles a DataFrame and returns a rule-based
     list of problems found (nulls, duplicates, outliers, mixed types) plus
     a *suggested* operation for each. These suggestions are heuristics
     (IQR for outliers, majority-type mismatch for type issues) — not an
     LLM call. Real AI-driven suggestions (reading column semantics,
     domain-aware imputation) are Phase 8 (AI Insights); this phase gives
     the AI something correct and well-labeled to build on.

  2. `apply_operations(df, operations)` — actually mutates a DataFrame
     according to a list of *user-approved* operations, and returns both
     the cleaned DataFrame and a log of what changed (for lineage). Nothing
     in this module writes to the database or object storage — it's pure
     pandas in, pandas + log out, so it's easy to unit test.

Nothing here is auto-applied. The API layer always requires an explicit
list of operations from the caller — see the module docstring in
app/api/cleaning.py for why "AI-suggested, human-approved" is the rule.
"""

from typing import Any

import pandas as pd


# --- Issue detection -------------------------------------------------

def detect_issues(df: pd.DataFrame) -> list[dict]:
    issues: list[dict] = []

    duplicate_count = int(df.duplicated().sum())
    if duplicate_count > 0:
        issues.append(
            {
                "issue_type": "duplicate_rows",
                "column": None,
                "severity": "medium",
                "description": f"{duplicate_count} fully duplicate row(s) found",
                "affected_count": duplicate_count,
                "suggested_operation": {"op_type": "drop_duplicate_rows", "column": None, "params": {}},
            }
        )

    for column in df.columns:
        series = df[column]
        null_count = int(series.isna().sum())
        if null_count > 0:
            null_pct = round(null_count / len(df) * 100, 1)
            severity = "high" if null_pct > 30 else "medium" if null_pct > 5 else "low"
            strategy = "mode" if series.dtype.kind == "O" else "median"
            issues.append(
                {
                    "issue_type": "missing_values",
                    "column": column,
                    "severity": severity,
                    "description": f"{null_count} missing value(s) ({null_pct}%)",
                    "affected_count": null_count,
                    "suggested_operation": {
                        "op_type": "fill_nulls",
                        "column": column,
                        "params": {"strategy": strategy},
                    },
                }
            )

        if series.dtype.kind in ("i", "u", "f"):
            outlier_idx = _iqr_outlier_index(series, multiplier=1.5)
            if len(outlier_idx) > 0:
                issues.append(
                    {
                        "issue_type": "outliers",
                        "column": column,
                        "severity": "low",
                        "description": f"{len(outlier_idx)} value(s) outside 1.5x IQR",
                        "affected_count": len(outlier_idx),
                        "suggested_operation": {
                            "op_type": "remove_outliers",
                            "column": column,
                            "params": {"method": "iqr", "multiplier": 1.5},
                        },
                    }
                )

        if series.dtype.kind == "O":
            non_null = series.dropna().astype(str)
            has_whitespace = non_null.apply(lambda v: v != v.strip()).sum()
            if has_whitespace > 0:
                issues.append(
                    {
                        "issue_type": "untrimmed_whitespace",
                        "column": column,
                        "severity": "low",
                        "description": f"{has_whitespace} value(s) have leading/trailing whitespace",
                        "affected_count": int(has_whitespace),
                        "suggested_operation": {
                            "op_type": "trim_whitespace",
                            "column": column,
                            "params": {},
                        },
                    }
                )

    return issues


def _iqr_outlier_index(series: pd.Series, multiplier: float = 1.5):
    non_null = series.dropna()
    if len(non_null) < 4:
        return non_null.index[:0]
    q1, q3 = non_null.quantile(0.25), non_null.quantile(0.75)
    iqr = q3 - q1
    if iqr == 0:
        return non_null.index[:0]
    lower, upper = q1 - multiplier * iqr, q3 + multiplier * iqr
    return non_null[(non_null < lower) | (non_null > upper)].index


# --- Operation application -------------------------------------------

SUPPORTED_OPERATIONS = {
    "drop_duplicate_rows",
    "drop_null_rows",
    "fill_nulls",
    "remove_outliers",
    "trim_whitespace",
    "cast_type",
}

_CAST_MAP = {
    "integer": "Int64",  # pandas nullable integer — plain int64 can't hold NaN
    "float": "float64",
    "string": "string",
    "boolean": "boolean",
    "datetime": "datetime64[ns]",
}


def apply_operations(df: pd.DataFrame, operations: list[dict]) -> tuple[pd.DataFrame, list[dict]]:
    """
    Applies operations in the given order. Returns (cleaned_df, log) where
    log is a list of {"op_type", "column", "params", "rows_affected"} —
    this is exactly what gets stored as DatasetVersion.transformations_applied.
    """
    working = df.copy()
    log: list[dict] = []

    for op in operations:
        op_type = op.get("op_type")
        column = op.get("column")
        params = op.get("params", {}) or {}

        if op_type not in SUPPORTED_OPERATIONS:
            raise ValueError(f"Unsupported operation: {op_type}")
        if column is not None and column not in working.columns:
            raise ValueError(f"Column '{column}' not found in dataset")

        before_rows = len(working)
        before_nulls = int(working[column].isna().sum()) if column else None

        if op_type == "drop_duplicate_rows":
            subset = params.get("subset")
            working = working.drop_duplicates(subset=subset)
            affected = before_rows - len(working)

        elif op_type == "drop_null_rows":
            subset = [column] if column else None
            working = working.dropna(subset=subset)
            affected = before_rows - len(working)

        elif op_type == "fill_nulls":
            strategy = params.get("strategy", "constant")
            affected = before_nulls or 0
            working[column] = _fill_nulls(working[column], strategy, params.get("value"))

        elif op_type == "remove_outliers":
            multiplier = params.get("multiplier", 1.5)
            outlier_idx = _iqr_outlier_index(working[column], multiplier=multiplier)
            working = working.drop(index=outlier_idx)
            affected = len(outlier_idx)

        elif op_type == "trim_whitespace":
            columns = [column] if column else working.select_dtypes(include="object").columns.tolist()
            affected = 0
            for col in columns:
                trimmed = working[col].astype(str).str.strip()
                affected += int((trimmed != working[col].astype(str)).sum())
                working[col] = trimmed

        elif op_type == "cast_type":
            target = params.get("target_type")
            pandas_dtype = _CAST_MAP.get(target)
            if pandas_dtype is None:
                raise ValueError(f"Unsupported cast target: {target}")
            if pandas_dtype == "datetime64[ns]":
                working[column] = pd.to_datetime(working[column], errors="coerce", format="mixed")
            else:
                working[column] = pd.to_numeric(working[column], errors="coerce") if pandas_dtype in ("Int64", "float64") else working[column].astype(pandas_dtype)
            affected = before_rows

        log.append(
            {"op_type": op_type, "column": column, "params": params, "rows_affected": affected}
        )

    return working, log


def _fill_nulls(series: pd.Series, strategy: str, constant_value: Any) -> pd.Series:
    if strategy == "mean":
        return series.fillna(series.mean())
    if strategy == "median":
        return series.fillna(series.median())
    if strategy == "mode":
        mode = series.mode()
        return series.fillna(mode.iloc[0] if not mode.empty else "")
    if strategy == "constant":
        return series.fillna(constant_value)
    raise ValueError(f"Unsupported fill strategy: {strategy}")
