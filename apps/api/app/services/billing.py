"""
Stripe integration for organization subscriptions.

This is deliberately NOT built like the AI-narration or invite-email
fallbacks (Phases 8 and 12), which quietly degrade to a template/log when
their external dependency is missing. Billing can't do that: there is no
honest local substitute for "the customer paid." So instead:

  - Reading plan/usage/limits (`app/api/billing.py`'s GET endpoint) never
    touches Stripe at all — `Organization.plan` and the counts from
    `plan_limits.py` are the local source of truth and always work.
  - Anything that actually moves money or manages a real subscription
    (checkout, the billing portal) requires `STRIPE_SECRET_KEY` to be set
    and raises `BillingNotConfiguredError` — surfaced by the API as a
    clear 503 — if it isn't. No fake checkout session, no silent no-op.

`PRICE_ID_TO_PLAN` maps a Stripe Price id back to our plan vocabulary
("pro"/"enterprise") when a webhook event arrives — see
`plan_for_price_id`. Kept in this module (not config) since it's derived
from config, not itself configuration.
"""

from datetime import datetime, timezone

import stripe

from app.core.config import get_settings


class BillingNotConfiguredError(RuntimeError):
    """Raised when a Stripe-backed action is attempted without STRIPE_SECRET_KEY set."""


def _require_stripe_configured() -> None:
    settings = get_settings()
    if not settings.stripe_secret_key:
        raise BillingNotConfiguredError(
            "Billing is not configured on this deployment (STRIPE_SECRET_KEY is unset)."
        )
    stripe.api_key = settings.stripe_secret_key


def plan_for_price_id(price_id: str) -> str | None:
    settings = get_settings()
    mapping = {
        settings.stripe_price_id_pro: "pro",
        settings.stripe_price_id_enterprise: "enterprise",
    }
    # Blank config values would otherwise collide as a `"" -> last-plan`
    # entry and misclassify an unrelated price id.
    mapping.pop("", None)
    return mapping.get(price_id)


def get_or_create_stripe_customer(*, existing_customer_id: str | None, email: str, org_name: str) -> str:
    _require_stripe_configured()
    if existing_customer_id:
        return existing_customer_id
    customer = stripe.Customer.create(email=email, name=org_name)
    return customer["id"]


def create_checkout_session(
    *, customer_id: str, price_id: str, success_url: str, cancel_url: str
) -> str:
    """Returns the Checkout Session URL to redirect the user to."""
    _require_stripe_configured()
    session = stripe.checkout.Session.create(
        customer=customer_id,
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
    )
    return session["url"]


def create_billing_portal_session(*, customer_id: str, return_url: str) -> str:
    """Returns the Stripe billing portal URL for the customer to manage/cancel their subscription."""
    _require_stripe_configured()
    session = stripe.billing_portal.Session.create(customer=customer_id, return_url=return_url)
    return session["url"]


def construct_webhook_event(*, payload: bytes, sig_header: str) -> stripe.Event:
    settings = get_settings()
    if not settings.stripe_webhook_secret:
        raise BillingNotConfiguredError(
            "Billing webhooks are not configured on this deployment (STRIPE_WEBHOOK_SECRET is unset)."
        )
    return stripe.Webhook.construct_event(payload, sig_header, settings.stripe_webhook_secret)


def epoch_to_datetime(epoch_seconds: int | None) -> datetime | None:
    if epoch_seconds is None:
        return None
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)
