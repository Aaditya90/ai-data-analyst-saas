"""
Billing API — organization plan, usage vs. limits, Stripe checkout, the
Stripe billing portal, and the Stripe webhook that keeps `Organization` in
sync with what actually happened on Stripe's side.

  GET  /organizations/{id}/billing                    - plan, usage, subscription status (no Stripe call)
  POST /organizations/{id}/billing/checkout-session    - start a Checkout Session for "pro" or "enterprise"
  POST /organizations/{id}/billing/portal-session       - Stripe-hosted portal to manage/cancel
  POST /billing/webhook                                 - Stripe -> us (signature-verified, no auth header)

All org-scoped routes require `require_organization_owner` — see that
dependency's docstring for why it's derived from workspace ownership
rather than a dedicated org-membership table.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_organization_owner
from app.core.config import get_settings
from app.core.database import get_db
from app.models.billing_event import BillingEvent
from app.models.organization import Organization
from app.models.user import User
from app.services import billing
from app.services.billing import BillingNotConfiguredError
from app.services.plan_limits import (
    get_plan_limits,
    seats_in_use_for_workspace,
    workspaces_in_use_for_org,
)

router = APIRouter(prefix="/organizations/{organization_id}/billing", tags=["billing"])
webhook_router = APIRouter(tags=["billing"])


class CheckoutSessionCreate(BaseModel):
    plan: str  # "pro" | "enterprise"
    success_url: str
    cancel_url: str


class PortalSessionCreate(BaseModel):
    return_url: str


def _get_org_or_404(db: Session, organization_id: uuid.UUID) -> Organization:
    org = db.get(Organization, organization_id)
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    return org


@router.get("")
def get_billing_status(
    organization_id: uuid.UUID,
    current_user: User = Depends(require_organization_owner()),
    db: Session = Depends(get_db),
) -> dict:
    org = _get_org_or_404(db, organization_id)
    limits = get_plan_limits(org.plan)

    workspace_count = workspaces_in_use_for_org(db, organization_id)
    from app.models.workspace import Workspace  # avoid a module-load cycle with services/plan_limits

    per_workspace_seats = [
        {
            "workspace_id": str(w.id),
            "workspace_name": w.name,
            "seats_in_use": seats_in_use_for_workspace(db, w.id),
            "max_seats": limits["max_seats_per_workspace"],
        }
        for w in db.query(Workspace).filter(Workspace.organization_id == organization_id).all()
    ]

    return {
        "organization_id": str(org.id),
        "plan": org.plan,
        "subscription_status": org.subscription_status,
        "current_period_end": org.current_period_end.isoformat() if org.current_period_end else None,
        "cancel_at_period_end": org.cancel_at_period_end,
        "has_payment_method_on_file": org.stripe_customer_id is not None,
        "limits": limits,
        "usage": {
            "workspaces": workspace_count,
            "max_workspaces": limits["max_workspaces"],
        },
        "seats_by_workspace": per_workspace_seats,
        "billing_configured": bool(get_settings().stripe_secret_key),
    }


@router.post("/checkout-session")
def start_checkout(
    organization_id: uuid.UUID,
    body: CheckoutSessionCreate,
    current_user: User = Depends(require_organization_owner()),
    db: Session = Depends(get_db),
) -> dict:
    org = _get_org_or_404(db, organization_id)
    settings = get_settings()

    price_id = {"pro": settings.stripe_price_id_pro, "enterprise": settings.stripe_price_id_enterprise}.get(
        body.plan
    )
    if not price_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="plan must be 'pro' or 'enterprise' (and its Stripe price id must be configured)",
        )

    try:
        customer_id = billing.get_or_create_stripe_customer(
            existing_customer_id=org.stripe_customer_id,
            email=current_user.email,
            org_name=org.name,
        )
        checkout_url = billing.create_checkout_session(
            customer_id=customer_id,
            price_id=price_id,
            success_url=body.success_url,
            cancel_url=body.cancel_url,
        )
    except BillingNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    if org.stripe_customer_id != customer_id:
        org.stripe_customer_id = customer_id
        db.commit()

    return {"checkout_url": checkout_url}


@router.post("/portal-session")
def start_portal_session(
    organization_id: uuid.UUID,
    body: PortalSessionCreate,
    current_user: User = Depends(require_organization_owner()),
    db: Session = Depends(get_db),
) -> dict:
    org = _get_org_or_404(db, organization_id)
    if not org.stripe_customer_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This organization has no Stripe customer yet — start a checkout first",
        )

    try:
        portal_url = billing.create_billing_portal_session(
            customer_id=org.stripe_customer_id, return_url=body.return_url
        )
    except BillingNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    return {"portal_url": portal_url}


@webhook_router.post("/billing/webhook", status_code=status.HTTP_204_NO_CONTENT)
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = billing.construct_webhook_event(payload=payload, sig_header=sig_header)
    except BillingNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc
    except Exception as exc:  # stripe.error.SignatureVerificationError, ValueError, etc.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook signature or payload"
        ) from exc

    # Idempotency: Stripe redelivers events, so a duplicate is silently
    # accepted (204) rather than reprocessed — same "detect, don't
    # reprocess" reasoning that unique constraints give everywhere else.
    already_seen = (
        db.query(BillingEvent).filter(BillingEvent.stripe_event_id == event["id"]).one_or_none()
    )
    if already_seen is not None:
        return None

    data_object = event["data"]["object"]
    _apply_webhook_event(db, event_type=event["type"], data_object=data_object)

    db.add(
        BillingEvent(
            organization_id=_organization_for_customer(db, data_object.get("customer")),
            stripe_event_id=event["id"],
            event_type=event["type"],
            payload_json=dict(data_object),
        )
    )
    db.commit()
    return None


def _organization_for_customer(db: Session, stripe_customer_id: str | None) -> uuid.UUID | None:
    if not stripe_customer_id:
        return None
    org = db.query(Organization).filter(Organization.stripe_customer_id == stripe_customer_id).one_or_none()
    return org.id if org else None


def _apply_webhook_event(db: Session, *, event_type: str, data_object: dict) -> None:
    """
    Handles the three subscription lifecycle events this system cares
    about. Anything else (invoice.paid, payment_method.attached, ...) is
    accepted (204) but not acted on — recorded in BillingEvent regardless,
    so nothing is silently dropped, but only these three actually change
    `Organization`.
    """
    customer_id = data_object.get("customer")
    if not customer_id:
        return
    org = db.query(Organization).filter(Organization.stripe_customer_id == customer_id).one_or_none()
    if org is None:
        return  # webhook for a customer we don't (or no longer) recognize

    if event_type in ("customer.subscription.created", "customer.subscription.updated"):
        org.stripe_subscription_id = data_object.get("id")
        org.subscription_status = data_object.get("status")
        org.cancel_at_period_end = bool(data_object.get("cancel_at_period_end", False))
        org.current_period_end = billing.epoch_to_datetime(data_object.get("current_period_end"))

        items = data_object.get("items", {}).get("data", [])
        if items:
            price_id = items[0].get("price", {}).get("id")
            resolved_plan = billing.plan_for_price_id(price_id) if price_id else None
            if resolved_plan:
                org.plan = resolved_plan

    elif event_type == "customer.subscription.deleted":
        org.plan = "free"
        org.subscription_status = "canceled"
        org.stripe_subscription_id = None
        org.cancel_at_period_end = False
        org.current_period_end = None
