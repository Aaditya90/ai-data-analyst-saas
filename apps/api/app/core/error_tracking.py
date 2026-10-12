"""
Sentry error tracking. Same graceful-degradation posture as every other
optional external dependency in this codebase (AI narration's fallback
since Phase 8, invite email since Phase 12): if `SENTRY_DSN` isn't set,
`init_error_tracking()` does nothing and the API runs exactly as it did
before this phase — unhandled exceptions still propagate to FastAPI's own
error handling and get logged (Phase 16's JSON logging), they just aren't
also reported to Sentry.

`sentry_sdk.init()` itself is also wrapped in try/except: a bad DSN, a
network hiccup reaching Sentry at startup, or an incompatible installed
SDK version should not prevent the API from starting. Observability
tooling failing is not a reason for the actual product to go down.
"""

import logging

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def is_error_tracking_configured() -> bool:
    return bool(get_settings().sentry_dsn)


def init_error_tracking() -> bool:
    """Returns True if Sentry was actually initialized, False if skipped/failed."""
    settings = get_settings()
    if not settings.sentry_dsn:
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration

        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.environment,
            traces_sample_rate=settings.sentry_traces_sample_rate,
            integrations=[StarletteIntegration(), FastApiIntegration()],
            # Request bodies can contain dataset content / free-text AI
            # questions — don't attach them to error reports by default.
            send_default_pii=False,
        )
        return True
    except Exception:
        # Broad except is deliberate here: this is startup-time
        # initialization of an optional third-party integration, not
        # request-handling code — see module docstring for why a failure
        # here must never be allowed to crash the API.
        logger.warning("Sentry initialization failed; continuing without error tracking.", exc_info=True)
        return False
