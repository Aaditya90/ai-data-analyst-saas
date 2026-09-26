"""
Read-only activity feed for a workspace — GET only, since entries are
written internally via app/services/activity.log_activity() from the
actions that generate them (invites, member changes, comments), not
posted directly by clients.

  GET /workspaces/{id}/activity?limit=50&before=<ISO timestamp>&action=member.invited

Cursor-paginated on created_at (newest first) rather than offset, since
this is an append-only, ever-growing log — offset pagination on a table
that only grows gets slower and more skip-prone the further back you page.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_workspace_role
from app.core.database import get_db
from app.models.activity_log import ActivityLog
from app.models.user import User
from app.models.workspace_member import WorkspaceMember, WorkspaceRole

router = APIRouter(prefix="/workspaces/{workspace_id}/activity", tags=["activity"])

MAX_LIMIT = 200


class ActivityOut(BaseModel):
    id: str
    actor_user_id: str | None
    actor_email: str | None
    action: str
    target_type: str | None
    target_id: str | None
    metadata: dict
    created_at: str


def _serialize(entry: ActivityLog, db: Session) -> ActivityOut:
    actor = db.get(User, entry.actor_user_id) if entry.actor_user_id else None
    return ActivityOut(
        id=str(entry.id),
        actor_user_id=str(entry.actor_user_id) if entry.actor_user_id else None,
        actor_email=actor.email if actor else None,
        action=entry.action,
        target_type=entry.target_type,
        target_id=str(entry.target_id) if entry.target_id else None,
        metadata=entry.metadata_json,
        created_at=entry.created_at.isoformat(),
    )


@router.get("", response_model=list[ActivityOut])
def list_activity(
    workspace_id: uuid.UUID,
    limit: int = Query(default=50, le=MAX_LIMIT, gt=0),
    before: datetime | None = None,
    action: str | None = None,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.VIEWER)),
    db: Session = Depends(get_db),
) -> list[ActivityOut]:
    query = db.query(ActivityLog).filter(ActivityLog.workspace_id == workspace_id)
    if before is not None:
        query = query.filter(ActivityLog.created_at < before)
    if action is not None:
        query = query.filter(ActivityLog.action == action)

    entries = query.order_by(ActivityLog.created_at.desc()).limit(limit).all()
    return [_serialize(e, db) for e in entries]
