"""security: RLS on every workspace-owned table + a non-superuser runtime role

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-27

This is the other half of the RLS scaffold from migration 0001 (see its
docstring). Two things happen here:

1. FORCE ROW LEVEL SECURITY on `workspaces` (0001 only did ENABLE) and
   ENABLE + FORCE + the same isolation policy on every other table that
   carries a direct `workspace_id` column: activity_logs, dashboards,
   dashboard_comments, dashboard_versions, data_connections, datasets,
   forecasts, ml_models, reports, workspace_invites, workspace_members.

   FORCE matters because Postgres table owners bypass RLS by default —
   ENABLE alone (0001's state) does nothing for a connection using the
   owner role, which is exactly what this app's migrations (and, until
   this phase, its runtime) connect as. FORCE makes the owner subject to
   the same policy as everyone else.

   dashboard_widgets and dataset_versions are NOT given their own policy
   here — they don't carry a workspace_id column (only dashboard_id /
   dataset_id), and their parent tables (dashboards, datasets) are
   already RLS-protected. A subquery-based policy for them
   (`dashboard_id IN (SELECT id FROM dashboards WHERE workspace_id = ...)`)
   is a reasonable future refinement, deliberately left out here — see
   README "What Phase 15 Deliberately Does NOT Include."

2. A new `app_runtime` Postgres role: NOSUPERUSER, NOBYPASSRLS (the
   default — stated explicitly for clarity), LOGIN, no password set here
   (see README "Testing Phase 15" for why — a password belongs in ops
   secrets management, never in a committed migration). Granted
   SELECT/INSERT/UPDATE/DELETE on every existing table, plus
   ALTER DEFAULT PRIVILEGES so every table any *future* migration creates
   is automatically granted too, without a per-phase GRANT to remember.

   IMPORTANT — this migration on its own does not change what the app
   connects as. `app/core/database.py` now reads `APP_DB_URL` (falling
   back to `DATABASE_URL` if unset) for its runtime engine, while Alembic
   keeps using `DATABASE_URL` (the owner/admin role) for migrations. RLS
   is only actually enforced once `APP_DB_URL` points at `app_runtime`
   with a password set via `ALTER ROLE app_runtime WITH PASSWORD '...'`
   (run by an operator, not by this migration). Note also that in the
   default local docker-compose setup, `POSTGRES_USER` is created as a
   Postgres *superuser* by the official Postgres image's initdb — and
   superusers bypass RLS unconditionally, FORCE or not. `app_runtime` is
   explicitly NOT a superuser, which is what makes it the role that
   actually gets enforced.
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

WORKSPACE_SCOPED_TABLES = [
    "activity_logs",
    "dashboards",
    "dashboard_comments",
    "dashboard_versions",
    "data_connections",
    "datasets",
    "forecasts",
    "ml_models",
    "reports",
    "workspace_invites",
    "workspace_members",
]


def upgrade() -> None:
    # --- 1. Finish enforcing RLS on `workspaces` (0001 left it ENABLE-only) ---
    op.execute("ALTER TABLE workspaces FORCE ROW LEVEL SECURITY")

    # --- 2. Same ENABLE + FORCE + policy pattern on every other workspace-owned table ---
    for table in WORKSPACE_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_isolation_policy ON {table}
            USING (
                workspace_id = current_setting('app.current_workspace_id', true)::uuid
                OR current_setting('app.current_workspace_id', true) IS NULL
            )
            """
        )

    # --- 3. Low-privilege runtime role, no password (see docstring) ---
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_runtime') THEN
                CREATE ROLE app_runtime NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS LOGIN;
            END IF;
        END
        $$;
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO app_runtime")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_runtime")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_runtime")
    # Applies to tables created by future migrations too, run as the same
    # admin role that's running this one — no per-phase GRANT to remember.
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_runtime"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT USAGE, SELECT ON SEQUENCES TO app_runtime"
    )


def downgrade() -> None:
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM app_runtime"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "REVOKE USAGE, SELECT ON SEQUENCES FROM app_runtime"
    )
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM app_runtime")
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM app_runtime")
    op.execute("REVOKE USAGE ON SCHEMA public FROM app_runtime")
    # Role itself is intentionally left in place on downgrade — dropping a
    # role that might own a live app connection mid-deploy is riskier than
    # leaving an unused, now-unprivileged role behind. Drop it manually
    # (`DROP ROLE app_runtime;`) once nothing references it, if desired.

    for table in reversed(WORKSPACE_SCOPED_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_isolation_policy ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.execute("ALTER TABLE workspaces NO FORCE ROW LEVEL SECURITY")
