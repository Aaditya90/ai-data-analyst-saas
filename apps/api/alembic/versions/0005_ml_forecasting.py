"""ml & forecasting: ml_models, forecasts

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-22

Adds the Phase 10 tables:
    ml_models  - one trained AutoML run (regression/classification),
                 metrics + feature importance stored inline, the
                 serialized sklearn pipeline in object storage
    forecasts  - one time-series forecasting run, history + projected
                 points + backtest accuracy stored inline
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    ml_task_type = postgresql.ENUM("regression", "classification", name="ml_task_type")
    ml_task_type.create(op.get_bind())
    ml_model_status = postgresql.ENUM(
        "pending", "training", "ready", "failed", name="ml_model_status"
    )
    ml_model_status.create(op.get_bind())

    op.create_table(
        "ml_models",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dataset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("datasets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dataset_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dataset_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("target_column", sa.String(255), nullable=False),
        sa.Column("feature_columns", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column(
            "task_type",
            postgresql.ENUM("regression", "classification", name="ml_task_type", create_type=False),
            nullable=True,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "pending", "training", "ready", "failed", name="ml_model_status", create_type=False
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("algorithm", sa.String(100), nullable=True),
        sa.Column("metrics_json", postgresql.JSONB, nullable=True),
        sa.Column("feature_importance_json", postgresql.JSONB, nullable=True),
        sa.Column("candidates_json", postgresql.JSONB, nullable=True),
        sa.Column("storage_key", sa.String(1024), nullable=True),
        sa.Column("train_row_count", sa.Integer, nullable=True),
        sa.Column("test_row_count", sa.Integer, nullable=True),
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
    op.create_index("ix_ml_models_workspace_id", "ml_models", ["workspace_id"])
    op.create_index("ix_ml_models_dataset_version_id", "ml_models", ["dataset_version_id"])

    forecast_frequency = postgresql.ENUM("daily", "weekly", "monthly", name="forecast_frequency")
    forecast_frequency.create(op.get_bind())
    forecast_status = postgresql.ENUM("pending", "running", "ready", "failed", name="forecast_status")
    forecast_status.create(op.get_bind())

    op.create_table(
        "forecasts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dataset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("datasets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dataset_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dataset_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("date_column", sa.String(255), nullable=False),
        sa.Column("value_column", sa.String(255), nullable=False),
        sa.Column("horizon", sa.Integer, nullable=False),
        sa.Column(
            "frequency",
            postgresql.ENUM("daily", "weekly", "monthly", name="forecast_frequency", create_type=False),
            nullable=True,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "pending", "running", "ready", "failed", name="forecast_status", create_type=False
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("method", sa.String(100), nullable=True),
        sa.Column("history_json", postgresql.JSONB, nullable=True),
        sa.Column("forecast_json", postgresql.JSONB, nullable=True),
        sa.Column("metrics_json", postgresql.JSONB, nullable=True),
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
    op.create_index("ix_forecasts_workspace_id", "forecasts", ["workspace_id"])
    op.create_index("ix_forecasts_dataset_version_id", "forecasts", ["dataset_version_id"])


def downgrade() -> None:
    op.drop_index("ix_forecasts_dataset_version_id", table_name="forecasts")
    op.drop_index("ix_forecasts_workspace_id", table_name="forecasts")
    op.drop_table("forecasts")
    op.execute("DROP TYPE IF EXISTS forecast_status")
    op.execute("DROP TYPE IF EXISTS forecast_frequency")

    op.drop_index("ix_ml_models_dataset_version_id", table_name="ml_models")
    op.drop_index("ix_ml_models_workspace_id", table_name="ml_models")
    op.drop_table("ml_models")
    op.execute("DROP TYPE IF EXISTS ml_model_status")
    op.execute("DROP TYPE IF EXISTS ml_task_type")
