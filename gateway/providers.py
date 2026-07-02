"""
Mock providers with CONTROLLABLE failure modes — the demo can take a provider
down and watch the gateway's fallback + circuit breaker respond.

Each tier has a fallback chain across providers, defined per tier (not per
specific model), so the gateway always has somewhere to go.
"""

import random


class MockProvider:
    def __init__(self, name: str, base_latency_ms: float, cost_per_call: float):
        self.name = name
        self.base_latency_ms = base_latency_ms
        self.cost_per_call = cost_per_call
        self.forced_down = False        # demo control: simulate an outage
        self.error_rate = 0.02          # baseline transient errors

    def call(self, prompt: str, rng: random.Random) -> tuple[str, float, float]:
        """Returns (response, latency_ms, cost). Raises on failure."""
        if self.forced_down or rng.random() < self.error_rate:
            raise TimeoutError(f"{self.name}: request timed out")
        latency = max(50.0, rng.gauss(self.base_latency_ms, self.base_latency_ms * 0.2))
        return f"[{self.name}] answer to: {prompt[:40]}", latency, self.cost_per_call


PROVIDERS = {
    "openai": MockProvider("openai", base_latency_ms=800, cost_per_call=0.0030),
    "anthropic": MockProvider("anthropic", base_latency_ms=900, cost_per_call=0.0032),
    "ollama-local": MockProvider("ollama-local", base_latency_ms=1500, cost_per_call=0.0),
}

# Fallback chains per tier: primary first, then fallbacks in order.
TIER_CHAINS = {
    "standard": ["openai", "anthropic", "ollama-local"],
    "premium": ["anthropic", "openai"],
    "local-only": ["ollama-local"],
}
