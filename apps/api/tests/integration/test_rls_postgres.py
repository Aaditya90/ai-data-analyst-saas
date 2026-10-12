"""
Phase 17 (Testing): real Postgres Row-Level Security verification.

Phase 15 shipped RLS but could only document how to verify it by hand
(see README "Testing Phase 15" and test_security.py) because SQLite has
no RLS. This automates that manual check for CI, where a real Postgres
service is available.

Requires two env vars (CI sets both; see .github/workflows/tests.yml):
  DATABASE_URL  - admin/owner connection (a Postgres superuser bypasses
                  RLS, which is what we want for seeding + cleanup)
  APP_DB_URL    - connection as the non-superuser `app_runtime` role, the
                  one RLS actually applies to
and migrations applied (`alembic upgrade head`) plus a password set on
`app_runtime`. If either var is missing or the DB is unreachable, every
test here is skipped rather than failed — this file must never break a
plain `pytest` run on a laptop without Postgres.

Run with: pytest tests/integration/test_rls_postgres.py -m postgres
"""

import os
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

pytestmark = pytest.mark.postgres


def _to_psycopg_url(url: str) -> str:
    # SQLAlchemy URLs look like postgresql+psycopg://...; psycopg wants
    # plain postgresql://...
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


@pytest.fixture(scope="module")
def admin_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — Postgres RLS tests skipped")
    return _to_psycopg_url(url)


@pytest.fixture(scope="module")
def runtime_url() -> str:
    url = os.environ.get("APP_DB_URL")
    if not url:
        pytest.skip("APP_DB_URL not set — Postgres RLS tests skipped")
    return _to_psycopg_url(url)


@pytest.fixture
def two_workspaces(admin_url):
    """Seeds one org with two workspaces as the admin role (bypasses RLS),
    yields their ids, and deletes them afterward."""
    try:
        conn = psycopg.connect(admin_url, autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("Postgres not reachable — RLS tests skipped")

    org_id, ws_a, ws_b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    suffix = uuid.uuid4().hex[:8]
    with conn:
        conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES (%s, %s, %s)",
            (org_id, "RLS Test Org", f"rls-org-{suffix}"),
        )
        for ws_id, label in ((ws_a, "a"), (ws_b, "b")):
            conn.execute(
                "INSERT INTO workspaces (id, name, slug, organization_id) VALUES (%s, %s, %s, %s)",
                (ws_id, f"RLS WS {label}", f"rls-ws-{label}-{suffix}", org_id),
            )
        try:
            yield ws_a, ws_b
        finally:
            conn.execute("DELETE FROM workspaces WHERE id = ANY(%s)", ([ws_a, ws_b],))
            conn.execute("DELETE FROM organizations WHERE id = %s", (org_id,))


def _visible_ids(runtime_url: str, context_workspace_id) -> set:
    """Opens a fresh runtime-role transaction, optionally sets the same GUC
    app.core.rls.set_workspace_context sets, and returns visible workspace ids."""
    with psycopg.connect(runtime_url) as conn:
        if context_workspace_id is not None:
            conn.execute(
                "SELECT set_config('app.current_workspace_id', %s, true)",
                (str(context_workspace_id),),
            )
        rows = conn.execute("SELECT id FROM workspaces").fetchall()
        return {r[0] for r in rows}


def test_context_set_shows_only_that_workspace(runtime_url, two_workspaces):
    ws_a, ws_b = two_workspaces
    visible = _visible_ids(runtime_url, ws_a)
    assert ws_a in visible
    assert ws_b not in visible


def test_switching_context_switches_visibility(runtime_url, two_workspaces):
    ws_a, ws_b = two_workspaces
    visible = _visible_ids(runtime_url, ws_b)
    assert ws_b in visible
    assert ws_a not in visible


def test_context_for_unrelated_workspace_shows_neither(runtime_url, two_workspaces):
    ws_a, ws_b = two_workspaces
    visible = _visible_ids(runtime_url, uuid.uuid4())
    assert ws_a not in visible
    assert ws_b not in visible


def test_context_is_transaction_scoped_and_does_not_leak(runtime_url, two_workspaces):
    # is_local=true means the setting must vanish when the transaction
    # ends. A fresh connection with no context set sees rows under the
    # policy's "GUC is NULL -> allow" branch (the deliberate escape hatch
    # for migrations/system jobs — see migration 0010), which proves the
    # earlier context did not persist into it.
    ws_a, _ = two_workspaces
    _visible_ids(runtime_url, ws_a)  # sets + discards a context
    with psycopg.connect(runtime_url) as conn:
        current = conn.execute("SELECT current_setting('app.current_workspace_id', true)").fetchone()[0]
    assert current in (None, "")


def test_unset_context_on_a_reused_connection_does_not_error(runtime_url, two_workspaces):
    """
    Phase 19 addition. Org-level routes (billing, audit export, API-key
    auth's workspace lookup) query RLS-protected tables with NO workspace
    context set. The policy handles a *never-set* setting (NULL), but on a
    pooled connection that previously ran a workspace-scoped transaction,
    Postgres may report the reset setting as '' (empty string) rather than
    NULL, and `''::uuid` raises. This runs a scoped transaction and then an
    unscoped one on the SAME connection to prove which behavior you get.

    If this fails with 'invalid input syntax for type uuid', the fix is a
    migration replacing the policies' current_setting(...) reads with
    NULLIF(current_setting('app.current_workspace_id', true), ''). It
    was not verifiable in the sandbox this suite was written in — that is
    exactly why it is a test.
    """
    with psycopg.connect(runtime_url) as conn:
        with conn.transaction():
            conn.execute("SELECT set_config('app.current_workspace_id', %s, true)", (str(two_workspaces[0]),))
            conn.execute("SELECT id FROM workspaces").fetchall()
        with conn.transaction():
            rows = conn.execute("SELECT id FROM workspaces").fetchall()  # no context set
    ids = {r[0] for r in rows}
    assert two_workspaces[0] in ids and two_workspaces[1] in ids
