"""
Reusable FastAPI dependencies for authentication and workspace-scoped
authorization.

Every protected route in every future phase should depend on
`get_current_user` (who is this?) and, where the route operates on
workspace data, `require_workspace_role(...)` (are they allowed to do this,
in this workspace?). Keeping both checks as dependencies — rather than
scattered manual checks inside route bodies — means they can't accidentally
be skipped in a new route.
"""

import uuid
from enum import IntEnum

from fastapi import Depends, HTTPException, Header, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import verify_session_token
from app.models.user import User
from app.models.workspace_member import WorkspaceMember, WorkspaceRole


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """
    Verifies the bearer token and returns the corresponding local User,
    creating one on first sight (Clerk is the source of truth for identity;
    our `users` table is a local mirror keyed on auth_provider_id).
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header",
        )
    token = authorization.removeprefix("Bearer ").strip()
    claims = verify_session_token(token)

    auth_provider_id = claims["sub"]
    email = claims.get("email") or claims.get("primary_email_address")

    user = (
        db.query(User)
        .filter(User.auth_provider_id == auth_provider_id)
        .one_or_none()
    )
    if user is None:
        # First request from this identity — mirror it locally. The webhook
        # handler (app/api/auth.py) also creates/updates users so this path
        # mainly covers the case where the webhook hasn't fired yet (e.g.
        # local dev without a public webhook URL).
        if not email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Token is missing an email claim; cannot create user",
            )
        user = User(auth_provider_id=auth_provider_id, email=email)
        db.add(user)
        db.commit()
        db.refresh(user)

    return user


# Role hierarchy — higher number = more privilege. Used so
# require_workspace_role("editor") also accepts admin/owner, not just editor.
_ROLE_RANK: dict[WorkspaceRole, int] = {
    WorkspaceRole.VIEWER: 0,
    WorkspaceRole.EDITOR: 1,
    WorkspaceRole.ADMIN: 2,
    WorkspaceRole.OWNER: 3,
}


def require_workspace_role(minimum_role: WorkspaceRole):
    """
    Returns a dependency that:
      1. Confirms the current user is a member of the given workspace_id
      2. Confirms their role meets or exceeds `minimum_role`

    Usage:
        @router.get("/workspaces/{workspace_id}/datasets")
        def list_datasets(
            member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.VIEWER)),
        ):
            ...

    Every future phase's workspace-scoped routes should use this rather than
    querying WorkspaceMember manually, so the isolation/authorization rule
    lives in exactly one place.
    """

    def dependency(
        workspace_id: uuid.UUID,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> WorkspaceMember:
        member = (
            db.query(WorkspaceMember)
            .filter(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == current_user.id,
            )
            .one_or_none()
        )
        if member is None:
            # 404, not 403 — don't reveal whether the workspace exists to
            # someone who isn't a member of it
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workspace not found",
            )
        if _ROLE_RANK[member.role] < _ROLE_RANK[minimum_role]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires '{minimum_role.value}' role or higher in this workspace",
            )
        return member

    return dependency
