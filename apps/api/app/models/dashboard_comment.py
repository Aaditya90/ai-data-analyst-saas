"""
A DashboardComment is a discussion note attached to a Dashboard (Phase 6)
and, optionally, a single DashboardWidget on it — e.g. "why did this KPI
jump in March?" pinned to the KPI tile rather than the dashboard at large.

Threading is one level deep (parent_comment_id -> top-level comment only):
replies-to-replies would need recursive UI/query work this phase doesn't
need to justify yet. `resolved` lets a thread be marked done without
deleting it, same "never destroy history, just mark state" instinct as
DatasetVersion/MLModel immutability elsewhere in this codebase.

Unlike most tables so far, comments are intentionally mutable in place
(editing/resolving a comment updates the same row) — a comment is a live
conversation artifact, not a computed/derived one, so it doesn't fit the
immutable-version pattern used for datasets, models, and reports.
"""

import uuid

from sqlalchemy import Boolean, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class DashboardComment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "dashboard_comments"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dashboard_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dashboards.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    widget_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dashboard_widgets.id", ondelete="CASCADE"), nullable=True
    )
    parent_comment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dashboard_comments.id", ondelete="CASCADE"), nullable=True
    )

    author_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)

    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    author: Mapped["User"] = relationship(foreign_keys=[author_user_id])  # noqa: F821
    replies: Mapped[list["DashboardComment"]] = relationship(
        "DashboardComment",
        back_populates="parent",
        cascade="all, delete-orphan",
        order_by="DashboardComment.created_at",
    )
    parent: Mapped["DashboardComment | None"] = relationship(
        "DashboardComment",
        remote_side="DashboardComment.id",
        back_populates="replies",
    )
