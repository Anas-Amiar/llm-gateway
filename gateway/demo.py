"""
The full gateway demo on a simulated clock:

  Scene 1 — normal traffic across two teams
  Scene 2 — a rate-limited team hammers the gateway and gets clean 429s
  Scene 3 — the primary provider goes DOWN: retries, fallback routing,
            circuit opens, traffic flows via the fallback
  Scene 4 — provider recovers: half-open probe, circuit closes
  Scene 5 — a team exhausts its daily budget and gets blocked
"""

from gateway.models import TeamConfig, GatewayRequest
from gateway.gateway import Gateway
from gateway.providers import PROVIDERS

TEAMS = [
    TeamConfig(team_id="search-team", api_key="key-search",
               allowed_tiers=["standard", "premium"],
               requests_per_minute=60, daily_budget_usd=1.00),
    TeamConfig(team_id="batch-team", api_key="key-batch",
               allowed_tiers=["standard"],
               requests_per_minute=5, daily_budget_usd=0.02, priority="batch"),
]


def main() -> None:
    gw = Gateway(TEAMS)
    now = 0.0

    print("=== Scene 1: normal traffic ===")
    for i in range(3):
        r = gw.handle(GatewayRequest(api_key="key-search", prompt=f"question {i}"), now)
        print(f"  [{r.status}] via {r.provider_used}  {r.latency_ms}ms  ${r.cost_usd}")
        now += 1.0

    print("\n=== Scene 2: batch team exceeds 5 req/min ===")
    statuses = []
    for i in range(8):
        r = gw.handle(GatewayRequest(api_key="key-batch", prompt=f"batch {i}"), now)
        statuses.append(r.status)
        now += 0.1
    print(f"  8 rapid requests -> {statuses.count('ok')} ok, "
          f"{statuses.count('rate_limited')} rate-limited (429 + Retry-After)")

    print("\n=== Scene 3: openai goes DOWN ===")
    PROVIDERS["openai"].forced_down = True
    now += 5
    for i in range(4):
        r = gw.handle(GatewayRequest(api_key="key-search", prompt=f"during outage {i}"), now)
        print(f"  [{r.status}] via {r.provider_used}  fallback={r.fallback_used}  "
              f"retries={r.retries}")
        now += 2.0
    print("  Circuit breaker states:")
    for h in gw.health():
        print(f"    {h.provider:14s} {h.state:9s} failures={h.total_failures}/{h.total_calls}")

    print("\n=== Scene 4: openai recovers, circuit re-closes after cooldown ===")
    PROVIDERS["openai"].forced_down = False
    now += 65   # past the 60s cooldown -> half-open probe on next call
    for i in range(2):
        r = gw.handle(GatewayRequest(api_key="key-search", prompt=f"after recovery {i}"), now)
        print(f"  [{r.status}] via {r.provider_used}  fallback={r.fallback_used}")
        now += 1.0
    print("  Breaker log for openai:")
    for line in gw.breakers["openai"].state_log:
        print(f"    {line}")

    print("\n=== Scene 5: batch team exhausts its $0.02 daily budget ===")
    now += 120  # let the rate bucket refill
    while True:
        r = gw.handle(GatewayRequest(api_key="key-batch", prompt="spend it"), now)
        now += 15.0
        if r.status != "ok":
            print(f"  [{r.status}] {r.error_detail}")
            break

    print("\n=== Gateway metrics ===")
    m = gw.metrics
    print(f"  requests={m['requests']}  ok={m['ok']}  rate_limited={m['rate_limited']}  "
          f"budget_blocked={m['budget_blocked']}")
    print(f"  fallbacks={m['fallbacks']}  provider_calls={m['provider_calls']}")
    if m["warnings"]:
        print(f"  budget warnings: {m['warnings'][-1]}")


if __name__ == "__main__":
    main()
