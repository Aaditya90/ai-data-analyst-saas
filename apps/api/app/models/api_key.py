"""
ApiKey (Phase 19): a long-lived programmatic credential, scoped to one
organization and acting as the user who created it.

The full key is never stored — only `key_prefix` (for lookup and so a key
can be recognised in a list) and `key_hash` (SHA-256 of the whole key); see
app/services/enterprise.py for the format and why SHA-256 is appropriate.

"Acts as the creating user" is deliberate: the key's effective permissions
are that user's workspace roles at request time (require_workspace_role
still runs), further narrowed by `scope` and restricted to workspaces in
`organization_id`. Remove the user from a workspace and the key loses
access to it immediately; delete the user and the key is deleted with them.

Not RLS-protected: like organizations/billing_events, this table is
organization-level, not workspace-level (no workspace_id column), and the
key must be looked up *before* any workspace context exists.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class ApiKey(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "api_keys"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(8), unique=True, nullable=False, index=True)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[str] = mapped_column(String(20), nullable=False)  # "read" | "read_write"
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
