"""
Run with: python tests/test_ai_query.py

Covers the two parts of the AI Query Engine that don't need an Anthropic
API key or network access: SQL safety validation and query execution
against the ephemeral SQLite sandbox. nl_to_sql.py's actual Claude call
isn't covered here (needs a real API key) but its response-parsing logic
is simple enough to be low-risk; manual testing via the API is the check
for that piece — see README's "Testing Phase 7" section.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from app.services.query_executor import QueryExecutionError, run_query
from app.services.sql_guard import DEFAULT_ROW_LIMIT, UnsafeQueryError, validate_and_limit


def test_allows_plain_select():
    result = validate_and_limit("SELECT * FROM dataset")
    assert result.startswith("SELECT * FROM dataset")
    assert "LIMIT" in result


def test_allows_with_clause():
    result = validate_and_limit("WITH t AS (SELECT 1) SELECT * FROM t")
    assert "LIMIT" in result


def test_blocks_drop():
    try:
        validate_and_limit("DROP TABLE dataset")
        raise AssertionError("Expected UnsafeQueryError")
    except UnsafeQueryError:
        pass


def test_blocks_statement_chaining():
    try:
        validate_and_limit("SELECT * FROM dataset; DROP TABLE dataset")
        raise AssertionError("Expected UnsafeQueryError")
    except UnsafeQueryError:
        pass


def test_blocks_insert_update_delete():
    for stmt in [
        "INSERT INTO dataset VALUES (1)",
        "UPDATE dataset SET x = 1",
        "DELETE FROM dataset",
    ]:
        try:
            validate_and_limit(stmt)
            raise AssertionError(f"Expected UnsafeQueryError for: {stmt}")
        except UnsafeQueryError:
            pass


def test_blocks_empty_query():
    try:
        validate_and_limit("")
        raise AssertionError("Expected UnsafeQueryError")
    except UnsafeQueryError:
        pass


def test_preserves_existing_smaller_limit():
    result = validate_and_limit("SELECT * FROM dataset LIMIT 5")
    assert "LIMIT 5" in result
    assert "LIMIT 500" not in result


def test_clamps_oversized_limit():
    result = validate_and_limit("SELECT * FROM dataset LIMIT 999999", row_limit=100)
    assert "LIMIT 100" in result
    assert "999999" not in result


def test_appends_limit_when_missing():
    result = validate_and_limit("SELECT * FROM dataset WHERE x > 1")
    assert f"LIMIT {DEFAULT_ROW_LIMIT}" in result


def test_query_executor_runs_aggregation():
    df = pd.DataFrame({"dept": ["Eng", "Sales", "Eng"], "salary": [80000, 60000, 90000]})
    sql = validate_and_limit("SELECT dept, AVG(salary) as avg_salary FROM dataset GROUP BY dept")
    result = run_query(df, sql)
    assert result["columns"] == ["dept", "avg_salary"]
    assert result["row_count"] == 2


def test_query_executor_raises_on_bad_column():
    df = pd.DataFrame({"a": [1, 2, 3]})
    try:
        run_query(df, "SELECT nonexistent_column FROM dataset")
        raise AssertionError("Expected QueryExecutionError")
    except QueryExecutionError:
        pass


if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"\nAll {len(tests)} Phase 7 unit tests passed.")
