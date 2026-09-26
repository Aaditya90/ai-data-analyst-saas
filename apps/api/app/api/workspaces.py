"""
Workspace management. Phase 2 established creation, listing, and adding an
already-registered member; Phase 12 fills in the rest of membership
lifecycle: changing a member's role, removing a member, and leaving a
workspace yourself — all guarded so the last OWNER can never be demoted,
removed, or leave, which would strand the workspace with no one able to
manage it.
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
from app.services.activity import log_activity

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


class WorkspaceCreate(BaseModel):
    name: str
    slug: str
    organization_id: uuid.UUID


class MemberAdd(BaseModel):
    email: str
    role: WorkspaceRole = WorkspaceRole.VIEWER


class MemberRoleUpdate(BaseModel):
    role: WorkspaceRole


def _owner_count(db: Session, workspace_id: uuid.UUID) -> int:
    return (
        db.query(WorkspaceMember)
        .filter(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.role == WorkspaceRole.OWNER,
        )
        .count()
    )


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


@router.patch("/{workspace_id}/members/{user_id}")
def update_member_role(
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    body: MemberRoleUpdate,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.ADMIN)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    target = (
        db.query(WorkspaceMember)
        .filter(WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user_id)
        .one_or_none()
    )
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    # An admin can't promote themselves or anyone else to owner — only an
    # existing owner can hand off ownership, same reasoning as "you can't
    # grant a role higher than your own."
    if body.role == WorkspaceRole.OWNER and member.role != WorkspaceRole.OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an owner can promote another member to owner",
        )

    if target.role == WorkspaceRole.OWNER and body.role != WorkspaceRole.OWNER:
        if _owner_count(db, workspace_id) <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot demote the last owner — promote another member to owner first",
            )

    from_role = target.role
    target.role = body.role

    log_activity(
        db,
        workspace_id=workspace_id,
        actor_user_id=current_user.id,
        action="member.role_changed",
        target_type="workspace_member",
        target_id=user_id,
        metadata={"from_role": from_role.value, "to_role": body.role.value},
    )
    db.commit()

    return {"user_id": str(user_id), "role": target.role.value}


@router.delete("/{workspace_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.ADMIN)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    target = (
        db.query(WorkspaceMember)
        .filter(WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user_id)
        .one_or_none()
    )
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    if target.role == WorkspaceRole.OWNER and _owner_count(db, workspace_id) <= 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot remove the last owner — promote another member to owner first",
        )

    target_email = target.user.email
    log_activity(
        db,
        workspace_id=workspace_id,
        actor_user_id=current_user.id,
        action="member.removed",
        target_type="workspace_member",
        target_id=user_id,
        metadata={"email": target_email, "role": target.role.value},
    )
    db.delete(target)
    db.commit()


@router.post("/{workspace_id}/leave", status_code=status.HTTP_204_NO_CONTENT)
def leave_workspace(
    workspace_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    membership = (
        db.query(WorkspaceMember)
        .filter(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == current_user.id,
        )
        .one_or_none()
    )
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

    if membership.role == WorkspaceRole.OWNER and _owner_count(db, workspace_id) <= 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You're the last owner — promote another member to owner before leaving",
        )

    log_activity(
        db,
        workspace_id=workspace_id,
        actor_user_id=current_user.id,
        action="member.left",
        target_type="workspace_member",
        target_id=current_user.id,
        metadata={"email": current_user.email, "role": membership.role.value},
    )
    db.delete(membership)
    db.commit()
