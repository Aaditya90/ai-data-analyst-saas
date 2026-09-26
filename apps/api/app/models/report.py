"""
A Report is a one-shot export of a Dashboard (Phase 6/9) to a downloadable
PDF or PPTX file. Same "computed once, never mutated" posture as every
generated artifact since Phase 9's AI dashboards: a Report row is tied to
the dashboard it was built from at generation time, not kept in sync with
it — if the dashboard changes later, that's a new POST, not an update to
this row. The generated file itself lives in object storage (like raw
uploads and MLModel's serialized pipeline); this row is metadata + a
pointer to it.
"""

import enum
import uuid

from sqlalchemy import Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class ReportFormat(str, enum.Enum):
    PDF = "pdf"
    PPTX = "pptx"


class ReportStatus(str, enum.Enum):
    PENDING = "pending"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


class Report(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "reports"

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

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    format: Mapped[ReportFormat] = mapped_column(Enum(ReportFormat, name="report_format"), nullable=False)
    status: Mapped[ReportStatus] = mapped_column(
        Enum(ReportStatus, name="report_status"), nullable=False, default=ReportStatus.PENDING
    )

    storage_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    widget_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # {"summary": str, "ai_narration_used": bool} — the executive summary
    # shown on the report's cover, kept here too so GET doesn't need to
    # re-render the whole file just to show what it said.
    summary_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    dashboard: Mapped["Dashboard"] = relationship()  # noqa: F821
