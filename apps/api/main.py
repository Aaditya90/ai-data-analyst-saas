"""
FastAPI application entrypoint.

Run locally with:
    uvicorn main:app --reload --port 8000
"""

import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    activity,
    auth,
    billing,
    cleaning,
    comments,
    connections,
    dashboard_generator,
    dashboard_versions,
    dashboards,
    datasets,
    eda,
    enterprise,
    forecasts,
    health,
    insights,
    invites,
    ml_models,
    organizations,
    query,
    reports,
    workspaces,
)
from app.core.config import get_settings
from app.core.error_tracking import init_error_tracking
from app.core.logging_config import configure_logging
from app.core.production_checks import enforce_production_config
from app.core.request_context import new_request_id, reset_request_id, set_request_id

configure_logging()
init_error_tracking()
logger = logging.getLogger("app.request")

settings = get_settings()
# Phase 18: in production, refuse to boot with default secrets / no auth config.
enforce_production_config(settings)

app = FastAPI(
    title="AI Data Analyst SaaS API",
    version="0.19.0",
    description="Phase 19: Enterprise — API keys, IP allowlist, audit log export, plan-gated features.",
)

# CORS — dev always allows localhost:3000; production reads CORS_ALLOWED_ORIGINS (Phase 15)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    """
    Phase 16: gives every request a correlation id, echoed back as
    `X-Request-ID` (reusing one the client already sent, if present, so a
    frontend-generated id survives end to end) and attached to every JSON
    log line written during the request via app.core.request_context —
    including ones written deep inside a service function that has no
    idea an HTTP request is even involved. Also logs one structured line
    per request with method/path/status/duration, so request-level
    observability doesn't depend on every route remembering to log
    anything itself.
    """
    request_id = request.headers.get("x-request-id") or new_request_id()
    token = set_request_id(request_id)
    started_at = time.time()

    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "request_failed",
            extra={
                "http_method": request.method,
                "http_path": request.url.path,
                "duration_ms": round((time.time() - started_at) * 1000, 1),
            },
        )
        raise
    else:
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request_completed",
            extra={
                "http_method": request.method,
                "http_path": request.url.path,
                "http_status": response.status_code,
                "duration_ms": round((time.time() - started_at) * 1000, 1),
            },
        )
        return response
    finally:
        reset_request_id(token)


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """
    Phase 15: a handful of standard defensive response headers, applied to
    every response. Deliberately does NOT set Content-Security-Policy —
    FastAPI's built-in /docs (Swagger UI) loads its JS/CSS from
    cdn.jsdelivr.net, and a CSP strict enough to be worth adding would
    break it; a real CSP is a frontend (Next.js) concern for the pages
    that actually render HTML, not this JSON API. Strict-Transport-
    Security is only sent in production — sending it in local dev (plain
    HTTP) has no effect and templates that once encountered it will keep
    demanding HTTPS on that host for the header's max-age even after this
    API returns to dev use.
    """
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
    if settings.is_production:
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return response


app.include_router(health.router)
app.include_router(enterprise.router)
app.include_router(auth.router)
app.include_router(organizations.router)
app.include_router(workspaces.router)
app.include_router(datasets.router)
app.include_router(connections.router)
app.include_router(cleaning.router)
app.include_router(eda.router)
app.include_router(dashboards.router)
app.include_router(dashboard_versions.router)
app.include_router(query.router)
app.include_router(insights.router)
app.include_router(dashboard_generator.router)
app.include_router(ml_models.router)
app.include_router(forecasts.router)
app.include_router(reports.router)
app.include_router(invites.router)
app.include_router(invites.public_router)
app.include_router(comments.router)
app.include_router(activity.router)
app.include_router(billing.router)
app.include_router(billing.webhook_router)


@app.get("/")
def root() -> dict:
    return {
        "service": settings.app_name,
        "environment": settings.environment,
        "phase": "16 - observability (structured logging, request tracing, Sentry, Langfuse)",
    }
