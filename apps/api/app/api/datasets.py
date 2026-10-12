"""
File-upload ingestion pipeline:
  POST /workspaces/{id}/datasets/upload
    -> validate size/extension
    -> parse with pandas (in-memory; not yet stored)
    -> infer schema
    -> upload raw bytes to object storage
    -> write Dataset + DatasetVersion(status=READY) rows

Kept synchronous for this phase (upload and parse happen in the same
request) since files are capped at a modest size. Once large files or slow
external connectors are common, this moves to a background job (Celery,
introduced properly in Phase 10) — the DatasetVersion.status field already
supports PENDING/PROCESSING so that migration won't need a schema change.
"""

import io
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import require_workspace_role
from app.core.config import get_settings
from app.core.database import get_db
from app.core.storage import build_storage_key, download_to_buffer, upload_bytes
from app.models.dataset import Dataset, DatasetSourceType
from app.models.dataset_version import DatasetVersion, DatasetVersionStatus
from app.models.workspace_member import WorkspaceMember, WorkspaceRole
from app.services.schema_inference import infer_schema, read_tabular_file

router = APIRouter(prefix="/workspaces/{workspace_id}/datasets", tags=["datasets"])
settings = get_settings()

ALLOWED_EXTENSIONS = (".csv", ".tsv", ".xlsx", ".xls", ".json")


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_dataset(
    workspace_id: uuid.UUID,
    file: UploadFile = File(...),
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.EDITOR)),
    db: Session = Depends(get_db),
) -> dict:
    if not file.filename or not file.filename.lower().endswith(ALLOWED_EXTENSIONS):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    raw_bytes = await file.read()
    size_mb = len(raw_bytes) / (1024 * 1024)
    if size_mb > settings.max_upload_size_mb:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the {settings.max_upload_size_mb}MB limit",
        )
    if len(raw_bytes) == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File is empty")

    # Parse before we commit anything — a garbage file should fail loudly,
    # not create a dataset row pointing at unparseable data.
    try:
        df = read_tabular_file(io.BytesIO(raw_bytes), file.filename)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    if df.empty:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File parsed successfully but contains no rows",
        )

    dataset = Dataset(
        workspace_id=workspace_id,
        name=file.filename.rsplit(".", 1)[0],
        source_type=DatasetSourceType.FILE_UPLOAD,
        created_by_user_id=member.user_id,
    )
    db.add(dataset)
    db.flush()

    storage_key = build_storage_key(workspace_id, dataset.id, file.filename)
    upload_bytes(storage_key, raw_bytes)

    version = DatasetVersion(
        dataset_id=dataset.id,
        version_number=1,
        status=DatasetVersionStatus.READY,
        storage_key=storage_key,
        original_filename=file.filename,
        row_count=len(df),
        column_count=len(df.columns),
        schema_json=infer_schema(df),
    )
    db.add(version)
    db.commit()
    db.refresh(dataset)
    db.refresh(version)

    return _serialize_dataset(dataset, version)


@router.get("")
def list_datasets(
    workspace_id: uuid.UUID,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.VIEWER)),
    db: Session = Depends(get_db),
) -> list[dict]:
    datasets = (
        db.query(Dataset)
        .filter(Dataset.workspace_id == workspace_id)
        .order_by(Dataset.created_at.desc())
        .all()
    )
    return [_serialize_dataset(d, d.versions[0] if d.versions else None) for d in datasets]


@router.get("/{dataset_id}")
def get_dataset(
    workspace_id: uuid.UUID,
    dataset_id: uuid.UUID,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.VIEWER)),
    db: Session = Depends(get_db),
) -> dict:
    dataset = _get_dataset_or_404(db, workspace_id, dataset_id)
    latest = dataset.versions[0] if dataset.versions else None
    return _serialize_dataset(dataset, latest, include_schema=True)


@router.get("/{dataset_id}/preview")
def preview_dataset(
    workspace_id: uuid.UUID,
    dataset_id: uuid.UUID,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.VIEWER)),
    db: Session = Depends(get_db),
) -> dict:
    dataset = _get_dataset_or_404(db, workspace_id, dataset_id)
    if not dataset.versions:
        raise HTTPException(status_code=404, detail="Dataset has no versions yet")
    version = dataset.versions[0]

    if dataset.source_type != DatasetSourceType.FILE_UPLOAD or not version.storage_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Preview is only available for file-upload datasets in this phase",
        )

    buffer = download_to_buffer(version.storage_key)
    df = read_tabular_file(buffer, version.original_filename or "file.csv")
    limited = df.head(settings.dataset_preview_row_limit)

    return {
        "columns": list(map(str, limited.columns)),
        "rows": limited.astype(str).values.tolist(),
        "total_rows": version.row_count,
        "showing": len(limited),
    }


def _get_dataset_or_404(db: Session, workspace_id: uuid.UUID, dataset_id: uuid.UUID) -> Dataset:
    dataset = (
        db.query(Dataset)
        .filter(Dataset.id == dataset_id, Dataset.workspace_id == workspace_id)
        .one_or_none()
    )
    if dataset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset not found")
    return dataset


def _serialize_dataset(dataset: Dataset, version: DatasetVersion | None, include_schema: bool = False) -> dict:
    result = {
        "id": str(dataset.id),
        "name": dataset.name,
        "source_type": dataset.source_type.value,
        "created_at": dataset.created_at.isoformat(),
        "latest_version": None,
    }
    if version is not None:
        result["latest_version"] = {
            "id": str(version.id),
            "version_number": version.version_number,
            "status": version.status.value,
            "row_count": version.row_count,
            "column_count": version.column_count,
            "original_filename": version.original_filename,
            "source_table_name": version.source_table_name,
            "error_message": version.error_message,
        }
        if include_schema:
            result["latest_version"]["schema"] = version.schema_json
    return result
