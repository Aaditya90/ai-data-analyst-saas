"""
Two responsibilities:
  1. GET /me — lets the frontend fetch the logged-in user's profile.
  2. POST /webhooks/clerk — Clerk calls this whenever a user is created,
     updated, or deleted on their end, so our local `users` mirror stays in
     sync without waiting for that user's next API request.

Webhook signature verification uses svix (the library Clerk's webhooks are
built on) rather than trusting the payload outright — anyone could otherwise
POST a fake "user.created" event.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from svix.webhooks import Webhook, WebhookVerificationError

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.database import get_db
from app.models.user import User

router = APIRouter(tags=["auth"])
settings = get_settings()


@router.get("/me")
def read_current_user(current_user: User = Depends(get_current_user)) -> dict:
    return {
        "id": str(current_user.id),
        "email": current_user.email,
        "full_name": current_user.full_name,
        "avatar_url": current_user.avatar_url,
    }


@router.post("/webhooks/clerk", status_code=status.HTTP_204_NO_CONTENT)
async def clerk_webhook(request: Request, db: Session = Depends(get_db)):
    if not settings.clerk_secret_key:
        # Reusing clerk_secret_key would be wrong long-term (webhooks have
        # their own signing secret in the Clerk dashboard); this check just
        # ensures *some* Clerk config exists before we bother verifying.
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Clerk is not configured on this server",
        )

    payload = await request.body()
    headers = request.headers

    try:
        wh = Webhook(settings.clerk_secret_key)
        event = wh.verify(payload, headers)
    except WebhookVerificationError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid webhook signature",
        )

    event_type = event.get("type")
    data = event.get("data", {})
    auth_provider_id = data.get("id")

    if event_type == "user.created" or event_type == "user.updated":
        email = _extract_primary_email(data)
        full_name = _extract_full_name(data)
        avatar_url = data.get("image_url")

        user = (
            db.query(User)
            .filter(User.auth_provider_id == auth_provider_id)
            .one_or_none()
        )
        if user is None:
            user = User(auth_provider_id=auth_provider_id, email=email)
            db.add(user)
        else:
            user.email = email or user.email
        user.full_name = full_name
        user.avatar_url = avatar_url
        db.commit()

    elif event_type == "user.deleted":
        user = (
            db.query(User)
            .filter(User.auth_provider_id == auth_provider_id)
            .one_or_none()
        )
        if user is not None:
            db.delete(user)
            db.commit()

    return None


def _extract_primary_email(data: dict) -> str | None:
    email_addresses = data.get("email_addresses", [])
    primary_id = data.get("primary_email_address_id")
    for entry in email_addresses:
        if entry.get("id") == primary_id:
            return entry.get("email_address")
    return email_addresses[0].get("email_address") if email_addresses else None


def _extract_full_name(data: dict) -> str | None:
    first = data.get("first_name") or ""
    last = data.get("last_name") or ""
    full = f"{first} {last}".strip()
    return full or None
