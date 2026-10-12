"""
Structured (JSON) logging, so log lines are machine-parseable by whatever
aggregates them (CloudWatch, Datadog, a self-hosted ELK stack, ...) instead
of needing a regex to pull fields back out of a formatted string.

Module-level imports are deliberately stdlib-only (`logging`, `json`,
`datetime`, plus this package's own dependency-free
`app.core.request_context`) — `configure_logging()` reads
`app.core.config.get_settings()` for LOG_LEVEL, but only inside the
function body, so importing this module on its own (e.g. from a test)
never requires pydantic-settings/FastAPI/etc. to be installed. See
tests/test_observability.py, which exercises `JSONFormatter` directly.
"""

import json
import logging
from datetime import datetime, timezone

from app.core.request_context import get_request_id

# Fields every LogRecord already carries that we don't want duplicated
# under `extra` — anything NOT in this set that a caller passes via
# `logger.info(..., extra={...})` gets included automatically.
_STANDARD_LOG_RECORD_FIELDS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName",
}


class JSONFormatter(logging.Formatter):
    """Renders each LogRecord as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        request_id = get_request_id()
        if request_id is not None:
            payload["request_id"] = request_id

        for key, value in record.__dict__.items():
            if key not in _STANDARD_LOG_RECORD_FIELDS and key not in payload:
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def configure_logging() -> None:
    """
    Call once at process startup (main.py). Idempotent — safe to call more
    than once (e.g. under a test runner that imports main.py repeatedly);
    replaces any handlers already on the root logger rather than stacking
    duplicates.
    """
    from app.core.config import get_settings  # deferred — see module docstring

    settings = get_settings()

    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())
    root.handlers = []

    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    root.addHandler(handler)

    # Quiet a couple of noisy third-party loggers down to WARNING so
    # normal operation isn't dominated by their own INFO chatter; app
    # code logs at whatever level LOG_LEVEL says.
    for noisy_logger in ("uvicorn.access", "httpx"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)
