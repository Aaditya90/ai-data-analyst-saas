"""
GET .../versions/{version_id}/insights

Pipeline: detect_insights() (pure statistics, ranked) -> narrate_insights()
(Claude phrases the pre-computed numbers) -> falls back to
fallback_narration() per-insight if the AI call fails entirely, so a
Claude outage degrades this endpoint's readability, not its availability.

Cached by version_id like Phase 5's EDA and Phase 7's query cache — same
"DatasetVersions are immutable" reasoning applies.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_workspace_role
from app.core.cache import get_json, set_json
from app.core.database import get_db
from app.core.storage import download_to_buffer
from app.models.dataset import Dataset, DatasetSourceType
from app.models.dataset_version import DatasetVersion
from app.models.workspace_member import WorkspaceMember, WorkspaceRole
from app.services.ai_narration import NarrationError, fallback_narration, narrate_insights
from app.services.insights import detect_insights
from app.services.schema_inference import read_tabular_file

router = APIRouter(
    prefix="/workspaces/{workspace_id}/datasets/{dataset_id}/versions/{version_id}",
    tags=["insights"],
)

CACHE_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days — same rationale as Phase 5/7's caches


@router.get("/insights")
def get_insights(
    workspace_id: uuid.UUID,
    dataset_id: uuid.UUID,
    version_id: uuid.UUID,
    refresh: bool = False,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.VIEWER)),
    db: Session = Depends(get_db),
) -> dict:
    cache_key = f"insights:{version_id}"
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
            detail="Insights are only available for file-upload datasets in this phase",
        )

    buffer = download_to_buffer(version.storage_key)
    df = read_tabular_file(buffer, version.original_filename or "file.csv")

    insights = detect_insights(df)
    if not insights:
        result = {"version_id": str(version_id), "insights": [], "ai_narration_used": False}
        set_json(cache_key, result, ttl_seconds=CACHE_TTL_SECONDS)
        return {**result, "cached": False}

    ai_narration_used = True
    try:
        narrations = narrate_insights(insights)
    except NarrationError:
        ai_narration_used = False
        narrations = [fallback_narration(i) for i in insights]

    enriched = [
        {**insight, "narration": narration}
        for insight, narration in zip(insights, narrations)
    ]

    result = {
        "version_id": str(version_id),
        "insights": enriched,
        "ai_narration_used": ai_narration_used,
    }
    set_json(cache_key, result, ttl_seconds=CACHE_TTL_SECONDS)
    return {**result, "cached": False}
