"""
Run with: python tests/test_automl.py

Covers app/services/automl.py end to end with pure pandas/sklearn synthetic
data — no DB, no network, no API key needed. narrate_model_result()'s
actual Claude call isn't covered here — see README's "Testing Phase 10"
section for the manual check.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from app.services.automl import (
    AutoMLError,
    deserialize_pipeline,
    detect_task_type,
    predict_with_pipeline,
    select_feature_columns,
    serialize_pipeline,
    train_automl_model,
)


def _regression_df(n=200, seed=0):
    rng = np.random.default_rng(seed)
    x1 = rng.normal(50, 10, n)
    x2 = rng.normal(20, 5, n)
    category = rng.choice(["A", "B", "C"], size=n)
    category_bonus = pd.Series(category).map({"A": 0, "B": 10, "C": -10}).to_numpy()
    noise = rng.normal(0, 2, n)
    target = 3 * x1 - 2 * x2 + category_bonus + noise
    return pd.DataFrame({"x1": x1, "x2": x2, "category": category, "target": target})


def _classification_df(n=200, seed=0):
    rng = np.random.default_rng(seed)
    x1 = rng.normal(0, 1, n)
    x2 = rng.normal(0, 1, n)
    label = np.where(x1 + x2 > 0, "high", "low")
    return pd.DataFrame({"x1": x1, "x2": x2, "label": label})


def test_detect_task_type_continuous_numeric_is_regression():
    series = pd.Series(np.linspace(0, 1000, 200))
    assert detect_task_type(series) == "regression"


def test_detect_task_type_low_cardinality_numeric_is_classification():
    series = pd.Series([0, 1] * 100)
    assert detect_task_type(series) == "classification"


def test_detect_task_type_string_is_classification():
    series = pd.Series(["cat", "dog", "cat", "bird"] * 20)
    assert detect_task_type(series) == "classification"


def test_detect_task_type_raises_on_all_null():
    series = pd.Series([None, None, None])
    try:
        detect_task_type(series)
        assert False, "expected AutoMLError"
    except AutoMLError:
        pass


def test_select_feature_columns_excludes_target():
    df = pd.DataFrame({"a": [1, 2, 3], "target": [1, 2, 3]})
    features = select_feature_columns(df, "target", None)
    assert "target" not in features
    assert "a" in features


def test_select_feature_columns_excludes_near_unique_identifier():
    n = 100
    df = pd.DataFrame(
        {
            "order_id": [f"ORD-{i}" for i in range(n)],  # near-unique -> should be dropped
            "amount": np.arange(n),
            "target": np.arange(n),
        }
    )
    features = select_feature_columns(df, "target", None)
    assert "order_id" not in features
    assert "amount" in features


def test_select_feature_columns_respects_explicit_list():
    df = pd.DataFrame({"a": [1, 2], "b": [3, 4], "target": [5, 6]})
    features = select_feature_columns(df, "target", ["a"])
    assert features == ["a"]


def test_select_feature_columns_raises_on_unknown_column():
    df = pd.DataFrame({"a": [1, 2], "target": [3, 4]})
    try:
        select_feature_columns(df, "target", ["does_not_exist"])
        assert False, "expected AutoMLError"
    except AutoMLError:
        pass


def test_train_automl_model_regression():
    df = _regression_df()
    result = train_automl_model(df, target_column="target")
    assert result["task_type"] == "regression"
    assert result["algorithm"] in ("linear_model", "random_forest")
    assert set(result["metrics"].keys()) == {"r2", "mae", "rmse"}
    # This synthetic target is a near-noiseless linear function of the
    # features, so a well-fit model should explain most of the variance.
    assert result["metrics"]["r2"] > 0.8
    assert result["train_row_count"] + result["test_row_count"] == len(df)
    assert len(result["candidates"]) == 2
    importances = result["feature_importance"]
    assert {f["column"] for f in importances} == {"x1", "x2", "category"}
    assert abs(sum(f["importance"] for f in importances) - 1.0) < 1e-6


def test_train_automl_model_classification():
    df = _classification_df()
    result = train_automl_model(df, target_column="label")
    assert result["task_type"] == "classification"
    assert set(result["metrics"].keys()) == {"accuracy", "f1_macro", "precision_macro", "recall_macro"}
    assert result["metrics"]["accuracy"] > 0.8


def test_train_automl_model_respects_explicit_task_type_override():
    # A numeric target with only two distinct values would normally
    # auto-detect as classification; forcing regression should be honored.
    df = pd.DataFrame({"x": np.arange(50, dtype=float), "y": [0, 1] * 25})
    result = train_automl_model(df, target_column="y", task_type="regression")
    assert result["task_type"] == "regression"


def test_train_automl_model_raises_on_missing_target():
    df = pd.DataFrame({"a": [1, 2, 3]})
    try:
        train_automl_model(df, target_column="does_not_exist")
        assert False, "expected AutoMLError"
    except AutoMLError:
        pass


def test_train_automl_model_raises_on_too_few_rows():
    df = pd.DataFrame({"a": [1, 2, 3], "target": [1, 2, 3]})
    try:
        train_automl_model(df, target_column="target")
        assert False, "expected AutoMLError"
    except AutoMLError:
        pass


def test_train_automl_model_raises_when_no_usable_features():
    n = 30
    df = pd.DataFrame(
        {
            "id_col": [f"ID-{i}" for i in range(n)],  # dropped as near-unique identifier
            "target": np.arange(n),
        }
    )
    try:
        train_automl_model(df, target_column="target")
        assert False, "expected AutoMLError"
    except AutoMLError:
        pass


def test_pipeline_serialize_roundtrip_predicts_identically():
    df = _regression_df()
    result = train_automl_model(df, target_column="target")
    blob = serialize_pipeline(result["pipeline"])
    restored = deserialize_pipeline(blob)

    sample_rows = df.head(5)[result["feature_columns"]].to_dict(orient="records")
    original_preds = predict_with_pipeline(result["pipeline"], sample_rows, result["feature_columns"])
    restored_preds = predict_with_pipeline(restored, sample_rows, result["feature_columns"])
    assert np.allclose(original_preds, restored_preds)


def test_predict_with_pipeline_fills_missing_keys_with_nan():
    df = _regression_df()
    result = train_automl_model(df, target_column="target")
    # Omit "category" entirely from the input row — should be imputed, not crash.
    rows = [{"x1": 55.0, "x2": 18.0}]
    predictions = predict_with_pipeline(result["pipeline"], rows, result["feature_columns"])
    assert len(predictions) == 1
    assert isinstance(predictions[0], float)


if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"\nAll {len(tests)} Phase 10 AutoML unit tests passed.")
