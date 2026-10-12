"""dashboard builder: dashboards, dashboard_widgets

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-13

Adds the Phase 6 tables:
    dashboards          - named canvas within a workspace
    dashboard_widgets    - positioned tiles (chart/table/kpi/text) with
                           JSONB config, so new widget types don't need a
                           future migration
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dashboards",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_dashboards_workspace_id", "dashboards", ["workspace_id"])

    widget_type = postgresql.ENUM("chart", "table", "kpi", "text", name="widget_type")
    widget_type.create(op.get_bind())

    op.create_table(
        "dashboard_widgets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "dashboard_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dashboards.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dataset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("datasets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "widget_type",
            postgresql.ENUM("chart", "table", "kpi", "text", name="widget_type", create_type=False),
            nullable=False,
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("config_json", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("x", sa.Integer, nullable=False, server_default="0"),
        sa.Column("y", sa.Integer, nullable=False, server_default="0"),
        sa.Column("w", sa.Integer, nullable=False, server_default="4"),
        sa.Column("h", sa.Integer, nullable=False, server_default="3"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_dashboard_widgets_dashboard_id", "dashboard_widgets", ["dashboard_id"])


def downgrade() -> None:
    op.drop_index("ix_dashboard_widgets_dashboard_id", table_name="dashboard_widgets")
    op.drop_table("dashboard_widgets")
    op.execute("DROP TYPE IF EXISTS widget_type")

    op.drop_index("ix_dashboards_workspace_id", table_name="dashboards")
    op.drop_table("dashboards")
