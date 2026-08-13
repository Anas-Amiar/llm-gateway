"""
HTTP layer for the gateway — turns the pure `Gateway` core into a runnable
service you can curl or point a client at.

    uvicorn gateway.app:app --reload

Everything runs on mock providers by default, so the API needs **no secrets**
and is safe to deploy as a public demo. Swap `MockProvider` for real provider
SDKs in providers.py to make it live.

Gateway status codes are mapped to the HTTP codes a client actually expects:

    ok                -> 200
    rate_limited      -> 429  (+ Retry-After header)
    budget_exceeded   -> 402
    auth_failed       -> 401
    tier_not_allowed  -> 403
    all_providers_down-> 503
"""

import time

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse

from gateway.models import TeamConfig, GatewayRequest
from gateway.gateway import Gateway

# Demo teams. In production these come from a config store / DB, not code.
# Budgets are generous here so the public demo doesn't block after a few calls.
DEMO_TEAMS = [
    TeamConfig(team_id="demo", api_key="demo-key",
               allowed_tiers=["standard", "premium", "local-only"],
               requests_per_minute=30, daily_budget_usd=100.0),
    TeamConfig(team_id="batch", api_key="batch-key",
               allowed_tiers=["standard"],
               requests_per_minute=5, daily_budget_usd=1.00, priority="batch"),
]

STATUS_TO_HTTP = {
    "ok": 200,
    "rate_limited": 429,
    "budget_exceeded": 402,
    "auth_failed": 401,
    "tier_not_allowed": 403,
    "all_providers_down": 503,
}

app = FastAPI(
    title="LLM Gateway",
    version="1.0.0",
    description="Multi-provider LLM gateway: per-team rate limits, budget caps, "
                "retry-then-fallback routing, and circuit breakers.",
)

gateway = Gateway(DEMO_TEAMS)


def _clock() -> float:
    """Monotonic wall clock for rate-limit / circuit-breaker windows."""
    return time.monotonic()


@app.get("/")
def root() -> dict:
    return {
        "service": "llm-gateway",
        "docs": "/docs",
        "try": "POST /v1/chat with {\"api_key\": \"demo-key\", \"prompt\": \"hi\", \"tier\": \"standard\"}",
        "health": "/health",
        "metrics": "/metrics",
    }


@app.post("/v1/chat")
def chat(req: GatewayRequest) -> Response:
    result = gateway.handle(req, _clock())
    http_status = STATUS_TO_HTTP.get(result.status, 500)
    headers = {}
    if result.status == "rate_limited":
        # Surface a real Retry-After header, not just a message body.
        headers["Retry-After"] = str(_retry_after_seconds(req))
    return JSONResponse(status_code=http_status, content=result.model_dump(), headers=headers)


def _retry_after_seconds(req: GatewayRequest) -> int:
    """Best-effort Retry-After for a throttled team; falls back to 1s."""
    team = gateway.teams_by_key.get(req.api_key)
    if team is None:
        return 1
    bucket = gateway.limiter._buckets.get(team.team_id)
    if bucket is None:
        return 1
    return max(1, int(round(bucket.retry_after_seconds())))


@app.get("/health")
def health() -> dict:
    """Per-provider circuit-breaker state — the liveness a dashboard would poll."""
    return {
        "status": "ok",
        "providers": [h.model_dump() for h in gateway.health()],
    }


@app.get("/metrics")
def metrics() -> dict:
    """Aggregate counters: requests, oks, rate-limits, fallbacks, per-provider calls."""
    return gateway.metrics
