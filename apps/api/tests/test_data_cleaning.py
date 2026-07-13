"""
Unlike Phase 2's tests, these run fully standalone — detect_issues and
apply_operations are pure pandas functions with no DB, storage, or Clerk
dependency, so this is a real (if small) automated test suite, not just a
manual-testing pointer.

Run with: python tests/test_data_cleaning.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from app.services.data_cleaning import apply_operations, detect_issues


def test_detects_missing_values():
    df = pd.DataFrame({"a": [1, 2, None, 4]})
    issues = detect_issues(df)
    missing = [i for i in issues if i["issue_type"] == "missing_values"]
    assert len(missing) == 1
    assert missing[0]["affected_count"] == 1


def test_detects_duplicate_rows():
    df = pd.DataFrame({"a": [1, 1, 2], "b": ["x", "x", "y"]})
    issues = detect_issues(df)
    dupes = [i for i in issues if i["issue_type"] == "duplicate_rows"]
    assert len(dupes) == 1
    assert dupes[0]["affected_count"] == 1


def test_detects_outliers():
    df = pd.DataFrame({"a": [1, 2, 3, 4, 5, 1000]})
    issues = detect_issues(df)
    outliers = [i for i in issues if i["issue_type"] == "outliers"]
    assert len(outliers) == 1


def test_detects_untrimmed_whitespace():
    df = pd.DataFrame({"a": [" x", "y ", "z"]})
    issues = detect_issues(df)
    ws = [i for i in issues if i["issue_type"] == "untrimmed_whitespace"]
    assert len(ws) == 1
    assert ws[0]["affected_count"] == 2


def test_apply_drop_duplicate_rows():
    df = pd.DataFrame({"a": [1, 1, 2]})
    cleaned, log = apply_operations(df, [{"op_type": "drop_duplicate_rows", "column": None, "params": {}}])
    assert len(cleaned) == 2
    assert log[0]["rows_affected"] == 1


def test_apply_fill_nulls_median():
    df = pd.DataFrame({"a": [1.0, 2.0, None, 4.0]})
    cleaned, log = apply_operations(
        df, [{"op_type": "fill_nulls", "column": "a", "params": {"strategy": "median"}}]
    )
    assert cleaned["a"].isna().sum() == 0
    assert log[0]["rows_affected"] == 1


def test_apply_remove_outliers():
    df = pd.DataFrame({"a": [1, 2, 3, 4, 5, 1000]})
    cleaned, log = apply_operations(
        df, [{"op_type": "remove_outliers", "column": "a", "params": {"multiplier": 1.5}}]
    )
    assert 1000 not in cleaned["a"].values
    assert log[0]["rows_affected"] == 1


def test_apply_trim_whitespace():
    df = pd.DataFrame({"a": [" x ", "y"]})
    cleaned, log = apply_operations(df, [{"op_type": "trim_whitespace", "column": "a", "params": {}}])
    assert cleaned["a"].tolist() == ["x", "y"]


def test_apply_unsupported_operation_raises():
    df = pd.DataFrame({"a": [1, 2]})
    try:
        apply_operations(df, [{"op_type": "not_a_real_op", "column": "a", "params": {}}])
        raise AssertionError("Expected ValueError")
    except ValueError:
        pass


def test_apply_unknown_column_raises():
    df = pd.DataFrame({"a": [1, 2]})
    try:
        apply_operations(df, [{"op_type": "fill_nulls", "column": "nope", "params": {"strategy": "mean"}}])
        raise AssertionError("Expected ValueError")
    except ValueError:
        pass


if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"\nAll {len(tests)} Phase 4 unit tests passed.")
