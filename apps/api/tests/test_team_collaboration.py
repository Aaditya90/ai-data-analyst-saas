"""
Standalone unit tests for Phase 12 (Team Collaboration).

Covers the pure-Python pieces: WorkspaceInvite's expiry/pending logic,
the activity-log action vocabulary, invite token generation, and the
email service's SMTP-not-configured degradation path. Anything that needs
a live Postgres session (the actual API routes, the last-owner-protection
queries) is exercised manually against a running stack — see README.md
"Testing Phase 12".

Run with: python tests/test_team_collaboration.py
"""

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.models.workspace_invite import WorkspaceInvite, WorkspaceInviteStatus
from app.models.workspace_member import WorkspaceRole
from app.services.activity import ACTIONS
from app.services.email import EmailSendError, _smtp_configured, send_invite_email


def _make_invite(expires_in: timedelta, status_=WorkspaceInviteStatus.PENDING) -> WorkspaceInvite:
    invite = WorkspaceInvite(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        email="person@example.com",
        role=WorkspaceRole.EDITOR,
        status=status_,
        token="tok_" + uuid.uuid4().hex,
    )
    invite.expires_at = datetime.now(timezone.utc) + expires_in
    return invite


def test_invite_not_expired_when_future():
    invite = _make_invite(timedelta(days=7))
    assert invite.is_expired is False
    assert invite.is_pending is True


def test_invite_expired_when_past():
    invite = _make_invite(timedelta(days=-1))
    assert invite.is_expired is True
    assert invite.is_pending is False


def test_invite_not_pending_when_revoked_even_if_not_expired():
    invite = _make_invite(timedelta(days=7), status_=WorkspaceInviteStatus.REVOKED)
    assert invite.is_expired is False
    assert invite.is_pending is False


def test_invite_expiry_handles_naive_datetime():
    # Defensive: a value that round-tripped through a DB driver without a
    # tzinfo attached shouldn't blow up the comparison.
    invite = _make_invite(timedelta(days=1))
    invite.expires_at = invite.expires_at.replace(tzinfo=None)
    assert invite.is_expired is False


def test_activity_actions_cover_invite_and_member_lifecycle():
    expected = {
        "member.invited",
        "member.invite_accepted",
        "member.invite_revoked",
        "member.role_changed",
        "member.removed",
        "member.left",
    }
    assert expected.issubset(ACTIONS)


def test_activity_actions_cover_comment_lifecycle():
    expected = {"comment.created", "comment.resolved", "comment.reopened", "comment.deleted"}
    assert expected.issubset(ACTIONS)


def test_email_degrades_gracefully_without_smtp_host():
    get_settings.cache_clear()
    os.environ["SMTP_HOST"] = ""
    get_settings.cache_clear()
    assert _smtp_configured() is False

    sent = send_invite_email(
        to_email="new.person@example.com",
        workspace_name="Marketing",
        inviter_email="owner@example.com",
        role="viewer",
        token="sometoken123",
    )
    assert sent is False  # degraded to logging, did not raise


def test_email_send_error_is_the_right_exception_type():
    assert issubclass(EmailSendError, RuntimeError)


if __name__ == "__main__":
    test_invite_not_expired_when_future()
    test_invite_expired_when_past()
    test_invite_not_pending_when_revoked_even_if_not_expired()
    test_invite_expiry_handles_naive_datetime()
    test_activity_actions_cover_invite_and_member_lifecycle()
    test_activity_actions_cover_comment_lifecycle()
    test_email_degrades_gracefully_without_smtp_host()
    test_email_send_error_is_the_right_exception_type()
    print("All Phase 12 unit tests passed.")
