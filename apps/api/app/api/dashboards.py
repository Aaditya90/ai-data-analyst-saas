"""
Dashboard Builder API:
  POST   /workspaces/{id}/dashboards                          - create dashboard
  GET    /workspaces/{id}/dashboards                          - list dashboards
  GET    /workspaces/{id}/dashboards/{did}                    - get dashboard + widgets
  PATCH  /workspaces/{id}/dashboards/{did}                    - rename dashboard
  DELETE /workspaces/{id}/dashboards/{did}                    - delete dashboard
  POST   /workspaces/{id}/dashboards/{did}/widgets            - add widget
  PATCH  /workspaces/{id}/dashboards/{did}/widgets/{wid}      - update widget (position/config)
  DELETE /workspaces/{id}/dashboards/{did}/widgets/{wid}      - remove widget
  GET    /workspaces/{id}/dashboards/{did}/widgets/{wid}/data - compute the widget's render data

The last endpoint is where config_json actually gets executed against the
widget's dataset — everything else is plain CRUD on the definition. This
split means moving/resizing widgets (very frequent, drag-and-drop) never
touches the dataset file, and only /data (rendered on load, or on refresh)
does the pandas work.

Since Phase 13, every shape-changing action here (create, rename, widget
add/update/delete) also snapshots the dashboard via
`app/services/dashboard_versioning.create_version` in the *same*
transaction as the change — see `app/api/dashboard_versions.py` for the
history/diff/restore endpoints that read those snapshots back.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_workspace_role
from app.core.database import get_db
from app.core.storage import download_to_buffer
from app.models.dashboard import Dashboard
from app.models.dashboard_widget import DashboardWidget, WidgetType
from app.models.dataset import Dataset, DatasetSourceType
from app.models.user import User
from app.models.workspace_member import WorkspaceMember, WorkspaceRole
from app.services.aggregation import compute_chart, compute_kpi, compute_table
from app.services.dashboard_versioning import create_version
from app.services.schema_inference import read_tabular_file

router = APIRouter(prefix="/workspaces/{workspace_id}/dashboards", tags=["dashboards"])


class DashboardCreate(BaseModel):
    name: str


class DashboardRename(BaseModel):
    name: str


class WidgetCreate(BaseModel):
    widget_type: WidgetType
    title: str
    dataset_id: uuid.UUID | None = None
    config_json: dict = {}
    x: int = 0
    y: int = 0
    w: int = 4
    h: int = 3


class WidgetUpdate(BaseModel):
    title: str | None = None
    config_json: dict | None = None
    x: int | None = None
    y: int | None = None
    w: int | None = None
    h: int | None = None


@router.post("", status_code=status.HTTP_201_CREATED)
def create_dashboard(
    workspace_id: uuid.UUID,
    body: DashboardCreate,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.EDITOR)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    dashboard = Dashboard(workspace_id=workspace_id, name=body.name, created_by_user_id=member.user_id)
    db.add(dashboard)
    db.flush()
    create_version(
        db, dashboard=dashboard, actor_user_id=current_user.id, change_summary="Dashboard created"
    )
    db.commit()
    db.refresh(dashboard)
    return _serialize_dashboard(dashboard)


@router.get("")
def list_dashboards(
    workspace_id: uuid.UUID,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.VIEWER)),
    db: Session = Depends(get_db),
) -> list[dict]:
    dashboards = (
        db.query(Dashboard)
        .filter(Dashboard.workspace_id == workspace_id)
        .order_by(Dashboard.created_at.desc())
        .all()
    )
    return [_serialize_dashboard(d) for d in dashboards]


@router.get("/{dashboard_id}")
def get_dashboard(
    workspace_id: uuid.UUID,
    dashboard_id: uuid.UUID,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.VIEWER)),
    db: Session = Depends(get_db),
) -> dict:
    dashboard = _get_dashboard_or_404(db, workspace_id, dashboard_id)
    result = _serialize_dashboard(dashboard)
    result["widgets"] = [_serialize_widget(w) for w in dashboard.widgets]
    return result


@router.patch("/{dashboard_id}")
def rename_dashboard(
    workspace_id: uuid.UUID,
    dashboard_id: uuid.UUID,
    body: DashboardRename,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.EDITOR)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    dashboard = _get_dashboard_or_404(db, workspace_id, dashboard_id)
    if not body.name.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Name cannot be empty")

    old_name = dashboard.name
    dashboard.name = body.name.strip()
    create_version(
        db,
        dashboard=dashboard,
        actor_user_id=current_user.id,
        change_summary=f"Renamed from \"{old_name}\" to \"{dashboard.name}\"",
    )
    db.commit()
    db.refresh(dashboard)
    return _serialize_dashboard(dashboard)


@router.delete("/{dashboard_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dashboard(
    workspace_id: uuid.UUID,
    dashboard_id: uuid.UUID,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.EDITOR)),
    db: Session = Depends(get_db),
):
    dashboard = _get_dashboard_or_404(db, workspace_id, dashboard_id)
    db.delete(dashboard)
    db.commit()
    return None


@router.post("/{dashboard_id}/widgets", status_code=status.HTTP_201_CREATED)
def create_widget(
    workspace_id: uuid.UUID,
    dashboard_id: uuid.UUID,
    body: WidgetCreate,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.EDITOR)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    dashboard = _get_dashboard_or_404(db, workspace_id, dashboard_id)

    if body.widget_type != WidgetType.TEXT and body.dataset_id is not None:
        dataset = (
            db.query(Dataset)
            .filter(Dataset.id == body.dataset_id, Dataset.workspace_id == workspace_id)
            .one_or_none()
        )
        if dataset is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset not found")

    widget = DashboardWidget(
        dashboard_id=dashboard_id,
        dataset_id=body.dataset_id,
        widget_type=body.widget_type,
        title=body.title,
        config_json=body.config_json,
        x=body.x,
        y=body.y,
        w=body.w,
        h=body.h,
    )
    db.add(widget)
    db.flush()
    create_version(
        db,
        dashboard=dashboard,
        actor_user_id=current_user.id,
        change_summary=f"Added widget \"{widget.title}\"",
    )
    db.commit()
    db.refresh(widget)
    return _serialize_widget(widget)


@router.patch("/{dashboard_id}/widgets/{widget_id}")
def update_widget(
    workspace_id: uuid.UUID,
    dashboard_id: uuid.UUID,
    widget_id: uuid.UUID,
    body: WidgetUpdate,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.EDITOR)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    dashboard = _get_dashboard_or_404(db, workspace_id, dashboard_id)
    widget = _get_widget_or_404(db, dashboard_id, widget_id)

    changed_fields = body.model_dump(exclude_unset=True)
    for field, value in changed_fields.items():
        setattr(widget, field, value)

    create_version(
        db,
        dashboard=dashboard,
        actor_user_id=current_user.id,
        change_summary=f"Updated widget \"{widget.title}\" ({', '.join(changed_fields.keys()) or 'no fields'})",
    )
    db.commit()
    db.refresh(widget)
    return _serialize_widget(widget)


@router.delete("/{dashboard_id}/widgets/{widget_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_widget(
    workspace_id: uuid.UUID,
    dashboard_id: uuid.UUID,
    widget_id: uuid.UUID,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.EDITOR)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    dashboard = _get_dashboard_or_404(db, workspace_id, dashboard_id)
    widget = _get_widget_or_404(db, dashboard_id, widget_id)
    widget_title = widget.title
    db.delete(widget)
    create_version(
        db,
        dashboard=dashboard,
        actor_user_id=current_user.id,
        change_summary=f"Removed widget \"{widget_title}\"",
    )
    db.commit()
    return None


@router.get("/{dashboard_id}/widgets/{widget_id}/data")
def get_widget_data(
    workspace_id: uuid.UUID,
    dashboard_id: uuid.UUID,
    widget_id: uuid.UUID,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.VIEWER)),
    db: Session = Depends(get_db),
) -> dict:
    _get_dashboard_or_404(db, workspace_id, dashboard_id)
    widget = _get_widget_or_404(db, dashboard_id, widget_id)

    if widget.widget_type == WidgetType.TEXT:
        return {"content": widget.config_json.get("content", "")}

    if widget.dataset_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Widget has no dataset")

    dataset = db.get(Dataset, widget.dataset_id)
    if dataset is None or not dataset.versions:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset not found")

    version = dataset.versions[0]
    if dataset.source_type != DatasetSourceType.FILE_UPLOAD or not version.storage_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Widget data is only available for file-upload datasets in this phase",
        )

    buffer = download_to_buffer(version.storage_key)
    df = read_tabular_file(buffer, version.original_filename or "file.csv")
    config = widget.config_json

    try:
        if widget.widget_type == WidgetType.KPI:
            return compute_kpi(df, config["column"], config.get("aggregation", "sum"))
        if widget.widget_type == WidgetType.TABLE:
            return compute_table(df, config.get("columns"))
        if widget.widget_type == WidgetType.CHART:
            return compute_chart(
                df,
                config.get("chart_type", "bar"),
                config["x_column"],
                config.get("y_column"),
                config.get("aggregation", "sum"),
            )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown widget type")


def _get_dashboard_or_404(db: Session, workspace_id: uuid.UUID, dashboard_id: uuid.UUID) -> Dashboard:
    dashboard = (
        db.query(Dashboard)
        .filter(Dashboard.id == dashboard_id, Dashboard.workspace_id == workspace_id)
        .one_or_none()
    )
    if dashboard is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dashboard not found")
    return dashboard


def _get_widget_or_404(db: Session, dashboard_id: uuid.UUID, widget_id: uuid.UUID) -> DashboardWidget:
    widget = (
        db.query(DashboardWidget)
        .filter(DashboardWidget.id == widget_id, DashboardWidget.dashboard_id == dashboard_id)
        .one_or_none()
    )
    if widget is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Widget not found")
    return widget


def _serialize_dashboard(dashboard: Dashboard) -> dict:
    return {
        "id": str(dashboard.id),
        "name": dashboard.name,
        "created_at": dashboard.created_at.isoformat(),
        "widget_count": len(dashboard.widgets),
    }


def _serialize_widget(widget: DashboardWidget) -> dict:
    return {
        "id": str(widget.id),
        "widget_type": widget.widget_type.value,
        "title": widget.title,
        "dataset_id": str(widget.dataset_id) if widget.dataset_id else None,
        "config_json": widget.config_json,
        "x": widget.x,
        "y": widget.y,
        "w": widget.w,
        "h": widget.h,
    }
