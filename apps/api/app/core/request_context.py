"""
Request-scoped context, readable from anywhere in the call stack without
threading a parameter through every function signature — specifically the
request id, so a log line written deep inside a service function (e.g.
app/services/nl_to_sql.py) can still be correlated with the HTTP request
that triggered it.

Deliberately zero third-party imports (stdlib `contextvars`/`uuid` only)
so this module — and app/core/logging_config.py, which only depends on
this one — can be imported and unit-tested without pulling in
FastAPI/SQLAlchemy/etc. See tests/test_observability.py.
"""

import contextvars
import uuid

_request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)


def new_request_id() -> str:
    return uuid.uuid4().hex


def set_request_id(request_id: str) -> contextvars.Token:
    return _request_id_var.set(request_id)


def reset_request_id(token: contextvars.Token) -> None:
    _request_id_var.reset(token)


def get_request_id() -> str | None:
    return _request_id_var.get()
