"""
A Dashboard is a named canvas of widgets within a workspace. The dashboard
itself holds no data — each DashboardWidget references a Dataset (via its
latest READY version at render time) plus enough config to compute what it
displays. This mirrors the Dataset/DatasetVersion split from Phase 3: the
dashboard definition is stable, but the data it renders can change as new
dataset versions arrive without needing to touch the dashboard itself.
"""

import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Dashboard(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "dashboards"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    widgets: Mapped[list["DashboardWidget"]] = relationship(  # noqa: F821
        back_populates="dashboard",
        cascade="all, delete-orphan",
        order_by="DashboardWidget.created_at",
    )
