"""billing: subscription fields on organizations, billing_events

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-26

Adds subscription-state columns to the existing `organizations` table
(stripe_subscription_id, subscription_status, current_period_end,
cancel_at_period_end — `stripe_customer_id` and `plan` already existed
since Phase 1) and a new `billing_events` table for Stripe webhook
idempotency/audit (see app/models/billing_event.py).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations", sa.Column("stripe_subscription_id", sa.String(255), nullable=True)
    )
    op.add_column(
        "organizations", sa.Column("subscription_status", sa.String(50), nullable=True)
    )
    op.add_column(
        "organizations",
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column(
            "cancel_at_period_end", sa.Boolean, nullable=False, server_default=sa.false()
        ),
    )

    op.create_table(
        "billing_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("stripe_event_id", sa.String(255), nullable=False, unique=True),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("payload_json", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_billing_events_organization_id", "billing_events", ["organization_id"])
    op.create_index(
        "ix_billing_events_stripe_event_id", "billing_events", ["stripe_event_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_billing_events_stripe_event_id", table_name="billing_events")
    op.drop_index("ix_billing_events_organization_id", table_name="billing_events")
    op.drop_table("billing_events")

    op.drop_column("organizations", "cancel_at_period_end")
    op.drop_column("organizations", "current_period_end")
    op.drop_column("organizations", "subscription_status")
    op.drop_column("organizations", "stripe_subscription_id")
