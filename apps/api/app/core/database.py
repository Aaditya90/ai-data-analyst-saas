"""
SQLAlchemy engine + session management.

FastAPI routes depend on `get_db` to receive a request-scoped session.

Since Phase 15, this engine connects with `settings.effective_app_database_url`
(the low-privilege `app_runtime` role once configured — see migration 0010
and config.py's `app_db_url`), not the migration/admin URL, so Row-Level
Security policies actually apply to the app's own queries instead of being
bypassed by an owner/superuser connection.
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.effective_app_database_url,
    pool_pre_ping=True,   # avoids stale-connection errors after idle periods
    pool_size=10,
    max_overflow=20,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """Base class every ORM model inherits from."""
    pass


def get_db() -> Generator:
    """FastAPI dependency — yields a DB session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
