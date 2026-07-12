"""
FastAPI application entrypoint.

Run locally with:
    uvicorn main:app --reload --port 8000
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, health, organizations, workspaces
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(
    title="AI Data Analyst SaaS API",
    version="0.2.0",
    description="Phase 2: Authentication — Clerk-based auth + workspace RBAC.",
)

# CORS — permissive in dev, tighten in Phase 15 (Security)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"] if not settings.is_production else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(organizations.router)
app.include_router(workspaces.router)


@app.get("/")
def root() -> dict:
    return {
        "service": settings.app_name,
        "environment": settings.environment,
        "phase": "2 - authentication",
    }
