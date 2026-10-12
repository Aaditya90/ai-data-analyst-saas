"""
Fake for `anthropic.Anthropic`, standing in for the one call site all AI
features go through: `app.services.ai_client.complete()`, which does

    client = anthropic.Anthropic(api_key=...)
    response = client.messages.create(model=..., max_tokens=..., system=..., messages=...)
    text = "".join(b.text for b in response.content if b.type == "text")

`FakeAnthropicClient` implements exactly that surface (a `.messages` object
with `.create(...)`) so `ai_client.complete()` runs completely unmodified
against it — the only thing tests swap is `anthropic.Anthropic` itself,
via `monkeypatch.setattr("app.services.ai_client.anthropic.Anthropic", ...)`.

Two ways to script a response:
  - `FakeAnthropicClient(responses=["...", "..."])` — a fixed queue,
    popped in order, one per `.messages.create()` call; useful when a
    single test drives multiple AI calls (e.g. NL→SQL, then insight
    narration) and each needs a different canned reply.
  - `FakeAnthropicClient(responder=lambda system, messages: "...")` — a
    callable that inspects the prompt and decides what to answer;
    useful for a test that wants the fake to behave differently
    depending on what was asked (e.g. return invalid SQL once, to prove
    `sql_guard.py` rejects it rather than the AI being relied on to
    never misbehave — the "AI decides, code validates" split this
    codebase is built around since Phase 7).
"""

from types import SimpleNamespace


def make_fake_message(text: str, *, input_tokens: int = 42, output_tokens: int = 7):
    """Builds an object shaped like `anthropic.types.Message` — just deep
    enough for `ai_client._extract_text()` and `_trace_generation()` to
    work against it (`.content` blocks, `.usage`)."""
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


class _FakeMessages:
    def __init__(self, client: "FakeAnthropicClient"):
        self._client = client

    def create(self, *, model: str, max_tokens: int, system: str, messages: list[dict]):
        self._client.calls.append(
            {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages}
        )
        if self._client.raise_on_call is not None:
            raise self._client.raise_on_call

        if self._client.responder is not None:
            text = self._client.responder(system=system, messages=messages)
        elif self._client.responses:
            text = self._client.responses.pop(0)
        else:
            text = self._client.default_response
        return make_fake_message(text)


class FakeAnthropicClient:
    """Drop-in for `anthropic.Anthropic(api_key=...)` in tests."""

    def __init__(
        self,
        *,
        responses: list[str] | None = None,
        responder=None,
        default_response: str = "",
        raise_on_call: Exception | None = None,
        **_ignored_init_kwargs,
    ):
        # `**_ignored_init_kwargs` swallows `api_key=...` — `ai_client.py`
        # always passes it positionally-as-kwarg; the fake doesn't need
        # a real key, but must accept the argument without TypeError-ing.
        self.responses = list(responses) if responses else []
        self.responder = responder
        self.default_response = default_response
        self.raise_on_call = raise_on_call
        self.calls: list[dict] = []
        self.messages = _FakeMessages(self)

    def __call__(self, *, api_key: str | None = None):
        # `ai_client.complete()` does `anthropic.Anthropic(api_key=...)`
        # each call rather than holding a client instance — so the thing
        # monkeypatched in for `anthropic.Anthropic` needs to be
        # *callable* and return something with `.messages.create`. Making
        # the fake instance itself callable (returning itself) means one
        # `FakeAnthropicClient()` can be constructed in a fixture, handed
        # to `monkeypatch.setattr(..., lambda **kw: fake)` — see
        # conftest.py's `fake_ai_client` fixture for the actual wiring.
        return self
