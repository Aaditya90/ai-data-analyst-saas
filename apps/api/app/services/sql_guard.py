"""
Validates SQL before it's ever executed — this is the actual security
boundary for the AI Query Engine, not the LLM's good behavior. Never trust
generated SQL just because a well-behaved prompt asked for SELECT-only.

Deliberately conservative and keyword-based rather than a full SQL parser:
combined with the fact that queries only ever run against an ephemeral,
single-table, in-memory SQLite database that exists for one request and is
discarded immediately after (see query_executor.py), the worst case for a
guard bypass is reading rows from a table the caller already has read
access to — there's no write path, no other tables, no persistence, and no
network access from that SQLite connection. Defense in depth, not reliance
on this one layer.
"""

import re

_ALLOWED_START = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)

_BLOCKED_KEYWORDS = [
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "REPLACE",
    "TRUNCATE", "ATTACH", "DETACH", "PRAGMA", "VACUUM", "GRANT", "REVOKE",
    "EXEC", "EXECUTE", "CALL",
]
_BLOCKED_PATTERN = re.compile(
    r"\b(" + "|".join(_BLOCKED_KEYWORDS) + r")\b", re.IGNORECASE
)

DEFAULT_ROW_LIMIT = 500


class UnsafeQueryError(ValueError):
    """Raised when a generated query fails the safety guard — the API
    layer should surface this as a 400, not attempt to "fix" the query."""


def validate_and_limit(sql: str, row_limit: int = DEFAULT_ROW_LIMIT) -> str:
    """
    Raises UnsafeQueryError for anything that isn't a single, plain SELECT
    (or WITH ... SELECT). Returns the query with a LIMIT clause enforced
    (appended if missing, left alone if already present and smaller).
    """
    if not sql or not sql.strip():
        raise UnsafeQueryError("Empty query")

    stripped = sql.strip()

    # Reject statement chaining — a semicolon anywhere except a single
    # trailing one means more than one statement was supplied.
    body = stripped[:-1] if stripped.endswith(";") else stripped
    if ";" in body:
        raise UnsafeQueryError("Multiple statements are not allowed")

    if not _ALLOWED_START.match(body):
        raise UnsafeQueryError("Only SELECT/WITH queries are allowed")

    match = _BLOCKED_PATTERN.search(body)
    if match:
        raise UnsafeQueryError(f"Query contains a disallowed keyword: {match.group(1)}")

    return _enforce_limit(body, row_limit)


def _enforce_limit(sql: str, row_limit: int) -> str:
    existing_limit = re.search(r"\bLIMIT\s+(\d+)\b", sql, re.IGNORECASE)
    if existing_limit:
        current = int(existing_limit.group(1))
        if current <= row_limit:
            return sql
        # Query asked for more rows than we allow — clamp it down rather
        # than reject, since the intent (a SELECT with a limit) is fine.
        return sql[: existing_limit.start()] + f"LIMIT {row_limit}" + sql[existing_limit.end():]
    return f"{sql}\nLIMIT {row_limit}"
