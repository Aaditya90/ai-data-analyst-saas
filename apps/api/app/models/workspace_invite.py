"""
A WorkspaceInvite lets an admin/owner bring someone into a workspace by
email, whether or not that email has ever signed in before. This is the
piece Phase 2's workspaces.py explicitly deferred ("invites by email to
non-existing users") — `add_member` (Phase 2) only works if the target
user already exists locally; invites work for anyone.

Lifecycle: PENDING -> ACCEPTED | REVOKED, or PENDING -> (expires_at passes)
treated as expired at read-time (we don't flip a background job to do
this — `is_expired` is computed from `expires_at` whenever the row is
read, same "derive, don't drift" reasoning as elsewhere in this codebase).

The token is a high-entropy random string (not a JWT — it doesn't need to
carry claims, just to be unguessable), emailed to the invitee as part of
an accept link. It's stored hashed-free in plaintext here because it's a
single-use, short-lived, revocable secret scoped to one workspace/email
pair — not a credential that unlocks anything on its own without also
controlling that email inbox. (Contrast with DataConnection passwords,
which are Fernet-encrypted because they're long-lived third-party
credentials.)
"""

import enum
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.workspace_member import WorkspaceRole

INVITE_EXPIRY_DAYS = 7


class WorkspaceInviteStatus(str, enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"


def _default_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=INVITE_EXPIRY_DAYS)


class WorkspaceInvite(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "workspace_invites"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    role: Mapped[WorkspaceRole] = mapped_column(
        Enum(WorkspaceRole, name="workspace_role"), nullable=False, default=WorkspaceRole.VIEWER
    )
    status: Mapped[WorkspaceInviteStatus] = mapped_column(
        Enum(WorkspaceInviteStatus, name="workspace_invite_status"),
        nullable=False,
        default=WorkspaceInviteStatus.PENDING,
    )
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    invited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    accepted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_default_expiry
    )

    workspace: Mapped["Workspace"] = relationship()  # noqa: F821

    @property
    def is_expired(self) -> bool:
        expires_at = self.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > expires_at

    @property
    def is_pending(self) -> bool:
        return self.status == WorkspaceInviteStatus.PENDING and not self.is_expired
