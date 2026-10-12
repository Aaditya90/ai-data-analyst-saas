"""
Phase 17 (Testing) integration tests: the Stripe billing flow
(app/api/billing.py + app/services/billing.py), end-to-end through real
route handlers against `fake_stripe` (tests/fakes/stripe_fake.py) — no
real Stripe account or network call involved.

Needs: pip install -r requirements-dev.txt. See tests/test_testing_infra.py
for the fakes' own dependency-free tests, which are what's actually
executable in a sandbox without those installed.

Run with: pytest tests/integration/test_billing_flow.py -m integration
"""

import pytest

from app.models.workspace_member import WorkspaceRole

pytestmark = pytest.mark.integration


def test_checkout_session_returns_fake_checkout_url_and_persists_customer_id(
    client, make_workspace, add_member, fake_stripe, db_session
):
    workspace = make_workspace(name="Billing Test Org Workspace")
    add_member(workspace=workspace, user=client.test_user, role=WorkspaceRole.OWNER)
    organization_id = workspace.organization_id

    response = client.post(
        f"/organizations/{organization_id}/billing/checkout-session",
        json={"plan": "pro", "success_url": "https://app.test/ok", "cancel_url": "https://app.test/cancel"},
    )

    assert response.status_code == 200
    assert response.json()["checkout_url"] == fake_stripe.checkout_url

    from app.models.organization import Organization

    db_session.expire_all()
    org = db_session.get(Organization, organization_id)
    assert org.stripe_customer_id is not None
    assert org.stripe_customer_id.startswith("cus_fake_")


def test_checkout_session_rejects_unknown_plan(client, make_workspace, add_member, fake_stripe):
    workspace = make_workspace()
    add_member(workspace=workspace, user=client.test_user, role=WorkspaceRole.OWNER)

    response = client.post(
        f"/organizations/{workspace.organization_id}/billing/checkout-session",
        json={"plan": "not-a-real-plan", "success_url": "https://app.test/ok", "cancel_url": "https://app.test/cancel"},
    )

    assert response.status_code == 400


def test_checkout_session_requires_organization_owner_role(client, make_workspace, add_member, fake_stripe):
    workspace = make_workspace()
    add_member(workspace=workspace, user=client.test_user, role=WorkspaceRole.EDITOR)  # not OWNER

    response = client.post(
        f"/organizations/{workspace.organization_id}/billing/checkout-session",
        json={"plan": "pro", "success_url": "https://app.test/ok", "cancel_url": "https://app.test/cancel"},
    )

    # require_organization_owner returns 404 (not 403) for a non-owner —
    # same "don't reveal it exists" posture as require_workspace_role.
    assert response.status_code == 404


def test_checkout_without_stripe_configured_returns_503(client, make_workspace, add_member, override_settings):
    workspace = make_workspace()
    add_member(workspace=workspace, user=client.test_user, role=WorkspaceRole.OWNER)
    override_settings(stripe_secret_key="", stripe_price_id_pro="price_pro_configured_but_no_key")

    response = client.post(
        f"/organizations/{workspace.organization_id}/billing/checkout-session",
        json={"plan": "pro", "success_url": "https://app.test/ok", "cancel_url": "https://app.test/cancel"},
    )

    assert response.status_code == 503


def test_webhook_subscription_updated_upgrades_org_plan(
    client, make_workspace, add_member, fake_stripe, db_session
):
    from app.models.organization import Organization

    workspace = make_workspace()
    add_member(workspace=workspace, user=client.test_user, role=WorkspaceRole.OWNER)
    org = db_session.get(Organization, workspace.organization_id)
    org.stripe_customer_id = "cus_existing_123"
    db_session.commit()

    event = fake_stripe.make_subscription_event(
        event_type="customer.subscription.updated",
        stripe_event_id="evt_upgrade_1",
        customer_id="cus_existing_123",
        subscription_id="sub_abc",
        price_id="price_fake_pro",  # matches settings.stripe_price_id_pro set by the fake_stripe fixture
        status="active",
    )
    fake_stripe.next_event = event

    response = client.post(
        "/billing/webhook",
        content=b"{}",
        headers={"stripe-signature": "fake-sig"},
    )

    assert response.status_code == 204

    db_session.expire_all()
    org = db_session.get(Organization, workspace.organization_id)
    assert org.plan == "pro"
    assert org.subscription_status == "active"
    assert org.stripe_subscription_id == "sub_abc"


def test_webhook_is_idempotent_on_duplicate_event_id(client, make_workspace, add_member, fake_stripe, db_session):
    from app.models.billing_event import BillingEvent
    from app.models.organization import Organization

    workspace = make_workspace()
    add_member(workspace=workspace, user=client.test_user, role=WorkspaceRole.OWNER)
    org = db_session.get(Organization, workspace.organization_id)
    org.stripe_customer_id = "cus_dup_test"
    db_session.commit()

    event = fake_stripe.make_subscription_event(
        event_type="customer.subscription.updated",
        stripe_event_id="evt_dup_1",
        customer_id="cus_dup_test",
        subscription_id="sub_dup",
        price_id="price_fake_pro",
    )
    fake_stripe.next_event = event

    first = client.post("/billing/webhook", content=b"{}", headers={"stripe-signature": "fake-sig"})
    second = client.post("/billing/webhook", content=b"{}", headers={"stripe-signature": "fake-sig"})

    assert first.status_code == 204
    assert second.status_code == 204  # still 204, not an error — see billing.py's idempotency check

    db_session.expire_all()
    stored = db_session.query(BillingEvent).filter(BillingEvent.stripe_event_id == "evt_dup_1").all()
    assert len(stored) == 1  # only recorded once despite two deliveries


def test_webhook_invalid_signature_returns_400(client, fake_stripe):
    fake_stripe.invalid_signature = True

    response = client.post("/billing/webhook", content=b"{}", headers={"stripe-signature": "bad-sig"})

    assert response.status_code == 400
