"""
FastAPI application entrypoint.

Run locally with:
    uvicorn main:app --reload --port 8000
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, cleaning, connections, dashboard_generator, dashboards, datasets, eda, forecasts, health, insights, ml_models, organizations, query, reports, workspaces
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(
    title="AI Data Analyst SaaS API",
    version="0.11.0",
    description="Phase 11: Reports (PDF/PPT Generator) — exportable PDF and PowerPoint reports from dashboards.",
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
app.include_router(datasets.router)
app.include_router(connections.router)
app.include_router(cleaning.router)
app.include_router(eda.router)
app.include_router(dashboards.router)
app.include_router(query.router)
app.include_router(insights.router)
app.include_router(dashboard_generator.router)
app.include_router(ml_models.router)
app.include_router(forecasts.router)
app.include_router(reports.router)


@app.get("/")
def root() -> dict:
    return {
        "service": settings.app_name,
        "environment": settings.environment,
        "phase": "11 - reports (pdf/ppt generator)",
    }
