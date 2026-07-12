"""data ingestion: connections, datasets, dataset versions

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-12

Adds the Phase 3 tables:
    data_connections   - saved, encrypted-credential DB connections
    datasets            - logical dataset entity (workspace-owned)
    dataset_versions    - immutable per-ingestion snapshot with schema_json

All three carry (directly or transitively via datasets) a workspace_id and
follow the same RLS-scaffolding pattern established in 0001 — see that
migration's docstring for the enforcement caveat (app connects as table
owner until Phase 15).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- connection_type enum + data_connections ---
    connection_type = postgresql.ENUM("postgres", "mysql", name="connection_type")
    connection_type.create(op.get_bind())

    op.create_table(
        "data_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "connection_type",
            postgresql.ENUM("postgres", "mysql", name="connection_type", create_type=False),
            nullable=False,
        ),
        sa.Column("host", sa.String(512), nullable=False),
        sa.Column("port", sa.Integer, nullable=False),
        sa.Column("database_name", sa.String(255), nullable=False),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column("encrypted_password", sa.String(1024), nullable=False),
        sa.Column("ssl_mode", sa.String(50), nullable=False, server_default="prefer"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_data_connections_workspace_id", "data_connections", ["workspace_id"])

    # --- dataset_source_type enum + datasets ---
    dataset_source_type = postgresql.ENUM(
        "file_upload", "database_connection", name="dataset_source_type"
    )
    dataset_source_type.create(op.get_bind())

    op.create_table(
        "datasets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "source_type",
            postgresql.ENUM(
                "file_upload", "database_connection", name="dataset_source_type", create_type=False
            ),
            nullable=False,
        ),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("data_connections.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_datasets_workspace_id", "datasets", ["workspace_id"])

    # --- dataset_version_status enum + dataset_versions ---
    dataset_version_status = postgresql.ENUM(
        "pending", "processing", "ready", "failed", name="dataset_version_status"
    )
    dataset_version_status.create(op.get_bind())

    op.create_table(
        "dataset_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "dataset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("datasets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "pending", "processing", "ready", "failed",
                name="dataset_version_status", create_type=False,
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("storage_key", sa.String(1024), nullable=True),
        sa.Column("original_filename", sa.String(512), nullable=True),
        sa.Column("source_table_name", sa.String(255), nullable=True),
        sa.Column("row_count", sa.BigInteger, nullable=True),
        sa.Column("column_count", sa.Integer, nullable=True),
        sa.Column("schema_json", postgresql.JSONB, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_dataset_versions_dataset_id", "dataset_versions", ["dataset_id"])


def downgrade() -> None:
    op.drop_index("ix_dataset_versions_dataset_id", table_name="dataset_versions")
    op.drop_table("dataset_versions")
    op.execute("DROP TYPE IF EXISTS dataset_version_status")

    op.drop_index("ix_datasets_workspace_id", table_name="datasets")
    op.drop_table("datasets")
    op.execute("DROP TYPE IF EXISTS dataset_source_type")

    op.drop_index("ix_data_connections_workspace_id", table_name="data_connections")
    op.drop_table("data_connections")
    op.execute("DROP TYPE IF EXISTS connection_type")
