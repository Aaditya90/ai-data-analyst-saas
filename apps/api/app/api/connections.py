"""
DB connector flow:
  1. POST /workspaces/{id}/connections        - save a connection (password encrypted immediately)
  2. GET  /workspaces/{id}/connections/{cid}/tables         - browse available tables
  3. POST /workspaces/{id}/connections/{cid}/datasets       - snapshot one table into a Dataset

Creating a connection always test-connects first — we don't save
credentials we can't confirm the read-only role can actually reach.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_workspace_role
from app.core.crypto import encrypt_secret
from app.core.database import get_db
from app.models.data_connection import ConnectionType, DataConnection
from app.models.dataset import Dataset, DatasetSourceType
from app.models.dataset_version import DatasetVersion, DatasetVersionStatus
from app.models.workspace_member import WorkspaceMember, WorkspaceRole
from app.services import db_connector

router = APIRouter(prefix="/workspaces/{workspace_id}/connections", tags=["connections"])


class ConnectionCreate(BaseModel):
    name: str
    connection_type: ConnectionType
    host: str
    port: int
    database_name: str
    username: str
    password: str
    ssl_mode: str = "prefer"


class DatasetFromTable(BaseModel):
    table_name: str
    dataset_name: str | None = None


@router.post("", status_code=status.HTTP_201_CREATED)
def create_connection(
    workspace_id: uuid.UUID,
    body: ConnectionCreate,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.ADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    connection = DataConnection(
        workspace_id=workspace_id,
        name=body.name,
        connection_type=body.connection_type,
        host=body.host,
        port=body.port,
        database_name=body.database_name,
        username=body.username,
        encrypted_password=encrypt_secret(body.password),
        ssl_mode=body.ssl_mode,
    )

    try:
        db_connector.test_connection(connection)
    except db_connector.ConnectionError_ as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not connect: {exc}. Use a read-only database role for this.",
        )

    db.add(connection)
    db.commit()
    db.refresh(connection)

    return _serialize_connection(connection)


@router.get("")
def list_connections(
    workspace_id: uuid.UUID,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.VIEWER)),
    db: Session = Depends(get_db),
) -> list[dict]:
    connections = (
        db.query(DataConnection).filter(DataConnection.workspace_id == workspace_id).all()
    )
    return [_serialize_connection(c) for c in connections]


@router.get("/{connection_id}/tables")
def list_tables(
    workspace_id: uuid.UUID,
    connection_id: uuid.UUID,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.VIEWER)),
    db: Session = Depends(get_db),
) -> list[str]:
    connection = _get_connection_or_404(db, workspace_id, connection_id)
    try:
        return db_connector.list_tables(connection)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not list tables: {exc}",
        )


@router.post("/{connection_id}/datasets", status_code=status.HTTP_201_CREATED)
def create_dataset_from_table(
    workspace_id: uuid.UUID,
    connection_id: uuid.UUID,
    body: DatasetFromTable,
    member: WorkspaceMember = Depends(require_workspace_role(WorkspaceRole.EDITOR)),
    db: Session = Depends(get_db),
) -> dict:
    connection = _get_connection_or_404(db, workspace_id, connection_id)

    try:
        schema = db_connector.get_table_schema(connection, body.table_name)
        preview_rows = db_connector.preview_table(connection, body.table_name, limit=20)
        row_count = db_connector.get_row_count(connection, body.table_name)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not read table '{body.table_name}': {exc}",
        )

    # Backfill sample_values into the reflected schema from the preview,
    # so DB-connector datasets and file-upload datasets look the same shape
    # to anything downstream (Phase 5 EDA, Phase 6 dashboard builder).
    for col in schema:
        col["sample_values"] = [
            str(row.get(col["name"])) for row in preview_rows[:5] if row.get(col["name"]) is not None
        ]

    dataset = Dataset(
        workspace_id=workspace_id,
        name=body.dataset_name or body.table_name,
        source_type=DatasetSourceType.DATABASE_CONNECTION,
        connection_id=connection.id,
        created_by_user_id=member.user_id,
    )
    db.add(dataset)
    db.flush()

    version = DatasetVersion(
        dataset_id=dataset.id,
        version_number=1,
        status=DatasetVersionStatus.READY,
        source_table_name=body.table_name,
        row_count=row_count,
        column_count=len(schema),
        schema_json=schema,
    )
    db.add(version)
    db.commit()
    db.refresh(dataset)

    return {"id": str(dataset.id), "name": dataset.name, "row_count": row_count}


def _get_connection_or_404(db: Session, workspace_id: uuid.UUID, connection_id: uuid.UUID) -> DataConnection:
    connection = (
        db.query(DataConnection)
        .filter(DataConnection.id == connection_id, DataConnection.workspace_id == workspace_id)
        .one_or_none()
    )
    if connection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")
    return connection


def _serialize_connection(connection: DataConnection) -> dict:
    # Never return encrypted_password or anything derived from it
    return {
        "id": str(connection.id),
        "name": connection.name,
        "connection_type": connection.connection_type.value,
        "host": connection.host,
        "port": connection.port,
        "database_name": connection.database_name,
        "username": connection.username,
    }
