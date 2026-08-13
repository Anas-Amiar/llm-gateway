"""
HTTP-layer tests: gateway statuses map to the right HTTP codes and the
Retry-After header is present when a team is throttled.
"""

from fastapi.testclient import TestClient

from gateway.app import app, gateway
from gateway.providers import PROVIDERS

client = TestClient(app)


def setup_function():
    for p in PROVIDERS.values():
        p.forced_down = False
        p.error_rate = 0.0


def test_root_ok():
    assert client.get("/").status_code == 200


def test_chat_happy_path():
    r = client.post("/v1/chat", json={"api_key": "demo-key", "prompt": "hello", "tier": "standard"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["provider_used"] == "openai"


def test_unknown_key_is_401():
    r = client.post("/v1/chat", json={"api_key": "bad", "prompt": "hi", "tier": "standard"})
    assert r.status_code == 401


def test_tier_not_allowed_is_403():
    r = client.post("/v1/chat", json={"api_key": "batch-key", "prompt": "hi", "tier": "premium"})
    assert r.status_code == 403


def test_rate_limit_is_429_with_retry_after():
    # batch-key is capped at 5 req/min; hammer it until throttled.
    got_429 = False
    for _ in range(12):
        r = client.post("/v1/chat", json={"api_key": "batch-key", "prompt": "x", "tier": "standard"})
        if r.status_code == 429:
            got_429 = True
            assert "Retry-After" in r.headers
            break
    assert got_429


def test_health_and_metrics():
    assert client.get("/health").status_code == 200
    assert client.get("/metrics").status_code == 200
