"""
Reusable FastAPI dependencies for authentication and workspace-scoped
authorization.

Every protected route in every future phase should depend on
`get_current_user` (who is this?) and, where the route operates on
workspace data, `require_workspace_role(...)` (are they allowed to do this,
in this workspace?). Keeping both checks as dependencies — rather than
scattered manual checks inside route bodies — means they can't accidentally
be skipped in a new route.

Uses FastAPI's HTTPBearer security scheme (rather than reading the
Authorization header manually) specifically so /docs renders a global
"Authorize" padlock — paste the token once there and it's attached to every
request /docs sends, instead of having to paste it into each endpoint's
"Try it out" form individually.
"""

import time
import uuid

import redis
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.cache import get_redis_client
from app.core.database import get_db
from app.core.rls import set_workspace_context
from app.core.security import verify_session_token
from app.models.api_key import ApiKey
from app.models.organization import Organization
from app.services import enterprise
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_member import WorkspaceMember, WorkspaceRole

# auto_error=False so a missing token surfaces as our own 401 message below
# rather than FastAPI's generic "Not authenticated" — same behavior either
# way, clearer detail message.
bearer_scheme = HTTPBearer(auto_error=False)


def get_client_ip(request: Request) -> str | None:
    """The caller's address as uvicorn resolved it. Behind Caddy the API
    runs with --proxy-headers (Phase 18), so this is the real client, not
    the proxy. Only trustworthy because the API is unreachable except via
    that proxy in the production compose stack."""
    return request.client.host if request.client else None


def _authenticate_api_key(token: str, request: Request, db: Session) -> User:
    """
    Phase 19. Every rejection reason that depends on the *secret* (unknown
    prefix, wrong hash, revoked, expired) returns the same generic 401, so
    a caller can't distinguish "no such key" from "revoked key". Failures
    that only a holder of a valid key could ever see (scope, endpoint
    restrictions, plan) are specific, since they help legitimate users.

    API keys are deliberately limited to workspace-scoped endpoints —
    those with a `workspace_id` path parameter — inside the key's own
    organization. That single rule also keeps keys away from billing, key
    management and every other org-level route, none of which have a
    workspace_id in the path.
    """
    invalid = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    prefix = enterprise.parse_api_key_prefix(token)
    if prefix is None:
        raise invalid
    key = db.query(ApiKey).filter(ApiKey.key_prefix == prefix).one_or_none()
    if key is None or not enterprise.verify_api_key(token, key.key_hash):
        raise invalid

    now = enterprise.utcnow()
    if enterprise.key_unusable_reason(
        revoked_at=key.revoked_at, expires_at=key.expires_at, now=now
    ):
        raise invalid

    if not enterprise.scope_allows(key.scope, request.method):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This API key is read-only",
        )

    raw_workspace_id = request.path_params.get("workspace_id")
    if raw_workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API keys can only access workspace-scoped endpoints",
        )
    try:
        workspace_id = uuid.UUID(str(raw_workspace_id))
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    workspace = db.get(Workspace, workspace_id)
    if workspace is None or workspace.organization_id != key.organization_id:
        # Same 404 as "not a member": don't reveal workspaces in other orgs.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

    # A downgraded organization's keys stop working; they are not deleted,
    # so re-upgrading restores them.
    organization = db.get(Organization, key.organization_id)
    if organization is None or not enterprise.plan_has_feature(
        organization.plan, enterprise.FEATURE_API_KEYS
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API keys require the Enterprise plan",
        )

    user = db.get(User, key.created_by_user_id)
    if user is None:
        raise invalid

    if enterprise.should_touch_last_used(key.last_used_at, now):
        key.last_used_at = now
        db.commit()
    return user


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """
    Verifies the bearer token and returns the corresponding local User,
    creating one on first sight (Clerk is the source of truth for identity;
    our `users` table is a local mirror keyed on auth_provider_id).
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header",
        )
    token = credentials.credentials
    if enterprise.looks_like_api_key(token):
        return _authenticate_api_key(token, request, db)
    claims = verify_session_token(token)

    auth_provider_id = claims["sub"]
    email = claims.get("email") or claims.get("primary_email_address")

    user = (
        db.query(User)
        .filter(User.auth_provider_id == auth_provider_id)
        .one_or_none()
    )
    if user is None:
        # First request from this identity — mirror it locally. The webhook
        # handler (app/api/auth.py) also creates/updates users so this path
        # mainly covers the case where the webhook hasn't fired yet (e.g.
        # local dev without a public webhook URL).
        if not email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Token is missing an email claim; cannot create user",
            )
        user = User(auth_provider_id=auth_provider_id, email=email)
        db.add(user)
        db.commit()
        db.refresh(user)

    return user


# Role hierarchy — higher number = more privilege. Used so
# require_workspace_role("editor") also accepts admin/owner, not just editor.
_ROLE_RANK: dict[WorkspaceRole, int] = {
    WorkspaceRole.VIEWER: 0,
    WorkspaceRole.EDITOR: 1,
    WorkspaceRole.ADMIN: 2,
    WorkspaceRole.OWNER: 3,
}


def _enforce_ip_allowlist(db: Session, workspace_id: uuid.UUID, client_ip: str | None) -> None:
    """
    Phase 19: if the workspace's organization has an IP allowlist AND its
    plan still includes the feature, reject callers outside it. Runs after
    the membership check, so a non-member still gets the 404 and learns
    nothing. Applies to session and API-key traffic alike. Org-level
    routes (/organizations/...) are intentionally NOT covered, so an owner
    who mis-sets the list can always reach the endpoint that fixes it.
    """
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        return
    organization = db.get(Organization, workspace.organization_id)
    if organization is None or not organization.ip_allowlist:
        return
    if not enterprise.plan_has_feature(organization.plan, enterprise.FEATURE_IP_ALLOWLIST):
        return
    if not enterprise.ip_allowed(client_ip, organization.ip_allowlist):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access from your network address is not allowed by this organization's IP allowlist",
        )


def require_workspace_role(minimum_role: WorkspaceRole):
    """
    Returns a dependency that:
      1. Confirms the current user is a member of the given workspace_id
      2. Confirms their role meets or exceeds `minimum_role`

    Usage:
        @router.get("/workspaces/{workspace_id}/datasets")
        def list_datasets(
            member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.VIEWER)),
        ):
            ...

    Every future phase's workspace-scoped routes should use this rather than
    querying WorkspaceMember manually, so the isolation/authorization rule
    lives in exactly one place.
    """

    def dependency(
        workspace_id: uuid.UUID,
        request: Request,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> WorkspaceMember:
        member = (
            db.query(WorkspaceMember)
            .filter(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == current_user.id,
            )
            .one_or_none()
        )
        if member is None:
            # 404, not 403 — don't reveal whether the workspace exists to
            # someone who isn't a member of it
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workspace not found",
            )
        if _ROLE_RANK[member.role] < _ROLE_RANK[minimum_role]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires '{minimum_role.value}' role or higher in this workspace",
            )
        _enforce_ip_allowlist(db, workspace_id, get_client_ip(request))
        # Second, DB-enforced line of defense (Phase 15) — see
        # app/core/rls.py. The app-layer check above already decided this
        # request is allowed; this additionally scopes every RLS-protected
        # query for the rest of the transaction so a missing .filter()
        # elsewhere can't leak another workspace's rows.
        set_workspace_context(db, workspace_id)
        return member


def require_organization_owner():
    """
    Billing (Phase 14) is scoped to Organization, which — per
    organizations.py's design note — has no membership table of its own;
    access is derived from workspace membership. This dependency accepts
    anyone who is OWNER of at least one workspace under the given
    organization_id, mirroring how create_workspace already makes its
    creator that workspace's OWNER. A user who's only EDITOR/ADMIN/VIEWER
    everywhere in the org cannot view or manage billing.
    """

    def dependency(
        organization_id: uuid.UUID,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        is_owner_somewhere = (
            db.query(WorkspaceMember)
            .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
            .filter(
                Workspace.organization_id == organization_id,
                WorkspaceMember.user_id == current_user.id,
                WorkspaceMember.role == WorkspaceRole.OWNER,
            )
            .first()
            is not None
        )
        if not is_owner_somewhere:
            # 404, not 403 — same "don't reveal it exists" reasoning as
            # require_workspace_role.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Organization not found",
            )
        return current_user

    return dependency


def compute_fixed_window(now_epoch_seconds: float, window_seconds: int) -> int:
    """
    Pure: which fixed window `now_epoch_seconds` falls into, for a window
    of length `window_seconds`. Extracted out of `rate_limit`'s inner
    dependency purely so it's unit-testable without Redis/FastAPI —
    everything else about the limiter needs a real request/Redis to
    exercise meaningfully.
    """
    return int(now_epoch_seconds // window_seconds)


def rate_limit(*, name: str, max_requests: int, window_seconds: int):
    """
    Redis-backed fixed-window rate limiter (Phase 15), scoped per user.

    Fixed window (INCR + EXPIRE on a key that changes every
    `window_seconds`), not sliding-window/token-bucket — allows up to ~2x
    the stated rate right at a window boundary, an accepted tradeoff for
    something this cheap to reason about; nothing in this codebase needs a
    stricter guarantee than "stop obvious abuse/cost blowup."

    Fails OPEN if Redis is unreachable — same philosophy as
    app/core/cache.py's get_json/set_json. Rate limiting here is
    abuse/cost protection layered on top of the real security boundary
    (auth + RBAC +, since this phase, RLS); it degrading doesn't open a
    hole in that boundary, so an unreachable Redis blocking every request
    would be a worse outcome than temporarily unlimited requests.

    Usage:
        @router.post("/query")
        def ask_question(
            ...,
            _: None = Depends(rate_limit(name="ai-query", max_requests=20, window_seconds=60)),
        ):
    """

    def dependency(current_user: User = Depends(get_current_user)) -> None:
        window = compute_fixed_window(time.time(), window_seconds)
        key = f"ratelimit:{name}:{current_user.id}:{window}"

        try:
            client = get_redis_client()
            count = client.incr(key)
            if count == 1:
                client.expire(key, window_seconds)
        except redis.RedisError:
            return  # fail open — see docstring

        if count > max_requests:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Rate limit exceeded for '{name}': max {max_requests} requests "
                    f"per {window_seconds}s. Try again shortly."
                ),
            )

    return dependency
