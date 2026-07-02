from pydantic import BaseModel
from typing import Literal, Optional


class TeamConfig(BaseModel):
    team_id: str
    api_key: str
    allowed_tiers: list[str]            # e.g. ["standard", "premium"]
    requests_per_minute: int
    daily_budget_usd: float
    priority: Literal["realtime", "batch"] = "realtime"


class GatewayRequest(BaseModel):
    api_key: str
    prompt: str
    tier: str = "standard"              # model tier, not a specific provider


class GatewayResponse(BaseModel):
    status: Literal["ok", "rate_limited", "budget_exceeded", "auth_failed",
                    "all_providers_down", "tier_not_allowed"]
    response_text: Optional[str] = None
    provider_used: Optional[str] = None
    fallback_used: bool = False
    retries: int = 0
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    error_detail: Optional[str] = None


class ProviderHealth(BaseModel):
    provider: str
    state: Literal["closed", "open", "half_open"]   # circuit breaker state
    recent_failures: int
    total_calls: int
    total_failures: int
