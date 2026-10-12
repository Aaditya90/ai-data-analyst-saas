"""
Centralized entry point for every Claude API call in this codebase.

Before Phase 16, six call sites (app/services/nl_to_sql.py,
ai_narration.py x4, dashboard_generator.py) each independently built an
`anthropic.Anthropic` client, called `.messages.create(...)`, and
extracted the reply with the same
`"".join(block.text for block in response.content if block.type == "text")`
line. This module is that logic in one place, plus what Phase 16 adds on
top: Langfuse tracing (prompt, completion, token usage, latency, cost) for
every one of those calls, for free, without each caller needing to know
Langfuse exists.

Graceful degradation, same posture as Sentry (app/core/error_tracking.py):
if Langfuse isn't configured (LANGFUSE_PUBLIC_KEY/SECRET_KEY unset), or if
the Langfuse SDK call itself fails for any reason, `complete()` still
makes the actual Claude call and returns its text — tracing is strictly
additive, never a precondition for the feature working. Every
Langfuse-specific call below is wrapped in its own try/except for exactly
that reason.

A note on the Langfuse SDK surface used here: this targets Langfuse's
stable, lower-level trace/generation client API (`Langfuse().trace(...)`,
`.generation(...)`, `.end(...)`) rather than a decorator or an
auto-instrumenting wrapped-client import, since that lower-level surface
has been stable across more SDK versions. If the installed `langfuse`
version's API has since diverged, the try/except below means tracing
silently no-ops rather than breaking AI features — but it's worth
double-checking `_trace_generation`'s calls against whatever `langfuse`
version actually ends up pinned in requirements.txt.
"""

import time

import anthropic

from app.core.config import get_settings
from app.core.request_context import get_request_id


class AIClientNotConfiguredError(RuntimeError):
    pass


def _extract_text(response: anthropic.types.Message) -> str:
    return "".join(block.text for block in response.content if block.type == "text")


def _langfuse_configured() -> bool:
    settings = get_settings()
    return bool(settings.langfuse_public_key and settings.langfuse_secret_key)


def _trace_generation(
    *,
    feature: str,
    model: str,
    system: str,
    messages: list[dict],
    response: anthropic.types.Message | None,
    started_at: float,
    error: str | None,
) -> None:
    """Best-effort Langfuse trace. Never raises — see module docstring."""
    if not _langfuse_configured():
        return

    try:
        from langfuse import Langfuse

        settings = get_settings()
        client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
        trace = client.trace(
            name=feature,
            metadata={"request_id": get_request_id()},
            tags=[feature],
        )
        generation = trace.generation(
            name=feature,
            model=model,
            input={"system": system, "messages": messages},
            start_time=started_at,
        )
        if error is not None:
            generation.end(level="ERROR", status_message=error)
        else:
            usage = getattr(response, "usage", None)
            generation.end(
                output=_extract_text(response),
                usage={
                    "input": getattr(usage, "input_tokens", None),
                    "output": getattr(usage, "output_tokens", None),
                }
                if usage
                else None,
            )
        client.flush()
    except Exception:
        # Tracing is additive — a Langfuse SDK mismatch or network issue
        # must never surface as an AI-feature failure.
        pass


def complete(
    *,
    feature: str,
    system: str,
    messages: list[dict],
    max_tokens: int,
) -> str:
    """
    Makes one Claude API call and returns the extracted text reply,
    tracing it to Langfuse if configured.

    `feature` names the call site for tracing/debugging (e.g.
    "nl_to_sql", "insight_narration", "ai_dashboard_generator",
    "report_executive_summary") — every caller passes a short, stable
    string here rather than this module trying to infer one.
    """
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise AIClientNotConfiguredError(
            "ANTHROPIC_API_KEY is not set — add it to .env to use this AI feature."
        )

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    started_at = time.time()

    try:
        response = client.messages.create(
            model=settings.ai_model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        )
    except Exception as exc:
        _trace_generation(
            feature=feature,
            model=settings.ai_model,
            system=system,
            messages=messages,
            response=None,
            started_at=started_at,
            error=str(exc),
        )
        raise

    _trace_generation(
        feature=feature,
        model=settings.ai_model,
        system=system,
        messages=messages,
        response=response,
        started_at=started_at,
        error=None,
    )
    return _extract_text(response)
