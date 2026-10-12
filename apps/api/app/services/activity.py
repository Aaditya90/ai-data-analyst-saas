"""
log_activity() is the single write path for ActivityLog rows — routes call
this instead of constructing ActivityLog objects themselves, so the set of
valid action strings stays in one place and every call site gets the same
shape.

Doesn't commit: callers add the log entry to the same transaction as the
mutation it describes (via db.add + the caller's own db.commit()), so an
activity row is never written for a mutation that then fails to commit.
"""

import uuid

from sqlalchemy.orm import Session

from app.models.activity_log import ActivityLog

# Maintained here as documentation of the known vocabulary; the DB column
# is a plain string (not an enum) so new actions never need a migration.
ACTIONS = {
    "member.invited",
    "member.invite_accepted",
    "member.invite_revoked",
    "member.invite_resent",
    "member.role_changed",
    "member.removed",
    "member.left",
    "comment.created",
    "comment.resolved",
    "comment.reopened",
    "comment.deleted",
}


def log_activity(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    action: str,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    metadata: dict | None = None,
) -> ActivityLog:
    entry = ActivityLog(
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        metadata_json=metadata or {},
    )
    db.add(entry)
    db.flush()  # assigns entry.id without ending the caller's transaction
    return entry
