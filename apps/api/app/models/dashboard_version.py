"""
A DashboardVersion is a full, immutable snapshot of a Dashboard's name and
widget list at one point in time — the dashboard-builder equivalent of
Phase 3's DatasetVersion, for the one major mutable-in-place resource that
didn't already have version history (Dataset/DatasetVersion has had it
since Phase 3; MLModel/Forecast/Report are already immutable rows; a
Dashboard's widgets, by contrast, are edited in place with no trail).

Snapshots are taken automatically by `app/services/dashboard_versioning.py`
on every dashboard-shape-changing action (create, rename, add/update/delete
widget) — never created directly by a client request — so the history is
always complete rather than opt-in per edit.

`widgets_snapshot_json` stores each widget as a plain dict (see
`serialize_widgets_for_snapshot`), not a foreign key to DashboardWidget
rows, because a widget can be edited or deleted after the snapshot is
taken — the snapshot has to survive that.
"""

import uuid

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class DashboardVersion(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "dashboard_versions"
    __table_args__ = (
        UniqueConstraint("dashboard_id", "version_number", name="uq_dashboard_version_number"),
    )

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
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    widgets_snapshot_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # e.g. "Added widget 'Revenue by Region'", "Restored from version 3" —
    # a short, human-readable note on what this snapshot captures, shown in
    # the history list without needing to open the full diff.
    change_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    dashboard: Mapped["Dashboard"] = relationship()  # noqa: F821
