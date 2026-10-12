"""
Handles talking to a *customer's* external database (not our own app DB).
Deliberately generic across Postgres/MySQL using SQLAlchemy's engine +
inspector, rather than writing a bespoke client per database — adding a
third SQL-based connector later should mostly mean adding a driver package
and one branch in `_build_connection_url`.

Every query here is read-only by construction: we only ever call
`inspect()` for metadata and `SELECT ... LIMIT n` for previews. We never
execute connection-supplied SQL as-is (that's Phase 7's problem, with much
stronger guardrails — this module doesn't accept arbitrary queries at all).
"""

from contextlib import contextmanager

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from app.core.crypto import decrypt_secret
from app.models.data_connection import ConnectionType, DataConnection

_DRIVER_BY_TYPE = {
    ConnectionType.POSTGRES: "postgresql+psycopg",
    ConnectionType.MYSQL: "mysql+pymysql",
}


class ConnectionError_(RuntimeError):
    """Wraps driver-specific connection errors in one type the API layer
    can catch without importing psycopg/pymysql exception classes."""


def _build_connection_url(conn: DataConnection) -> str:
    driver = _DRIVER_BY_TYPE.get(conn.connection_type)
    if driver is None:
        raise ValueError(f"Unsupported connection type: {conn.connection_type}")

    password = decrypt_secret(conn.encrypted_password)
    return (
        f"{driver}://{conn.username}:{password}@"
        f"{conn.host}:{conn.port}/{conn.database_name}"
    )


@contextmanager
def _engine_for(conn: DataConnection) -> Engine:
    engine = create_engine(_build_connection_url(conn), pool_pre_ping=True)
    try:
        yield engine
    finally:
        engine.dispose()


def test_connection(conn: DataConnection) -> None:
    """Raises ConnectionError_ on failure; returns None on success."""
    try:
        with _engine_for(conn) as engine, engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        raise ConnectionError_(str(exc)) from exc


def list_tables(conn: DataConnection) -> list[str]:
    with _engine_for(conn) as engine:
        inspector = inspect(engine)
        return sorted(inspector.get_table_names())


def get_table_schema(conn: DataConnection, table_name: str) -> list[dict]:
    with _engine_for(conn) as engine:
        inspector = inspect(engine)
        columns = inspector.get_columns(table_name)
        return [
            {
                "name": col["name"],
                "inferred_type": str(col["type"]).lower(),
                "nullable": bool(col.get("nullable", True)),
                "sample_values": [],  # populated separately via preview_table
            }
            for col in columns
        ]


def preview_table(conn: DataConnection, table_name: str, limit: int = 50) -> list[dict]:
    with _engine_for(conn) as engine, engine.connect() as connection:
        # table_name comes from inspector.get_table_names(), not user free
        # text, but we still quote it defensively rather than trust it raw
        quoted = engine.dialect.identifier_preparer.quote(table_name)
        result = connection.execute(text(f"SELECT * FROM {quoted} LIMIT :limit"), {"limit": limit})
        return [dict(row._mapping) for row in result]


def get_row_count(conn: DataConnection, table_name: str) -> int:
    with _engine_for(conn) as engine, engine.connect() as connection:
        quoted = engine.dialect.identifier_preparer.quote(table_name)
        result = connection.execute(text(f"SELECT COUNT(*) FROM {quoted}"))
        return result.scalar_one()
