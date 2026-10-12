"""
Run with: python tests/test_insights.py

Covers detect_insights() (pure pandas, no API key needed) plus the
fallback_narration templates. narrate_insights()'s actual Claude call isn't
covered here — see README's "Testing Phase 8" section for the manual
check.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from app.services.ai_narration import fallback_narration
from app.services.insights import detect_insights


def test_detects_outlier():
    df = pd.DataFrame({"value": [10, 12, 11, 13, 9, 500]})
    insights = detect_insights(df)
    outlier = next(i for i in insights if i["insight_type"] == "outlier")
    assert outlier["stats"]["outlier_count"] == 1


def test_detects_correlation():
    df = pd.DataFrame({"a": list(range(1, 11)), "b": [x * 2 for x in range(1, 11)]})
    insights = detect_insights(df)
    corr = next(i for i in insights if i["insight_type"] == "correlation")
    assert corr["stats"]["correlation"] == 1.0
    assert corr["stats"]["direction"] == "positive"


def test_detects_negative_correlation():
    df = pd.DataFrame({"a": list(range(1, 11)), "b": [-x for x in range(1, 11)]})
    insights = detect_insights(df)
    corr = next(i for i in insights if i["insight_type"] == "correlation")
    assert corr["stats"]["direction"] == "negative"


def test_ignores_weak_correlation():
    rng = np.random.default_rng(0)
    df = pd.DataFrame({"a": rng.normal(size=200), "b": rng.normal(size=200)})
    insights = detect_insights(df)
    assert not any(i["insight_type"] == "correlation" for i in insights)


def test_detects_imbalance():
    df = pd.DataFrame({"category": ["A"] * 90 + ["B"] * 10})
    insights = detect_insights(df)
    imbalance = next(i for i in insights if i["insight_type"] == "imbalance")
    assert imbalance["stats"]["dominant_value"] == "A"
    assert imbalance["stats"]["dominant_pct"] == 90.0


def test_ignores_balanced_categories():
    df = pd.DataFrame({"category": ["A"] * 50 + ["B"] * 50})
    insights = detect_insights(df)
    assert not any(i["insight_type"] == "imbalance" for i in insights)


def test_detects_increasing_trend():
    dates = pd.date_range("2024-01-01", periods=30, freq="D")
    df = pd.DataFrame({"date": dates, "value": range(30)})
    insights = detect_insights(df)
    trend = next(i for i in insights if i["insight_type"] == "trend")
    assert trend["stats"]["direction"] == "increasing"


def test_detects_decreasing_trend():
    dates = pd.date_range("2024-01-01", periods=30, freq="D")
    df = pd.DataFrame({"date": dates, "value": list(range(30, 0, -1))})
    insights = detect_insights(df)
    trend = next(i for i in insights if i["insight_type"] == "trend")
    assert trend["stats"]["direction"] == "decreasing"


def test_no_trend_without_datetime_column():
    df = pd.DataFrame({"value": range(30)})
    insights = detect_insights(df)
    assert not any(i["insight_type"] == "trend" for i in insights)


def test_insights_ranked_by_significance_descending():
    dates = pd.date_range("2024-01-01", periods=30, freq="D")
    df = pd.DataFrame(
        {
            "date": dates,
            "value": range(30),
            "category": ["A"] * 29 + ["B"],  # weak imbalance, low significance
        }
    )
    insights = detect_insights(df)
    significances = [i["significance"] for i in insights]
    assert significances == sorted(significances, reverse=True)


def test_respects_max_insights():
    rng = np.random.default_rng(1)
    df = pd.DataFrame({f"col_{i}": rng.normal(100, 10, 50) for i in range(10)})
    for i in range(10):
        df.loc[0, f"col_{i}"] = 100000  # inject an outlier in every column
    insights = detect_insights(df, max_insights=3)
    assert len(insights) <= 3


def test_fallback_narration_covers_every_insight_type():
    examples = [
        {"insight_type": "outlier", "column": "x", "stats": {"outlier_count": 1, "outlier_pct": 2.0, "mean": 10.0}},
        {"insight_type": "correlation", "column": "a, b", "stats": {"column_a": "a", "column_b": "b", "correlation": 0.9, "direction": "positive"}},
        {"insight_type": "imbalance", "column": "cat", "stats": {"dominant_pct": 80.0, "dominant_value": "A"}},
        {"insight_type": "trend", "column": "y", "stats": {"direction": "increasing", "pct_change_over_period": 20.0}},
    ]
    for ex in examples:
        text = fallback_narration(ex)
        assert isinstance(text, str) and len(text) > 0


if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"\nAll {len(tests)} Phase 8 unit tests passed.")
