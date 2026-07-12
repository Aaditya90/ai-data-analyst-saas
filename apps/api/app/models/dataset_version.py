"""
Each ingestion (file upload, or DB table snapshot) creates one
DatasetVersion. Versions are treated as immutable once `status` reaches
READY — a re-upload or re-sync creates version N+1, not an in-place edit.
This is what makes Phase 4 (Data Cleaning) safe to build on: a cleaning
operation reads version N and writes version N+1, never mutating N.

`schema_json` and `row_count`/`column_count` are a cached snapshot of the
inferred schema at ingestion time, so the UI can render a preview without
re-reading the underlying file/table on every request.
"""

import enum
import uuid

from sqlalchemy import BigInteger, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class DatasetVersionStatus(str, enum.Enum):
    PENDING = "pending"        # row created, ingestion job not yet run
    PROCESSING = "processing"  # actively being parsed/profiled
    READY = "ready"
    FAILED = "failed"


class DatasetVersion(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "dataset_versions"

    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("datasets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[DatasetVersionStatus] = mapped_column(
        Enum(DatasetVersionStatus, name="dataset_version_status"),
        nullable=False,
        default=DatasetVersionStatus.PENDING,
    )

    # For FILE_UPLOAD datasets: where the raw file lives in object storage.
    storage_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # For DATABASE_CONNECTION datasets: which table/query this snapshots.
    source_table_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    row_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    column_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # List of {"name": str, "inferred_type": str, "nullable": bool,
    # "sample_values": [...]} — see app/services/schema_inference.py
    schema_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    dataset: Mapped["Dataset"] = relationship(back_populates="versions")  # noqa: F821
