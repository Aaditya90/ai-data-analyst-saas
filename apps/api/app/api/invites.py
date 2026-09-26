"""
Workspace invites API.

Two routers live in this file:
  - `router` (workspace-scoped, requires ADMIN+): create/list/revoke/resend
    invites for a specific workspace.
  - `public_router` (not workspace-scoped — the person accepting isn't a
    member yet, so `require_workspace_role` can't gate it): look up an
    invite by its token and accept it.

Endpoints:
  POST   /workspaces/{id}/invites                - create an invite
  GET    /workspaces/{id}/invites                - list invites (default: pending only)
  DELETE /workspaces/{id}/invites/{invite_id}     - revoke a pending invite
  POST   /workspaces/{id}/invites/{invite_id}/resend - resend the invite email
  GET    /invites/{token}                        - look up invite details
  POST   /invites/{token}/accept                  - accept -> becomes a member

This is the feature Phase 2's workspaces.py deliberately left out: inviting
someone by email who has never signed in to the platform. They can accept
as soon as they *have* signed in (via Clerk) with a matching email — we
don't create a placeholder User row up front, since Phase 2's auth flow
(`get_current_user`) already creates the local User mirror on first sign-in.
"""

import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_workspace_role
from app.core.database import get_db
from app.models.user import User
from app.models.organization import Organization
from app.models.workspace import Workspace
from app.models.workspace_invite import (
    INVITE_EXPIRY_DAYS,
    WorkspaceInvite,
    WorkspaceInviteStatus,
    _default_expiry,
)
from app.models.workspace_member import WorkspaceMember, WorkspaceRole
from app.services.activity import log_activity
from app.services.email import EmailSendError, send_invite_email
from app.services.plan_limits import get_plan_limits, is_under_limit, seats_in_use_for_workspace

router = APIRouter(prefix="/workspaces/{workspace_id}/invites", tags=["invites"])
public_router = APIRouter(prefix="/invites", tags=["invites"])


class InviteCreate(BaseModel):
    email: EmailStr
    role: WorkspaceRole = WorkspaceRole.VIEWER


class InviteOut(BaseModel):
    id: str
    email: str
    role: str
    status: str
    is_expired: bool
    expires_at: str
    invited_by_email: str | None
    created_at: str


def _serialize(invite: WorkspaceInvite, db: Session) -> InviteOut:
    inviter = db.get(User, invite.invited_by_user_id) if invite.invited_by_user_id else None
    return InviteOut(
        id=str(invite.id),
        email=invite.email,
        role=invite.role.value,
        status=invite.status.value,
        is_expired=invite.is_expired,
        expires_at=invite.expires_at.isoformat(),
        invited_by_email=inviter.email if inviter else None,
        created_at=invite.created_at.isoformat(),
    )


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=InviteOut)
def create_invite(
    workspace_id: uuid.UUID,
    body: InviteCreate,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.ADMIN)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InviteOut:
    workspace = db.get(Workspace, workspace_id)
    email_normalized = body.email.lower()

    existing_member_user = db.query(User).filter(User.email == email_normalized).one_or_none()
    if existing_member_user is not None:
        already_member = (
            db.query(WorkspaceMember)
            .filter(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == existing_member_user.id,
            )
            .one_or_none()
        )
        if already_member is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This person is already a member of the workspace",
            )

    existing_pending = (
        db.query(WorkspaceInvite)
        .filter(
            WorkspaceInvite.workspace_id == workspace_id,
            WorkspaceInvite.email == email_normalized,
            WorkspaceInvite.status == WorkspaceInviteStatus.PENDING,
        )
        .one_or_none()
    )
    if existing_pending is not None and not existing_pending.is_expired:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An invite is already pending for this email",
        )

    org = db.get(Organization, workspace.organization_id)
    limits = get_plan_limits(org.plan)
    current_seats = seats_in_use_for_workspace(db, workspace_id)
    if not is_under_limit(current_seats, limits["max_seats_per_workspace"]):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"The '{org.plan}' plan allows at most {limits['max_seats_per_workspace']} "
            f"member(s)/pending invite(s) in this workspace. Upgrade or free up a seat first.",
        )

    invite = WorkspaceInvite(
        workspace_id=workspace_id,
        email=email_normalized,
        role=body.role,
        token=_generate_token(),
        invited_by_user_id=current_user.id,
        expires_at=_default_expiry(),
    )
    db.add(invite)
    db.flush()

    log_activity(
        db,
        workspace_id=workspace_id,
        actor_user_id=current_user.id,
        action="member.invited",
        target_type="workspace_invite",
        target_id=invite.id,
        metadata={"email": email_normalized, "role": body.role.value},
    )
    db.commit()
    db.refresh(invite)

    try:
        send_invite_email(
            to_email=email_normalized,
            workspace_name=workspace.name,
            inviter_email=current_user.email,
            role=body.role.value,
            token=invite.token,
        )
    except EmailSendError:
        # The invite row + activity log are already committed — a mail
        # server hiccup shouldn't roll back the invite itself, just
        # surface as a 202-ish note. Resend covers retrying the email.
        pass

    return _serialize(invite, db)


@router.get("", response_model=list[InviteOut])
def list_invites(
    workspace_id: uuid.UUID,
    include_all: bool = False,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.ADMIN)),
    db: Session = Depends(get_db),
) -> list[InviteOut]:
    query = db.query(WorkspaceInvite).filter(WorkspaceInvite.workspace_id == workspace_id)
    if not include_all:
        query = query.filter(WorkspaceInvite.status == WorkspaceInviteStatus.PENDING)
    invites = query.order_by(WorkspaceInvite.created_at.desc()).all()
    return [_serialize(i, db) for i in invites]


@router.delete("/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_invite(
    workspace_id: uuid.UUID,
    invite_id: uuid.UUID,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.ADMIN)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    invite = (
        db.query(WorkspaceInvite)
        .filter(WorkspaceInvite.id == invite_id, WorkspaceInvite.workspace_id == workspace_id)
        .one_or_none()
    )
    if invite is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
    if invite.status != WorkspaceInviteStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Only a pending invite can be revoked"
        )

    invite.status = WorkspaceInviteStatus.REVOKED
    log_activity(
        db,
        workspace_id=workspace_id,
        actor_user_id=current_user.id,
        action="member.invite_revoked",
        target_type="workspace_invite",
        target_id=invite.id,
        metadata={"email": invite.email},
    )
    db.commit()


@router.post("/{invite_id}/resend", response_model=InviteOut)
def resend_invite(
    workspace_id: uuid.UUID,
    invite_id: uuid.UUID,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.ADMIN)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InviteOut:
    invite = (
        db.query(WorkspaceInvite)
        .filter(WorkspaceInvite.id == invite_id, WorkspaceInvite.workspace_id == workspace_id)
        .one_or_none()
    )
    if invite is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
    if invite.status != WorkspaceInviteStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Only a pending invite can be resent"
        )

    # Resending also refreshes the expiry — otherwise a resend two days
    # before the original expiry would still die a day later, confusingly.
    invite.expires_at = _default_expiry()
    workspace = db.get(Workspace, workspace_id)

    log_activity(
        db,
        workspace_id=workspace_id,
        actor_user_id=current_user.id,
        action="member.invite_resent",
        target_type="workspace_invite",
        target_id=invite.id,
        metadata={"email": invite.email},
    )
    db.commit()
    db.refresh(invite)

    try:
        send_invite_email(
            to_email=invite.email,
            workspace_name=workspace.name,
            inviter_email=current_user.email,
            role=invite.role.value,
            token=invite.token,
        )
    except EmailSendError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc

    return _serialize(invite, db)


class InviteDetail(BaseModel):
    workspace_name: str
    email: str
    role: str
    status: str
    is_expired: bool
    expires_in_days: int


@public_router.get("/{token}", response_model=InviteDetail)
def get_invite(token: str, db: Session = Depends(get_db)) -> InviteDetail:
    invite = db.query(WorkspaceInvite).filter(WorkspaceInvite.token == token).one_or_none()
    if invite is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")

    workspace = db.get(Workspace, invite.workspace_id)
    return InviteDetail(
        workspace_name=workspace.name,
        email=invite.email,
        role=invite.role.value,
        status=invite.status.value,
        is_expired=invite.is_expired,
        expires_in_days=INVITE_EXPIRY_DAYS,
    )


@public_router.post("/{token}/accept", status_code=status.HTTP_201_CREATED)
def accept_invite(
    token: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    invite = db.query(WorkspaceInvite).filter(WorkspaceInvite.token == token).one_or_none()
    if invite is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
    if invite.status != WorkspaceInviteStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This invite has already been {invite.status.value}",
        )
    if invite.is_expired:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="This invite has expired")
    if current_user.email.lower() != invite.email.lower():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This invite was sent to a different email address. Sign in with "
            f"{invite.email} to accept it.",
        )

    existing = (
        db.query(WorkspaceMember)
        .filter(
            WorkspaceMember.workspace_id == invite.workspace_id,
            WorkspaceMember.user_id == current_user.id,
        )
        .one_or_none()
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You are already a member of this workspace",
        )

    membership = WorkspaceMember(
        workspace_id=invite.workspace_id, user_id=current_user.id, role=invite.role
    )
    db.add(membership)

    invite.status = WorkspaceInviteStatus.ACCEPTED
    invite.accepted_by_user_id = current_user.id

    log_activity(
        db,
        workspace_id=invite.workspace_id,
        actor_user_id=current_user.id,
        action="member.invite_accepted",
        target_type="workspace_member",
        target_id=current_user.id,
        metadata={"email": invite.email, "role": invite.role.value},
    )
    db.commit()

    return {
        "workspace_id": str(invite.workspace_id),
        "role": invite.role.value,
        "message": "Invite accepted — you're now a member of this workspace.",
    }
