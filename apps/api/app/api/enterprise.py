"""
Enterprise API (Phase 19) — organization-level, owner-only (same
`require_organization_owner` rule as billing).

  GET    /organizations/{id}/enterprise/features         - which enterprise features this plan has
  POST   /organizations/{id}/api-keys                    - create a key (shown ONCE); Enterprise only
  GET    /organizations/{id}/api-keys                    - list keys (prefix only, never the secret)
  DELETE /organizations/{id}/api-keys/{key_id}           - revoke
  GET    /organizations/{id}/security/ip-allowlist
  PUT    /organizations/{id}/security/ip-allowlist       - replace the list; Enterprise only
  GET    /organizations/{id}/audit-log/export?format=csv|json&since=&until=   - Enterprise only

Listing/revoking keys and reading the allowlist are NOT plan-gated: an
organization that downgrades must still be able to see and revoke what it
created, and to clear an allowlist. Only creating/changing is gated.

API keys themselves cannot call any of these routes (none has a
workspace_id path parameter — see deps._authenticate_api_key), so a leaked
key can't mint more keys or rewrite the allowlist.
"""

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_client_ip, rate_limit, require_organization_owner
from app.core.database import get_db
from app.models.activity_log import ActivityLog
from app.models.api_key import ApiKey
from app.models.organization import Organization
from app.models.user import User
from app.models.workspace import Workspace
from app.services import enterprise

router = APIRouter(prefix="/organizations/{organization_id}", tags=["enterprise"])
audit_logger = logging.getLogger("app.audit")


def _get_org(db: Session, organization_id: uuid.UUID) -> Organization:
    org = db.get(Organization, organization_id)
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    return org


def _require_feature(org: Organization, feature: str, label: str) -> None:
    if not enterprise.plan_has_feature(org.plan, feature):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"{label} requires the Enterprise plan",
        )


# --- features -------------------------------------------------------------


@router.get("/enterprise/features")
def get_features(
    organization_id: uuid.UUID,
    owner: User = Depends(require_organization_owner()),
    db: Session = Depends(get_db),
) -> dict:
    org = _get_org(db, organization_id)
    return {"plan": org.plan, "features": enterprise.features_for_plan(org.plan)}


# --- API keys -------------------------------------------------------------


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scope: str = enterprise.KEY_SCOPE_READ
    ttl_days: int | None = Field(default=None, ge=1, le=enterprise.MAX_KEY_TTL_DAYS)


class ApiKeyOut(BaseModel):
    id: str
    name: str
    key_prefix: str
    scope: str
    created_by_user_id: str
    expires_at: str
    last_used_at: str | None
    revoked_at: str | None
    created_at: str


class ApiKeyCreated(ApiKeyOut):
    # The only response that ever contains the full key.
    api_key: str


def _key_out(key: ApiKey) -> dict:
    return dict(
        id=str(key.id),
        name=key.name,
        key_prefix=key.key_prefix,
        scope=key.scope,
        created_by_user_id=str(key.created_by_user_id),
        expires_at=key.expires_at.isoformat(),
        last_used_at=key.last_used_at.isoformat() if key.last_used_at else None,
        revoked_at=key.revoked_at.isoformat() if key.revoked_at else None,
        created_at=key.created_at.isoformat(),
    )


@router.post(
    "/api-keys",
    response_model=ApiKeyCreated,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit(name="api_key_create", max_requests=20, window_seconds=3600))],
)
def create_api_key(
    organization_id: uuid.UUID,
    body: ApiKeyCreate,
    owner: User = Depends(require_organization_owner()),
    db: Session = Depends(get_db),
) -> ApiKeyCreated:
    org = _get_org(db, organization_id)
    _require_feature(org, enterprise.FEATURE_API_KEYS, "API keys")
    if body.scope not in enterprise.VALID_KEY_SCOPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"scope must be one of: {', '.join(sorted(enterprise.VALID_KEY_SCOPES))}",
        )

    now = enterprise.utcnow()
    expires_at = enterprise.compute_expiry(now, body.ttl_days)

    # 8 hex chars = 32 bits of prefix; a collision is unlikely but the
    # prefix column is unique, so retry rather than surface a 500.
    for _ in range(5):
        full_key, prefix, key_hash = enterprise.generate_api_key()
        if db.query(ApiKey).filter(ApiKey.key_prefix == prefix).first() is None:
            break
    else:  # pragma: no cover - astronomically unlikely
        raise HTTPException(status_code=500, detail="Could not allocate a unique key prefix")

    key = ApiKey(
        organization_id=organization_id,
        created_by_user_id=owner.id,
        name=body.name,
        key_prefix=prefix,
        key_hash=key_hash,
        scope=body.scope,
        expires_at=expires_at,
    )
    db.add(key)
    db.commit()
    db.refresh(key)

    audit_logger.info(
        "api_key_created",
        extra={"organization_id": str(organization_id), "actor_user_id": str(owner.id),
               "key_prefix": prefix, "scope": body.scope},
    )
    return ApiKeyCreated(**_key_out(key), api_key=full_key)


@router.get("/api-keys", response_model=list[ApiKeyOut])
def list_api_keys(
    organization_id: uuid.UUID,
    owner: User = Depends(require_organization_owner()),
    db: Session = Depends(get_db),
) -> list[dict]:
    keys = (
        db.query(ApiKey)
        .filter(ApiKey.organization_id == organization_id)
        .order_by(ApiKey.created_at.desc())
        .all()
    )
    return [_key_out(k) for k in keys]


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_api_key(
    organization_id: uuid.UUID,
    key_id: uuid.UUID,
    owner: User = Depends(require_organization_owner()),
    db: Session = Depends(get_db),
) -> Response:
    key = (
        db.query(ApiKey)
        .filter(ApiKey.id == key_id, ApiKey.organization_id == organization_id)
        .one_or_none()
    )
    if key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")
    if key.revoked_at is None:  # idempotent: revoking twice keeps the first timestamp
        key.revoked_at = enterprise.utcnow()
        db.commit()
        audit_logger.info(
            "api_key_revoked",
            extra={"organization_id": str(organization_id), "actor_user_id": str(owner.id),
                   "key_prefix": key.key_prefix},
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- IP allowlist ---------------------------------------------------------


class IpAllowlistBody(BaseModel):
    entries: list[str]


@router.get("/security/ip-allowlist")
def get_ip_allowlist(
    organization_id: uuid.UUID,
    owner: User = Depends(require_organization_owner()),
    db: Session = Depends(get_db),
) -> dict:
    org = _get_org(db, organization_id)
    return {"entries": list(org.ip_allowlist or []), "enforced": bool(org.ip_allowlist)
            and enterprise.plan_has_feature(org.plan, enterprise.FEATURE_IP_ALLOWLIST)}


@router.put("/security/ip-allowlist")
def set_ip_allowlist(
    organization_id: uuid.UUID,
    body: IpAllowlistBody,
    request: Request,
    owner: User = Depends(require_organization_owner()),
    db: Session = Depends(get_db),
) -> dict:
    org = _get_org(db, organization_id)
    _require_feature(org, enterprise.FEATURE_IP_ALLOWLIST, "IP allowlist")
    try:
        entries = enterprise.normalize_allowlist(body.entries)
    except enterprise.InvalidAllowlistError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    # Self-lockout guard: refuse a non-empty list that excludes the address
    # making this very request. (An empty list clears the restriction.)
    caller_ip = get_client_ip(request)
    if entries and not enterprise.ip_allowed(caller_ip, entries):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"This list would exclude your current address ({caller_ip}); add it first",
        )

    org.ip_allowlist = entries
    db.commit()
    audit_logger.info(
        "ip_allowlist_updated",
        extra={"organization_id": str(organization_id), "actor_user_id": str(owner.id),
               "entry_count": len(entries)},
    )
    return {"entries": entries, "enforced": bool(entries)}


# --- audit export ---------------------------------------------------------


@router.get("/audit-log/export")
def export_audit_log(
    organization_id: uuid.UUID,
    format: str = Query(default="csv", pattern="^(csv|json)$"),
    since: datetime | None = None,
    until: datetime | None = None,
    owner: User = Depends(require_organization_owner()),
    db: Session = Depends(get_db),
) -> Response:
    org = _get_org(db, organization_id)
    _require_feature(org, enterprise.FEATURE_AUDIT_EXPORT, "Audit log export")

    # The org filter below is the isolation guarantee: activity_logs is
    # RLS-scoped per *workspace*, and this query intentionally spans all of
    # the organization's workspaces, so it must never select outside them.
    workspaces = {
        w.id: w.name for w in db.query(Workspace).filter(Workspace.organization_id == organization_id).all()
    }
    if not workspaces:
        entries: list[ActivityLog] = []
    else:
        query = db.query(ActivityLog).filter(ActivityLog.workspace_id.in_(list(workspaces)))
        if since is not None:
            query = query.filter(ActivityLog.created_at >= since)
        if until is not None:
            query = query.filter(ActivityLog.created_at < until)
        entries = query.order_by(ActivityLog.created_at.asc()).limit(enterprise.MAX_EXPORT_ROWS + 1).all()

    truncated = len(entries) > enterprise.MAX_EXPORT_ROWS
    entries = entries[: enterprise.MAX_EXPORT_ROWS]

    actor_emails = {}
    actor_ids = {e.actor_user_id for e in entries if e.actor_user_id}
    if actor_ids:
        actor_emails = {u.id: u.email for u in db.query(User).filter(User.id.in_(actor_ids)).all()}

    rows = [
        {
            "created_at": e.created_at.isoformat(),
            "workspace_id": str(e.workspace_id),
            "workspace_name": workspaces.get(e.workspace_id),
            "actor_user_id": str(e.actor_user_id) if e.actor_user_id else None,
            "actor_email": actor_emails.get(e.actor_user_id),
            "action": e.action,
            "target_type": e.target_type,
            "target_id": str(e.target_id) if e.target_id else None,
            "metadata": e.metadata_json,
        }
        for e in entries
    ]

    audit_logger.info(
        "audit_log_exported",
        extra={"organization_id": str(organization_id), "actor_user_id": str(owner.id),
               "row_count": len(rows), "format": format, "truncated": truncated},
    )

    headers = {"X-Export-Truncated": "true" if truncated else "false"}
    if format == "json":
        import json

        return Response(
            content=json.dumps(rows, default=str),
            media_type="application/json",
            headers=headers,
        )
    headers["Content-Disposition"] = 'attachment; filename="audit-log.csv"'
    return Response(content=enterprise.rows_to_csv(rows), media_type="text/csv", headers=headers)
