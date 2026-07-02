"""
The gateway core: auth -> tier check -> rate limit -> budget -> routed call
with retry + fallback + circuit breakers -> metrics.

Retry policy: up to 2 retries on the primary with (simulated) exponential
backoff, then fall back to the next provider in the tier's chain.  Circuit
breakers sit in front of every provider; an open circuit skips that provider
instantly instead of burning a timeout on it.
"""

import random

from gateway.models import TeamConfig, GatewayRequest, GatewayResponse, ProviderHealth
from gateway.providers import PROVIDERS, TIER_CHAINS
from gateway.ratelimit import RateLimiter
from gateway.circuit import CircuitBreaker

MAX_RETRIES = 2
BUDGET_WARN_FRACTION = 0.8


class Gateway:
    def __init__(self, teams: list[TeamConfig], seed: int = 3):
        self.teams_by_key = {t.api_key: t for t in teams}
        self.limiter = RateLimiter()
        self.breakers = {name: CircuitBreaker(name) for name in PROVIDERS}
        self.spend: dict[str, float] = {t.team_id: 0.0 for t in teams}
        self.rng = random.Random(seed)
        self.metrics = {"requests": 0, "ok": 0, "rate_limited": 0,
                        "budget_blocked": 0, "fallbacks": 0, "retries": 0,
                        "provider_calls": {}, "warnings": []}

    def handle(self, req: GatewayRequest, now: float) -> GatewayResponse:
        self.metrics["requests"] += 1

        team = self.teams_by_key.get(req.api_key)
        if team is None:
            return GatewayResponse(status="auth_failed", error_detail="unknown API key")

        if req.tier not in team.allowed_tiers:
            return GatewayResponse(status="tier_not_allowed",
                                   error_detail=f"team {team.team_id} cannot use tier '{req.tier}'")

        ok, retry_after = self.limiter.allow(team.team_id, team.requests_per_minute, now)
        if not ok:
            self.metrics["rate_limited"] += 1
            return GatewayResponse(status="rate_limited",
                                   error_detail=f"429 — Retry-After: {retry_after}s")

        if self.spend[team.team_id] >= team.daily_budget_usd:
            self.metrics["budget_blocked"] += 1
            return GatewayResponse(
                status="budget_exceeded",
                error_detail=f"daily budget ${team.daily_budget_usd} exhausted "
                             f"(spent ${self.spend[team.team_id]:.4f})")

        # Routed call with retry -> fallback -> circuit breakers
        chain = TIER_CHAINS[req.tier]
        fallback_used = False
        total_retries = 0

        for idx, provider_name in enumerate(chain):
            breaker = self.breakers[provider_name]
            if not breaker.can_call(now):
                continue    # circuit open — skip instantly, no timeout burned

            provider = PROVIDERS[provider_name]
            attempts = 1 + (MAX_RETRIES if idx == 0 else 0)  # retries only on primary
            for attempt in range(attempts):
                try:
                    text, latency, cost = provider.call(req.prompt, self.rng)
                    breaker.record_success(now)
                    self.spend[team.team_id] += cost
                    self.metrics["ok"] += 1
                    self.metrics["retries"] += total_retries
                    if idx > 0:
                        self.metrics["fallbacks"] += 1
                        fallback_used = True
                    pc = self.metrics["provider_calls"]
                    pc[provider_name] = pc.get(provider_name, 0) + 1
                    if self.spend[team.team_id] >= BUDGET_WARN_FRACTION * team.daily_budget_usd:
                        self.metrics["warnings"].append(
                            f"team {team.team_id} at "
                            f"{self.spend[team.team_id]/team.daily_budget_usd:.0%} of budget")
                    return GatewayResponse(
                        status="ok", response_text=text, provider_used=provider_name,
                        fallback_used=fallback_used, retries=total_retries,
                        latency_ms=round(latency, 1), cost_usd=cost)
                except TimeoutError:
                    breaker.record_failure(now)
                    if attempt < attempts - 1:
                        total_retries += 1     # simulated backoff before the retry

        return GatewayResponse(status="all_providers_down",
                               error_detail="every provider in the chain failed or has an open circuit")

    def health(self) -> list[ProviderHealth]:
        return [ProviderHealth(provider=b.provider, state=b.state,
                               recent_failures=len(b.failure_times),
                               total_calls=b.total_calls, total_failures=b.total_failures)
                for b in self.breakers.values()]
