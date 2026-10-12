"""
Standalone tests for Phase 18 (Deployment).

Two kinds of test, both runnable with a bare interpreter (no fastapi,
sqlalchemy, or Docker needed):

  1. app/core/production_checks.py — pure logic over a settings-like
     object, so exercised directly with SimpleNamespace stand-ins.
  2. Static validation of the deployment artifacts (docker-compose.prod.yml,
     Dockerfiles, .env.production.example, Caddyfile, next.config.js).
     These can't prove an image builds or a stack boots — only a real
     `docker compose up` does — but they catch the mistakes that are
     cheap to make and expensive to discover on a server: a variable used
     in compose but missing from the env template, a typo'd setting name
     the app would silently ignore (Settings has extra="ignore"), a
     Dockerfile running as root, a service accidentally publishing a port.

Run with: python tests/test_deployment.py   (or via pytest)
"""

import ast
import re
import sys
from pathlib import Path
from types import SimpleNamespace

API_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = API_DIR.parents[1]
sys.path.insert(0, str(API_DIR))

from app.core.production_checks import (  # noqa: E402
    ProductionConfigError,
    enforce_production_config,
    find_production_config_problems,
)

STRONG_SECRET = "a" * 40


def good_settings(**overrides) -> SimpleNamespace:
    base = dict(
        environment="production",
        jwt_secret=STRONG_SECRET,
        database_url="postgresql+psycopg://admin:S3cure@postgres:5432/db",
        app_db_url="postgresql+psycopg://app_runtime:S3cure2@postgres:5432/db",
        s3_secret_key="s3-secure-secret",
        clerk_jwks_url="https://x.clerk.accounts.dev/.well-known/jwks.json",
        clerk_issuer="https://x.clerk.accounts.dev",
        cors_allowed_origins="https://app.example.com",
        frontend_base_url="https://app.example.com",
        data_encryption_key="somekey",
        stripe_secret_key="",
        stripe_webhook_secret="",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def settings_of(problems, severity=None):
    return {p.setting for p in problems if severity is None or p.severity == severity}


# ---------------------------------------------------------------------------
# production_checks
# ---------------------------------------------------------------------------


def test_clean_production_config_has_no_problems():
    assert find_production_config_problems(good_settings()) == []


def test_default_jwt_secret_is_critical():
    problems = find_production_config_problems(good_settings(jwt_secret="change_me_locally"))
    assert "JWT_SECRET" in settings_of(problems, "critical")


def test_short_jwt_secret_is_critical():
    problems = find_production_config_problems(good_settings(jwt_secret="short"))
    assert "JWT_SECRET" in settings_of(problems, "critical")


def test_jwt_secret_at_exactly_minimum_length_passes():
    problems = find_production_config_problems(good_settings(jwt_secret="x" * 32))
    assert "JWT_SECRET" not in settings_of(problems)


def test_default_database_password_is_critical():
    problems = find_production_config_problems(
        good_settings(database_url="postgresql+psycopg://a:change_me_locally@h/db")
    )
    assert "DATABASE_URL" in settings_of(problems, "critical")


def test_default_password_in_app_db_url_is_critical():
    problems = find_production_config_problems(
        good_settings(app_db_url="postgresql+psycopg://app_runtime:change_me_locally@h/db")
    )
    assert "APP_DB_URL" in settings_of(problems, "critical")


def test_default_s3_secret_is_critical():
    problems = find_production_config_problems(good_settings(s3_secret_key="change_me_locally"))
    assert "S3_SECRET_KEY" in settings_of(problems, "critical")


def test_missing_clerk_config_is_critical():
    assert "CLERK_JWKS_URL / CLERK_ISSUER" in settings_of(
        find_production_config_problems(good_settings(clerk_jwks_url="")), "critical"
    )
    assert "CLERK_JWKS_URL / CLERK_ISSUER" in settings_of(
        find_production_config_problems(good_settings(clerk_issuer="")), "critical"
    )


def test_unset_app_db_url_is_a_warning_not_critical():
    problems = find_production_config_problems(good_settings(app_db_url=""))
    assert "APP_DB_URL" in settings_of(problems, "warning")
    assert "APP_DB_URL" not in settings_of(problems, "critical")


def test_empty_cors_is_a_warning():
    problems = find_production_config_problems(good_settings(cors_allowed_origins="  "))
    assert "CORS_ALLOWED_ORIGINS" in settings_of(problems, "warning")


def test_localhost_frontend_url_is_a_warning():
    for url in ("http://localhost:3000", "http://127.0.0.1:3000"):
        problems = find_production_config_problems(good_settings(frontend_base_url=url))
        assert "FRONTEND_BASE_URL" in settings_of(problems, "warning"), url


def test_missing_encryption_key_is_a_warning():
    problems = find_production_config_problems(good_settings(data_encryption_key=""))
    assert "DATA_ENCRYPTION_KEY" in settings_of(problems, "warning")


def test_stripe_key_without_webhook_secret_is_a_warning():
    problems = find_production_config_problems(good_settings(stripe_secret_key="sk_live_x"))
    assert "STRIPE_WEBHOOK_SECRET" in settings_of(problems, "warning")


def test_stripe_fully_unset_is_not_a_problem():
    assert "STRIPE_WEBHOOK_SECRET" not in settings_of(find_production_config_problems(good_settings()))


def test_criticals_are_sorted_before_warnings():
    problems = find_production_config_problems(good_settings(jwt_secret="x", app_db_url="", cors_allowed_origins=""))
    severities = [p.severity for p in problems]
    assert severities == sorted(severities, key=lambda s: 0 if s == "critical" else 1)
    assert severities[0] == "critical"


def test_enforce_is_a_noop_outside_production():
    # The whole point: dev with every default must keep booting.
    dev = good_settings(environment="development", jwt_secret="change_me_locally", clerk_jwks_url="")
    assert enforce_production_config(dev) == []
    staging = good_settings(environment="staging", jwt_secret="change_me_locally")
    assert enforce_production_config(staging) == []


def test_enforce_raises_on_critical_in_production():
    try:
        enforce_production_config(good_settings(jwt_secret="change_me_locally"))
    except ProductionConfigError as exc:
        assert "JWT_SECRET" in str(exc)
        assert [p.setting for p in exc.problems] == ["JWT_SECRET"]
    else:
        raise AssertionError("expected ProductionConfigError")


def test_enforce_reports_every_critical_at_once():
    # An operator should see all the problems in one failed boot, not fix
    # them one restart at a time.
    try:
        enforce_production_config(good_settings(jwt_secret="x", s3_secret_key="change_me_locally", clerk_issuer=""))
    except ProductionConfigError as exc:
        assert len(exc.problems) == 3
    else:
        raise AssertionError("expected ProductionConfigError")


def test_enforce_does_not_raise_for_warnings_only_and_logs_them():
    import logging

    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("test.production_checks")
    logger.addHandler(Capture())
    logger.setLevel(logging.DEBUG)

    problems = enforce_production_config(good_settings(app_db_url="", cors_allowed_origins=""), logger)
    assert {p.setting for p in problems} == {"APP_DB_URL", "CORS_ALLOWED_ORIGINS"}
    assert len(records) == 2
    assert all(r.levelno == logging.WARNING for r in records)


# ---------------------------------------------------------------------------
# Static validation of deployment artifacts
# ---------------------------------------------------------------------------


def _load_yaml(path: Path):
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _settings_field_names() -> set[str]:
    """Field names declared on Settings in config.py, read via ast so this
    needs no pydantic install."""
    tree = ast.parse((API_DIR / "app/core/config.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            return {
                stmt.target.id
                for stmt in node.body
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
            }
    raise AssertionError("Settings class not found")


def _compose():
    return _load_yaml(REPO_ROOT / "docker-compose.prod.yml")


def test_every_api_env_var_maps_to_a_real_setting():
    # Settings uses extra="ignore", so a typo'd env var is silently
    # dropped — the app would run with the default and nobody would know.
    fields = _settings_field_names()
    api_env = _compose()["x-api-env"]
    # APP_RUNTIME_PASSWORD is read by scripts/set_app_runtime_password.py, not Settings.
    not_settings = {"APP_RUNTIME_PASSWORD"}
    unknown = [k for k in api_env if k.lower() not in fields and k not in not_settings]
    assert unknown == [], f"env vars with no matching Settings field: {unknown}"


def test_every_compose_variable_is_documented_in_env_example():
    compose_text = (REPO_ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    referenced = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)[:\-?}]", compose_text))
    example_text = (REPO_ROOT / ".env.production.example").read_text(encoding="utf-8")
    documented = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]*)=", example_text, flags=re.MULTILINE))
    missing = sorted(referenced - documented)
    assert missing == [], f"used in compose but missing from .env.production.example: {missing}"


def test_required_variables_use_fail_fast_syntax():
    compose_text = (REPO_ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    for var in ("POSTGRES_PASSWORD", "APP_RUNTIME_PASSWORD", "S3_SECRET_KEY", "WEB_DOMAIN", "API_DOMAIN"):
        assert f"${{{var}:?" in compose_text, f"{var} should fail compose if unset"


def test_only_caddy_publishes_host_ports():
    services = _compose()["services"]
    published = {name for name, svc in services.items() if svc.get("ports")}
    assert published == {"caddy"}, f"services publishing ports: {published}"


def test_api_waits_for_migrations_to_complete():
    api = _compose()["services"]["api"]
    assert api["depends_on"]["migrate"]["condition"] == "service_completed_successfully"


def test_migrate_job_does_not_restart():
    assert _compose()["services"]["migrate"]["restart"] == "no"


def test_api_connects_as_app_runtime_so_rls_is_enforced():
    api_env = _compose()["x-api-env"]
    assert api_env["APP_DB_URL"].startswith("postgresql+psycopg://app_runtime:")
    # ...while migrations use the admin URL (never the runtime role).
    assert "${POSTGRES_USER}" in api_env["DATABASE_URL"]


def test_api_environment_is_forced_to_production():
    assert _compose()["x-api-env"]["ENVIRONMENT"] == "production"


def test_web_build_args_match_dockerfile_args():
    compose_args = set(_compose()["services"]["web"]["build"]["args"])
    dockerfile = (REPO_ROOT / "infra/docker/web.Dockerfile").read_text(encoding="utf-8")
    dockerfile_args = set(re.findall(r"^ARG\s+(\w+)", dockerfile, flags=re.MULTILINE))
    assert compose_args == dockerfile_args


def test_dockerfiles_run_as_non_root():
    for name in ("api", "web"):
        text = (REPO_ROOT / f"infra/docker/{name}.Dockerfile").read_text(encoding="utf-8")
        users = re.findall(r"^USER\s+(\S+)", text, flags=re.MULTILINE)
        assert users and users[-1] not in ("root", "0"), f"{name}.Dockerfile must end as a non-root USER"


def test_dockerfiles_define_healthchecks():
    for name in ("api", "web"):
        text = (REPO_ROOT / f"infra/docker/{name}.Dockerfile").read_text(encoding="utf-8")
        assert "HEALTHCHECK" in text, name


def test_api_dockerfile_does_not_ship_dev_dependencies_or_tests():
    dockerfile = (REPO_ROOT / "infra/docker/api.Dockerfile").read_text(encoding="utf-8")
    assert "requirements-dev" not in dockerfile
    dockerignore = (API_DIR / ".dockerignore").read_text(encoding="utf-8")
    assert "tests/" in dockerignore and ".env" in dockerignore


def test_api_dockerfile_trusts_proxy_headers():
    assert "--proxy-headers" in (REPO_ROOT / "infra/docker/api.Dockerfile").read_text(encoding="utf-8")


def test_next_config_uses_standalone_output():
    # web.Dockerfile copies .next/standalone — it doesn't exist without this.
    assert 'output: "standalone"' in (REPO_ROOT / "apps/web/next.config.js").read_text(encoding="utf-8")


def test_caddyfile_routes_both_domains():
    caddy = (REPO_ROOT / "infra/caddy/Caddyfile").read_text(encoding="utf-8")
    assert "{$WEB_DOMAIN}" in caddy and "reverse_proxy web:3000" in caddy
    assert "{$API_DOMAIN}" in caddy and "reverse_proxy api:8000" in caddy


def test_caddy_body_limit_exceeds_default_upload_limit():
    caddy = (REPO_ROOT / "infra/caddy/Caddyfile").read_text(encoding="utf-8")
    limit_mb = int(re.search(r"max_size\s+(\d+)MB", caddy).group(1))
    default_upload_mb = 200  # Settings.max_upload_size_mb default
    assert limit_mb > default_upload_mb


def test_readiness_probe_is_defined_and_returns_503_on_db_failure():
    health = (API_DIR / "app/api/health.py").read_text(encoding="utf-8")
    assert '"/health/ready"' in health
    assert "status_code=503" in health


def test_secrets_are_gitignored():
    ignored = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env.production" in ignored


def test_env_example_contains_no_real_looking_secrets():
    text = (REPO_ROOT / ".env.production.example").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split("#")[0].strip()
        if any(word in key for word in ("PASSWORD", "SECRET", "KEY")):
            assert value == "", f"{key} should be blank in the template, found a value"


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
    print("All Phase 18 unit tests passed.")
