"""
A DataConnection is a saved, workspace-scoped connection to an external
database (Postgres or MySQL for this phase; Snowflake/BigQuery are noted as
future connector types but not implemented here).

Credentials are encrypted at rest with Fernet (see app/core/crypto.py) using
a key from settings — never stored or logged in plaintext. The connection
this app makes should always use read-only credentials on the customer's
side; we don't enforce that at the DB level (we can't), but the UI copy and
README should keep telling users to create a read-only role for this.
"""

import enum
import uuid

from sqlalchemy import Enum, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class ConnectionType(str, enum.Enum):
    POSTGRES = "postgres"
    MYSQL = "mysql"


class DataConnection(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "data_connections"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    connection_type: Mapped[ConnectionType] = mapped_column(
        Enum(ConnectionType, name="connection_type"), nullable=False
    )

    host: Mapped[str] = mapped_column(String(512), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    database_name: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False)

    # Fernet-encrypted ciphertext, never plaintext — see app/core/crypto.py
    encrypted_password: Mapped[str] = mapped_column(String(1024), nullable=False)

    ssl_mode: Mapped[str] = mapped_column(String(50), default="prefer", nullable=False)
