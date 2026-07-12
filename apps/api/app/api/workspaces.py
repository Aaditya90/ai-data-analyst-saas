"""
Minimal workspace management needed to exercise the RBAC dependencies added
in this phase:
  - creating a workspace (creator becomes its owner)
  - listing the current user's workspaces
  - listing / adding members with a role

Fuller workspace features (invites by email to non-existing users, workspace
settings, deletion) are deliberately out of scope here — this phase is about
proving the auth + RBAC plumbing works end-to-end, not building the full
Team Collaboration feature (that's Phase 12).
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_workspace_role
from app.core.database import get_db
from app.models.organization import Organization
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_member import WorkspaceMember, WorkspaceRole

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


class WorkspaceCreate(BaseModel):
    name: str
    slug: str
    organization_id: uuid.UUID


class MemberAdd(BaseModel):
    email: str
    role: WorkspaceRole = WorkspaceRole.VIEWER


@router.post("", status_code=status.HTTP_201_CREATED)
def create_workspace(
    body: WorkspaceCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    org = db.get(Organization, body.organization_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found"
        )

    workspace = Workspace(name=body.name, slug=body.slug, organization_id=org.id)
    db.add(workspace)
    db.flush()  # assigns workspace.id without committing yet

    membership = WorkspaceMember(
        workspace_id=workspace.id,
        user_id=current_user.id,
        role=WorkspaceRole.OWNER,
    )
    db.add(membership)
    db.commit()
    db.refresh(workspace)

    return {"id": str(workspace.id), "name": workspace.name, "slug": workspace.slug}


@router.get("")
def list_my_workspaces(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    memberships = (
        db.query(WorkspaceMember)
        .filter(WorkspaceMember.user_id == current_user.id)
        .all()
    )
    return [
        {
            "id": str(m.workspace_id),
            "name": m.workspace.name,
            "slug": m.workspace.slug,
            "role": m.role.value,
        }
        for m in memberships
    ]


@router.get("/{workspace_id}/members")
def list_members(
    workspace_id: uuid.UUID,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.VIEWER)),
    db: Session = Depends(get_db),
) -> list[dict]:
    members = (
        db.query(WorkspaceMember)
        .filter(WorkspaceMember.workspace_id == workspace_id)
        .all()
    )
    return [
        {
            "user_id": str(m.user_id),
            "email": m.user.email,
            "role": m.role.value,
        }
        for m in members
    ]


@router.post("/{workspace_id}/members", status_code=status.HTTP_201_CREATED)
def add_member(
    workspace_id: uuid.UUID,
    body: MemberAdd,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.ADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    target_user = db.query(User).filter(User.email == body.email).one_or_none()
    if target_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No user with that email has signed in to the platform yet",
        )

    existing = (
        db.query(WorkspaceMember)
        .filter(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == target_user.id,
        )
        .one_or_none()
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already a member of this workspace",
        )

    new_membership = WorkspaceMember(
        workspace_id=workspace_id, user_id=target_user.id, role=body.role
    )
    db.add(new_membership)
    db.commit()

    return {"user_id": str(target_user.id), "email": target_user.email, "role": body.role.value}
