"""
Unit tests for the parts of Phase 2 that don't require a live Clerk account:
the workspace role hierarchy used by `require_workspace_role`.

Full integration testing (real JWT verification, webhook signature checks)
needs actual Clerk credentials — see README.md "Testing Phase 2" section for
the manual steps. A proper automated test suite with mocked JWKS arrives in
Phase 17 (Testing).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.deps import _ROLE_RANK
from app.models.workspace_member import WorkspaceRole


def test_role_rank_ordering():
    assert _ROLE_RANK[WorkspaceRole.VIEWER] < _ROLE_RANK[WorkspaceRole.EDITOR]
    assert _ROLE_RANK[WorkspaceRole.EDITOR] < _ROLE_RANK[WorkspaceRole.ADMIN]
    assert _ROLE_RANK[WorkspaceRole.ADMIN] < _ROLE_RANK[WorkspaceRole.OWNER]


def test_all_roles_ranked():
    for role in WorkspaceRole:
        assert role in _ROLE_RANK


if __name__ == "__main__":
    test_role_rank_ordering()
    test_all_roles_ranked()
    print("All Phase 2 unit tests passed.")
