"""
Phase 18: production configuration sanity checks.

Every default in app/core/config.py is chosen so `docker compose up` works
on a laptop with zero setup ("change_me_locally" passwords, an empty
Clerk config, ...). That's the right dev default and the wrong production
one: a deployment that forgets to override JWT_SECRET or the database
password would boot fine and serve traffic while being trivially
compromised. These checks make that class of mistake loud at startup.

Two severities, deliberately:
  - critical: the deployment is insecure or cannot function (default
    secrets, no auth config). In production the process refuses to start.
  - warning: something is probably wrong but the app can run (RLS not
    enforced because APP_DB_URL is unset, CORS empty, localhost invite
    links). Logged at WARNING on every boot, never blocks startup —
    consistent with this codebase's graceful-degradation stance for
    optional pieces.

Only enforced when ENVIRONMENT=production; dev/staging boots are never
blocked. `find_production_config_problems` is a pure function over a
settings-like object (attribute access only) so it can be unit tested
without pydantic/fastapi installed — see tests/test_deployment.py.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

DEV_DEFAULT_SECRET = "change_me_locally"
MIN_JWT_SECRET_LENGTH = 32


@dataclass(frozen=True)
class ConfigProblem:
    severity: str  # "critical" | "warning"
    setting: str
    message: str


class ProductionConfigError(RuntimeError):
    """Raised at startup when a production deployment has critical config problems."""

    def __init__(self, problems: list[ConfigProblem]):
        self.problems = problems
        lines = "\n".join(f"  - {p.setting}: {p.message}" for p in problems)
        super().__init__(
            "Refusing to start in production with critical configuration problems:\n" + lines
        )


def find_production_config_problems(settings) -> list[ConfigProblem]:
    """Returns every problem found (criticals first). Empty list = clean."""
    problems: list[ConfigProblem] = []

    def critical(setting: str, message: str) -> None:
        problems.append(ConfigProblem("critical", setting, message))

    def warning(setting: str, message: str) -> None:
        problems.append(ConfigProblem("warning", setting, message))

    # --- critical ---
    if settings.jwt_secret == DEV_DEFAULT_SECRET or len(settings.jwt_secret) < MIN_JWT_SECRET_LENGTH:
        critical(
            "JWT_SECRET",
            f"must be set to a random value of at least {MIN_JWT_SECRET_LENGTH} characters "
            "(generate one with: openssl rand -hex 32)",
        )

    if DEV_DEFAULT_SECRET in settings.database_url:
        critical("DATABASE_URL", "still contains the development default password")

    if settings.app_db_url and DEV_DEFAULT_SECRET in settings.app_db_url:
        critical("APP_DB_URL", "still contains the development default password")

    if settings.s3_secret_key == DEV_DEFAULT_SECRET:
        critical("S3_SECRET_KEY", "still the development default")

    if not settings.clerk_jwks_url or not settings.clerk_issuer:
        critical(
            "CLERK_JWKS_URL / CLERK_ISSUER",
            "both must be set or no request can be authenticated",
        )

    # --- warnings ---
    if not settings.app_db_url:
        warning(
            "APP_DB_URL",
            "unset: the API connects as the table owner, so Postgres Row-Level Security "
            "is present but NOT enforced. Point it at the app_runtime role (Phase 15).",
        )

    if not settings.cors_allowed_origins.strip():
        warning(
            "CORS_ALLOWED_ORIGINS",
            "empty: CORS fails closed in production, so a browser frontend on another "
            "origin cannot call this API",
        )

    if "localhost" in settings.frontend_base_url or "127.0.0.1" in settings.frontend_base_url:
        warning(
            "FRONTEND_BASE_URL",
            "points at localhost: invite emails will contain links that only work on your machine",
        )

    if not settings.data_encryption_key:
        warning(
            "DATA_ENCRYPTION_KEY",
            "unset: saving database connections (which encrypts their passwords) will fail",
        )

    if settings.stripe_secret_key and not settings.stripe_webhook_secret:
        warning(
            "STRIPE_WEBHOOK_SECRET",
            "unset while STRIPE_SECRET_KEY is set: subscription changes will never sync back",
        )

    problems.sort(key=lambda p: 0 if p.severity == "critical" else 1)
    return problems


def enforce_production_config(settings, logger: logging.Logger | None = None) -> list[ConfigProblem]:
    """
    Called once at startup. Outside production: returns [] and does nothing
    (dev must keep booting with defaults). In production: logs every
    warning, raises ProductionConfigError if any critical problem exists.
    Returns the problems found, for callers/tests that want to inspect them.
    """
    if settings.environment != "production":
        return []

    log = logger or logging.getLogger("app.startup")
    problems = find_production_config_problems(settings)

    for problem in problems:
        if problem.severity == "warning":
            log.warning(
                "production_config_warning",
                extra={"setting": problem.setting, "detail": problem.message},
            )

    criticals = [p for p in problems if p.severity == "critical"]
    if criticals:
        raise ProductionConfigError(criticals)

    return problems
