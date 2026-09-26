"""
Reports API:
  POST   /workspaces/{id}/dashboards/{did}/reports             - generate a report
  GET    /workspaces/{id}/dashboards/{did}/reports             - list reports for a dashboard
  GET    /workspaces/{id}/dashboards/{did}/reports/{rid}        - report metadata
  GET    /workspaces/{id}/dashboards/{did}/reports/{rid}/download - stream the file
  DELETE /workspaces/{id}/dashboards/{did}/reports/{rid}        - delete a report

Pipeline: load the dashboard's widgets -> compute each widget's render data
the exact same way Phase 6's GET .../widgets/{wid}/data does (same
aggregation.py functions, same "dataset.versions[0] is current" rule) ->
build a code-only executive-summary payload (dashboard name + KPI values +
each chart's top category) -> narrate_report_summary() phrases those exact
numbers, with a template fallback (same split as every AI feature since
Phase 8) -> report_generator.py turns the sections into PDF or PPTX bytes
-> upload to object storage, exactly like MLModel's serialized pipeline ->
persist a Report row.

Same file-upload-only limitation as every data-processing feature so far:
a widget backed by a DB-connector dataset is skipped with a placeholder
rather than failing the whole report (one bad widget shouldn't block the
other nineteen).
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_workspace_role
from app.core.database import get_db
from app.core.storage import download_to_buffer, upload_bytes
from app.models.dashboard import Dashboard
from app.models.dashboard_widget import DashboardWidget, WidgetType
from app.models.dataset import Dataset, DatasetSourceType
from app.models.report import Report, ReportFormat, ReportStatus
from app.models.workspace_member import WorkspaceMember, WorkspaceRole
from app.services.aggregation import compute_chart, compute_kpi, compute_table
from app.services.ai_narration import (
    NarrationError,
    fallback_report_summary,
    narrate_report_summary,
)
from app.services.report_generator import (
    ReportGenerationError,
    generate_pdf_report,
    generate_pptx_report,
)
from app.services.schema_inference import read_tabular_file

router = APIRouter(
    prefix="/workspaces/{workspace_id}/dashboards/{dashboard_id}/reports", tags=["reports"]
)

_MEDIA_TYPES = {
    ReportFormat.PDF: "application/pdf",
    ReportFormat.PPTX: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
_EXTENSIONS = {ReportFormat.PDF: "pdf", ReportFormat.PPTX: "pptx"}
CHART_HIGHLIGHTS_LIMIT = 5


class ReportCreate(BaseModel):
    format: ReportFormat
    title: str | None = None


def _get_dashboard(db: Session, workspace_id: uuid.UUID, dashboard_id: uuid.UUID) -> Dashboard:
    dashboard = (
        db.query(Dashboard)
        .filter(Dashboard.id == dashboard_id, Dashboard.workspace_id == workspace_id)
        .one_or_none()
    )
    if dashboard is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dashboard not found")
    return dashboard


def _get_report(db: Session, dashboard_id: uuid.UUID, report_id: uuid.UUID) -> Report:
    report = (
        db.query(Report)
        .filter(Report.id == report_id, Report.dashboard_id == dashboard_id)
        .one_or_none()
    )
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    return report


def _compute_widget_data(db: Session, widget: DashboardWidget) -> dict | None:
    """Mirrors app/api/dashboards.py's get_widget_data exactly, so a report
    always shows what the dashboard would show right now. Returns None
    for a widget that can't be rendered (missing dataset, bad config,
    DB-connector source) rather than raising — one bad widget shouldn't
    sink the whole report."""
    if widget.widget_type == WidgetType.TEXT:
        return {"content": widget.config_json.get("content", "")}

    if widget.dataset_id is None:
        return None

    dataset = db.get(Dataset, widget.dataset_id)
    if dataset is None or not dataset.versions:
        return None

    version = dataset.versions[0]
    if dataset.source_type != DatasetSourceType.FILE_UPLOAD or not version.storage_key:
        return None

    try:
        buffer = download_to_buffer(version.storage_key)
        df = read_tabular_file(buffer, version.original_filename or "file.csv")
        config = widget.config_json

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
    except (KeyError, ValueError):
        return None
    return None


def _build_summary_payload(dashboard_name: str, sections: list[dict]) -> dict:
    """Code-only extraction of the numbers worth summarizing — the AI
    narrator only ever phrases exactly this, it never sees the raw widget
    configs or datasets."""
    kpis = [
        {"title": s["title"], "value": s["data"]["value"]}
        for s in sections
        if s["widget_type"] == "kpi" and s["data"].get("value") is not None
    ]
    chart_highlights = []
    for s in sections:
        if s["widget_type"] != "chart":
            continue
        labels, values = s["data"].get("labels") or [], s["data"].get("values") or []
        if not labels or not values:
            continue
        top_idx = max(range(len(values)), key=lambda i: (values[i] if values[i] is not None else float("-inf")))
        chart_highlights.append({"title": s["title"], "top_label": labels[top_idx], "top_value": values[top_idx]})
        if len(chart_highlights) >= CHART_HIGHLIGHTS_LIMIT:
            break

    return {"dashboard_name": dashboard_name, "kpis": kpis, "chart_highlights": chart_highlights}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_report(
    workspace_id: uuid.UUID,
    dashboard_id: uuid.UUID,
    body: ReportCreate,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.EDITOR)),
    db: Session = Depends(get_db),
) -> dict:
    dashboard = _get_dashboard(db, workspace_id, dashboard_id)
    widgets = (
        db.query(DashboardWidget)
        .filter(DashboardWidget.dashboard_id == dashboard_id)
        .order_by(DashboardWidget.y, DashboardWidget.x)
        .all()
    )

    sections = []
    for widget in widgets:
        data = _compute_widget_data(db, widget)
        if data is None:
            continue
        sections.append({"widget_type": widget.widget_type.value, "title": widget.title, "data": data})

    report = Report(
        workspace_id=workspace_id,
        dashboard_id=dashboard_id,
        title=body.title or f"{dashboard.name} Report",
        format=body.format,
        status=ReportStatus.GENERATING,
        widget_count=len(sections),
        created_by_user_id=member.user_id,
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    if not sections:
        report.status = ReportStatus.FAILED
        report.error_message = "Dashboard has no renderable widgets"
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=report.error_message)

    summary_payload = _build_summary_payload(dashboard.name, sections)
    try:
        summary = narrate_report_summary(summary_payload)
        ai_narration_used = True
    except NarrationError:
        summary = fallback_report_summary(summary_payload)
        ai_narration_used = False

    generated_at = datetime.now(timezone.utc)
    try:
        if body.format == ReportFormat.PDF:
            file_bytes = generate_pdf_report(dashboard.name, sections, summary["summary"], generated_at)
        else:
            file_bytes = generate_pptx_report(dashboard.name, sections, summary["summary"], generated_at)
    except ReportGenerationError as exc:
        report.status = ReportStatus.FAILED
        report.error_message = str(exc)
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    extension = _EXTENSIONS[body.format]
    storage_key = f"workspaces/{workspace_id}/dashboards/{dashboard_id}/reports/{report.id}.{extension}"
    upload_bytes(storage_key, file_bytes)

    report.status = ReportStatus.READY
    report.storage_key = storage_key
    report.file_size_bytes = len(file_bytes)
    report.summary_json = {"summary": summary["summary"], "ai_narration_used": ai_narration_used}
    db.commit()
    db.refresh(report)

    return _serialize_report(report)


@router.get("")
def list_reports(
    workspace_id: uuid.UUID,
    dashboard_id: uuid.UUID,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.VIEWER)),
    db: Session = Depends(get_db),
) -> list[dict]:
    _get_dashboard(db, workspace_id, dashboard_id)
    reports = (
        db.query(Report)
        .filter(Report.dashboard_id == dashboard_id)
        .order_by(Report.created_at.desc())
        .all()
    )
    return [_serialize_report(r) for r in reports]


@router.get("/{report_id}")
def get_report(
    workspace_id: uuid.UUID,
    dashboard_id: uuid.UUID,
    report_id: uuid.UUID,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.VIEWER)),
    db: Session = Depends(get_db),
) -> dict:
    _get_dashboard(db, workspace_id, dashboard_id)
    report = _get_report(db, dashboard_id, report_id)
    return _serialize_report(report)


@router.get("/{report_id}/download")
def download_report(
    workspace_id: uuid.UUID,
    dashboard_id: uuid.UUID,
    report_id: uuid.UUID,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.VIEWER)),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    _get_dashboard(db, workspace_id, dashboard_id)
    report = _get_report(db, dashboard_id, report_id)
    if report.status != ReportStatus.READY or not report.storage_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Report is not ready")

    buffer = download_to_buffer(report.storage_key)
    extension = _EXTENSIONS[report.format]
    filename = f"{report.title.replace(' ', '_')}.{extension}"
    return StreamingResponse(
        buffer,
        media_type=_MEDIA_TYPES[report.format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_report(
    workspace_id: uuid.UUID,
    dashboard_id: uuid.UUID,
    report_id: uuid.UUID,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.EDITOR)),
    db: Session = Depends(get_db),
) -> None:
    _get_dashboard(db, workspace_id, dashboard_id)
    report = _get_report(db, dashboard_id, report_id)
    db.delete(report)
    db.commit()


def _serialize_report(report: Report) -> dict:
    return {
        "id": str(report.id),
        "title": report.title,
        "format": report.format.value,
        "status": report.status.value,
        "widget_count": report.widget_count,
        "file_size_bytes": report.file_size_bytes,
        "summary": report.summary_json,
        "error_message": report.error_message,
        "created_at": report.created_at.isoformat(),
    }
