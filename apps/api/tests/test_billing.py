"""
Standalone unit tests for Phase 14 (Billing).

Covers the pure-Python pieces: plan limit lookup/fallback, the
under-limit check, and `plan_for_price_id`'s mapping (including the
"blank config values shouldn't collide" edge case). Anything that touches
the DB (seats_in_use_for_workspace, workspaces_in_use_for_org) or Stripe
itself (checkout, portal, webhook signature verification) is exercised
manually against a running stack + the Stripe CLI — see README.md
"Testing Phase 14".

Run with: python tests/test_billing.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.services.plan_limits import PLAN_LIMITS, get_plan_limits, is_under_limit


def test_free_plan_limits():
    limits = get_plan_limits("free")
    assert limits["max_workspaces"] == 1
    assert limits["max_seats_per_workspace"] == 3


def test_enterprise_plan_is_unlimited():
    limits = get_plan_limits("enterprise")
    assert limits["max_workspaces"] is None
    assert limits["max_seats_per_workspace"] is None


def test_unknown_plan_falls_back_to_free_not_unlimited():
    # The safe default on a bad/legacy plan string is the *most*
    # restrictive limits, not the most permissive.
    limits = get_plan_limits("some_removed_legacy_plan")
    assert limits == PLAN_LIMITS["free"]


def test_is_under_limit_with_finite_limit():
    assert is_under_limit(2, 3) is True
    assert is_under_limit(3, 3) is False
    assert is_under_limit(4, 3) is False


def test_is_under_limit_with_unlimited():
    assert is_under_limit(0, None) is True
    assert is_under_limit(10_000, None) is True


def test_plan_for_price_id_maps_configured_prices():
    get_settings.cache_clear()
    os.environ["STRIPE_PRICE_ID_PRO"] = "price_pro_123"
    os.environ["STRIPE_PRICE_ID_ENTERPRISE"] = "price_ent_456"
    get_settings.cache_clear()

    from app.services.billing import plan_for_price_id

    assert plan_for_price_id("price_pro_123") == "pro"
    assert plan_for_price_id("price_ent_456") == "enterprise"
    assert plan_for_price_id("price_unrelated") is None

    del os.environ["STRIPE_PRICE_ID_PRO"]
    del os.environ["STRIPE_PRICE_ID_ENTERPRISE"]
    get_settings.cache_clear()


def test_plan_for_price_id_blank_config_does_not_match_blank_lookup():
    # Regression guard: if both price ids are unset ("") and something
    # (incorrectly) looked up plan_for_price_id(""), it must not resolve
    # to a plan — the blank-value collision this mapping explicitly guards
    # against.
    get_settings.cache_clear()
    os.environ["STRIPE_PRICE_ID_PRO"] = ""
    os.environ["STRIPE_PRICE_ID_ENTERPRISE"] = ""
    get_settings.cache_clear()

    from app.services.billing import plan_for_price_id

    assert plan_for_price_id("") is None

    del os.environ["STRIPE_PRICE_ID_PRO"]
    del os.environ["STRIPE_PRICE_ID_ENTERPRISE"]
    get_settings.cache_clear()


if __name__ == "__main__":
    test_free_plan_limits()
    test_enterprise_plan_is_unlimited()
    test_unknown_plan_falls_back_to_free_not_unlimited()
    test_is_under_limit_with_finite_limit()
    test_is_under_limit_with_unlimited()
    test_plan_for_price_id_maps_configured_prices()
    test_plan_for_price_id_blank_config_does_not_match_blank_lookup()
    print("All Phase 14 unit tests passed.")
