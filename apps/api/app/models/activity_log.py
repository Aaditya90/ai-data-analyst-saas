"""
An ActivityLog row is one append-only entry in a workspace's activity feed
— "who did what, to what, when." Rows are never updated or deleted (except
by workspace cascade delete); this is an audit trail, not app state, so it
follows the same "computed once, immutable" posture as reports/models
rather than the mutable-in-place posture of DashboardComment.

This phase wires logging into the team-collaboration actions it introduces
(invites sent/accepted/revoked, member role changes/removals, comments).
Wiring it into every earlier phase's mutations too (dataset uploads,
dashboard edits, report generation, ...) is deliberately left out — see
README "What Phase 12 Deliberately Does Not Include." The `metadata_json`
shape is intentionally per-action-type free-form (JSONB) so future phases
can log their own events through `log_activity()` without a migration.
"""

import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class ActivityLog(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "activity_logs"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # e.g. "member.invited", "member.role_changed", "comment.created" — a
    # small closed-ish vocabulary maintained in app/services/activity.py,
    # not a DB enum, so new event types don't need a migration.
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    # What the action was about, e.g. target_type="workspace_member",
    # target_id=<user id>. Both nullable for workspace-level events.
    target_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # Free-form details specific to the action, e.g. {"email": ..., "role": ...}
    # or {"from_role": ..., "to_role": ...}. Rendered into human-readable
    # feed text by the frontend / a small formatter, not stored pre-rendered.
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    actor: Mapped["User | None"] = relationship()  # noqa: F821
