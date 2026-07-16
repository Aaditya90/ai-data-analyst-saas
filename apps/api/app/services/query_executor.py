"""
Executes a validated SQL query against exactly one dataset's data —
nothing else is reachable from this connection.

Why in-memory SQLite rather than the app's own Postgres, or the customer's
connected database:
  - Never touches our own app database — an AI-generated query, however
    validated, should never share a connection with tables like `users`
    or `workspace_members`.
  - Never touches a customer's connected DB (Phase 3's DataConnection) —
    generating and running arbitrary SQL against a customer's real
    database is out of scope for this phase; only file-upload datasets
    are queryable here (same limitation carried from Phases 4-6).
  - The SQLite DB is created fresh per request, holds a single table named
    `dataset`, and is discarded when the connection closes — there's
    nothing to leak or persist across requests.
"""

import sqlite3

import pandas as pd

TABLE_NAME = "dataset"


class QueryExecutionError(RuntimeError):
    pass


def run_query(df: pd.DataFrame, sql: str) -> dict:
    """
    Loads df into an in-memory SQLite table called `dataset` and runs sql
    against it. Caller must have already validated `sql` with
    app.services.sql_guard — this function does not re-validate.
    """
    conn = sqlite3.connect(":memory:")
    try:
        df.to_sql(TABLE_NAME, conn, index=False, if_exists="replace")
        cursor = conn.execute(sql)
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = cursor.fetchall()
        return {
            "columns": columns,
            "rows": [[_serialize(v) for v in row] for row in rows],
            "row_count": len(rows),
        }
    except sqlite3.Error as exc:
        raise QueryExecutionError(str(exc)) from exc
    finally:
        conn.close()


def _serialize(value):
    # sqlite3 returns Python-native types already; this just guards against
    # anything non-JSON-serializable slipping through (e.g. bytes)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
