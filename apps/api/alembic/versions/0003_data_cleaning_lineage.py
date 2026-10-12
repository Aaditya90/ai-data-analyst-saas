"""data cleaning: lineage columns on dataset_versions

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-12

Adds lineage tracking directly onto dataset_versions rather than a separate
edges table: `parent_version_id` (self-referencing FK) plus
`transformations_applied` (JSONB log of what operations produced this
version from its parent). A self-referencing chain is sufficient to
reconstruct the full raw -> cleaned graph for a dataset and is simpler than
a dedicated edges table for the same information.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dataset_versions",
        sa.Column(
            "parent_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dataset_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "dataset_versions",
        sa.Column("transformations_applied", postgresql.JSONB, nullable=True),
    )
    op.create_index(
        "ix_dataset_versions_parent_version_id",
        "dataset_versions",
        ["parent_version_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_dataset_versions_parent_version_id", table_name="dataset_versions")
    op.drop_column("dataset_versions", "transformations_applied")
    op.drop_column("dataset_versions", "parent_version_id")
