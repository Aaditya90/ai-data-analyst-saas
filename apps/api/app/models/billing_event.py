"""
A BillingEvent is one processed Stripe webhook event, recorded for two
reasons:

  1. Idempotency — Stripe can and does deliver the same event more than
     once (retries on timeout, etc.). `stripe_event_id` is unique, so a
     redelivered event is detected and skipped rather than double-applying
     a plan change.
  2. Audit trail — same append-only, immutable-row posture as
     Phase 12's ActivityLog, but for billing specifically: "what did
     Stripe tell us, and when" is worth keeping separate from
     user-initiated activity.

`payload_json` stores the event's `data.object` (not the full envelope)
so the record is useful for debugging a webhook-processing bug without
needing to replay against Stripe's API.
"""

import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class BillingEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "billing_events"

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True
    )
    stripe_event_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
