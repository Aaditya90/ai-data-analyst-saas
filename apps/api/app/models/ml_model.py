"""
An MLModel is one trained AutoML run: pick a target column on a dataset
version, and the service (app/services/automl.py) auto-detects whether it's
a regression or classification problem, trains a couple of candidate
sklearn estimators, and keeps the best one by holdout score.

Same immutability posture as DatasetVersion: a model is trained once
against one specific dataset_version_id and never retrained in place. If
the underlying data changes (a new version), that's a new POST -> a new
MLModel row, never an update to this one. This keeps "which data produced
this model" unambiguous forever, which matters for both correctness and
for Phase 13 (Version History) later.

The serialized estimator itself (joblib-pickled) lives in object storage,
not the DB — `storage_key` points at it, mirroring how DatasetVersion
stores raw files. metrics_json/feature_importance_json are small enough to
cache inline as JSONB, same reasoning as schema_json on DatasetVersion.
"""

import enum
import uuid

from sqlalchemy import Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class MLTaskType(str, enum.Enum):
    REGRESSION = "regression"
    CLASSIFICATION = "classification"


class MLModelStatus(str, enum.Enum):
    PENDING = "pending"
    TRAINING = "training"
    READY = "ready"
    FAILED = "failed"


class MLModel(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ml_models"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    dataset_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dataset_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    target_column: Mapped[str] = mapped_column(String(255), nullable=False)
    # Columns actually used as model inputs — a subset of the dataset's
    # columns, auto-selected (all columns except target/high-cardinality
    # identifiers) unless the caller explicitly picks feature_columns.
    feature_columns: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    task_type: Mapped[MLTaskType | None] = mapped_column(
        Enum(MLTaskType, name="ml_task_type"), nullable=True
    )
    status: Mapped[MLModelStatus] = mapped_column(
        Enum(MLModelStatus, name="ml_model_status"),
        nullable=False,
        default=MLModelStatus.PENDING,
    )

    # Which candidate algorithm won, e.g. "random_forest", "linear_model"
    algorithm: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Regression: {"r2": .., "mae": .., "rmse": ..}
    # Classification: {"accuracy": .., "f1_macro": .., "precision_macro": .., "recall_macro": ..}
    # Always computed by sklearn on a held-out test split — never AI-estimated.
    metrics_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # [{"column": str, "importance": float}, ...] sorted descending.
    feature_importance_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # Compact per-candidate leaderboard so the caller can see what else was
    # tried and why the winner won, without re-running training.
    candidates_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # joblib-pickled sklearn Pipeline (preprocessing + estimator), stored in
    # object storage the same way DatasetVersion stores raw files.
    storage_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    train_row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    test_row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    dataset_version: Mapped["DatasetVersion"] = relationship()  # noqa: F821
