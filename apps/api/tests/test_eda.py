"""
Standalone tests, same pattern as test_data_cleaning.py — pure pandas in,
structured dict out, no DB/Redis/Clerk needed.

Run with: python tests/test_eda.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from app.services.eda import compute_column_summaries, compute_correlations, suggest_charts


def test_numeric_column_gets_stats():
    df = pd.DataFrame({"age": [20, 30, 40, 50]})
    summaries = compute_column_summaries(df)
    assert summaries[0]["type"] == "numeric"
    assert summaries[0]["stats"]["mean"] == 35.0
    assert summaries[0]["stats"]["min"] == 20.0
    assert summaries[0]["stats"]["max"] == 50.0


def test_categorical_column_gets_top_values():
    df = pd.DataFrame({"dept": ["Eng", "Eng", "Sales", "Eng"]})
    summaries = compute_column_summaries(df)
    assert summaries[0]["type"] == "categorical"
    top = summaries[0]["stats"]["top_values"]
    assert top[0]["value"] == "Eng"
    assert top[0]["count"] == 3


def test_null_percentage_computed():
    df = pd.DataFrame({"a": [1, None, None, 4]})
    summaries = compute_column_summaries(df)
    assert summaries[0]["null_count"] == 2
    assert summaries[0]["null_pct"] == 50.0


def test_correlations_finds_strong_relationship():
    df = pd.DataFrame({"a": [1, 2, 3, 4, 5], "b": [2, 4, 6, 8, 10]})
    result = compute_correlations(df)
    assert len(result["pairs"]) == 1
    assert result["pairs"][0]["correlation"] == 1.0


def test_correlations_skipped_with_less_than_two_numeric_columns():
    df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    result = compute_correlations(df)
    assert result["pairs"] == []


def test_suggest_charts_histogram_for_numeric():
    summaries = compute_column_summaries(pd.DataFrame({"age": [1, 2, 3]}))
    charts = suggest_charts(summaries)
    assert any(c["chart_type"] == "histogram" for c in charts)


def test_suggest_charts_bar_for_low_cardinality_categorical():
    summaries = compute_column_summaries(pd.DataFrame({"dept": ["Eng", "Sales", "Eng"]}))
    charts = suggest_charts(summaries)
    assert any(c["chart_type"] == "bar" for c in charts)


def test_suggest_charts_scatter_for_two_numeric():
    df = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
    summaries = compute_column_summaries(df)
    charts = suggest_charts(summaries)
    assert any(c["chart_type"] == "scatter" for c in charts)


def test_suggest_charts_respects_max_limit():
    df = pd.DataFrame({f"col_{i}": [1, 2, 3] for i in range(15)})
    summaries = compute_column_summaries(df)
    charts = suggest_charts(summaries, max_suggestions=3)
    assert len(charts) == 3


if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"\nAll {len(tests)} Phase 5 unit tests passed.")
