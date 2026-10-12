"""
Phase 18: automates the manual step Phase 15 left to the operator —
giving the `app_runtime` Postgres role a password after migration 0010
creates it (a password never belongs in a committed migration).

Run after `alembic upgrade head`, as the admin/owner role:
    APP_RUNTIME_PASSWORD=... python scripts/set_app_runtime_password.py

Reads DATABASE_URL (admin) and APP_RUNTIME_PASSWORD from the environment.
The password is embedded via psycopg's sql.Literal, so it is quoted
correctly rather than string-formatted. Idempotent: safe to run on every
deploy. Exits non-zero if the role is missing (migrations not applied).
"""

import os
import sys

import psycopg
from psycopg import sql


def main() -> int:
    database_url = os.environ.get("DATABASE_URL", "")
    password = os.environ.get("APP_RUNTIME_PASSWORD", "")
    if not database_url or not password:
        print("DATABASE_URL and APP_RUNTIME_PASSWORD must both be set", file=sys.stderr)
        return 2

    url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(url, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'app_runtime'").fetchone()
        if not exists:
            print("role app_runtime not found — run `alembic upgrade head` first", file=sys.stderr)
            return 1
        conn.execute(
            sql.SQL("ALTER ROLE app_runtime WITH PASSWORD {}").format(sql.Literal(password))
        )
    print("app_runtime password set")
    return 0


if __name__ == "__main__":
    sys.exit(main())
