"""
Central application configuration.

All later phases (auth, ingestion, AI, billing, observability) read their
config from this single Settings object instead of scattering os.getenv()
calls across the codebase. This is intentional: one source of truth makes
it trivial to see everything the system depends on at a glance.
"""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- App ---
    environment: str = "development"
    app_name: str = "ai-data-analyst"
    log_level: str = "info"

    # --- Database ---
    # `database_url` is the migration/admin connection (able to CREATE
    # TABLE, ALTER, CREATE ROLE, ...) — Alembic always uses this one.
    # `app_db_url` is what the running API connects with; Phase 15 adds
    # this split so the app can run as the low-privilege `app_runtime`
    # role (see migration 0010) instead of the table-owning admin role,
    # which is what makes Row-Level Security actually apply instead of
    # being silently bypassed by an owner/superuser connection. Falls
    # back to `database_url` when unset, so existing setups that haven't
    # done the Phase 15 role split keep working exactly as before — RLS
    # policies exist either way, they just aren't enforced against an
    # owner connection until `app_db_url` points at a non-owner role.
    database_url: str = (
        "postgresql+psycopg://analyst_admin:change_me_locally@localhost:5432/ai_data_analyst"
    )
    app_db_url: str = ""

    # --- Redis ---
    redis_url: str = "redis://localhost:6379/0"

    # --- Object storage ---
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "minio_admin"
    s3_secret_key: str = "change_me_locally"
    s3_bucket_name: str = "ai-analyst-uploads"
    s3_region: str = "us-east-1"

    # --- Data ingestion (Phase 3) ---
    data_encryption_key: str = ""  # Fernet key for DataConnection passwords — see app/core/crypto.py
    max_upload_size_mb: int = 200
    dataset_preview_row_limit: int = 50

    # --- Auth (wired for Phase 2) ---
    auth_provider: str = "clerk"
    clerk_secret_key: str = ""
    clerk_publishable_key: str = ""
    clerk_jwks_url: str = ""       # e.g. https://<your-domain>.clerk.accounts.dev/.well-known/jwks.json
    clerk_issuer: str = ""         # e.g. https://<your-domain>.clerk.accounts.dev
    jwt_secret: str = "change_me_locally"

    # --- AI provider (wired for Phase 7+) ---
    anthropic_api_key: str = ""
    ai_model: str = "claude-sonnet-4-6"
    ai_query_row_limit: int = 500

    # --- Team collaboration (Phase 12) ---
    # SMTP is optional in dev: if smtp_host is unset, invite emails are
    # logged instead of sent (same graceful-degradation instinct as the
    # AI fallback paths since Phase 8) rather than failing the invite.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = "no-reply@example.com"
    smtp_use_tls: bool = True
    frontend_base_url: str = "http://localhost:3000"
    # --- CORS (Phase 15) ---
    # In dev (environment != "production"), the API always allows
    # http://localhost:3000 (the Next.js dev server) regardless of this
    # setting, so local dev needs zero CORS config. In production, this
    # comma-separated list is the *only* thing that populates allowed
    # origins — previously that list was hardcoded to `[]` in main.py with
    # no way to change it, meaning a production deployment's own frontend
    # could never actually call the API from a browser. Left blank, CORS
    # still fails closed (the safe default for a misconfigured prod
    # deployment), but now there's an actual way to fix it: set this to
    # your real frontend origin(s), e.g.
    # "https://app.example.com,https://staging.example.com".
    cors_allowed_origins: str = ""

    # --- Observability (Phase 16) ---
    log_level: str = "INFO"
    sentry_dsn: str = ""
    sentry_traces_sample_rate: float = 0.0
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # --- Billing (Phase 14) ---
    # Reading plan/usage/limits never needs Stripe — those live on
    # Organization and are computed from local counts. Only checkout and
    # the billing portal actually call Stripe, and only if configured;
    # see app/services/billing.py for why this does NOT degrade silently
    # the way AI/email do (you can't fake a real payment flow).
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_id_pro: str = ""
    stripe_price_id_enterprise: str = ""

    # --- Observability (wired for Phase 16) ---
    sentry_dsn: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def effective_app_database_url(self) -> str:
        """The URL the running API should actually connect with — see
        `app_db_url`'s comment above for the fallback reasoning."""
        return self.app_db_url or self.database_url

    @property
    def cors_origins(self) -> list[str]:
        """See `cors_allowed_origins`'s comment for the fail-closed-until-
        configured reasoning in production."""
        configured = [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]
        if self.is_production:
            return configured
        # Dev always includes the Next.js dev server, plus anything extra
        # the operator configured (e.g. a second local port).
        return list({"http://localhost:3000", *configured})


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance — env is read once per process."""
    return Settings()
