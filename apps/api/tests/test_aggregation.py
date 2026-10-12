"""
Run with: python tests/test_aggregation.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from app.services.aggregation import compute_chart, compute_kpi, compute_table


def _sample_df():
    return pd.DataFrame(
        {
            "department": ["Eng", "Sales", "Eng", "Marketing", "Eng", "Sales"],
            "salary": [80000, 60000, 90000, 55000, 85000, 62000],
        }
    )


def test_kpi_sum():
    result = compute_kpi(_sample_df(), "salary", "sum")
    assert result["value"] == 432000.0


def test_kpi_avg():
    result = compute_kpi(_sample_df(), "salary", "avg")
    assert result["value"] == 72000.0


def test_kpi_unsupported_aggregation_raises():
    try:
        compute_kpi(_sample_df(), "salary", "median")
        raise AssertionError("Expected ValueError")
    except ValueError:
        pass


def test_kpi_missing_column_raises():
    try:
        compute_kpi(_sample_df(), "nope", "sum")
        raise AssertionError("Expected ValueError")
    except ValueError:
        pass


def test_table_all_columns():
    result = compute_table(_sample_df(), None, 10)
    assert result["columns"] == ["department", "salary"]
    assert result["total_rows"] == 6


def test_table_subset_columns():
    result = compute_table(_sample_df(), ["department"], 10)
    assert result["columns"] == ["department"]


def test_table_respects_row_limit():
    result = compute_table(_sample_df(), None, 2)
    assert len(result["rows"]) == 2
    assert result["total_rows"] == 6  # total_rows reflects full dataset, not the limited page


def test_chart_avg_by_category():
    result = compute_chart(_sample_df(), "bar", "department", "salary", "avg")
    idx = result["labels"].index("Eng")
    assert result["values"][idx] == 85000.0


def test_chart_count_by_category_no_y_column():
    result = compute_chart(_sample_df(), "bar", "department", None, "count")
    idx = result["labels"].index("Eng")
    assert result["values"][idx] == 3


if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"\nAll {len(tests)} Phase 6 unit tests passed.")
