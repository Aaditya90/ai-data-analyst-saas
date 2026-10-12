"""
Phase 19 (Testing the phase it ships with): enterprise features end to end
through real route handlers — API keys, IP allowlist, audit export — on the
SQLite fixtures from conftest.py.

Needs: pip install -r requirements-dev.txt. NOT executed in the sandbox
this phase was built in (no fastapi/sqlalchemy); the pure logic these
routes call is covered by tests/test_enterprise.py, which was.

Authentication note: the shared `client` fixture overrides
`get_current_user`, which would bypass exactly the code under test for
API-key requests. Tests below use `client` to *create* keys (as the org
owner), then call `use_real_auth()` so subsequent requests go through the
genuine `get_current_user` and its API-key branch.

Run with: pytest tests/integration/test_enterprise_flow.py -m integration
"""

import uuid
from datetime import timedelta

import pytest

from app.models.activity_log import ActivityLog
from app.models.api_key import ApiKey
from app.models.organization import Organization
from app.models.workspace_member import WorkspaceRole
from app.services import enterprise

pytestmark = pytest.mark.integration


def use_real_auth():
    from main import app
    from app.api.deps import get_current_user

    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def enterprise_org(client, db_session, make_organization, make_workspace, add_member):
    """An Enterprise-plan org with one workspace, owned by client.test_user."""
    org = make_organization(name="Big Corp", plan="enterprise")
    workspace = make_workspace(organization=org, name="Big Corp WS")
    add_member(workspace=workspace, user=client.test_user, role=WorkspaceRole.OWNER)
    return org, workspace


def create_key(client, org, **body):
    payload = {"name": "ci", "scope": "read_write", **body}
    response = client.post(f"/organizations/{org.id}/api-keys", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# --- plan gating --------------------------------------------------------------


def test_free_plan_cannot_create_api_keys(client, make_workspace, add_member, make_organization):
    org = make_organization(plan="free")
    add_member(workspace=make_workspace(organization=org), user=client.test_user)

    response = client.post(f"/organizations/{org.id}/api-keys", json={"name": "x", "scope": "read"})

    assert response.status_code == 403
    assert "Enterprise" in response.json()["detail"]


def test_features_endpoint_reflects_plan(client, enterprise_org):
    org, _ = enterprise_org
    body = client.get(f"/organizations/{org.id}/enterprise/features").json()
    assert body["plan"] == "enterprise"
    assert all(body["features"].values())


def test_non_owner_cannot_manage_keys(client, make_organization, make_workspace, add_member):
    org = make_organization(plan="enterprise")
    add_member(workspace=make_workspace(organization=org), user=client.test_user, role=WorkspaceRole.ADMIN)

    response = client.get(f"/organizations/{org.id}/api-keys")

    assert response.status_code == 404


# --- key lifecycle --------------------------------------------------------------


def test_created_key_is_shown_once_and_never_listed_in_full(client, enterprise_org):
    org, _ = enterprise_org
    created = create_key(client, org)

    assert created["api_key"].startswith("aida_")
    listed = client.get(f"/organizations/{org.id}/api-keys").json()
    assert len(listed) == 1
    assert listed[0]["key_prefix"] == created["key_prefix"]
    assert "api_key" not in listed[0]
    assert created["api_key"] not in str(listed)


def test_only_the_hash_is_persisted(client, enterprise_org, db_session):
    org, _ = enterprise_org
    created = create_key(client, org)

    stored = db_session.query(ApiKey).one()
    assert stored.key_hash == enterprise.hash_api_key(created["api_key"])
    assert created["api_key"] not in (stored.key_hash, stored.key_prefix, stored.name)


def test_invalid_scope_rejected(client, enterprise_org):
    org, _ = enterprise_org
    response = client.post(f"/organizations/{org.id}/api-keys", json={"name": "x", "scope": "admin"})
    assert response.status_code == 400


# --- authenticating with a key ---------------------------------------------------


def test_key_can_read_a_workspace_in_its_org(client, enterprise_org):
    org, workspace = enterprise_org
    key = create_key(client, org, scope="read")["api_key"]
    use_real_auth()

    response = client.get(f"/workspaces/{workspace.id}/members", headers={"Authorization": f"Bearer {key}"})

    assert response.status_code == 200


def test_unknown_and_tampered_keys_get_the_same_generic_401(client, enterprise_org):
    org, workspace = enterprise_org
    key = create_key(client, org)["api_key"]
    use_real_auth()
    url = f"/workspaces/{workspace.id}/members"

    tampered = client.get(url, headers={"Authorization": f"Bearer {key}x"})
    unknown = client.get(url, headers={"Authorization": "Bearer aida_deadbeef_" + "z" * 40})
    malformed = client.get(url, headers={"Authorization": "Bearer aida_nope"})

    assert tampered.status_code == unknown.status_code == malformed.status_code == 401
    assert tampered.json() == unknown.json() == malformed.json()


def test_read_scope_key_cannot_write(client, enterprise_org):
    org, workspace = enterprise_org
    key = create_key(client, org, scope="read")["api_key"]
    use_real_auth()

    response = client.post(
        f"/workspaces/{workspace.id}/members",
        json={"email": "x@example.com", "role": "viewer"},
        headers={"Authorization": f"Bearer {key}"},
    )

    assert response.status_code == 403
    assert "read-only" in response.json()["detail"]


def test_key_cannot_reach_non_workspace_endpoints(client, enterprise_org):
    org, _ = enterprise_org
    key = create_key(client, org, scope="read_write")["api_key"]
    use_real_auth()
    headers = {"Authorization": f"Bearer {key}"}

    for path in ("/workspaces", f"/organizations/{org.id}/api-keys", f"/organizations/{org.id}/billing"):
        response = client.get(path, headers=headers)
        assert response.status_code == 403, path
    # ...including minting more keys:
    assert client.post(f"/organizations/{org.id}/api-keys", json={"name": "m", "scope": "read"}, headers=headers).status_code == 403


def test_key_cannot_reach_another_organizations_workspace(
    client, enterprise_org, make_organization, make_workspace, add_member
):
    org, _ = enterprise_org
    key = create_key(client, org)["api_key"]
    # Same user owns a workspace in a DIFFERENT org: the key must still not reach it.
    other_ws = make_workspace(organization=make_organization(plan="enterprise"))
    add_member(workspace=other_ws, user=client.test_user, role=WorkspaceRole.OWNER)
    use_real_auth()

    response = client.get(f"/workspaces/{other_ws.id}/members", headers={"Authorization": f"Bearer {key}"})

    assert response.status_code == 404


def test_revoked_key_stops_working_and_revoke_is_idempotent(client, enterprise_org):
    org, workspace = enterprise_org
    created = create_key(client, org)
    assert client.delete(f"/organizations/{org.id}/api-keys/{created['id']}").status_code == 204
    assert client.delete(f"/organizations/{org.id}/api-keys/{created['id']}").status_code == 204
    use_real_auth()

    response = client.get(f"/workspaces/{workspace.id}/members", headers={"Authorization": f"Bearer {created['api_key']}"})

    assert response.status_code == 401


def test_expired_key_is_rejected(client, enterprise_org, db_session):
    org, workspace = enterprise_org
    created = create_key(client, org)
    stored = db_session.query(ApiKey).one()
    stored.expires_at = enterprise.utcnow() - timedelta(seconds=1)
    db_session.commit()
    use_real_auth()

    response = client.get(f"/workspaces/{workspace.id}/members", headers={"Authorization": f"Bearer {created['api_key']}"})

    assert response.status_code == 401


def test_downgrade_disables_keys_without_deleting_them(client, enterprise_org, db_session):
    org, workspace = enterprise_org
    created = create_key(client, org)
    db_session.get(Organization, org.id).plan = "pro"
    db_session.commit()

    # Still listable/revocable after downgrade...
    assert client.get(f"/organizations/{org.id}/api-keys").status_code == 200
    use_real_auth()
    response = client.get(f"/workspaces/{workspace.id}/members", headers={"Authorization": f"Bearer {created['api_key']}"})
    # ...but no longer usable.
    assert response.status_code == 403


def test_removed_member_loses_key_access(client, enterprise_org, db_session):
    from app.models.workspace_member import WorkspaceMember

    org, workspace = enterprise_org
    created = create_key(client, org)
    db_session.query(WorkspaceMember).filter_by(workspace_id=workspace.id).delete()
    db_session.commit()
    use_real_auth()

    response = client.get(f"/workspaces/{workspace.id}/members", headers={"Authorization": f"Bearer {created['api_key']}"})

    assert response.status_code == 404  # require_workspace_role still runs for key traffic


def test_last_used_is_recorded(client, enterprise_org, db_session):
    org, workspace = enterprise_org
    created = create_key(client, org)
    assert db_session.query(ApiKey).one().last_used_at is None
    use_real_auth()

    client.get(f"/workspaces/{workspace.id}/members", headers={"Authorization": f"Bearer {created['api_key']}"})

    db_session.expire_all()
    assert db_session.query(ApiKey).one().last_used_at is not None


# --- IP allowlist ------------------------------------------------------------------


def test_allowlist_rejects_outside_addresses_and_admits_inside_ones(client, enterprise_org, monkeypatch):
    org, workspace = enterprise_org
    monkeypatch.setattr("app.api.enterprise.get_client_ip", lambda request: "203.0.113.9")
    put = client.put(f"/organizations/{org.id}/security/ip-allowlist", json={"entries": ["203.0.113.0/24"]})
    assert put.status_code == 200 and put.json()["enforced"] is True

    monkeypatch.setattr("app.api.deps.get_client_ip", lambda request: "198.51.100.1")
    assert client.get(f"/workspaces/{workspace.id}/members").status_code == 403

    monkeypatch.setattr("app.api.deps.get_client_ip", lambda request: "203.0.113.77")
    assert client.get(f"/workspaces/{workspace.id}/members").status_code == 200


def test_cannot_set_an_allowlist_that_locks_yourself_out(client, enterprise_org, monkeypatch):
    org, _ = enterprise_org
    monkeypatch.setattr("app.api.enterprise.get_client_ip", lambda request: "203.0.113.9")

    response = client.put(f"/organizations/{org.id}/security/ip-allowlist", json={"entries": ["198.51.100.0/24"]})

    assert response.status_code == 400
    assert "203.0.113.9" in response.json()["detail"]


def test_org_level_routes_stay_reachable_from_a_blocked_address(client, enterprise_org, monkeypatch):
    org, _ = enterprise_org
    monkeypatch.setattr("app.api.enterprise.get_client_ip", lambda request: "203.0.113.9")
    client.put(f"/organizations/{org.id}/security/ip-allowlist", json={"entries": ["203.0.113.0/24"]})

    monkeypatch.setattr("app.api.enterprise.get_client_ip", lambda request: "198.51.100.1")
    monkeypatch.setattr("app.api.deps.get_client_ip", lambda request: "198.51.100.1")
    # The escape hatch: an owner on a blocked network can still clear the list.
    cleared = client.put(f"/organizations/{org.id}/security/ip-allowlist", json={"entries": []})

    assert cleared.status_code == 200 and cleared.json()["entries"] == []


def test_invalid_allowlist_entries_rejected(client, enterprise_org):
    org, _ = enterprise_org
    for bad in (["nope"], ["0.0.0.0/0"]):
        assert client.put(f"/organizations/{org.id}/security/ip-allowlist", json={"entries": bad}).status_code == 400


def test_allowlist_not_enforced_once_plan_lacks_the_feature(client, enterprise_org, db_session, monkeypatch):
    org, workspace = enterprise_org
    monkeypatch.setattr("app.api.enterprise.get_client_ip", lambda request: "203.0.113.9")
    client.put(f"/organizations/{org.id}/security/ip-allowlist", json={"entries": ["203.0.113.0/24"]})
    db_session.get(Organization, org.id).plan = "pro"
    db_session.commit()

    monkeypatch.setattr("app.api.deps.get_client_ip", lambda request: "198.51.100.1")
    assert client.get(f"/workspaces/{workspace.id}/members").status_code == 200
    assert client.get(f"/organizations/{org.id}/security/ip-allowlist").json()["enforced"] is False


# --- audit export ------------------------------------------------------------------


def _log(db, workspace, action, **kw):
    entry = ActivityLog(workspace_id=workspace.id, actor_user_id=kw.pop("actor", None), action=action,
                        metadata_json=kw.pop("metadata", {}), **kw)
    db.add(entry)
    db.commit()
    return entry


def test_export_contains_only_this_organizations_events(
    client, enterprise_org, db_session, make_organization, make_workspace
):
    org, workspace = enterprise_org
    other_ws = make_workspace(organization=make_organization(plan="enterprise"), name="Other Org WS")
    _log(db_session, workspace, "member.invited", actor=client.test_user.id)
    _log(db_session, other_ws, "member.removed")

    response = client.get(f"/organizations/{org.id}/audit-log/export?format=json")

    assert response.status_code == 200
    rows = response.json()
    assert [r["action"] for r in rows] == ["member.invited"]
    assert rows[0]["workspace_name"] == "Big Corp WS"
    assert rows[0]["actor_email"] == client.test_user.email
    assert response.headers["x-export-truncated"] == "false"


def test_csv_export_neutralises_formula_injection(client, enterprise_org, db_session):
    import csv
    import io

    org, workspace = enterprise_org
    workspace.name = '=HYPERLINK("http://evil.example","click")'
    db_session.commit()
    _log(db_session, workspace, "member.invited")

    response = client.get(f"/organizations/{org.id}/audit-log/export")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows[0] == enterprise.EXPORT_COLUMNS
    cell = rows[1][enterprise.EXPORT_COLUMNS.index("workspace_name")]
    assert cell.startswith("'=")  # inert text, not a live formula


def test_export_date_filters(client, enterprise_org, db_session):
    org, workspace = enterprise_org
    _log(db_session, workspace, "comment.created")

    future = client.get(f"/organizations/{org.id}/audit-log/export?format=json&since=2999-01-01T00:00:00Z")
    past = client.get(f"/organizations/{org.id}/audit-log/export?format=json&until=2000-01-01T00:00:00Z")

    assert future.json() == [] and past.json() == []


def test_export_requires_enterprise_plan_and_a_valid_format(client, enterprise_org, make_organization, make_workspace, add_member):
    org, _ = enterprise_org
    assert client.get(f"/organizations/{org.id}/audit-log/export?format=xml").status_code == 422

    free = make_organization(plan="free")
    add_member(workspace=make_workspace(organization=free), user=client.test_user, role=WorkspaceRole.OWNER)
    assert client.get(f"/organizations/{free.id}/audit-log/export").status_code == 403


def test_export_of_org_with_no_events_is_just_a_header(client, enterprise_org):
    org, _ = enterprise_org
    response = client.get(f"/organizations/{org.id}/audit-log/export")
    assert response.text.strip() == ",".join(enterprise.EXPORT_COLUMNS)


def test_unknown_org_is_404_for_everyone(client):
    assert client.get(f"/organizations/{uuid.uuid4()}/api-keys").status_code == 404
