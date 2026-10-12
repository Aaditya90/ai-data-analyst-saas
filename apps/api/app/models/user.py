"""
User = a person, globally unique by email. A user can belong to multiple
workspaces (across different organizations) via WorkspaceMember.

Note: no password field here — Phase 2 (Authentication) delegates identity
to Clerk/Auth0, so this table only stores the profile data we need locally
plus the external auth provider's id for lookup.
"""

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Populated once Phase 2 wires up Clerk/Auth0
    auth_provider_id: Mapped[str | None] = mapped_column(
        String(255), unique=True, nullable=True, index=True
    )

    memberships: Mapped[list["WorkspaceMember"]] = relationship(  # noqa: F821
        back_populates="user",
        cascade="all, delete-orphan",
    )
