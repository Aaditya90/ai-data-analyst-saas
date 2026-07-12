"""
Import every model here so Alembic's `target_metadata` (which points at
Base.metadata) sees all tables during autogenerate. Every future phase that
adds a model MUST import it in this file.
"""

from app.models.organization import Organization
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_member import WorkspaceMember, WorkspaceRole

__all__ = [
    "Organization",
    "User",
    "Workspace",
    "WorkspaceMember",
    "WorkspaceRole",
]
