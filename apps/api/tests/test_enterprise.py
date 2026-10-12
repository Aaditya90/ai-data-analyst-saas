"""
Standalone tests for Phase 19 (Enterprise Features).

app/services/enterprise.py is standard-library only, so everything that is
a *decision* (is this key usable, may this scope make this request, is this
IP allowed, is this CSV cell safe) runs here with a bare interpreter. The
DB/request glue (deps.py, api/enterprise.py) can't run without fastapi and
sqlalchemy, so it gets static consistency checks instead — model vs.
migration columns, the migration revision chain, that the enforcement
hooks exist — plus tests/integration/test_enterprise_flow.py for real
execution where the dependencies are installed.

Run with: python tests/test_enterprise.py   (or via pytest)
"""

import ast
import csv
import io
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR))

from app.services import enterprise as ent  # noqa: E402

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


# --- feature gating --------------------------------------------------------


def test_only_enterprise_plan_has_enterprise_features():
    for feature in (ent.FEATURE_API_KEYS, ent.FEATURE_IP_ALLOWLIST, ent.FEATURE_AUDIT_EXPORT):
        assert ent.plan_has_feature("enterprise", feature)
        assert not ent.plan_has_feature("pro", feature)
        assert not ent.plan_has_feature("free", feature)


def test_unknown_plan_gets_no_features():
    assert not ent.plan_has_feature("platinum-legacy", ent.FEATURE_API_KEYS)
    assert not any(ent.features_for_plan("").values())


def test_features_for_plan_lists_every_feature():
    assert ent.features_for_plan("enterprise") == {
        "api_keys": True, "audit_export": True, "ip_allowlist": True,
    }
    assert set(ent.features_for_plan("free")) == {"api_keys", "audit_export", "ip_allowlist"}


def test_every_plan_in_plan_limits_has_a_feature_matrix_entry():
    # Guards drift: add a plan to plan_limits.py without deciding its
    # enterprise features and this fails.
    tree = ast.parse((API_DIR / "app/services/plan_limits.py").read_text(encoding="utf-8"))
    plans = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "PLAN_LIMITS":
            plans = {k.value for k in node.value.keys}
    assert plans and plans == set(ent.FEATURE_MATRIX)


# --- key generation / verification ------------------------------------------


def test_generated_key_has_documented_format():
    key, prefix, key_hash = ent.generate_api_key()
    assert key.startswith("aida_" + prefix + "_")
    assert len(prefix) == 8 and all(c in "0123456789abcdef" for c in prefix)
    assert len(key_hash) == 64
    assert ent.looks_like_api_key(key)


def test_key_round_trip_verifies():
    key, prefix, key_hash = ent.generate_api_key()
    assert ent.parse_api_key_prefix(key) == prefix
    assert ent.verify_api_key(key, key_hash)


def test_wrong_key_does_not_verify():
    key, _, key_hash = ent.generate_api_key()
    assert not ent.verify_api_key(key + "x", key_hash)
    other, _, _ = ent.generate_api_key()
    assert not ent.verify_api_key(other, key_hash)


def test_keys_are_unique_and_secret_is_not_stored_in_hash_input_form():
    keys = {ent.generate_api_key()[0] for _ in range(200)}
    assert len(keys) == 200
    key, _, key_hash = ent.generate_api_key()
    assert key not in key_hash and key.split("_", 2)[2] not in key_hash


def test_secret_containing_underscores_still_parses():
    # token_urlsafe can emit '_' and '-'; splitting must cap at 3 parts.
    for _ in range(300):
        key, prefix, _h = ent.generate_api_key()
        assert ent.parse_api_key_prefix(key) == prefix


def test_malformed_keys_are_rejected():
    bad = [
        "", "aida", "aida_", "aida_abcd1234", "aida_abcd1234_short",
        "aida_ABCD1234_" + "x" * 30,      # uppercase prefix
        "aida_abcd12_" + "x" * 30,        # prefix too short
        "aida_zzzzzzzz_" + "x" * 30,      # non-hex prefix
        "xxxx_abcd1234_" + "x" * 30,      # wrong namespace
    ]
    for token in bad:
        assert ent.parse_api_key_prefix(token) is None, token


def test_jwts_are_not_mistaken_for_api_keys():
    assert not ent.looks_like_api_key("eyJhbGciOiJSUzI1NiJ9.payload.sig")
    assert not ent.looks_like_api_key("aidax_abc")


# --- usability, scope, expiry ---------------------------------------------


def test_key_usable_when_neither_revoked_nor_expired():
    assert ent.key_unusable_reason(revoked_at=None, expires_at=NOW + timedelta(days=1), now=NOW) is None


def test_revoked_key_is_unusable_even_if_unexpired():
    assert ent.key_unusable_reason(revoked_at=NOW, expires_at=NOW + timedelta(days=30), now=NOW) == "revoked"


def test_expiry_boundary_is_exclusive():
    assert ent.key_unusable_reason(revoked_at=None, expires_at=NOW, now=NOW) == "expired"
    assert ent.key_unusable_reason(revoked_at=None, expires_at=NOW, now=NOW - timedelta(seconds=1)) is None


def test_naive_datetimes_from_sqlite_are_treated_as_utc():
    naive_future = (NOW + timedelta(days=1)).replace(tzinfo=None)
    naive_past = (NOW - timedelta(days=1)).replace(tzinfo=None)
    assert ent.key_unusable_reason(revoked_at=None, expires_at=naive_future, now=NOW) is None
    assert ent.key_unusable_reason(revoked_at=None, expires_at=naive_past, now=NOW) == "expired"
    assert not ent.should_touch_last_used((NOW - timedelta(minutes=1)).replace(tzinfo=None), NOW)
    assert ent.should_touch_last_used((NOW - timedelta(hours=1)).replace(tzinfo=None), NOW)


def test_read_scope_allows_only_safe_methods():
    for m in ("GET", "HEAD", "OPTIONS", "get"):
        assert ent.scope_allows("read", m)
    for m in ("POST", "PUT", "PATCH", "DELETE"):
        assert not ent.scope_allows("read", m)


def test_read_write_scope_allows_everything():
    for m in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        assert ent.scope_allows("read_write", m)


def test_unknown_scope_allows_nothing():
    assert not ent.scope_allows("admin", "GET")
    assert not ent.scope_allows("", "GET")


def test_default_expiry_is_90_days():
    assert ent.compute_expiry(NOW, None) == NOW + timedelta(days=90)


def test_explicit_expiry_and_bounds():
    assert ent.compute_expiry(NOW, 7) == NOW + timedelta(days=7)
    assert ent.compute_expiry(NOW, 365) == NOW + timedelta(days=365)
    for bad in (0, -1, 366):
        try:
            ent.compute_expiry(NOW, bad)
        except ValueError:
            continue
        raise AssertionError(f"ttl_days={bad} should be rejected")


def test_last_used_touch_is_throttled():
    assert ent.should_touch_last_used(None, NOW)
    assert not ent.should_touch_last_used(NOW - timedelta(minutes=1), NOW)
    assert ent.should_touch_last_used(NOW - timedelta(minutes=5), NOW)


# --- IP allowlist ----------------------------------------------------------


def test_empty_allowlist_allows_everyone_including_unknown_ip():
    assert ent.ip_allowed("203.0.113.9", [])
    assert ent.ip_allowed(None, [])


def test_cidr_and_single_ip_matching():
    allow = ent.normalize_allowlist(["203.0.113.0/24", "198.51.100.7"])
    assert ent.ip_allowed("203.0.113.200", allow)
    assert ent.ip_allowed("198.51.100.7", allow)
    assert not ent.ip_allowed("198.51.100.8", allow)
    assert not ent.ip_allowed("8.8.8.8", allow)


def test_bare_ips_are_canonicalised_to_host_networks():
    assert ent.normalize_allowlist(["198.51.100.7", "2001:db8::1"]) == ["198.51.100.7/32", "2001:db8::1/128"]


def test_host_bits_are_masked_and_duplicates_dropped():
    assert ent.normalize_allowlist(["10.1.2.3/8", "10.0.0.0/8", " 10.0.0.0/8 "]) == ["10.0.0.0/8"]


def test_nonempty_allowlist_fails_closed_on_unknown_or_garbage_client_ip():
    allow = ["203.0.113.0/24"]
    assert not ent.ip_allowed(None, allow)
    assert not ent.ip_allowed("", allow)
    assert not ent.ip_allowed("testclient", allow)
    assert not ent.ip_allowed("not-an-ip", allow)


def test_ipv4_mapped_ipv6_matches_ipv4_rule():
    assert ent.ip_allowed("::ffff:203.0.113.5", ["203.0.113.0/24"])
    assert not ent.ip_allowed("::ffff:198.51.100.5", ["203.0.113.0/24"])


def test_ipv6_ranges_work_and_families_do_not_cross_match():
    assert ent.ip_allowed("2001:db8::42", ["2001:db8::/32"])
    assert not ent.ip_allowed("2001:db9::1", ["2001:db8::/32"])
    assert not ent.ip_allowed("203.0.113.5", ["2001:db8::/32"])


def test_corrupt_stored_entry_neither_crashes_nor_allows():
    assert not ent.ip_allowed("203.0.113.5", ["garbage"])
    assert ent.ip_allowed("203.0.113.5", ["garbage", "203.0.113.0/24"])


def test_normalize_rejects_bad_input():
    for bad in (["nope"], ["1.2.3.4/33"], [""], ["   "], ["300.1.1.1"]):
        try:
            ent.normalize_allowlist(bad)
        except ent.InvalidAllowlistError:
            continue
        raise AssertionError(f"{bad} should be rejected")


def test_match_everything_ranges_are_rejected():
    for bad in ("0.0.0.0/0", "::/0"):
        try:
            ent.normalize_allowlist([bad])
        except ent.InvalidAllowlistError as exc:
            assert "every address" in str(exc)
        else:
            raise AssertionError(f"{bad} should be rejected")


def test_allowlist_size_cap():
    ok = [f"10.0.{i // 250}.{i % 250 + 1}" for i in range(ent.MAX_ALLOWLIST_ENTRIES)]
    assert len(ent.normalize_allowlist(ok)) == ent.MAX_ALLOWLIST_ENTRIES
    try:
        ent.normalize_allowlist(ok + ["192.0.2.1"])
    except ent.InvalidAllowlistError:
        pass
    else:
        raise AssertionError("expected size cap to reject")


# --- audit export ----------------------------------------------------------


def _row(**over):
    base = dict(
        created_at="2026-09-28T12:00:00+00:00", workspace_id="w1", workspace_name="Acme",
        actor_user_id="u1", actor_email="a@b.com", action="member.invited",
        target_type="workspace_member", target_id="t1", metadata={"role": "editor"},
    )
    base.update(over)
    return base


def test_csv_has_header_and_one_line_per_row():
    out = ent.rows_to_csv([_row(), _row(action="member.removed")])
    parsed = list(csv.reader(io.StringIO(out)))
    assert parsed[0] == ent.EXPORT_COLUMNS
    assert len(parsed) == 3
    assert parsed[1][ent.EXPORT_COLUMNS.index("action")] == "member.invited"


def test_csv_with_no_rows_is_just_the_header():
    assert list(csv.reader(io.StringIO(ent.rows_to_csv([])))) == [ent.EXPORT_COLUMNS]


def test_csv_metadata_is_stable_json():
    out = ent.rows_to_csv([_row(metadata={"b": 1, "a": 2})])
    cell = list(csv.reader(io.StringIO(out)))[1][-1]
    assert cell == '{"a": 2, "b": 1}'


def test_formula_injection_is_neutralised():
    for payload in ("=HYPERLINK(\"http://evil\")", "+1+1", "-2+3", "@SUM(A1)", "\tcmd", "\rcmd"):
        assert ent.csv_safe(payload).startswith("'"), payload
    out = ent.rows_to_csv([_row(actor_email="=cmd|' /C calc'!A0")])
    cell = list(csv.reader(io.StringIO(out)))[1][ent.EXPORT_COLUMNS.index("actor_email")]
    assert cell.startswith("'=")


def test_ordinary_values_are_untouched_and_none_is_empty():
    assert ent.csv_safe("hello") == "hello"
    assert ent.csv_safe(None) == ""
    assert ent.csv_safe("a=b") == "a=b"


def test_csv_quotes_commas_and_newlines_round_trip():
    out = ent.rows_to_csv([_row(workspace_name='Acme, "Inc"\nEast')])
    cell = list(csv.reader(io.StringIO(out)))[1][ent.EXPORT_COLUMNS.index("workspace_name")]
    assert cell == 'Acme, "Inc"\nEast'


# --- static consistency of the DB/request glue ---------------------------------


def _read(rel: str) -> str:
    return (API_DIR / rel).read_text(encoding="utf-8")


def test_migration_chain_is_linear_and_ends_at_0011():
    revs = {}
    for path in sorted((API_DIR / "alembic/versions").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        rev = re.search(r'^revision\s*=\s*"(\w+)"', text, re.M).group(1)
        down = re.search(r'^down_revision\s*=\s*(?:"(\w+)"|None)', text, re.M).group(1)
        revs[rev] = down
    assert revs["0011"] == "0010"
    assert len(set(revs.values())) == len(revs), "two migrations share a parent (branch)"
    heads = set(revs) - set(revs.values())
    assert heads == {"0011"}


def _model_columns(rel: str) -> set[str]:
    tree = ast.parse(_read(rel))
    return {
        n.target.id for n in ast.walk(tree)
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
    }


def test_api_key_migration_columns_match_model():
    mig = _read("alembic/versions/0011_enterprise.py")
    table_block = mig.split('"api_keys"', 1)[1].split("op.create_index", 1)[0]
    migrated = set(re.findall(r'sa\.Column\(\s*"(\w+)"', table_block))
    inherited = {"id", "created_at", "updated_at"}  # from the mixins
    assert migrated == (_model_columns("app/models/api_key.py") | inherited)


def test_ip_allowlist_migration_matches_model():
    assert "ip_allowlist" in _model_columns("app/models/organization.py")
    assert '"ip_allowlist"' in _read("alembic/versions/0011_enterprise.py")


def test_api_key_model_is_registered_for_alembic_and_test_fixtures():
    assert "ApiKey" in _read("app/models/__init__.py")
    assert "api_key" in _read("tests/conftest.py")


def test_only_hash_is_stored_never_the_key():
    cols = _model_columns("app/models/api_key.py")
    assert "key_hash" in cols
    assert not ({"key", "api_key", "secret", "raw_key"} & cols)


def test_api_key_auth_is_wired_before_jwt_verification():
    deps = _read("app/api/deps.py")
    assert deps.index("looks_like_api_key(token)") < deps.index("verify_session_token(token)")


def test_api_key_path_enforces_workspace_scope_org_scope_and_plan():
    body = _read("app/api/deps.py").split("def _authenticate_api_key", 1)[1].split("def get_current_user", 1)[0]
    for needle in ("scope_allows", 'path_params.get("workspace_id")', "key.organization_id",
                   "plan_has_feature", "key_unusable_reason", "verify_api_key"):
        assert needle in body, needle
    assert body.count('detail="Invalid API key"') == 1, "all secret-dependent failures must share one generic 401"


def test_ip_allowlist_enforced_after_membership_check_in_require_workspace_role():
    deps = _read("app/api/deps.py")
    inner = deps.split("def require_workspace_role", 1)[1]
    assert inner.index("member is None") < inner.index("_enforce_ip_allowlist(")


def test_no_enterprise_route_has_a_workspace_id_path_param():
    # API keys are only accepted on routes with a workspace_id path param
    # (deps._authenticate_api_key), and the IP allowlist only applies to
    # such routes. Both properties depend on every route here lacking one.
    src = _read("app/api/enterprise.py")
    paths = re.findall(r'@router\.(?:get|post|put|delete)\(\s*"([^"]*)"', src)
    prefix = re.search(r'APIRouter\(prefix="([^"]*)"', src).group(1)
    assert len(paths) == 7
    for path in paths + [prefix]:
        assert "workspace_id" not in path, path


def test_every_enterprise_route_requires_org_owner():
    src = _read("app/api/enterprise.py")
    handlers = re.findall(r"@router\.(?:get|post|put|delete)\(.*?\n(?:.*\n)*?def (\w+)\(", src)
    assert len(handlers) == 7
    for name in handlers:
        block = src.split(f"def {name}(", 1)[1].split("\n@router", 1)[0]
        assert "require_organization_owner()" in block, name


def test_gated_actions_check_the_feature_and_reads_do_not():
    src = _read("app/api/enterprise.py")

    def block(name):
        return src.split(f"def {name}(", 1)[1].split("\n@router", 1)[0]

    for name in ("create_api_key", "set_ip_allowlist", "export_audit_log"):
        assert "_require_feature(" in block(name), name
    for name in ("list_api_keys", "revoke_api_key", "get_ip_allowlist"):
        assert "_require_feature(" not in block(name), f"{name} must stay usable after a downgrade"


def test_full_key_is_only_returned_from_create():
    src = _read("app/api/enterprise.py")
    assert src.count("api_key=full_key") == 1
    assert "key_hash" not in src.split("def _key_out", 1)[1].split("@router", 1)[0]


def test_audit_export_filters_to_the_organizations_workspaces():
    block = _read("app/api/enterprise.py").split("def export_audit_log", 1)[1]
    assert "ActivityLog.workspace_id.in_(" in block
    assert "Workspace.organization_id == organization_id" in block


def test_router_registered_in_main():
    main = _read("main.py")
    assert "app.include_router(enterprise.router)" in main
    assert "0.19.0" in main


def test_readme_status_table_marks_phase_19_complete():
    readme = (API_DIR.parents[1] / "README.md").read_text(encoding="utf-8")
    assert "| 19 | Enterprise Features | ✅ Complete |" in readme


if __name__ == "__main__":
    tests = [(n, f) for n, f in list(globals().items()) if n.startswith("test_") and callable(f)]
    failures = []
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception as exc:  # noqa: BLE001
            failures.append(name)
            print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    if failures:
        sys.exit(1)
    print("All Phase 19 unit tests passed.")
