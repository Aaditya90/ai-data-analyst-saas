"""reports: PDF/PPTX exports of dashboards

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-23

Adds the Phase 11 table:
    reports - one generated PDF/PPTX export of a Dashboard's widgets,
              with the file itself in object storage and its executive
              summary cached inline for quick display
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    report_format = postgresql.ENUM("pdf", "pptx", name="report_format")
    report_format.create(op.get_bind())
    report_status = postgresql.ENUM("pending", "generating", "ready", "failed", name="report_status")
    report_status.create(op.get_bind())

    op.create_table(
        "reports",
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
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column(
            "format",
            postgresql.ENUM("pdf", "pptx", name="report_format", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "pending", "generating", "ready", "failed", name="report_status", create_type=False
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("storage_key", sa.String(1024), nullable=True),
        sa.Column("file_size_bytes", sa.Integer, nullable=True),
        sa.Column("widget_count", sa.Integer, nullable=True),
        sa.Column("summary_json", postgresql.JSONB, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_reports_workspace_id", "reports", ["workspace_id"])
    op.create_index("ix_reports_dashboard_id", "reports", ["dashboard_id"])


def downgrade() -> None:
    op.drop_index("ix_reports_dashboard_id", table_name="reports")
    op.drop_index("ix_reports_workspace_id", table_name="reports")
    op.drop_table("reports")
    op.execute("DROP TYPE IF EXISTS report_status")
    op.execute("DROP TYPE IF EXISTS report_format")
