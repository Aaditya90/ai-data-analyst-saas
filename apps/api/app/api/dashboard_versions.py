"""
Dashboard version history API — read the automatic snapshots
`app/api/dashboards.py` creates on every shape-changing action, diff any
two of them, and restore the live dashboard to a past one.

  GET  /workspaces/{id}/dashboards/{did}/versions                          - list snapshots (newest first)
  GET  /workspaces/{id}/dashboards/{did}/versions/{n}                      - one snapshot, full widget list
  GET  /workspaces/{id}/dashboards/{did}/versions/{from}/diff/{to}         - added/removed/modified widgets between two snapshots
  POST /workspaces/{id}/dashboards/{did}/versions/{n}/restore              - make the live dashboard match snapshot n again

Restoring does not delete or rewrite history — consistent with every other
"immutable version" resource in this codebase (DatasetVersion, MLModel,
Report). It replaces the live widgets with the snapshot's widgets, then
takes a *new* snapshot on top (see dashboards.py's create_version call
sites), so "restore to version 3" shows up as version N+1 with
change_summary "Restored from version 3" — the full timeline, including
the restore itself, stays intact and readable.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_workspace_role
from app.core.database import get_db
from app.models.dashboard import Dashboard
from app.models.dashboard_version import DashboardVersion
from app.models.dashboard_widget import DashboardWidget, WidgetType
from app.models.dataset import Dataset
from app.models.user import User
from app.models.workspace_member import WorkspaceMember, WorkspaceRole
from app.services.dashboard_versioning import create_version, diff_widget_snapshots

router = APIRouter(
    prefix="/workspaces/{workspace_id}/dashboards/{dashboard_id}/versions", tags=["dashboard-versions"]
)


class VersionSummary(BaseModel):
    version_number: int
    name: str
    widget_count: int
    change_summary: str
    created_by_email: str | None
    created_at: str


class VersionDetail(VersionSummary):
    widgets: list[dict]


class DiffOut(BaseModel):
    from_version: int
    to_version: int
    added: list[dict]
    removed: list[dict]
    modified: list[dict]
    unchanged_count: int


class RestoreOut(BaseModel):
    restored_from_version: int
    new_version_number: int
    widget_count: int
    skipped_missing_datasets: int


def _get_dashboard(db: Session, workspace_id: uuid.UUID, dashboard_id: uuid.UUID) -> Dashboard:
    dashboard = (
        db.query(Dashboard)
        .filter(Dashboard.id == dashboard_id, Dashboard.workspace_id == workspace_id)
        .one_or_none()
    )
    if dashboard is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dashboard not found")
    return dashboard


def _get_version(db: Session, dashboard_id: uuid.UUID, version_number: int) -> DashboardVersion:
    version = (
        db.query(DashboardVersion)
        .filter(
            DashboardVersion.dashboard_id == dashboard_id,
            DashboardVersion.version_number == version_number,
        )
        .one_or_none()
    )
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Version {version_number} not found"
        )
    return version


def _summary(version: DashboardVersion, db: Session) -> VersionSummary:
    creator = db.get(User, version.created_by_user_id) if version.created_by_user_id else None
    return VersionSummary(
        version_number=version.version_number,
        name=version.name,
        widget_count=len(version.widgets_snapshot_json),
        change_summary=version.change_summary,
        created_by_email=creator.email if creator else None,
        created_at=version.created_at.isoformat(),
    )


@router.get("", response_model=list[VersionSummary])
def list_versions(
    workspace_id: uuid.UUID,
    dashboard_id: uuid.UUID,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.VIEWER)),
    db: Session = Depends(get_db),
) -> list[VersionSummary]:
    _get_dashboard(db, workspace_id, dashboard_id)
    versions = (
        db.query(DashboardVersion)
        .filter(DashboardVersion.dashboard_id == dashboard_id)
        .order_by(DashboardVersion.version_number.desc())
        .all()
    )
    return [_summary(v, db) for v in versions]


@router.get("/{version_number}", response_model=VersionDetail)
def get_version(
    workspace_id: uuid.UUID,
    dashboard_id: uuid.UUID,
    version_number: int,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.VIEWER)),
    db: Session = Depends(get_db),
) -> VersionDetail:
    _get_dashboard(db, workspace_id, dashboard_id)
    version = _get_version(db, dashboard_id, version_number)
    summary = _summary(version, db)
    return VersionDetail(**summary.model_dump(), widgets=version.widgets_snapshot_json)


@router.get("/{from_version}/diff/{to_version}", response_model=DiffOut)
def diff_versions(
    workspace_id: uuid.UUID,
    dashboard_id: uuid.UUID,
    from_version: int,
    to_version: int,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.VIEWER)),
    db: Session = Depends(get_db),
) -> DiffOut:
    _get_dashboard(db, workspace_id, dashboard_id)
    old = _get_version(db, dashboard_id, from_version)
    new = _get_version(db, dashboard_id, to_version)

    result = diff_widget_snapshots(old.widgets_snapshot_json, new.widgets_snapshot_json)
    return DiffOut(from_version=from_version, to_version=to_version, **result)


@router.post("/{version_number}/restore", response_model=RestoreOut, status_code=status.HTTP_201_CREATED)
def restore_version(
    workspace_id: uuid.UUID,
    dashboard_id: uuid.UUID,
    version_number: int,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.EDITOR)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RestoreOut:
    dashboard = _get_dashboard(db, workspace_id, dashboard_id)
    target = _get_version(db, dashboard_id, version_number)

    # Replace the live widget set with the snapshot's. New rows, new ids —
    # see diff_widget_snapshots' docstring for why that's the honest
    # representation of what a restore does at the row level.
    for widget in list(dashboard.widgets):
        db.delete(widget)
    db.flush()

    dashboard.name = target.name

    skipped = 0
    for w in target.widgets_snapshot_json:
        dataset_id = w.get("dataset_id")
        if dataset_id is not None:
            dataset = (
                db.query(Dataset)
                .filter(Dataset.id == uuid.UUID(dataset_id), Dataset.workspace_id == workspace_id)
                .one_or_none()
            )
            if dataset is None:
                # The dataset this widget used to point at is gone since
                # the snapshot was taken — restore the widget anyway
                # (title/position/config intact) but drop the dangling
                # reference, same graceful-degradation posture as every
                # other "the thing this pointed to no longer exists" case
                # in this codebase, rather than failing the whole restore.
                dataset_id = None
                skipped += 1

        db.add(
            DashboardWidget(
                dashboard_id=dashboard.id,
                dataset_id=uuid.UUID(dataset_id) if dataset_id else None,
                widget_type=WidgetType(w["widget_type"]),
                title=w["title"],
                config_json=w["config_json"],
                x=w["x"],
                y=w["y"],
                w=w["w"],
                h=w["h"],
            )
        )
    db.flush()

    new_version = create_version(
        db,
        dashboard=dashboard,
        actor_user_id=current_user.id,
        change_summary=f"Restored from version {version_number}",
    )
    db.commit()

    return RestoreOut(
        restored_from_version=version_number,
        new_version_number=new_version.version_number,
        widget_count=len(target.widgets_snapshot_json),
        skipped_missing_datasets=skipped,
    )
