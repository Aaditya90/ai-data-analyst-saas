"""
POST /workspaces/{id}/datasets/{did}/generate-dashboard

Orchestrates three things that already exist rather than reimplementing
any of them:
  1. Phase 5's EDA functions (column summaries + chart suggestions) build
     the candidate widget list.
  2. app/services/dashboard_generator.py has the AI pick + title a subset
     (falling back to a rule-based selection on any AI failure).
  3. Phase 6's Dashboard/DashboardWidget models persist the result — this
     endpoint's output is indistinguishable from a dashboard a user built
     by hand, and can be edited the same way afterward.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_workspace_role
from app.core.database import get_db
from app.core.storage import download_to_buffer
from app.models.dashboard import Dashboard
from app.models.dashboard_widget import DashboardWidget, WidgetType
from app.models.dataset import Dataset, DatasetSourceType
from app.models.workspace_member import WorkspaceMember, WorkspaceRole
from app.services.dashboard_generator import (
    DashboardGeneratorError,
    ai_select_widgets,
    build_candidates,
    pack_layout,
    select_fallback,
)
from app.services.eda import compute_column_summaries, suggest_charts
from app.services.schema_inference import read_tabular_file

router = APIRouter(prefix="/workspaces/{workspace_id}/datasets/{dataset_id}", tags=["ai-dashboard"])


class GenerateDashboardRequest(BaseModel):
    dashboard_name: str | None = None


@router.post("/generate-dashboard", status_code=status.HTTP_201_CREATED)
def generate_dashboard(
    workspace_id: uuid.UUID,
    dataset_id: uuid.UUID,
    body: GenerateDashboardRequest,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.EDITOR)),
    db: Session = Depends(get_db),
) -> dict:
    dataset = (
        db.query(Dataset)
        .filter(Dataset.id == dataset_id, Dataset.workspace_id == workspace_id)
        .one_or_none()
    )
    if dataset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset not found")
    if not dataset.versions:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Dataset has no versions")

    version = dataset.versions[0]
    if dataset.source_type != DatasetSourceType.FILE_UPLOAD or not version.storage_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dashboard generation is only available for file-upload datasets in this phase",
        )

    buffer = download_to_buffer(version.storage_key)
    df = read_tabular_file(buffer, version.original_filename or "file.csv")

    summaries = compute_column_summaries(df)
    chart_suggestions = suggest_charts(summaries)
    candidates = build_candidates(summaries, chart_suggestions)

    ai_used = True
    try:
        selection = ai_select_widgets(candidates, dataset.name)
    except DashboardGeneratorError:
        ai_used = False
        selection = select_fallback(candidates)

    positioned_widgets = pack_layout(selection["widgets"])

    dashboard_name = body.dashboard_name or selection["dashboard_name"]
    dashboard = Dashboard(workspace_id=workspace_id, name=dashboard_name, created_by_user_id=member.user_id)
    db.add(dashboard)
    db.flush()

    for widget_spec in positioned_widgets:
        db.add(
            DashboardWidget(
                dashboard_id=dashboard.id,
                dataset_id=dataset.id if widget_spec["widget_type"] != "text" else None,
                widget_type=WidgetType(widget_spec["widget_type"]),
                title=widget_spec["title"],
                config_json=widget_spec["config_json"],
                x=widget_spec["x"],
                y=widget_spec["y"],
                w=widget_spec["w"],
                h=widget_spec["h"],
            )
        )

    db.commit()
    db.refresh(dashboard)

    return {
        "dashboard_id": str(dashboard.id),
        "dashboard_name": dashboard.name,
        "widget_count": len(positioned_widgets),
        "ai_used": ai_used,
    }
