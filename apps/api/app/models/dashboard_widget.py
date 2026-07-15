"""
A widget is one tile on a dashboard: a chart, table, KPI card, or text
block. Position/size (`x`, `y`, `w`, `h`) are grid units consumed directly
by the frontend's drag-and-drop grid (react-grid-layout) — the backend
doesn't interpret them, just stores and returns them.

`config_json` shape depends on `widget_type`:
  - chart: {"chart_type": "bar"|"line"|"scatter", "x_column": str,
            "y_column": str | None, "aggregation": "sum"|"avg"|"count"|"min"|"max"}
  - table: {"columns": [str, ...]}  (omit/empty = all columns)
  - kpi:   {"column": str, "aggregation": "sum"|"avg"|"count"|"min"|"max"}
  - text:  {"content": str}  (dataset_id is null for text widgets)

Validated at the API layer (Pydantic), not the DB layer — JSONB stays
schema-flexible so new widget types don't need a migration.
"""

import enum
import uuid

from sqlalchemy import Enum, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class WidgetType(str, enum.Enum):
    CHART = "chart"
    TABLE = "table"
    KPI = "kpi"
    TEXT = "text"


class DashboardWidget(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "dashboard_widgets"

    dashboard_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dashboards.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dataset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("datasets.id", ondelete="SET NULL"), nullable=True
    )

    widget_type: Mapped[WidgetType] = mapped_column(
        Enum(WidgetType, name="widget_type"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    config_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Grid position/size — react-grid-layout units, opaque to the backend
    x: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    y: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    w: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    h: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

    dashboard: Mapped["Dashboard"] = relationship(back_populates="widgets")
