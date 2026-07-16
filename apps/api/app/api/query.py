"""
POST /workspaces/{id}/datasets/{did}/query

Pipeline: question + schema -> Claude generates SQL -> sql_guard validates
-> query_executor runs it against an ephemeral in-memory SQLite loaded
from the dataset -> results returned alongside the SQL itself (explainability:
the user always sees exactly what ran, never just a black-box answer — see
Phase 1's roadmap note on this).

Caching: keyed on (version_id, question) since DatasetVersions are
immutable (same reasoning as Phase 5's EDA cache) — an identical question
against the same version always gets the same SQL and results, so a repeat
question costs zero LLM tokens.
"""

import hashlib
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_workspace_role
from app.core.cache import get_json, set_json
from app.core.config import get_settings
from app.core.database import get_db
from app.core.storage import download_to_buffer
from app.models.dataset import Dataset, DatasetSourceType
from app.models.workspace_member import WorkspaceMember, WorkspaceRole
from app.services.nl_to_sql import NLToSQLError, generate_sql
from app.services.query_executor import QueryExecutionError, run_query
from app.services.schema_inference import read_tabular_file
from app.services.sql_guard import UnsafeQueryError, validate_and_limit

router = APIRouter(prefix="/workspaces/{workspace_id}/datasets/{dataset_id}", tags=["ai-query"])
settings = get_settings()

CACHE_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days, same rationale as Phase 5's EDA cache


class QueryRequest(BaseModel):
    question: str


@router.post("/query")
def ask_question(
    workspace_id: uuid.UUID,
    dataset_id: uuid.UUID,
    body: QueryRequest,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.VIEWER)),
    db: Session = Depends(get_db),
) -> dict:
    if not body.question.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Question is required")

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
            detail="AI queries are only available for file-upload datasets in this phase",
        )
    if not version.schema_json:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Dataset has no schema")

    question_key = hashlib.sha256(body.question.strip().lower().encode()).hexdigest()[:16]
    cache_key = f"query:{version.id}:{question_key}"
    cached = get_json(cache_key)
    if cached is not None:
        return {**cached, "cached": True}

    try:
        generated = generate_sql(body.question, version.schema_json)
    except NLToSQLError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    if not generated.get("sql"):
        return {
            "question": body.question,
            "sql": None,
            "explanation": generated.get("explanation", "Could not answer this question."),
            "columns": [],
            "rows": [],
            "row_count": 0,
            "cached": False,
        }

    try:
        safe_sql = validate_and_limit(generated["sql"], row_limit=settings.ai_query_row_limit)
    except UnsafeQueryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Generated query failed safety validation: {exc}",
        )

    buffer = download_to_buffer(version.storage_key)
    df = read_tabular_file(buffer, version.original_filename or "file.csv")

    try:
        result = run_query(df, safe_sql)
    except QueryExecutionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Query failed to execute: {exc}",
        )

    response = {
        "question": body.question,
        "sql": safe_sql,
        "explanation": generated.get("explanation", ""),
        **result,
    }
    set_json(cache_key, response, ttl_seconds=CACHE_TTL_SECONDS)
    return {**response, "cached": False}
