"""
Organization = the billing entity. A customer signs up and gets one
Organization. Subscription state (Phase 14) lives directly on this row
rather than a separate one-to-one table, since an organization has at most
one active Stripe subscription in this system's scope — see
app/services/billing.py and app/services/plan_limits.py for how `plan` is
interpreted and enforced.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Organization(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)

    # Billing (Phase 14). `plan` is a plain string ("free" | "pro" |
    # "enterprise"), not a DB enum — see app/services/plan_limits.py's
    # PLAN_LIMITS, which is the single source of truth for what each
    # string means, same "vocabulary lives in code, not a migration"
    # reasoning as ActivityLog.action (Phase 12).
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    plan: Mapped[str] = mapped_column(String(50), default="free", nullable=False)
    # Mirrors the Stripe Subscription's `status` (active, trialing,
    # past_due, canceled, incomplete, ...) — null until a subscription has
    # ever been created. Kept as a free string for the same reason as
    # `plan`: Stripe's own status vocabulary is the source of truth, not
    # a Postgres enum we'd have to migrate every time Stripe adds one.
    subscription_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    workspaces: Mapped[list["Workspace"]] = relationship(  # noqa: F821
        back_populates="organization",
        cascade="all, delete-orphan",
    )
