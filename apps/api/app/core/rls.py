"""
Row-Level Security (RLS) session context.

Phase 1's initial migration enabled RLS on `workspaces` with a policy that
reads `current_setting('app.current_workspace_id', true)`; Phase 15's
migration (0010) extends that same policy to every other workspace-owned
table and adds FORCE ROW LEVEL SECURITY so it actually applies (see that
migration's docstring for the full story, including why the app also
needs to connect as a non-superuser role for any of this to matter).

This module is the runtime half: actually setting that session variable,
safely, once per workspace-scoped request.
"""

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

# Uses Postgres's set_config() function rather than a literal
# `SET LOCAL ... = '<value>'` string, specifically so the workspace id is
# passed as a genuine bound parameter — like any other query parameter —
# instead of being interpolated into SQL text. This is the one place in
# the codebase that can't avoid handwritten SQL (there's no ORM-level API
# for setting a Postgres GUC), so it gets the same "never string-format a
# value into a query" discipline as everywhere else, via set_config's
# third positional argument.
#
# is_local=true (that third argument) makes this transaction-scoped: it's
# automatically cleared when the current transaction commits or rolls
# back. That matters because `db`'s underlying connection returns to a
# pool and gets reused by a *different* request afterward — a plain
# session-level SET would otherwise leak this workspace's id into
# whichever request reuses that pooled connection next. Transaction-local
# scoping means there's nothing to remember to reset.
_SET_WORKSPACE_CONTEXT = text("SELECT set_config('app.current_workspace_id', :workspace_id, true)")


def set_workspace_context(db: Session, workspace_id: uuid.UUID) -> None:
    """
    Scopes every RLS-protected query on `db` for the rest of the current
    transaction to `workspace_id`. Called once per request by
    `require_workspace_role` (app/api/deps.py), right after confirming
    the caller is actually a member of that workspace — so RLS becomes a
    second, database-enforced line of defense behind the app-layer
    membership check that already happened, not a replacement for it. A
    bug that forgot a `.filter(Model.workspace_id == workspace_id)`
    somewhere would still be caught here instead of leaking cross-tenant
    rows.
    """
    db.execute(_SET_WORKSPACE_CONTEXT, {"workspace_id": str(workspace_id)})
