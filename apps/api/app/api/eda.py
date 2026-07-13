"""
One endpoint: GET .../versions/{version_id}/eda — returns column summaries,
correlations, and chart suggestions together, cached in Redis.

Caching key is just the version_id (no TTL needed in principle, since
DatasetVersions are immutable — but a TTL is set anyway as a safety net in
case that invariant is ever violated by a bug, rather than caching forever
on trust). A short-lived cache miss just means one recomputation, so this
errs toward correctness over aggressive caching.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_workspace_role
from app.core.cache import get_json, set_json
from app.core.database import get_db
from app.models.dataset import Dataset, DatasetSourceType
from app.models.dataset_version import DatasetVersion
from app.models.workspace_member import WorkspaceMember, WorkspaceRole
from app.services.eda import compute_column_summaries, compute_correlations, suggest_charts
from app.services.schema_inference import read_tabular_file
from app.core.storage import download_to_buffer

router = APIRouter(
    prefix="/workspaces/{workspace_id}/datasets/{dataset_id}/versions/{version_id}",
    tags=["eda"],
)

CACHE_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days — see module docstring on why this is a safety net, not a real staleness window


@router.get("/eda")
def get_eda_profile(
    workspace_id: uuid.UUID,
    dataset_id: uuid.UUID,
    version_id: uuid.UUID,
    refresh: bool = False,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.VIEWER)),
    db: Session = Depends(get_db),
) -> dict:
    cache_key = f"eda:{version_id}"
    if not refresh:
        cached = get_json(cache_key)
        if cached is not None:
            return {**cached, "cached": True}

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
            detail="EDA is only available for file-upload datasets in this phase",
        )

    buffer = download_to_buffer(version.storage_key)
    df = read_tabular_file(buffer, version.original_filename or "file.csv")

    summaries = compute_column_summaries(df)
    profile = {
        "version_id": str(version_id),
        "row_count": len(df),
        "column_count": len(df.columns),
        "columns": summaries,
        "correlations": compute_correlations(df),
        "chart_suggestions": suggest_charts(summaries),
    }

    set_json(cache_key, profile, ttl_seconds=CACHE_TTL_SECONDS)
    return {**profile, "cached": False}
