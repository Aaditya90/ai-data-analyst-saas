"""
Minimal email sending for workspace invites.

Follows the same graceful-degradation posture established for AI features
since Phase 8: if SMTP isn't configured (no `smtp_host`), we don't hard-fail
the invite — we log the email that *would* have been sent (including the
accept link, so local/dev workflows can just copy it out of the API logs)
and return normally. A missing mail server should never block someone from
being invited to a workspace.

Kept deliberately tiny (stdlib `smtplib`, one plaintext template) — a
templated/HTML mailer with a provider SDK (Postmark, SES, ...) is future
work for whichever later phase needs richer email, not this one.
"""

import logging
import smtplib
from email.message import EmailMessage

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class EmailSendError(RuntimeError):
    """Raised when SMTP is configured but sending still fails."""


def _smtp_configured() -> bool:
    return bool(get_settings().smtp_host)


def build_invite_accept_url(token: str) -> str:
    settings = get_settings()
    return f"{settings.frontend_base_url.rstrip('/')}/invites/{token}"


def send_invite_email(
    *, to_email: str, workspace_name: str, inviter_email: str, role: str, token: str
) -> bool:
    """
    Returns True if an SMTP send was actually attempted and succeeded,
    False if it degraded to logging instead. Raises EmailSendError only
    when SMTP *is* configured but the send itself fails, since that
    usually means misconfiguration worth surfacing rather than hiding.
    """
    settings = get_settings()
    accept_url = build_invite_accept_url(token)

    subject = f"You've been invited to join {workspace_name}"
    body = (
        f"{inviter_email} invited you to join the \"{workspace_name}\" workspace "
        f"as a {role}.\n\n"
        f"Accept the invite:\n{accept_url}\n\n"
        f"This link expires in 7 days. If you weren't expecting this, you can "
        f"ignore this email."
    )

    if not _smtp_configured():
        logger.info(
            "[dev-mode email] SMTP not configured — invite email not sent. "
            "To: %s | Subject: %s | Accept URL: %s",
            to_email,
            subject,
            accept_url,
        )
        return False

    message = EmailMessage()
    message["From"] = settings.smtp_from_email
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        raise EmailSendError(f"Failed to send invite email: {exc}") from exc

    return True
