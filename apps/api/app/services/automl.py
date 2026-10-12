"""
AutoML training: pure sklearn/pandas code, no AI involved anywhere in this
file. Given a DataFrame, a target column, and (optionally) a caller-chosen
subset of feature columns, this:

  1. Auto-detects the task type (regression vs classification) from the
     target column's dtype and cardinality, unless the caller pins it.
  2. Builds a shared preprocessing pipeline (median-impute + scale for
     numeric columns, most-frequent-impute + one-hot for categorical
     columns) so every candidate estimator is compared on identical input.
  3. Trains a small, fixed set of candidate estimators, evaluates each on
     a held-out test split, and keeps the best one by a fixed primary
     metric (R^2 for regression, macro F1 for classification).
  4. Refits the winning algorithm on the FULL dataset (train + test) for
     the pipeline that actually gets serialized and used for prediction —
     the holdout metrics reported are from the train-only fit, so they
     stay an honest estimate of generalization, but the shipped model uses
     every available row.

This mirrors Phases 7-9's "AI decides/narrates, code validates" split:
nothing here ever calls the Claude API. ai_narration.py's
narrate_model_result() is the only place model output touches AI, and it's
handed the exact metrics computed here to phrase, never to compute.
"""

import io

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    precision_score,
    r2_score,
    recall_score,
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

MIN_ROWS = 20
TEST_SIZE = 0.25
# A numeric target with very few distinct values behaves like a
# classification label (0/1 flags, small category codes stored as ints),
# not a continuous quantity — this cutoff decides which side of the line
# an ambiguous numeric column falls on.
MAX_CLASSES_FOR_CLASSIFICATION = 20
RANDOM_STATE = 42


class AutoMLError(ValueError):
    """Raised for input that can't be trained on — the API layer surfaces
    this as a 400, it never means "the code broke"."""


def detect_task_type(series: pd.Series) -> str:
    non_null = series.dropna()
    if non_null.empty:
        raise AutoMLError("Target column has no non-null values")

    if series.dtype.kind in ("i", "u", "f"):
        unique_count = non_null.nunique()
        cardinality_cutoff = min(MAX_CLASSES_FOR_CLASSIFICATION, max(2, int(len(non_null) * 0.05)))
        if unique_count <= cardinality_cutoff:
            return "classification"
        return "regression"

    return "classification"


def select_feature_columns(
    df: pd.DataFrame, target_column: str, feature_columns: list[str] | None
) -> list[str]:
    if feature_columns:
        missing = [c for c in feature_columns if c not in df.columns]
        if missing:
            raise AutoMLError(f"Unknown feature column(s): {', '.join(missing)}")
        return [c for c in feature_columns if c != target_column]

    n = len(df)
    selected = []
    for col in df.columns:
        if col == target_column:
            continue
        series = df[col]
        # Skip likely-identifier columns: a near-unique text column (an
        # order ID, a free-text name) is noise for the model, not signal,
        # and one-hot-encoding it would blow up the feature space for no
        # predictive benefit. The floor of 10 keeps this from misfiring on
        # small datasets where a legitimate low-cardinality category could
        # otherwise look "mostly unique" relative to row count.
        if series.dtype.kind == "O" and series.nunique(dropna=True) > max(10, int(n * 0.9)):
            continue
        selected.append(col)
    return selected


def _build_preprocessor(
    numeric_features: list[str], categorical_features: list[str]
) -> ColumnTransformer:
    transformers = []
    if numeric_features:
        transformers.append(
            (
                "num",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="median")),
                        ("scale", StandardScaler()),
                    ]
                ),
                numeric_features,
            )
        )
    if categorical_features:
        transformers.append(
            (
                "cat",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="most_frequent")),
                        ("onehot", OneHotEncoder(handle_unknown="ignore")),
                    ]
                ),
                categorical_features,
            )
        )
    return ColumnTransformer(transformers)


def _candidates_for(task_type: str) -> list[tuple[str, object]]:
    if task_type == "regression":
        return [
            ("linear_model", LinearRegression()),
            (
                "random_forest",
                RandomForestRegressor(n_estimators=200, random_state=RANDOM_STATE, n_jobs=-1),
            ),
        ]
    return [
        ("logistic_regression", LogisticRegression(max_iter=1000)),
        (
            "random_forest",
            RandomForestClassifier(n_estimators=200, random_state=RANDOM_STATE, n_jobs=-1),
        ),
    ]


def _regression_metrics(y_true, y_pred) -> dict:
    return {
        "r2": round(float(r2_score(y_true, y_pred)), 4),
        "mae": round(float(mean_absolute_error(y_true, y_pred)), 4),
        "rmse": round(float(np.sqrt(mean_squared_error(y_true, y_pred))), 4),
    }


def _classification_metrics(y_true, y_pred) -> dict:
    return {
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 4),
        "f1_macro": round(float(f1_score(y_true, y_pred, average="macro", zero_division=0)), 4),
        "precision_macro": round(
            float(precision_score(y_true, y_pred, average="macro", zero_division=0)), 4
        ),
        "recall_macro": round(
            float(recall_score(y_true, y_pred, average="macro", zero_division=0)), 4
        ),
    }


def _primary_metric(task_type: str, metrics: dict) -> float:
    return metrics["r2"] if task_type == "regression" else metrics["f1_macro"]


def _feature_segments(
    preprocessor: ColumnTransformer, numeric_features: list[str], categorical_features: list[str]
) -> list[tuple[str, int]]:
    """Maps the flat array of transformed-feature importances/coefficients
    back to original column names. Numeric columns contribute exactly one
    output column each, in order; each categorical column expands into one
    output column per category it was fitted on (OneHotEncoder.categories_,
    in the same order the ColumnTransformer processed them)."""
    segments = [(col, 1) for col in numeric_features]
    if categorical_features:
        cat_pipeline = preprocessor.named_transformers_["cat"]
        encoder = cat_pipeline.named_steps["onehot"]
        for col, cats in zip(categorical_features, encoder.categories_):
            segments.append((col, len(cats)))
    return segments


def _feature_importance(
    fitted_pipeline: Pipeline,
    task_type: str,
    algorithm: str,
    numeric_features: list[str],
    categorical_features: list[str],
) -> list[dict]:
    model = fitted_pipeline.named_steps["model"]
    preprocessor = fitted_pipeline.named_steps["preprocess"]

    if algorithm == "random_forest":
        raw = np.asarray(model.feature_importances_)
    else:
        coef = np.asarray(model.coef_)
        raw = np.mean(np.abs(coef), axis=0) if coef.ndim > 1 else np.abs(coef)

    segments = _feature_segments(preprocessor, numeric_features, categorical_features)

    result = []
    idx = 0
    for column, width in segments:
        chunk = raw[idx : idx + width]
        result.append({"column": column, "importance": float(np.sum(chunk))})
        idx += width

    total = sum(r["importance"] for r in result) or 1.0
    for r in result:
        r["importance"] = round(r["importance"] / total, 4)
    result.sort(key=lambda r: r["importance"], reverse=True)
    return result


def train_automl_model(
    df: pd.DataFrame,
    target_column: str,
    feature_columns: list[str] | None = None,
    task_type: str | None = None,
) -> dict:
    """
    Returns:
        {
          "task_type": "regression" | "classification",
          "algorithm": str,               # winning candidate
          "feature_columns": [str, ...],
          "metrics": {...},               # winner's holdout metrics
          "feature_importance": [{"column": str, "importance": float}, ...],
          "candidates": [{"algorithm": str, "metrics": {...}}, ...],
          "train_row_count": int,
          "test_row_count": int,
          "pipeline": sklearn Pipeline,   # fitted on ALL rows — caller serializes this
        }
    Raises AutoMLError for input that can't be trained on.
    """
    if target_column not in df.columns:
        raise AutoMLError(f"Target column '{target_column}' not found in dataset")

    df = df.dropna(subset=[target_column])
    if len(df) < MIN_ROWS:
        raise AutoMLError(
            f"Need at least {MIN_ROWS} rows with a non-null target; found {len(df)}"
        )

    resolved_task_type = task_type or detect_task_type(df[target_column])
    if resolved_task_type not in ("regression", "classification"):
        raise AutoMLError(f"Unsupported task_type '{resolved_task_type}'")

    features = select_feature_columns(df, target_column, feature_columns)
    if not features:
        raise AutoMLError("No usable feature columns found")

    X = df[features]
    y = df[target_column]

    numeric_features = [c for c in features if X[c].dtype.kind in ("i", "u", "f")]
    categorical_features = [c for c in features if c not in numeric_features]

    stratify = None
    if resolved_task_type == "classification":
        class_counts = y.value_counts()
        if (class_counts >= 2).all() and len(class_counts) >= 2:
            stratify = y

    try:
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=TEST_SIZE, random_state=RANDOM_STATE, stratify=stratify
        )
    except ValueError:
        # Stratification failed anyway (e.g. a class only in the would-be
        # test fold) — fall back to a plain random split.
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=TEST_SIZE, random_state=RANDOM_STATE
        )

    metric_fn = _regression_metrics if resolved_task_type == "regression" else _classification_metrics

    candidates_results = []
    best = None  # (primary_metric, algorithm, metrics)
    for algorithm, estimator in _candidates_for(resolved_task_type):
        preprocessor = _build_preprocessor(numeric_features, categorical_features)
        pipeline = Pipeline([("preprocess", preprocessor), ("model", estimator)])
        pipeline.fit(X_train, y_train)
        y_pred = pipeline.predict(X_test)
        metrics = metric_fn(y_test, y_pred)
        candidates_results.append({"algorithm": algorithm, "metrics": metrics})

        primary = _primary_metric(resolved_task_type, metrics)
        if best is None or primary > best[0]:
            best = (primary, algorithm, metrics)

    _, winning_algorithm, winning_metrics = best

    # Refit the winning algorithm on every available row for the pipeline
    # that actually gets shipped/serialized — the metrics above already
    # captured an honest holdout estimate, so this doesn't taint them.
    final_preprocessor = _build_preprocessor(numeric_features, categorical_features)
    final_estimator = dict(_candidates_for(resolved_task_type))[winning_algorithm]
    final_pipeline = Pipeline([("preprocess", final_preprocessor), ("model", final_estimator)])
    final_pipeline.fit(X, y)

    feature_importance = _feature_importance(
        final_pipeline, resolved_task_type, winning_algorithm, numeric_features, categorical_features
    )

    return {
        "task_type": resolved_task_type,
        "algorithm": winning_algorithm,
        "feature_columns": features,
        "metrics": winning_metrics,
        "feature_importance": feature_importance,
        "candidates": candidates_results,
        "train_row_count": len(X_train),
        "test_row_count": len(X_test),
        "pipeline": final_pipeline,
    }


def predict_with_pipeline(pipeline: Pipeline, rows: list[dict], feature_columns: list[str]) -> list:
    """Runs the fitted pipeline on new input rows. `rows` are plain dicts
    (as received over the API); missing feature keys become NaN, which the
    pipeline's imputers handle the same way they did at training time."""
    input_df = pd.DataFrame(rows)
    for col in feature_columns:
        if col not in input_df.columns:
            input_df[col] = np.nan
    predictions = pipeline.predict(input_df[feature_columns])
    return [p.item() if hasattr(p, "item") else p for p in predictions]


def serialize_pipeline(pipeline: Pipeline) -> bytes:
    buffer = io.BytesIO()
    joblib.dump(pipeline, buffer)
    return buffer.getvalue()


def deserialize_pipeline(data: bytes) -> Pipeline:
    return joblib.load(io.BytesIO(data))
