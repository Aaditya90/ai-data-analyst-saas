"""
Fake for the `stripe` module, standing in for the four calls
app/services/billing.py makes:

    stripe.Customer.create(...)                    -> {"id": ...}
    stripe.checkout.Session.create(...)             -> {"url": ...}
    stripe.billing_portal.Session.create(...)       -> {"url": ...}
    stripe.Webhook.construct_event(payload, sig, secret) -> event dict

All four return plain dicts, not SDK model objects — real `stripe`
objects (`stripe.Customer`, `stripe.checkout.Session`, ...) are also
subscriptable like dicts (`customer["id"]`), which is exactly the
interface `billing.py` relies on, so a plain dict is a faithful stand-in
here without pulling in the SDK's model classes.

Swap in with:
    monkeypatch.setattr("app.services.billing.stripe", fake_stripe)
Since `_require_stripe_configured()` also does `stripe.api_key = ...`,
the fake accepts that assignment too (plain attribute).
"""


class _FakeCustomer:
    def __init__(self, fake: "FakeStripe"):
        self._fake = fake

    def create(self, *, email: str, name: str):
        self._fake.calls.append({"method": "Customer.create", "email": email, "name": name})
        customer_id = f"cus_fake_{len(self._fake.calls)}"
        return {"id": customer_id, "email": email, "name": name}


class _FakeCheckoutSession:
    def __init__(self, fake: "FakeStripe"):
        self._fake = fake

    def create(self, *, customer: str, mode: str, line_items: list[dict], success_url: str, cancel_url: str):
        self._fake.calls.append(
            {
                "method": "checkout.Session.create",
                "customer": customer,
                "mode": mode,
                "line_items": line_items,
            }
        )
        return {"id": f"cs_fake_{len(self._fake.calls)}", "url": self._fake.checkout_url}


class _FakeCheckout:
    def __init__(self, fake: "FakeStripe"):
        self.Session = _FakeCheckoutSession(fake)


class _FakeBillingPortalSession:
    def __init__(self, fake: "FakeStripe"):
        self._fake = fake

    def create(self, *, customer: str, return_url: str):
        self._fake.calls.append({"method": "billing_portal.Session.create", "customer": customer})
        return {"id": f"bps_fake_{len(self._fake.calls)}", "url": self._fake.portal_url}


class _FakeBillingPortal:
    def __init__(self, fake: "FakeStripe"):
        self.Session = _FakeBillingPortalSession(fake)


class _FakeWebhook:
    def __init__(self, fake: "FakeStripe"):
        self._fake = fake

    def construct_event(self, payload, sig_header, secret):
        self._fake.calls.append({"method": "Webhook.construct_event", "sig_header": sig_header})
        if self._fake.invalid_signature:
            # Mirrors stripe.SignatureVerificationError being raised for a
            # bad signature — billing.py's route catches `Exception` broadly
            # around construct_webhook_event, so any exception type proves
            # the "reject the webhook" path.
            raise ValueError("invalid Stripe webhook signature (fake)")
        if self._fake.next_event is None:
            raise ValueError("FakeStripe.next_event not set — configure it before calling the webhook route")
        return self._fake.next_event


class FakeStripe:
    """Drop-in for the `stripe` module in tests."""

    def __init__(self, *, checkout_url: str = "https://checkout.stripe.test/session/fake"):
        self.api_key = None
        self.calls: list[dict] = []
        self.checkout_url = checkout_url
        self.portal_url = "https://billing.stripe.test/portal/fake"
        self.invalid_signature = False
        self.next_event: dict | None = None

        self.Customer = _FakeCustomer(self)
        self.checkout = _FakeCheckout(self)
        self.billing_portal = _FakeBillingPortal(self)
        self.Webhook = _FakeWebhook(self)

    def make_subscription_event(
        self,
        *,
        event_type: str,
        stripe_event_id: str,
        customer_id: str,
        subscription_id: str,
        price_id: str,
        status: str = "active",
        current_period_end: int = 1_900_000_000,
        cancel_at_period_end: bool = False,
    ) -> dict:
        """Builds a minimal `customer.subscription.*` webhook event shaped
        the way `_apply_webhook_event` in app/api/billing.py reads it."""
        return {
            "id": stripe_event_id,
            "type": event_type,
            "data": {
                "object": {
                    "id": subscription_id,
                    "customer": customer_id,
                    "status": status,
                    "current_period_end": current_period_end,
                    "cancel_at_period_end": cancel_at_period_end,
                    "items": {"data": [{"price": {"id": price_id}}]},
                }
            },
        }
