"""
Phase 17 (Testing) shared fixtures.

Design notes, since these choices matter for what the tests built on top
of them can and can't prove:

  - DB: SQLite in-memory (`create_engine("sqlite://", ...)`, StaticPool so
    every connection in the pool is the *same* in-memory DB rather than
    each getting its own empty one). This is fast and needs nothing
    running locally, which is why it's the default for `db_session` /
    `client`. It is NOT a Postgres stand-in for Row-Level Security —
    SQLite has no `set_config()`/GUC mechanism, so `set_workspace_context`
    (app/core/rls.py) is monkeypatched to a no-op for these fixtures (see
    `_patch_rls_for_sqlite` below). What these fixtures DO faithfully
    exercise is the *application-layer* workspace_id filtering every
    route is supposed to do regardless of RLS — RLS is Phase 15's second,
    database-enforced line of defense behind that, not a replacement for
    it, so testing the app layer here is testing the thing that has to be
    correct on its own either way. Genuine RLS behavior (a query that
    forgot its `.filter(workspace_id=...)` still getting blocked) needs a
    real Postgres connection — see `tests/integration/test_rls_postgres.py`
    and its `postgres` marker.

  - Auth: `get_current_user` is overridden directly to return a fixture-
    built `User`, bypassing real Clerk JWT verification (`test_security.py`
    already covers the token-verification logic itself in isolation).
    `require_workspace_role` is deliberately NOT overridden — it's left
    to run for real against the SQLite session, so integration tests are
    actually exercising role/membership enforcement, not assuming it away.

  - External services (Claude, Stripe, SMTP): every test gets a fake by
    default (`fake_ai_client`, `fake_stripe`, `smtp_inbox` fixtures) so no
    test accidentally makes a real network call. A test that wants to
    assert on "AI/Stripe/SMTP not configured" behavior instead can just
    not request that fixture and additionally clear the relevant setting
    via `override_settings`.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.database import Base, get_db
from app.models.organization import Organization
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_member import WorkspaceMember, WorkspaceRole

from tests.fakes.ai import FakeAnthropicClient
from tests.fakes.smtp_fake import FakeSMTPFactory, FakeSMTPInbox
from tests.fakes.stripe_fake import FakeStripe

# Import every model module so its table is registered on Base.metadata
# before create_all() runs below — SQLAlchemy only knows about a table
# once the class defining it has actually been imported somewhere.
from app.models import (  # noqa: F401
    activity_log,
    api_key,
    billing_event,
    dashboard,
    dashboard_comment,
    dashboard_version,
    dashboard_widget,
    data_connection,
    dataset,
    dataset_version,
    forecast,
    ml_model,
    report,
    workspace_invite,
)


# ---------------------------------------------------------------------------
# Settings overrides
# ---------------------------------------------------------------------------


@pytest.fixture
def override_settings(monkeypatch):
    """
    Yields a function `set(**kwargs)` that overwrites attributes on the
    process-wide cached Settings instance for the duration of one test,
    e.g. `override_settings(anthropic_api_key="", stripe_secret_key="")`
    to test the "not configured" degradation path. Settings is an
    `lru_cache`d singleton (app/core/config.py) so this mutates the same
    instance every module already holds a reference to, rather than
    trying to bust and rebuild the cache mid-test.
    """
    settings = get_settings()
    originals: dict[str, object] = {}

    def _set(**kwargs):
        for key, value in kwargs.items():
            if key not in originals:
                originals[key] = getattr(settings, key)
            monkeypatch.setattr(settings, key, value)

    yield _set
    # monkeypatch's own teardown already reverts each setattr; `originals`
    # is kept only so a test could inspect prior values if it needed to.


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


@pytest.fixture
def db_session(monkeypatch) -> Generator[Session, None, None]:
    """A fresh SQLite in-memory DB, schema created from the real models,
    torn down after the test. See module docstring for what this can and
    can't stand in for."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)

    _patch_rls_for_sqlite(monkeypatch)

    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _patch_rls_for_sqlite(monkeypatch) -> None:
    """`app.api.deps.require_workspace_role` calls `set_workspace_context`,
    which issues a Postgres-only `set_config(...)` call — see module
    docstring for why that's a no-op here rather than something these
    fixtures try to emulate."""
    monkeypatch.setattr("app.api.deps.set_workspace_context", lambda db, workspace_id: None)


# ---------------------------------------------------------------------------
# Fakes for optional external dependencies
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_ai_client(monkeypatch) -> FakeAnthropicClient:
    """Swaps `anthropic.Anthropic` (used inside app.services.ai_client.complete)
    for a scripted fake, and makes sure the feature is considered
    "configured" so `complete()` doesn't short-circuit with
    AIClientNotConfiguredError before ever reaching it."""
    fake = FakeAnthropicClient(default_response="")
    monkeypatch.setattr("app.services.ai_client.anthropic.Anthropic", fake)
    get_settings().anthropic_api_key = "sk-ant-fake-test-key"
    return fake


@pytest.fixture
def fake_stripe(monkeypatch) -> FakeStripe:
    """Swaps `stripe` (used inside app.services.billing) for a scripted
    fake, and makes sure billing is considered "configured"."""
    fake = FakeStripe()
    monkeypatch.setattr("app.services.billing.stripe", fake)
    settings = get_settings()
    settings.stripe_secret_key = "sk_test_fake"
    settings.stripe_webhook_secret = "whsec_fake"
    settings.stripe_price_id_pro = "price_fake_pro"
    settings.stripe_price_id_enterprise = "price_fake_enterprise"
    return fake


@pytest.fixture
def smtp_inbox(monkeypatch) -> FakeSMTPInbox:
    """Swaps `smtplib.SMTP` (used inside app.services.email) for a fake
    that captures sent messages instead of connecting anywhere, and makes
    sure SMTP is considered "configured"."""
    inbox = FakeSMTPInbox()
    monkeypatch.setattr("app.services.email.smtplib.SMTP", FakeSMTPFactory(inbox))
    settings = get_settings()
    settings.smtp_host = "smtp.fake.test"
    settings.smtp_port = 587
    settings.smtp_from_email = "noreply@fake.test"
    return inbox


# ---------------------------------------------------------------------------
# Model factories
# ---------------------------------------------------------------------------


@pytest.fixture
def make_user(db_session):
    def _make(*, email: str | None = None, auth_provider_id: str | None = None) -> User:
        unique = uuid.uuid4().hex[:8]
        user = User(
            email=email or f"user-{unique}@example.com",
            auth_provider_id=auth_provider_id or f"auth_{unique}",
            full_name="Test User",
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        return user

    return _make


@pytest.fixture
def make_organization(db_session):
    def _make(*, name: str = "Test Org", plan: str = "free") -> Organization:
        unique = uuid.uuid4().hex[:8]
        org = Organization(name=name, slug=f"test-org-{unique}", plan=plan)
        db_session.add(org)
        db_session.commit()
        db_session.refresh(org)
        return org

    return _make


@pytest.fixture
def make_workspace(db_session, make_organization):
    def _make(*, organization: Organization | None = None, name: str = "Test Workspace") -> Workspace:
        org = organization or make_organization()
        unique = uuid.uuid4().hex[:8]
        workspace = Workspace(name=name, slug=f"test-ws-{unique}", organization_id=org.id)
        db_session.add(workspace)
        db_session.commit()
        db_session.refresh(workspace)
        return workspace

    return _make


@pytest.fixture
def add_member(db_session):
    def _add(*, workspace: Workspace, user: User, role: WorkspaceRole = WorkspaceRole.OWNER) -> WorkspaceMember:
        member = WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role=role)
        db_session.add(member)
        db_session.commit()
        db_session.refresh(member)
        return member

    return _add


# ---------------------------------------------------------------------------
# FastAPI TestClient
# ---------------------------------------------------------------------------


@pytest.fixture
def client(db_session, make_user) -> Generator[TestClient, None, None]:
    """A TestClient wired to the SQLite `db_session` and authenticated as
    a fresh user (available as `client.test_user`). Import the real `app`
    lazily (inside the fixture, not at module import time) so `main.py`'s
    side effects — configure_logging(), init_error_tracking() — run only
    once fixtures (and any settings overrides a test applied first) are
    already in place."""
    from main import app
    from app.api.deps import get_current_user

    test_user = make_user()

    def _override_get_db():
        yield db_session

    def _override_get_current_user():
        return test_user

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user

    test_client = TestClient(app)
    test_client.test_user = test_user  # type: ignore[attr-defined]
    try:
        yield test_client
    finally:
        app.dependency_overrides.clear()
