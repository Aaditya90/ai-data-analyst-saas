"""
Standalone unit tests for Phase 17 (Testing) — but for the testing
infrastructure itself, not the app.

Everything else Phase 17 adds (conftest.py, tests/integration/*) imports
fastapi/sqlalchemy/anthropic/stripe transitively through `app.*` and can
only actually run where those are installed (`pip install -r
requirements-dev.txt && pytest`, or CI — see .github/workflows/tests.yml).
This file is the one piece of Phase 17 that is itself dependency-free
(tests/fakes/*.py imports nothing outside the standard library), so it's
the one Phase 17 test that can be — and was — actually executed in a
sandbox with no package installs and no network access, the same
constraint every phase since Phase 3 has been built under. It exists to
catch a very real class of bug: a fake whose interface silently drifts
from the real SDK call it's supposed to mirror (wrong kwarg name, wrong
return shape) would make every integration test built on it pass for the
wrong reason.

Run with: python tests/test_testing_infra.py
"""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.fakes.ai import FakeAnthropicClient, make_fake_message
from tests.fakes.smtp_fake import FakeSMTPFactory, FakeSMTPInbox
from tests.fakes.stripe_fake import FakeStripe


# ---------------------------------------------------------------------------
# FakeAnthropicClient
# ---------------------------------------------------------------------------


def test_fake_anthropic_client_is_callable_like_the_real_constructor():
    # ai_client.complete() does `anthropic.Anthropic(api_key=...)` — the
    # thing swapped in for `anthropic.Anthropic` must accept that call
    # and hand back something with `.messages.create`.
    fake = FakeAnthropicClient(default_response="hello")
    instance = fake(api_key="sk-ant-whatever")
    assert instance is fake
    assert hasattr(instance.messages, "create")


def test_fake_anthropic_client_extracts_text_the_same_way_ai_client_does():
    fake = FakeAnthropicClient(default_response="42")
    response = fake().messages.create(
        model="claude-sonnet-4-6", max_tokens=100, system="sys", messages=[{"role": "user", "content": "q"}]
    )
    # Mirrors app.services.ai_client._extract_text exactly.
    text = "".join(block.text for block in response.content if block.type == "text")
    assert text == "42"


def test_fake_anthropic_client_pops_scripted_responses_in_order():
    fake = FakeAnthropicClient(responses=["first", "second"])
    r1 = fake().messages.create(model="m", max_tokens=1, system="s", messages=[])
    r2 = fake().messages.create(model="m", max_tokens=1, system="s", messages=[])
    assert r1.content[0].text == "first"
    assert r2.content[0].text == "second"


def test_fake_anthropic_client_responder_sees_the_actual_prompt():
    seen = {}

    def responder(*, system, messages):
        seen["system"] = system
        seen["messages"] = messages
        return "responder-reply"

    fake = FakeAnthropicClient(responder=responder)
    response = fake().messages.create(
        model="m", max_tokens=1, system="be terse", messages=[{"role": "user", "content": "hi"}]
    )
    assert response.content[0].text == "responder-reply"
    assert seen["system"] == "be terse"
    assert seen["messages"] == [{"role": "user", "content": "hi"}]


def test_fake_anthropic_client_records_every_call():
    fake = FakeAnthropicClient(default_response="x")
    fake().messages.create(model="m1", max_tokens=10, system="s1", messages=[])
    fake().messages.create(model="m2", max_tokens=20, system="s2", messages=[])
    assert len(fake.calls) == 2
    assert fake.calls[0]["model"] == "m1"
    assert fake.calls[1]["max_tokens"] == 20


def test_fake_anthropic_client_can_simulate_an_api_error():
    fake = FakeAnthropicClient(raise_on_call=RuntimeError("simulated 529 overloaded"))
    try:
        fake().messages.create(model="m", max_tokens=1, system="s", messages=[])
    except RuntimeError as exc:
        assert "simulated" in str(exc)
    else:
        raise AssertionError("expected the fake to raise")


def test_make_fake_message_shape_matches_what_ai_client_reads():
    message = make_fake_message("hi there", input_tokens=10, output_tokens=3)
    assert message.content[0].type == "text"
    assert message.content[0].text == "hi there"
    assert message.usage.input_tokens == 10
    assert message.usage.output_tokens == 3


# ---------------------------------------------------------------------------
# FakeStripe
# ---------------------------------------------------------------------------


def test_fake_stripe_customer_create_returns_a_subscriptable_id():
    fake = FakeStripe()
    customer = fake.Customer.create(email="a@b.com", name="Acme")
    assert customer["id"].startswith("cus_fake_")
    assert customer["email"] == "a@b.com"


def test_fake_stripe_checkout_session_create_returns_configured_url():
    fake = FakeStripe(checkout_url="https://example.test/checkout")
    session = fake.checkout.Session.create(
        customer="cus_1",
        mode="subscription",
        line_items=[{"price": "price_1", "quantity": 1}],
        success_url="https://app.test/success",
        cancel_url="https://app.test/cancel",
    )
    assert session["url"] == "https://example.test/checkout"


def test_fake_stripe_billing_portal_session_create_returns_configured_url():
    fake = FakeStripe()
    session = fake.billing_portal.Session.create(customer="cus_1", return_url="https://app.test/account")
    assert session["url"] == fake.portal_url


def test_fake_stripe_webhook_construct_event_returns_configured_event():
    fake = FakeStripe()
    event = fake.make_subscription_event(
        event_type="customer.subscription.updated",
        stripe_event_id="evt_1",
        customer_id="cus_1",
        subscription_id="sub_1",
        price_id="price_pro",
    )
    fake.next_event = event
    result = fake.Webhook.construct_event(b"payload", "sig", "whsec_fake")
    assert result is event
    assert result["type"] == "customer.subscription.updated"
    assert result["data"]["object"]["id"] == "sub_1"


def test_fake_stripe_webhook_rejects_invalid_signature():
    fake = FakeStripe()
    fake.invalid_signature = True
    try:
        fake.Webhook.construct_event(b"payload", "bad-sig", "whsec_fake")
    except ValueError:
        pass
    else:
        raise AssertionError("expected an invalid signature to raise")


def test_fake_stripe_records_every_call():
    fake = FakeStripe()
    fake.Customer.create(email="a@b.com", name="Acme")
    fake.billing_portal.Session.create(customer="cus_1", return_url="https://app.test")
    assert [c["method"] for c in fake.calls] == ["Customer.create", "billing_portal.Session.create"]


def test_fake_stripe_subscription_event_shape_has_first_price_item():
    fake = FakeStripe()
    event = fake.make_subscription_event(
        event_type="customer.subscription.created",
        stripe_event_id="evt_2",
        customer_id="cus_2",
        subscription_id="sub_2",
        price_id="price_enterprise",
        status="trialing",
        cancel_at_period_end=True,
    )
    obj = event["data"]["object"]
    assert obj["status"] == "trialing"
    assert obj["cancel_at_period_end"] is True
    assert obj["items"]["data"][0]["price"]["id"] == "price_enterprise"


# ---------------------------------------------------------------------------
# FakeSMTP
# ---------------------------------------------------------------------------


class _FakeEmailMessage:
    """Minimal stand-in for email.message.EmailMessage — just enough for
    _FakeSMTPConnection.send_message to read what it needs."""

    def __init__(self, headers: dict, body: str):
        self._headers = headers
        self._body = body

    def __getitem__(self, key):
        return self._headers[key]

    def get_content(self):
        return self._body


def test_fake_smtp_factory_is_callable_like_the_real_class():
    inbox = FakeSMTPInbox()
    factory = FakeSMTPFactory(inbox)
    connection = factory("smtp.example.test", 587, timeout=10)
    assert connection.host == "smtp.example.test"
    assert connection.port == 587


def test_fake_smtp_send_message_lands_in_the_inbox():
    inbox = FakeSMTPInbox()
    factory = FakeSMTPFactory(inbox)

    message = _FakeEmailMessage(
        {"From": "noreply@fake.test", "To": "invitee@example.com", "Subject": "You're invited"},
        "Accept here: https://app.test/invites/abc123",
    )

    with factory("smtp.fake.test", 587, timeout=10) as smtp:
        smtp.starttls()
        smtp.login("user", "pass")
        smtp.send_message(message)

    assert len(inbox.sent_messages) == 1
    sent = inbox.sent_messages[0]
    assert sent["to"] == "invitee@example.com"
    assert sent["subject"] == "You're invited"
    assert sent["started_tls"] is True
    assert sent["logged_in_as"] == "user"
    assert "abc123" in sent["body"]


def test_fake_smtp_fail_next_send_raises_smtp_exception():
    import smtplib

    inbox = FakeSMTPInbox()
    factory = inbox.fail_next_send()

    message = _FakeEmailMessage(
        {"From": "a@b.com", "To": "c@d.com", "Subject": "s"}, "body"
    )

    raised = False
    try:
        with factory("smtp.fake.test", 587) as smtp:
            smtp.send_message(message)
    except smtplib.SMTPException:
        raised = True

    assert raised, "expected send_message to raise SMTPException"
    assert inbox.sent_messages == []  # the failed send must not appear in the inbox


def test_fake_smtp_each_connection_is_independent():
    # Guards against a factory accidentally sharing mutable state
    # (started_tls, logged_in_as) across what should be separate
    # connections/messages.
    inbox = FakeSMTPInbox()
    factory = FakeSMTPFactory(inbox)

    with factory("smtp.fake.test", 587) as smtp:
        smtp.starttls()

    with factory("smtp.fake.test", 587) as smtp2:
        assert smtp2.started_tls is False


# ---------------------------------------------------------------------------
# Manual runner (no pytest required — see module docstring)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_functions = [
        obj
        for name, obj in list(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    failures = []
    for fn in test_functions:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception as exc:  # noqa: BLE001 — this is a tiny standalone runner
            failures.append((fn.__name__, exc))
            print(f"FAIL  {fn.__name__}: {exc}")

    print(f"\n{len(test_functions) - len(failures)}/{len(test_functions)} passed")
    if failures:
        sys.exit(1)
