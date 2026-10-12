"""
Phase 17 (Testing): fakes for every optional external dependency this
codebase talks to — Claude (app/services/ai_client.py), Stripe
(app/services/billing.py), and SMTP (app/services/email.py).

None of these are mocking frameworks bolted onto the real SDK objects;
each is a small, real class that implements just the surface the app code
actually calls, so tests assert against plain attributes instead of
`mock_calls` introspection. See each module's docstring for what it
stands in for and why.
"""
