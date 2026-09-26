"""
Dashboard comments API — threaded discussion on a Dashboard (Phase 6),
optionally pinned to one DashboardWidget on it.

  POST   /workspaces/{id}/dashboards/{did}/comments              - add a comment or reply
  GET    /workspaces/{id}/dashboards/{did}/comments               - list top-level comments (with replies nested)
  PATCH  /workspaces/{id}/dashboards/{did}/comments/{cid}          - edit body, or resolve/reopen
  DELETE /workspaces/{id}/dashboards/{did}/comments/{cid}          - delete (author or admin+)

Read/write split follows the rest of this codebase: VIEWER can read and
comment (collaboration shouldn't require edit rights just to leave
feedback), but editing someone else's comment or deleting it requires
either being the author or ADMIN+. Resolving/reopening a thread only
requires EDITOR+ (you don't need to have written the comment to close out
the conversation it started).
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_workspace_role
from app.core.database import get_db
from app.models.dashboard import Dashboard
from app.models.dashboard_comment import DashboardComment
from app.models.dashboard_widget import DashboardWidget
from app.models.user import User
from app.models.workspace_member import WorkspaceMember, WorkspaceRole
from app.services.activity import log_activity

router = APIRouter(
    prefix="/workspaces/{workspace_id}/dashboards/{dashboard_id}/comments", tags=["comments"]
)


class CommentCreate(BaseModel):
    body: str
    widget_id: uuid.UUID | None = None
    parent_comment_id: uuid.UUID | None = None


class CommentUpdate(BaseModel):
    body: str | None = None
    resolved: bool | None = None


class CommentOut(BaseModel):
    id: str
    dashboard_id: str
    widget_id: str | None
    parent_comment_id: str | None
    author_user_id: str
    author_email: str
    body: str
    resolved: bool
    resolved_by_email: str | None
    created_at: str
    updated_at: str
    replies: list["CommentOut"] = []


CommentOut.model_rebuild()


def _get_dashboard(db: Session, workspace_id: uuid.UUID, dashboard_id: uuid.UUID) -> Dashboard:
    dashboard = (
        db.query(Dashboard)
        .filter(Dashboard.id == dashboard_id, Dashboard.workspace_id == workspace_id)
        .one_or_none()
    )
    if dashboard is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dashboard not found")
    return dashboard


def _serialize(comment: DashboardComment, db: Session, include_replies: bool = True) -> CommentOut:
    resolver = (
        db.get(User, comment.resolved_by_user_id) if comment.resolved_by_user_id else None
    )
    return CommentOut(
        id=str(comment.id),
        dashboard_id=str(comment.dashboard_id),
        widget_id=str(comment.widget_id) if comment.widget_id else None,
        parent_comment_id=str(comment.parent_comment_id) if comment.parent_comment_id else None,
        author_user_id=str(comment.author_user_id),
        author_email=comment.author.email,
        body=comment.body,
        resolved=comment.resolved,
        resolved_by_email=resolver.email if resolver else None,
        created_at=comment.created_at.isoformat(),
        updated_at=comment.updated_at.isoformat(),
        replies=[_serialize(r, db, include_replies=False) for r in comment.replies]
        if include_replies
        else [],
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=CommentOut)
def create_comment(
    workspace_id: uuid.UUID,
    dashboard_id: uuid.UUID,
    body: CommentCreate,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.VIEWER)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CommentOut:
    _get_dashboard(db, workspace_id, dashboard_id)

    if not body.body.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Comment body cannot be empty")

    if body.widget_id is not None:
        widget = (
            db.query(DashboardWidget)
            .filter(DashboardWidget.id == body.widget_id, DashboardWidget.dashboard_id == dashboard_id)
            .one_or_none()
        )
        if widget is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Widget not found on this dashboard")

    parent = None
    if body.parent_comment_id is not None:
        parent = (
            db.query(DashboardComment)
            .filter(
                DashboardComment.id == body.parent_comment_id,
                DashboardComment.dashboard_id == dashboard_id,
            )
            .one_or_none()
        )
        if parent is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Parent comment not found")
        if parent.parent_comment_id is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Replies can only be one level deep — reply to the top-level comment",
            )

    comment = DashboardComment(
        workspace_id=workspace_id,
        dashboard_id=dashboard_id,
        widget_id=body.widget_id,
        parent_comment_id=body.parent_comment_id,
        author_user_id=current_user.id,
        body=body.body.strip(),
    )
    db.add(comment)
    db.flush()

    log_activity(
        db,
        workspace_id=workspace_id,
        actor_user_id=current_user.id,
        action="comment.created",
        target_type="dashboard_comment",
        target_id=comment.id,
        metadata={"dashboard_id": str(dashboard_id), "is_reply": body.parent_comment_id is not None},
    )
    db.commit()
    db.refresh(comment)
    return _serialize(comment, db)


@router.get("", response_model=list[CommentOut])
def list_comments(
    workspace_id: uuid.UUID,
    dashboard_id: uuid.UUID,
    widget_id: uuid.UUID | None = None,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.VIEWER)),
    db: Session = Depends(get_db),
) -> list[CommentOut]:
    _get_dashboard(db, workspace_id, dashboard_id)

    query = db.query(DashboardComment).filter(
        DashboardComment.dashboard_id == dashboard_id,
        DashboardComment.parent_comment_id.is_(None),
    )
    if widget_id is not None:
        query = query.filter(DashboardComment.widget_id == widget_id)

    comments = query.order_by(DashboardComment.created_at.asc()).all()
    return [_serialize(c, db) for c in comments]


@router.patch("/{comment_id}", response_model=CommentOut)
def update_comment(
    workspace_id: uuid.UUID,
    dashboard_id: uuid.UUID,
    comment_id: uuid.UUID,
    body: CommentUpdate,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.VIEWER)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CommentOut:
    comment = (
        db.query(DashboardComment)
        .filter(DashboardComment.id == comment_id, DashboardComment.dashboard_id == dashboard_id)
        .one_or_none()
    )
    if comment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found")

    is_author = comment.author_user_id == current_user.id
    is_admin_plus = member.role in (WorkspaceRole.ADMIN, WorkspaceRole.OWNER)
    is_editor_plus = member.role in (WorkspaceRole.EDITOR, WorkspaceRole.ADMIN, WorkspaceRole.OWNER)

    if body.body is not None:
        if not is_author and not is_admin_plus:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the comment's author or a workspace admin can edit it",
            )
        if not body.body.strip():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Comment body cannot be empty")
        comment.body = body.body.strip()

    if body.resolved is not None:
        if not is_editor_plus:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Requires 'editor' role or higher to resolve/reopen a comment",
            )
        comment.resolved = body.resolved
        comment.resolved_by_user_id = current_user.id if body.resolved else None
        log_activity(
            db,
            workspace_id=workspace_id,
            actor_user_id=current_user.id,
            action="comment.resolved" if body.resolved else "comment.reopened",
            target_type="dashboard_comment",
            target_id=comment.id,
            metadata={"dashboard_id": str(dashboard_id)},
        )

    db.commit()
    db.refresh(comment)
    return _serialize(comment, db)


@router.delete("/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_comment(
    workspace_id: uuid.UUID,
    dashboard_id: uuid.UUID,
    comment_id: uuid.UUID,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.VIEWER)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    comment = (
        db.query(DashboardComment)
        .filter(DashboardComment.id == comment_id, DashboardComment.dashboard_id == dashboard_id)
        .one_or_none()
    )
    if comment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found")

    is_author = comment.author_user_id == current_user.id
    is_admin_plus = member.role in (WorkspaceRole.ADMIN, WorkspaceRole.OWNER)
    if not is_author and not is_admin_plus:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the comment's author or a workspace admin can delete it",
        )

    log_activity(
        db,
        workspace_id=workspace_id,
        actor_user_id=current_user.id,
        action="comment.deleted",
        target_type="dashboard_comment",
        target_id=comment.id,
        metadata={"dashboard_id": str(dashboard_id)},
    )
    db.delete(comment)
    db.commit()
