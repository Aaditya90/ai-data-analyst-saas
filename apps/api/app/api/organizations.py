"""
Organizations sit above workspaces (see README hierarchy diagram) and are
the billing entity from Phase 14 onward. For now, any authenticated user can
create one — it becomes the container they'll create workspaces under.
There's no "organization membership" table by design: access is granted at
the workspace level (WorkspaceMember), matching the isolation model from
Phase 1.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.organization import Organization
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_member import WorkspaceMember

router = APIRouter(prefix="/organizations", tags=["organizations"])


class OrganizationCreate(BaseModel):
    name: str
    slug: str


@router.post("", status_code=status.HTTP_201_CREATED)
def create_organization(
    body: OrganizationCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    existing = db.query(Organization).filter(Organization.slug == body.slug).one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An organization with that slug already exists",
        )

    org = Organization(name=body.name, slug=body.slug)
    db.add(org)
    db.commit()
    db.refresh(org)

    return {"id": str(org.id), "name": org.name, "slug": org.slug, "plan": org.plan}


@router.get("")
def list_my_organizations(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    orgs = (
        db.query(Organization)
        .join(Workspace, Workspace.organization_id == Organization.id)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .filter(WorkspaceMember.user_id == current_user.id)
        .distinct()
        .all()
    )
    return [{"id": str(o.id), "name": o.name, "slug": o.slug} for o in orgs]
