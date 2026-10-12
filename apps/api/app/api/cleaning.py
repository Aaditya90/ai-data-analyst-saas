"""
Cleaning flow, deliberately human-in-the-loop at every step:

  1. GET  /workspaces/{id}/datasets/{did}/versions/{vid}/issues
     -> profiles the version's data, returns issues + suggested operations.
     Nothing is applied yet.

  2. POST /workspaces/{id}/datasets/{did}/versions/{vid}/clean
     -> caller sends back the *exact* list of operations they want applied
     (typically the suggestions from step 1, possibly edited or a subset).
     This creates a NEW DatasetVersion (parent = vid) rather than mutating
     the original — see DatasetVersion's docstring for why.

  3. GET  /workspaces/{id}/datasets/{did}/lineage
     -> walks the parent_version_id chain and returns the full raw ->
     cleaned graph for a dataset.

Only FILE_UPLOAD datasets can be cleaned in this phase — cleaning a
DATABASE_CONNECTION dataset would mean writing to the customer's database,
which is out of scope (and dangerous) for an ingestion tool. Connector
datasets can still be viewed/profiled once dashboards exist, just not
cleaned here.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_workspace_role
from app.core.database import get_db
from app.core.storage import build_storage_key, download_to_buffer, upload_bytes
from app.models.dataset import Dataset, DatasetSourceType
from app.models.dataset_version import DatasetVersion, DatasetVersionStatus
from app.models.workspace_member import WorkspaceMember, WorkspaceRole
from app.services.data_cleaning import apply_operations, detect_issues
from app.services.schema_inference import infer_schema, read_tabular_file

router = APIRouter(prefix="/workspaces/{workspace_id}/datasets/{dataset_id}", tags=["cleaning"])


class CleaningOperation(BaseModel):
    op_type: str
    column: str | None = None
    params: dict = {}


class CleaningRequest(BaseModel):
    operations: list[CleaningOperation]
    new_dataset_name: str | None = None


@router.get("/versions/{version_id}/issues")
def get_issues(
    workspace_id: uuid.UUID,
    dataset_id: uuid.UUID,
    version_id: uuid.UUID,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.VIEWER)),
    db: Session = Depends(get_db),
) -> dict:
    version, dataset = _get_version_or_404(db, workspace_id, dataset_id, version_id)
    df = _load_version_dataframe(dataset, version)

    issues = detect_issues(df)
    return {
        "version_id": str(version.id),
        "row_count": len(df),
        "issues": issues,
        "issue_count": len(issues),
    }


@router.post("/versions/{version_id}/clean", status_code=status.HTTP_201_CREATED)
def clean_version(
    workspace_id: uuid.UUID,
    dataset_id: uuid.UUID,
    version_id: uuid.UUID,
    body: CleaningRequest,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.EDITOR)),
    db: Session = Depends(get_db),
) -> dict:
    if not body.operations:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one operation is required — this endpoint never cleans automatically",
        )

    version, dataset = _get_version_or_404(db, workspace_id, dataset_id, version_id)
    df = _load_version_dataframe(dataset, version)

    try:
        cleaned_df, log = apply_operations(df, [op.model_dump() for op in body.operations])
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    if cleaned_df.empty:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="These operations would remove every row — adjust and try again",
        )

    # New dataset row so cleaned output can carry its own name/history,
    # while transformations_applied + parent_version_id still link it back
    # to the raw source for lineage purposes.
    new_dataset = Dataset(
        workspace_id=workspace_id,
        name=body.new_dataset_name or f"{dataset.name} (cleaned)",
        source_type=DatasetSourceType.FILE_UPLOAD,
        created_by_user_id=member.user_id,
    )
    db.add(new_dataset)
    db.flush()

    csv_bytes = cleaned_df.to_csv(index=False).encode("utf-8")
    filename = f"{new_dataset.name}.csv"
    storage_key = build_storage_key(workspace_id, new_dataset.id, filename)
    upload_bytes(storage_key, csv_bytes)

    new_version = DatasetVersion(
        dataset_id=new_dataset.id,
        version_number=1,
        status=DatasetVersionStatus.READY,
        storage_key=storage_key,
        original_filename=filename,
        row_count=len(cleaned_df),
        column_count=len(cleaned_df.columns),
        schema_json=infer_schema(cleaned_df),
        parent_version_id=version.id,
        transformations_applied=log,
    )
    db.add(new_version)
    db.commit()
    db.refresh(new_dataset)
    db.refresh(new_version)

    return {
        "dataset_id": str(new_dataset.id),
        "version_id": str(new_version.id),
        "row_count": new_version.row_count,
        "transformations_applied": log,
    }


@router.get("/lineage")
def get_lineage(
    workspace_id: uuid.UUID,
    dataset_id: uuid.UUID,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.VIEWER)),
    db: Session = Depends(get_db),
) -> dict:
    """
    Returns the lineage chain for a dataset's current (latest) version,
    walking parent_version_id back to the root ingestion. Each dataset has
    its own version chain within itself; cleaning creates a *new* dataset
    whose first version points back at the source dataset's version — so
    to see the full raw -> cleaned graph, follow `parent_version_id` across
    dataset boundaries too, which this endpoint does.
    """
    dataset = (
        db.query(Dataset)
        .filter(Dataset.id == dataset_id, Dataset.workspace_id == workspace_id)
        .one_or_none()
    )
    if dataset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset not found")
    if not dataset.versions:
        return {"nodes": []}

    nodes = []
    current: DatasetVersion | None = dataset.versions[0]
    visited = set()
    while current is not None and current.id not in visited:
        visited.add(current.id)
        nodes.append(
            {
                "version_id": str(current.id),
                "dataset_id": str(current.dataset_id),
                "dataset_name": current.dataset.name,
                "version_number": current.version_number,
                "row_count": current.row_count,
                "transformations_applied": current.transformations_applied,
                "created_at": current.created_at.isoformat(),
            }
        )
        current = (
            db.get(DatasetVersion, current.parent_version_id)
            if current.parent_version_id
            else None
        )

    nodes.reverse()  # oldest (root) first
    return {"nodes": nodes}


def _get_version_or_404(
    db: Session, workspace_id: uuid.UUID, dataset_id: uuid.UUID, version_id: uuid.UUID
) -> tuple[DatasetVersion, Dataset]:
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

    return version, dataset


def _load_version_dataframe(dataset: Dataset, version: DatasetVersion):
    if dataset.source_type != DatasetSourceType.FILE_UPLOAD or not version.storage_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cleaning is only available for file-upload datasets in this phase",
        )
    buffer = download_to_buffer(version.storage_key)
    return read_tabular_file(buffer, version.original_filename or "file.csv")
