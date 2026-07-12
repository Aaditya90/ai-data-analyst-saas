"""
Health check endpoint. Used by:
- local dev, to confirm the API is up
- Docker/Kubernetes readiness+liveness probes (Phase 18)
- uptime monitoring (Phase 16)
"""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check() -> dict:
    """Basic liveness check — does not touch the DB."""
    return {"status": "ok"}


@router.get("/health/db")
def health_check_db(db: Session = Depends(get_db)) -> dict:
    """Readiness check — confirms the DB connection actually works."""
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}
