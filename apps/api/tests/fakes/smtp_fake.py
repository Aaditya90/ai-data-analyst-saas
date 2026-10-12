"""
Fake for `smtplib.SMTP`, standing in for the one call site in
app/services/email.py:

    with smtplib.SMTP(host, port, timeout=10) as smtp:
        smtp.starttls()
        smtp.login(username, password)
        smtp.send_message(message)

Swap in with:
    monkeypatch.setattr("app.services.email.smtplib.SMTP", fake_smtp_factory)
where `fake_smtp_factory` is `FakeSMTPFactory(inbox)` — a callable that,
like the real `smtplib.SMTP(host, port, timeout=...)`, returns a new
context-manager object per call, so a test can assert on
`inbox.sent_messages` afterward without needing to also fake `starttls`/
`login` as a separate mock chain.
"""


class _FakeSMTPConnection:
    def __init__(self, inbox: "FakeSMTPInbox", host: str, port: int, *, fail: bool):
        self.inbox = inbox
        self.host = host
        self.port = port
        self.fail = fail
        self.started_tls = False
        self.logged_in_as: str | None = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, username: str, password: str):
        self.logged_in_as = username

    def send_message(self, message):
        if self.fail:
            import smtplib

            raise smtplib.SMTPException("fake SMTP send failure (configured to fail)")
        self.inbox.sent_messages.append(
            {
                "from": message["From"],
                "to": message["To"],
                "subject": message["Subject"],
                "body": message.get_content(),
                "started_tls": self.started_tls,
                "logged_in_as": self.logged_in_as,
            }
        )


class FakeSMTPInbox:
    """Captures every message 'sent' through a FakeSMTPFactory."""

    def __init__(self):
        self.sent_messages: list[dict] = []

    def fail_next_send(self):
        """Returns a factory that raises on `send_message` — for testing
        `EmailSendError` is raised when SMTP is configured but sending
        itself fails (see email.py's docstring)."""
        return FakeSMTPFactory(self, fail=True)


class FakeSMTPFactory:
    """Callable replacement for `smtplib.SMTP` itself (the class, used as
    `smtplib.SMTP(host, port, timeout=...)`)."""

    def __init__(self, inbox: FakeSMTPInbox, *, fail: bool = False):
        self.inbox = inbox
        self.fail = fail

    def __call__(self, host: str, port: int, timeout: float | None = None):
        return _FakeSMTPConnection(self.inbox, host, port, fail=self.fail)
