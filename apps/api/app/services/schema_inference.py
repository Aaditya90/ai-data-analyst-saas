"""
Turns a pandas DataFrame into the schema_json shape stored on
DatasetVersion: a list of {name, inferred_type, nullable, sample_values}.

Kept as one shared function so file uploads and DB-connector snapshots
produce identically-shaped schema metadata — the EDA engine (Phase 5) and
dashboard builder (Phase 6) can then treat both dataset sources uniformly.
"""

import pandas as pd

# Maps pandas/numpy dtype kinds to a small, stable vocabulary the rest of
# the system reasons about — deliberately not exposing pandas-specific
# dtype strings (like "int64" vs "Int64") to the API layer.
_DTYPE_KIND_MAP = {
    "i": "integer",
    "u": "integer",
    "f": "float",
    "b": "boolean",
    "M": "datetime",
    "m": "duration",
    "O": "string",
    "U": "string",
    "S": "string",
}


def _infer_column_type(series: pd.Series) -> str:
    kind = series.dtype.kind
    inferred = _DTYPE_KIND_MAP.get(kind, "string")

    # object-dtype columns are pandas' catch-all; sniff further so obvious
    # numeric/boolean/datetime columns stored as strings aren't mislabeled
    if inferred == "string" and kind == "O":
        non_null = series.dropna()
        if non_null.empty:
            return "string"
        if pd.to_numeric(non_null, errors="coerce").notna().all():
            return "float"
        if non_null.isin([True, False, "True", "False", "true", "false"]).all():
            return "boolean"
        try:
            pd.to_datetime(non_null, errors="raise", format="mixed")
            return "datetime"
        except (ValueError, TypeError):
            pass
    return inferred


def infer_schema(df: pd.DataFrame, sample_size: int = 5) -> list[dict]:
    schema = []
    for column_name in df.columns:
        series = df[column_name]
        sample_values = (
            series.dropna().head(sample_size).astype(str).tolist()
        )
        schema.append(
            {
                "name": str(column_name),
                "inferred_type": _infer_column_type(series),
                "nullable": bool(series.isna().any()),
                "sample_values": sample_values,
            }
        )
    return schema


def read_tabular_file(buffer, filename: str) -> pd.DataFrame:
    """
    Dispatches to the right pandas reader by extension. Raises ValueError
    for unsupported types or files pandas can't parse — callers should
    surface this as a 400, not a 500 (it's a user input problem).
    """
    lower = filename.lower()
    try:
        if lower.endswith(".csv"):
            return pd.read_csv(buffer)
        if lower.endswith(".tsv"):
            return pd.read_csv(buffer, sep="\t")
        if lower.endswith((".xlsx", ".xls")):
            return pd.read_excel(buffer)
        if lower.endswith(".json"):
            return pd.read_json(buffer)
    except Exception as exc:  # pandas raises many different error types
        raise ValueError(f"Could not parse '{filename}': {exc}") from exc

    raise ValueError(
        f"Unsupported file type for '{filename}'. Supported: .csv, .tsv, .xlsx, .xls, .json"
    )
