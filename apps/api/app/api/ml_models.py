"""
AutoML API:
  POST   /workspaces/{id}/datasets/{did}/versions/{vid}/models              - train a model
  GET    /workspaces/{id}/datasets/{did}/versions/{vid}/models              - list models for a version
  GET    /workspaces/{id}/datasets/{did}/versions/{vid}/models/{mid}        - model detail (metrics, narration)
  DELETE /workspaces/{id}/datasets/{did}/versions/{vid}/models/{mid}        - delete a model
  POST   /workspaces/{id}/datasets/{did}/versions/{vid}/models/{mid}/predict - predict on new rows

Pipeline: read the version's file -> automl.train_automl_model() (pure
sklearn, picks + evaluates candidates) -> serialize the winning pipeline
with joblib and store it in object storage exactly the way DatasetVersion
stores raw files -> persist metrics/feature-importance/leaderboard inline
as JSONB, since those are small. Same "code computes, AI only narrates"
split as Phase 8/9: narrate_model_result() is handed the exact metrics
computed here and only phrases them, with a template fallback if the
Claude call fails (see app/services/ai_narration.py).

Same file-upload-only limitation as every data-processing feature so far
(Phase 5/7/8/9's pattern) — DB-connector datasets aren't supported yet.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_workspace_role
from app.core.database import get_db
from app.core.storage import download_to_buffer, upload_bytes
from app.models.dataset import Dataset, DatasetSourceType
from app.models.dataset_version import DatasetVersion
from app.models.ml_model import MLModel, MLModelStatus, MLTaskType
from app.models.workspace_member import WorkspaceMember, WorkspaceRole
from app.services.ai_narration import (
    NarrationError,
    fallback_model_narration,
    narrate_model_result,
)
from app.services.automl import (
    AutoMLError,
    deserialize_pipeline,
    predict_with_pipeline,
    serialize_pipeline,
    train_automl_model,
)
from app.services.schema_inference import read_tabular_file

router = APIRouter(
    prefix="/workspaces/{workspace_id}/datasets/{dataset_id}/versions/{version_id}/models",
    tags=["ml-models"],
)

TOP_FEATURES_FOR_NARRATION = 5


class TrainModelRequest(BaseModel):
    target_column: str
    feature_columns: list[str] | None = None
    task_type: str | None = None  # "regression" | "classification" — auto-detected if omitted
    name: str | None = None


class PredictRequest(BaseModel):
    rows: list[dict]


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
            detail="ML models are only available for file-upload datasets in this phase",
        )
    return dataset, version


def _get_model(db: Session, dataset_id: uuid.UUID, version_id: uuid.UUID, model_id: uuid.UUID) -> MLModel:
    model = (
        db.query(MLModel)
        .filter(
            MLModel.id == model_id,
            MLModel.dataset_id == dataset_id,
            MLModel.dataset_version_id == version_id,
        )
        .one_or_none()
    )
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found")
    return model


@router.post("", status_code=status.HTTP_201_CREATED)
def train_model(
    workspace_id: uuid.UUID,
    dataset_id: uuid.UUID,
    version_id: uuid.UUID,
    body: TrainModelRequest,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.EDITOR)),
    db: Session = Depends(get_db),
) -> dict:
    dataset, version = _get_dataset_and_version(db, workspace_id, dataset_id, version_id)

    buffer = download_to_buffer(version.storage_key)
    df = read_tabular_file(buffer, version.original_filename or "file.csv")

    model = MLModel(
        workspace_id=workspace_id,
        dataset_id=dataset_id,
        dataset_version_id=version_id,
        name=body.name or f"{body.target_column} model",
        target_column=body.target_column,
        feature_columns=body.feature_columns or [],
        status=MLModelStatus.TRAINING,
        created_by_user_id=member.user_id,
    )
    db.add(model)
    db.commit()
    db.refresh(model)

    try:
        result = train_automl_model(
            df,
            target_column=body.target_column,
            feature_columns=body.feature_columns,
            task_type=body.task_type,
        )
    except AutoMLError as exc:
        model.status = MLModelStatus.FAILED
        model.error_message = str(exc)
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    storage_key = (
        f"workspaces/{workspace_id}/datasets/{dataset_id}/models/{model.id}/pipeline.joblib"
    )
    upload_bytes(storage_key, serialize_pipeline(result["pipeline"]))

    model.status = MLModelStatus.READY
    model.task_type = MLTaskType(result["task_type"])
    model.algorithm = result["algorithm"]
    model.feature_columns = result["feature_columns"]
    model.metrics_json = result["metrics"]
    model.feature_importance_json = result["feature_importance"]
    model.candidates_json = result["candidates"]
    model.storage_key = storage_key
    model.train_row_count = result["train_row_count"]
    model.test_row_count = result["test_row_count"]
    db.commit()
    db.refresh(model)

    return _serialize_model(model, include_narration=True)


@router.get("")
def list_models(
    workspace_id: uuid.UUID,
    dataset_id: uuid.UUID,
    version_id: uuid.UUID,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.VIEWER)),
    db: Session = Depends(get_db),
) -> list[dict]:
    _get_dataset_and_version(db, workspace_id, dataset_id, version_id)
    models = (
        db.query(MLModel)
        .filter(MLModel.dataset_id == dataset_id, MLModel.dataset_version_id == version_id)
        .order_by(MLModel.created_at.desc())
        .all()
    )
    return [_serialize_model(m, include_narration=False) for m in models]


@router.get("/{model_id}")
def get_model(
    workspace_id: uuid.UUID,
    dataset_id: uuid.UUID,
    version_id: uuid.UUID,
    model_id: uuid.UUID,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.VIEWER)),
    db: Session = Depends(get_db),
) -> dict:
    _get_dataset_and_version(db, workspace_id, dataset_id, version_id)
    model = _get_model(db, dataset_id, version_id, model_id)
    return _serialize_model(model, include_narration=True)


@router.delete("/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_model(
    workspace_id: uuid.UUID,
    dataset_id: uuid.UUID,
    version_id: uuid.UUID,
    model_id: uuid.UUID,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.EDITOR)),
    db: Session = Depends(get_db),
) -> None:
    _get_dataset_and_version(db, workspace_id, dataset_id, version_id)
    model = _get_model(db, dataset_id, version_id, model_id)
    db.delete(model)
    db.commit()


@router.post("/{model_id}/predict")
def predict(
    workspace_id: uuid.UUID,
    dataset_id: uuid.UUID,
    version_id: uuid.UUID,
    model_id: uuid.UUID,
    body: PredictRequest,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.VIEWER)),
    db: Session = Depends(get_db),
) -> dict:
    _get_dataset_and_version(db, workspace_id, dataset_id, version_id)
    model = _get_model(db, dataset_id, version_id, model_id)

    if model.status != MLModelStatus.READY or not model.storage_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Model is not ready")
    if not body.rows:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="At least one row is required")

    pipeline = deserialize_pipeline(download_to_buffer(model.storage_key).getvalue())
    predictions = predict_with_pipeline(pipeline, body.rows, model.feature_columns)

    return {
        "model_id": str(model_id),
        "predictions": predictions,
        "row_count": len(predictions),
    }


def _serialize_model(model: MLModel, include_narration: bool) -> dict:
    data = {
        "id": str(model.id),
        "name": model.name,
        "target_column": model.target_column,
        "feature_columns": model.feature_columns,
        "task_type": model.task_type.value if model.task_type else None,
        "status": model.status.value,
        "algorithm": model.algorithm,
        "metrics": model.metrics_json,
        "feature_importance": model.feature_importance_json,
        "candidates": model.candidates_json,
        "train_row_count": model.train_row_count,
        "test_row_count": model.test_row_count,
        "error_message": model.error_message,
        "created_at": model.created_at.isoformat(),
    }

    if include_narration and model.status == MLModelStatus.READY:
        summary = {
            "task_type": model.task_type.value,
            "algorithm": model.algorithm,
            "target_column": model.target_column,
            "metrics": model.metrics_json,
            "feature_importance": (model.feature_importance_json or [])[:TOP_FEATURES_FOR_NARRATION],
        }
        try:
            narration = narrate_model_result(summary)
            data["narration"] = narration
            data["ai_narration_used"] = True
        except NarrationError:
            data["narration"] = fallback_model_narration(summary)
            data["ai_narration_used"] = False

    return data
