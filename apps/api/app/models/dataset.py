"""
A Dataset is the logical, user-facing entity ("Q3 Sales", "Customer List").
It doesn't itself hold data — each ingestion (a file upload, or a snapshot
of a DB table) creates a DatasetVersion. This split is what Phase 13
(Version History) and Phase 4 (Data Cleaning, which produces new versions
from raw ones) build on, so it's worth getting right from this phase.
"""

import enum
import uuid

from sqlalchemy import Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class DatasetSourceType(str, enum.Enum):
    FILE_UPLOAD = "file_upload"
    DATABASE_CONNECTION = "database_connection"


class Dataset(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "datasets"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[DatasetSourceType] = mapped_column(
        Enum(DatasetSourceType, name="dataset_source_type"), nullable=False
    )

    # Set only when source_type == DATABASE_CONNECTION
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data_connections.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    versions: Mapped[list["DatasetVersion"]] = relationship(  # noqa: F821
        back_populates="dataset",
        cascade="all, delete-orphan",
        order_by="DatasetVersion.version_number.desc()",
    )
