"""dashboard version history

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-25

Adds the Phase 13 table:
    dashboard_versions - immutable snapshot of a Dashboard's name + full
                          widget list, taken automatically on every
                          shape-changing dashboard/widget action.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dashboard_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dashboard_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dashboards.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("widgets_snapshot_json", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("change_summary", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("dashboard_id", "version_number", name="uq_dashboard_version_number"),
    )
    op.create_index("ix_dashboard_versions_workspace_id", "dashboard_versions", ["workspace_id"])
    op.create_index("ix_dashboard_versions_dashboard_id", "dashboard_versions", ["dashboard_id"])


def downgrade() -> None:
    op.drop_index("ix_dashboard_versions_dashboard_id", table_name="dashboard_versions")
    op.drop_index("ix_dashboard_versions_workspace_id", table_name="dashboard_versions")
    op.drop_table("dashboard_versions")
