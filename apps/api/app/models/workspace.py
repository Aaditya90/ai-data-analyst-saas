"""
Workspace = the isolation boundary for data, dashboards, and teams.
Every data-owning table in every future phase (datasets, dashboards,
reports, models, etc.) will carry a workspace_id foreign key and MUST
filter by it. This is the hard multi-tenancy line — enforced at the
application layer (query filters) and, from this phase onward, scaffolded
at the database layer via Postgres Row-Level Security (see the Alembic
migration for the RLS policy).
"""

import uuid

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Workspace(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "workspaces"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    organization: Mapped["Organization"] = relationship(  # noqa: F821
        back_populates="workspaces"
    )
    members: Mapped[list["WorkspaceMember"]] = relationship(  # noqa: F821
        back_populates="workspace",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        # slug must be unique within an organization, not globally
        # (two different orgs can both have a "marketing" workspace)
        UniqueConstraint("organization_id", "slug", name="uq_workspace_org_slug"),
    )
