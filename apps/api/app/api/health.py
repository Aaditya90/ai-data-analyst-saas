"""
Health check endpoints. Used by:
- local dev, to confirm the API is up
- Docker/Kubernetes readiness+liveness probes (Phase 18)
- uptime monitoring (Phase 16)

/health is the liveness probe (touches nothing — "is the process up").
/health/db and /health/redis are individual readiness checks for each
dependency. /health/detailed combines both plus which optional
integrations (AI, email, billing, error tracking, LLM tracing) are
actually configured — handy for confirming a deployment's .env is what
you think it is without grepping through it.
"""

import redis
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.cache import get_redis_client
from app.core.config import get_settings
from app.core.database import get_db
from app.core.error_tracking import is_error_tracking_configured

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check() -> dict:
    """Basic liveness check — does not touch the DB or Redis."""
    return {"status": "ok"}


@router.get("/health/db")
def health_check_db(db: Session = Depends(get_db)) -> dict:
    """Readiness check — confirms the DB connection actually works."""
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}


@router.get("/health/ready")
def health_check_ready(db: Session = Depends(get_db)) -> dict:
    """
    Phase 18: the readiness probe for load balancers / orchestrators.
    Unlike /health/db (which surfaces a DB failure as an unhandled 500)
    and /health/detailed (always 200), this returns a clean 503 when the
    database is unreachable, so traffic is routed away from the instance.
    Redis is deliberately NOT required: caching and rate limiting fail
    open (Phase 15), so a Redis outage degrades the app, it doesn't
    make an instance unfit to serve.
    """
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ready"}


@router.get("/health/redis")
def health_check_redis() -> dict:
    """Readiness check — confirms Redis is reachable."""
    try:
        get_redis_client().ping()
        return {"status": "ok", "redis": "connected"}
    except redis.RedisError as exc:
        return {"status": "degraded", "redis": "unreachable", "detail": str(exc)}


@router.get("/health/detailed")
def health_check_detailed(db: Session = Depends(get_db)) -> dict:
    """
    Everything /health/db and /health/redis check, plus which optional
    integrations are configured. Never fails the request just because an
    *optional* integration is unconfigured (that's expected in dev) —
    only `database` reflects a real failure if it can't connect, since
    every request needs the DB regardless of environment.
    """
    settings = get_settings()

    db_ok = True
    db_detail = "connected"
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        db_ok = False
        db_detail = str(exc)

    redis_ok = True
    redis_detail = "connected"
    try:
        get_redis_client().ping()
    except redis.RedisError as exc:
        redis_ok = False
        redis_detail = str(exc)

    return {
        "status": "ok" if db_ok else "degraded",
        "database": {"connected": db_ok, "detail": db_detail},
        "redis": {"connected": redis_ok, "detail": redis_detail},
        "integrations_configured": {
            "ai": bool(settings.anthropic_api_key),
            "email_smtp": bool(settings.smtp_host),
            "billing_stripe": bool(settings.stripe_secret_key),
            "error_tracking_sentry": is_error_tracking_configured(),
            "llm_tracing_langfuse": bool(
                settings.langfuse_public_key and settings.langfuse_secret_key
            ),
        },
    }
