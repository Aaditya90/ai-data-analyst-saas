"""team collaboration: invites, dashboard comments, activity log

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-24

Adds the Phase 12 tables:
    workspace_invites  - pending/accepted/revoked email invites into a
                          workspace, for people who may not have signed in
                          to the platform yet
    dashboard_comments - threaded (one level deep) discussion on a
                          dashboard, optionally pinned to one widget
    activity_logs       - append-only audit trail of team-collaboration
                          actions within a workspace

Reuses the existing `workspace_role` enum (created in 0001) for
workspace_invites.role rather than defining a new one.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    invite_status = postgresql.ENUM(
        "pending", "accepted", "revoked", name="workspace_invite_status"
    )
    invite_status.create(op.get_bind())

    op.create_table(
        "workspace_invites",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column(
            "role",
            postgresql.ENUM(
                "owner", "admin", "editor", "viewer", name="workspace_role", create_type=False
            ),
            nullable=False,
            server_default="viewer",
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "pending", "accepted", "revoked", name="workspace_invite_status", create_type=False
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("token", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "invited_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "accepted_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_workspace_invites_workspace_id", "workspace_invites", ["workspace_id"])
    op.create_index("ix_workspace_invites_email", "workspace_invites", ["email"])
    op.create_index("ix_workspace_invites_token", "workspace_invites", ["token"], unique=True)

    op.create_table(
        "dashboard_comments",
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
        sa.Column(
            "widget_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dashboard_widgets.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "parent_comment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dashboard_comments.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "author_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("resolved", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column(
            "resolved_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_dashboard_comments_workspace_id", "dashboard_comments", ["workspace_id"])
    op.create_index("ix_dashboard_comments_dashboard_id", "dashboard_comments", ["dashboard_id"])

    op.create_table(
        "activity_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("target_type", sa.String(50), nullable=True),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_activity_logs_workspace_id", "activity_logs", ["workspace_id"])
    op.create_index("ix_activity_logs_action", "activity_logs", ["action"])


def downgrade() -> None:
    op.drop_index("ix_activity_logs_action", table_name="activity_logs")
    op.drop_index("ix_activity_logs_workspace_id", table_name="activity_logs")
    op.drop_table("activity_logs")

    op.drop_index("ix_dashboard_comments_dashboard_id", table_name="dashboard_comments")
    op.drop_index("ix_dashboard_comments_workspace_id", table_name="dashboard_comments")
    op.drop_table("dashboard_comments")

    op.drop_index("ix_workspace_invites_token", table_name="workspace_invites")
    op.drop_index("ix_workspace_invites_email", table_name="workspace_invites")
    op.drop_index("ix_workspace_invites_workspace_id", table_name="workspace_invites")
    op.drop_table("workspace_invites")
    op.execute("DROP TYPE IF EXISTS workspace_invite_status")
