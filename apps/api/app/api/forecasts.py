"""
Forecasting API:
  POST   /workspaces/{id}/datasets/{did}/versions/{vid}/forecasts       - run a forecast
  GET    /workspaces/{id}/datasets/{did}/versions/{vid}/forecasts       - list forecasts for a version
  GET    /workspaces/{id}/datasets/{did}/versions/{vid}/forecasts/{fid} - forecast detail (+ narration)
  DELETE /workspaces/{id}/datasets/{did}/versions/{vid}/forecasts/{fid} - delete a forecast

Pipeline: read the version's file -> forecasting.run_forecast() (pure
numpy/pandas/sklearn: resample, fit trend+seasonality, backtest, project
forward) -> persist the run as a Forecast row -> narrate_forecast() phrases
the pre-computed trend/accuracy numbers in plain language, with a template
fallback if the Claude call fails (same split as every AI feature since
Phase 8: app/services/ai_narration.py).

Unlike Phase 5/7/8's EDA/query/insights (which recompute from an immutable
version and cache the result in Redis), a forecast run is itself the
persisted artifact — same posture as Phase 9's generated dashboards — so
there's no separate cache layer here; GET just reads the row back.

Same file-upload-only limitation as every data-processing feature so far.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_workspace_role
from app.core.database import get_db
from app.core.storage import download_to_buffer
from app.models.dataset import Dataset, DatasetSourceType
from app.models.dataset_version import DatasetVersion
from app.models.forecast import Forecast, ForecastFrequency, ForecastStatus
from app.models.workspace_member import WorkspaceMember, WorkspaceRole
from app.services.ai_narration import (
    NarrationError,
    fallback_forecast_narration,
    narrate_forecast,
)
from app.services.forecasting import ForecastingError, run_forecast
from app.services.schema_inference import read_tabular_file

router = APIRouter(
    prefix="/workspaces/{workspace_id}/datasets/{dataset_id}/versions/{version_id}/forecasts",
    tags=["forecasts"],
)

DEFAULT_HORIZON = 30
POINTS_FOR_NARRATION = 8  # first/last few forecast points given to the narrator, not the full list


class ForecastRequest(BaseModel):
    date_column: str
    value_column: str
    horizon: int = DEFAULT_HORIZON
    frequency: str | None = None  # "daily" | "weekly" | "monthly" — inferred if omitted


def _get_dataset_and_version(
    db: Session, workspace_id: uuid.UUID, dataset_id: uuid.UUID, version_id: uuid.UUID
) -> tuple[Dataset, DatasetVersion]:
    dataset = (
        db.query(Dataset)
        .filter(Dataset.id == dataset_id, Dataset.workspace_id == workspace_id)
        .one_or_none()
    )
    if dataset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset not found")

    version = (
        db.query(DatasetVersion)
        .filter(DatasetVersion.id == version_id, DatasetVersion.dataset_id == dataset_id)
        .one_or_none()
    )
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")

    if dataset.source_type != DatasetSourceType.FILE_UPLOAD or not version.storage_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Forecasting is only available for file-upload datasets in this phase",
        )
    return dataset, version


def _get_forecast(db: Session, dataset_id: uuid.UUID, version_id: uuid.UUID, forecast_id: uuid.UUID) -> Forecast:
    forecast = (
        db.query(Forecast)
        .filter(
            Forecast.id == forecast_id,
            Forecast.dataset_id == dataset_id,
            Forecast.dataset_version_id == version_id,
        )
        .one_or_none()
    )
    if forecast is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Forecast not found")
    return forecast


@router.post("", status_code=status.HTTP_201_CREATED)
def create_forecast(
    workspace_id: uuid.UUID,
    dataset_id: uuid.UUID,
    version_id: uuid.UUID,
    body: ForecastRequest,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.EDITOR)),
    db: Session = Depends(get_db),
) -> dict:
    dataset, version = _get_dataset_and_version(db, workspace_id, dataset_id, version_id)

    buffer = download_to_buffer(version.storage_key)
    df = read_tabular_file(buffer, version.original_filename or "file.csv")

    forecast = Forecast(
        workspace_id=workspace_id,
        dataset_id=dataset_id,
        dataset_version_id=version_id,
        date_column=body.date_column,
        value_column=body.value_column,
        horizon=body.horizon,
        frequency=ForecastFrequency(body.frequency) if body.frequency else None,
        status=ForecastStatus.RUNNING,
        created_by_user_id=member.user_id,
    )
    db.add(forecast)
    db.commit()
    db.refresh(forecast)

    try:
        result = run_forecast(
            df,
            date_column=body.date_column,
            value_column=body.value_column,
            horizon=body.horizon,
            frequency=body.frequency,
        )
    except ForecastingError as exc:
        forecast.status = ForecastStatus.FAILED
        forecast.error_message = str(exc)
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    forecast.status = ForecastStatus.READY
    forecast.frequency = ForecastFrequency(result["frequency"])
    forecast.method = result["method"]
    forecast.history_json = result["history"]
    forecast.forecast_json = result["forecast"]
    forecast.metrics_json = result["metrics"]
    db.commit()
    db.refresh(forecast)

    return _serialize_forecast(forecast, include_narration=True)


@router.get("")
def list_forecasts(
    workspace_id: uuid.UUID,
    dataset_id: uuid.UUID,
    version_id: uuid.UUID,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.VIEWER)),
    db: Session = Depends(get_db),
) -> list[dict]:
    _get_dataset_and_version(db, workspace_id, dataset_id, version_id)
    forecasts = (
        db.query(Forecast)
        .filter(Forecast.dataset_id == dataset_id, Forecast.dataset_version_id == version_id)
        .order_by(Forecast.created_at.desc())
        .all()
    )
    return [_serialize_forecast(f, include_narration=False) for f in forecasts]


@router.get("/{forecast_id}")
def get_forecast(
    workspace_id: uuid.UUID,
    dataset_id: uuid.UUID,
    version_id: uuid.UUID,
    forecast_id: uuid.UUID,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.VIEWER)),
    db: Session = Depends(get_db),
) -> dict:
    _get_dataset_and_version(db, workspace_id, dataset_id, version_id)
    forecast = _get_forecast(db, dataset_id, version_id, forecast_id)
    return _serialize_forecast(forecast, include_narration=True)


@router.delete("/{forecast_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_forecast(
    workspace_id: uuid.UUID,
    dataset_id: uuid.UUID,
    version_id: uuid.UUID,
    forecast_id: uuid.UUID,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.EDITOR)),
    db: Session = Depends(get_db),
) -> None:
    _get_dataset_and_version(db, workspace_id, dataset_id, version_id)
    forecast = _get_forecast(db, dataset_id, version_id, forecast_id)
    db.delete(forecast)
    db.commit()


def _serialize_forecast(forecast: Forecast, include_narration: bool) -> dict:
    data = {
        "id": str(forecast.id),
        "date_column": forecast.date_column,
        "value_column": forecast.value_column,
        "horizon": forecast.horizon,
        "frequency": forecast.frequency.value if forecast.frequency else None,
        "status": forecast.status.value,
        "method": forecast.method,
        "history": forecast.history_json,
        "forecast": forecast.forecast_json,
        "metrics": forecast.metrics_json,
        "error_message": forecast.error_message,
        "created_at": forecast.created_at.isoformat(),
    }

    if include_narration and forecast.status == ForecastStatus.READY:
        points = forecast.forecast_json or []
        sample_points = points[:4] + points[-4:] if len(points) > 8 else points
        summary = {
            "frequency": forecast.frequency.value,
            "method": forecast.method,
            "forecast": sample_points,
            "metrics": forecast.metrics_json,
        }
        try:
            narration = narrate_forecast(summary)
            data["narration"] = narration
            data["ai_narration_used"] = True
        except NarrationError:
            data["narration"] = fallback_forecast_narration({**summary, "forecast": points})
            data["ai_narration_used"] = False

    return data
