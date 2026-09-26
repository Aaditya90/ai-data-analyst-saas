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
    database_url: str = (
        "postgresql+psycopg://analyst_admin:change_me_locally@localhost:5432/ai_data_analyst"
    )

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

    # --- Billing (wired for Phase 14) ---
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""

    # --- Observability (wired for Phase 16) ---
    sentry_dsn: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance — env is read once per process."""
    return Settings()
