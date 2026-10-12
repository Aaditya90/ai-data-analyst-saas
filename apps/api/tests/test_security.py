"""
Standalone unit tests for Phase 15 (Security).

Covers the pure-Python pieces: the rate limiter's fixed-window bucketing,
the RLS session-context SQL shape, and the CORS origins fallback logic.
Everything that needs a live Postgres connection (RLS actually blocking a
cross-workspace query, FORCE ROW LEVEL SECURITY behavior, the
`app_runtime` role) or a live Redis (the rate limiter actually counting
and fail-open behavior) is exercised manually — see README.md
"Testing Phase 15".

Run with: python tests/test_security.py
"""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.deps import compute_fixed_window
from app.core.rls import _SET_WORKSPACE_CONTEXT, set_workspace_context


def test_fixed_window_groups_nearby_timestamps_together():
    # Same 60s window
    assert compute_fixed_window(1_700_000_000.0, 60) == compute_fixed_window(1_700_000_030.0, 60)


def test_fixed_window_separates_timestamps_across_a_boundary():
    window_seconds = 60
    # Using small epoch offsets from 0 (not a real-world timestamp) so the
    # boundary math is exact and doesn't depend on where 1_700_000_000-ish
    # happens to sit relative to a multiple of window_seconds.
    w1 = compute_fixed_window(59.999, window_seconds)
    w2 = compute_fixed_window(60.0, window_seconds)
    assert w2 == w1 + 1


def test_fixed_window_is_deterministic_and_monotonic():
    windows = [compute_fixed_window(1_700_000_000.0 + i * 10, 60) for i in range(10)]
    assert windows == sorted(windows)  # never goes backwards as time increases


class _FakeDBSession:
    """Minimal stand-in for a SQLAlchemy Session — just records calls."""

    def __init__(self):
        self.calls = []

    def execute(self, statement, params=None):
        self.calls.append((statement, params))


def test_set_workspace_context_uses_set_config_not_string_interpolation():
    # The statement text itself must be the parameterized set_config()
    # call, never a raw `SET ... = '<value>'` string built with f-strings
    # — that's the whole point of using set_config (see rls.py docstring).
    compiled = str(_SET_WORKSPACE_CONTEXT)
    assert "set_config" in compiled
    assert ":workspace_id" in compiled  # a bound parameter, not an interpolated value


def test_set_workspace_context_passes_workspace_id_as_a_bound_param():
    db = _FakeDBSession()
    workspace_id = uuid.uuid4()

    set_workspace_context(db, workspace_id)

    assert len(db.calls) == 1
    statement, params = db.calls[0]
    assert params == {"workspace_id": str(workspace_id)}
    # Never baked directly into the SQL text itself:
    assert str(workspace_id) not in str(statement)


def _cors_origins_pure(is_production: bool, cors_allowed_origins: str) -> list[str]:
    """Mirrors Settings.cors_origins's logic without needing pydantic-settings."""
    configured = [o.strip() for o in cors_allowed_origins.split(",") if o.strip()]
    if is_production:
        return configured
    return list({"http://localhost:3000", *configured})


def test_cors_dev_always_includes_localhost():
    origins = _cors_origins_pure(is_production=False, cors_allowed_origins="")
    assert "http://localhost:3000" in origins


def test_cors_production_fails_closed_when_unconfigured():
    origins = _cors_origins_pure(is_production=True, cors_allowed_origins="")
    assert origins == []


def test_cors_production_uses_configured_origins_only():
    origins = _cors_origins_pure(
        is_production=True, cors_allowed_origins="https://app.example.com, https://staging.example.com"
    )
    assert set(origins) == {"https://app.example.com", "https://staging.example.com"}
    assert "http://localhost:3000" not in origins  # not silently added in prod


if __name__ == "__main__":
    test_fixed_window_groups_nearby_timestamps_together()
    test_fixed_window_separates_timestamps_across_a_boundary()
    test_fixed_window_is_deterministic_and_monotonic()
    test_set_workspace_context_uses_set_config_not_string_interpolation()
    test_set_workspace_context_passes_workspace_id_as_a_bound_param()
    test_cors_dev_always_includes_localhost()
    test_cors_production_fails_closed_when_unconfigured()
    test_cors_production_uses_configured_origins_only()
    print("All Phase 15 unit tests passed.")
