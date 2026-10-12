"""
Phase 17 (Testing) integration tests: workspace isolation, exercised
through real route handlers (`app.api.deps.require_workspace_role`)
against the SQLite `client`/`db_session` fixtures in conftest.py.

This is the application-layer half of the isolation guarantee Phase 15
made database-enforced (Postgres RLS) as well — see conftest.py's module
docstring for exactly what these SQLite-backed tests can and can't prove,
and tests/integration/test_rls_postgres.py for the Postgres-only other
half.

Needs: pip install -r requirements-dev.txt (fastapi, sqlalchemy, ...).
Not runnable in an environment without those installed — see
tests/test_testing_infra.py for what *is* runnable there.

Run with: pytest tests/integration/test_workspace_isolation.py -m integration
"""

import pytest

from app.models.workspace_member import WorkspaceRole

pytestmark = pytest.mark.integration


def test_user_can_list_members_of_their_own_workspace(client, make_workspace, add_member):
    workspace = make_workspace(name="My Workspace")
    add_member(workspace=workspace, user=client.test_user, role=WorkspaceRole.OWNER)

    response = client.get(f"/workspaces/{workspace.id}/members")

    assert response.status_code == 200
    emails = [m["email"] for m in response.json()]
    assert client.test_user.email in emails


def test_user_cannot_list_members_of_a_workspace_they_do_not_belong_to(client, make_workspace, make_user, add_member):
    # A workspace owned entirely by someone else.
    other_owner = make_user()
    other_workspace = make_workspace(name="Someone Else's Workspace")
    add_member(workspace=other_workspace, user=other_owner, role=WorkspaceRole.OWNER)

    response = client.get(f"/workspaces/{other_workspace.id}/members")

    # 404, not 403 — require_workspace_role deliberately doesn't reveal
    # whether the workspace exists to a non-member (see its docstring).
    assert response.status_code == 404


def test_list_my_workspaces_only_returns_workspaces_the_user_is_a_member_of(
    client, make_workspace, make_user, add_member
):
    mine = make_workspace(name="Mine")
    add_member(workspace=mine, user=client.test_user, role=WorkspaceRole.EDITOR)

    other_owner = make_user()
    not_mine = make_workspace(name="Not Mine")
    add_member(workspace=not_mine, user=other_owner, role=WorkspaceRole.OWNER)

    response = client.get("/workspaces")

    assert response.status_code == 200
    names = {w["name"] for w in response.json()}
    assert names == {"Mine"}


def test_viewer_role_is_rejected_from_a_role_gated_action(client, make_workspace, add_member):
    # add_member (the route) requires ADMIN or higher; a VIEWER hitting it
    # should be forbidden, not merely "not found" — they ARE a member,
    # they just don't have enough role, so this is the 403 branch of
    # require_workspace_role rather than the 404 branch exercised above.
    workspace = make_workspace(name="Viewer Test")
    add_member(workspace=workspace, user=client.test_user, role=WorkspaceRole.VIEWER)

    response = client.post(
        f"/workspaces/{workspace.id}/members",
        json={"email": "someone-new@example.com", "role": "viewer"},
    )

    assert response.status_code == 403


def test_editor_role_is_sufficient_to_view_but_not_manage_members(client, make_workspace, add_member):
    workspace = make_workspace(name="Editor Test")
    add_member(workspace=workspace, user=client.test_user, role=WorkspaceRole.EDITOR)

    read_response = client.get(f"/workspaces/{workspace.id}/members")
    assert read_response.status_code == 200

    write_response = client.post(
        f"/workspaces/{workspace.id}/members",
        json={"email": "someone-new@example.com", "role": "viewer"},
    )
    assert write_response.status_code == 403


def test_nonexistent_workspace_returns_404_not_500(client):
    import uuid

    response = client.get(f"/workspaces/{uuid.uuid4()}/members")
    assert response.status_code == 404
