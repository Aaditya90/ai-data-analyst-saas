"""
Plan limits: the single source of truth for what "free" / "pro" /
"enterprise" mean in terms of usage caps. Deliberately plain Python data
(not a DB table) — same "vocabulary in code, not a migration" reasoning as
Phase 12's ActivityLog.action — so adjusting a limit is a code change and
a deploy, not a data migration.

Scoping choice: `max_seats_per_workspace` caps members *within a single
workspace*, not organization-wide across every workspace. WorkspaceMember
roles are already workspace-scoped (see workspace_member.py), so billing
enforcement follows that same boundary rather than introducing a new
org-wide aggregation query. `max_workspaces` is the org-wide cap.

Enforcement call sites: app/api/workspaces.py (create_workspace checks
max_workspaces), app/api/workspaces.py (add_member) and app/api/invites.py
(create_invite) both check max_seats_per_workspace via
`seats_in_use_for_workspace`.
"""

import uuid

from sqlalchemy.orm import Session

from app.models.workspace import Workspace
from app.models.workspace_invite import WorkspaceInvite, WorkspaceInviteStatus
from app.models.workspace_member import WorkspaceMember

# None means "no cap." Kept intentionally small/round for a demo SaaS —
# real numbers would come from actual pricing decisions, not this codebase.
PLAN_LIMITS: dict[str, dict[str, int | None]] = {
    "free": {
        "max_workspaces": 1,
        "max_seats_per_workspace": 3,
    },
    "pro": {
        "max_workspaces": 10,
        "max_seats_per_workspace": 25,
    },
    "enterprise": {
        "max_workspaces": None,
        "max_seats_per_workspace": None,
    },
}

DEFAULT_PLAN = "free"


def get_plan_limits(plan: str) -> dict[str, int | None]:
    """Unknown/legacy plan strings fall back to `free`'s limits — the safe
    default is the most restrictive one, not the most permissive."""
    return PLAN_LIMITS.get(plan, PLAN_LIMITS[DEFAULT_PLAN])


def is_under_limit(current_count: int, limit: int | None) -> bool:
    """`limit is None` means unlimited. Pure — no DB, easy to unit test."""
    return limit is None or current_count < limit


def workspaces_in_use_for_org(db: Session, organization_id: uuid.UUID) -> int:
    return db.query(Workspace).filter(Workspace.organization_id == organization_id).count()


def seats_in_use_for_workspace(db: Session, workspace_id: uuid.UUID) -> int:
    """
    Active members + still-pending, non-expired invites, so a workspace at
    its seat cap can't be over-invited past it while those invites are
    outstanding. An invite that's expired, revoked, or already accepted
    doesn't count (accepted ones are already active members by then).
    """
    member_count = (
        db.query(WorkspaceMember).filter(WorkspaceMember.workspace_id == workspace_id).count()
    )
    pending_invites = (
        db.query(WorkspaceInvite)
        .filter(
            WorkspaceInvite.workspace_id == workspace_id,
            WorkspaceInvite.status == WorkspaceInviteStatus.PENDING,
        )
        .all()
    )
    non_expired_pending = sum(1 for i in pending_invites if not i.is_expired)
    return member_count + non_expired_pending
