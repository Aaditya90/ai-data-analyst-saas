"""
A Forecast is one time-series forecasting run: pick a date column + a
numeric value column on a dataset version, and
app/services/forecasting.py fits a trend/seasonality model on the
historical points and projects `horizon` periods forward.

Same "one run per request, never mutated" posture as MLModel — a re-run
(e.g. after the dataset gets a new version) creates a new Forecast row.

Results are small (a few dozen to a few hundred points) so they're stored
inline as JSONB rather than in object storage, unlike MLModel's serialized
estimator.
"""

import enum
import uuid

from sqlalchemy import Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class ForecastFrequency(str, enum.Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class ForecastStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"


class Forecast(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "forecasts"

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

    date_column: Mapped[str] = mapped_column(String(255), nullable=False)
    value_column: Mapped[str] = mapped_column(String(255), nullable=False)
    horizon: Mapped[int] = mapped_column(Integer, nullable=False)
    frequency: Mapped[ForecastFrequency | None] = mapped_column(
        Enum(ForecastFrequency, name="forecast_frequency"), nullable=True
    )
    status: Mapped[ForecastStatus] = mapped_column(
        Enum(ForecastStatus, name="forecast_status"),
        nullable=False,
        default=ForecastStatus.PENDING,
    )

    # Which fitting method was used, e.g. "linear_trend_seasonal", "naive"
    method: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # [{"period": "2026-08-01", "actual": 123.4}, ...] — the historical
    # series actually used to fit, resampled to `frequency`.
    history_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # [{"period": "2026-09-01", "forecast": 130.2, "lower_80": .., "upper_80": ..,
    #   "lower_95": .., "upper_95": ..}, ...] — one row per horizon step.
    forecast_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # Backtest accuracy computed by holding out the last N real points and
    # comparing against what the fitted model would have predicted for
    # them: {"mae": .., "rmse": .., "mape": .., "holdout_periods": int}
    # Null when there isn't enough history to backtest.
    metrics_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    dataset_version: Mapped["DatasetVersion"] = relationship()  # noqa: F821
