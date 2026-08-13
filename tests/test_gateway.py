"""
Behavioural tests for the gateway core.

The core takes a caller-supplied clock (`now`), so every time-dependent rule —
rate-limit refill, circuit-breaker window, cooldown — is tested deterministically
by advancing a fake clock, with no sleeps and no real network.
"""

import pytest

from gateway.models import TeamConfig, GatewayRequest
from gateway.gateway import Gateway
from gateway.providers import PROVIDERS
from gateway.circuit import FAILURE_THRESHOLD, COOLDOWN_SECONDS


def make_gateway(**overrides):
    team = TeamConfig(
        team_id="t1", api_key="k1",
        allowed_tiers=overrides.get("allowed_tiers", ["standard", "premium"]),
        requests_per_minute=overrides.get("rpm", 60),
        daily_budget_usd=overrides.get("budget", 100.0),
    )
    # seed=1 keeps the mock providers' baseline error_rate from firing in tests
    return Gateway([team], seed=1)


@pytest.fixture(autouse=True)
def reset_providers():
    """Every test starts with all providers healthy and deterministic.

    The baseline 2% transient error rate is realistic in production but would
    make assertions flaky, so tests drive failure explicitly via `forced_down`.
    """
    saved = [(p, p.forced_down, p.error_rate) for p in PROVIDERS.values()]
    for p in PROVIDERS.values():
        p.forced_down = False
        p.error_rate = 0.0
    yield
    for p, down, err in saved:
        p.forced_down = down
        p.error_rate = err


def test_auth_failure_on_unknown_key():
    gw = make_gateway()
    res = gw.handle(GatewayRequest(api_key="nope", prompt="hi"), now=0.0)
    assert res.status == "auth_failed"


def test_tier_not_allowed():
    gw = make_gateway(allowed_tiers=["standard"])
    res = gw.handle(GatewayRequest(api_key="k1", prompt="hi", tier="premium"), now=0.0)
    assert res.status == "tier_not_allowed"


def test_happy_path_uses_primary():
    gw = make_gateway()
    res = gw.handle(GatewayRequest(api_key="k1", prompt="hi"), now=0.0)
    assert res.status == "ok"
    assert res.provider_used == "openai"      # primary of the standard chain
    assert res.fallback_used is False
    assert res.cost_usd > 0


def test_rate_limit_then_recovers_after_refill():
    gw = make_gateway(rpm=1)                    # 1 request/minute
    ok = gw.handle(GatewayRequest(api_key="k1", prompt="a"), now=0.0)
    assert ok.status == "ok"

    throttled = gw.handle(GatewayRequest(api_key="k1", prompt="b"), now=0.0)
    assert throttled.status == "rate_limited"

    # advance a full minute -> bucket refills one token
    recovered = gw.handle(GatewayRequest(api_key="k1", prompt="c"), now=60.0)
    assert recovered.status == "ok"


def test_budget_block():
    gw = make_gateway(budget=0.0)               # no budget at all
    res = gw.handle(GatewayRequest(api_key="k1", prompt="hi"), now=0.0)
    assert res.status == "budget_exceeded"


def test_fallback_when_primary_down():
    gw = make_gateway()
    PROVIDERS["openai"].forced_down = True      # primary outage
    res = gw.handle(GatewayRequest(api_key="k1", prompt="hi"), now=0.0)
    assert res.status == "ok"
    assert res.provider_used == "anthropic"     # next in the standard chain
    assert res.fallback_used is True
    assert res.retries == 2                      # 2 retries burned on primary first


def test_circuit_opens_after_threshold_failures():
    gw = make_gateway()
    PROVIDERS["openai"].forced_down = True
    # Each request retries the primary twice, so failures accumulate fast.
    gw.handle(GatewayRequest(api_key="k1", prompt="x"), now=0.0)
    breaker = gw.breakers["openai"]
    assert breaker.state == "open"
    assert breaker.total_failures >= FAILURE_THRESHOLD


def test_open_circuit_is_skipped_instantly():
    gw = make_gateway()
    PROVIDERS["openai"].forced_down = True
    gw.handle(GatewayRequest(api_key="k1", prompt="x"), now=0.0)
    assert gw.breakers["openai"].state == "open"

    calls_before = gw.breakers["openai"].total_calls
    res = gw.handle(GatewayRequest(api_key="k1", prompt="y"), now=1.0)
    # openai was skipped (no new call recorded); served by fallback instead.
    assert gw.breakers["openai"].total_calls == calls_before
    assert res.status == "ok"
    assert res.provider_used == "anthropic"


def test_circuit_half_opens_and_closes_after_recovery():
    gw = make_gateway()
    PROVIDERS["openai"].forced_down = True
    gw.handle(GatewayRequest(api_key="k1", prompt="x"), now=0.0)
    assert gw.breakers["openai"].state == "open"

    PROVIDERS["openai"].forced_down = False     # provider recovers
    # after the cooldown, the next call probes (half-open) and should close it
    res = gw.handle(GatewayRequest(api_key="k1", prompt="z"), now=COOLDOWN_SECONDS + 1)
    assert res.status == "ok"
    assert res.provider_used == "openai"
    assert gw.breakers["openai"].state == "closed"
