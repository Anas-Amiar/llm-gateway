# LLM Gateway with Rate Limiting, Fallback Routing, and Circuit Breakers

[![CI](https://github.com/Anas-Amiar/llm-gateway/actions/workflows/ci.yml/badge.svg)](https://github.com/Anas-Amiar/llm-gateway/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A production-style API gateway that sits in front of all LLM calls: per-team API keys,
token-bucket rate limits, daily budget enforcement, retry-then-fallback routing across
providers, and circuit breakers that skip a failing provider instantly instead of burning
timeouts on it.

Ships as a real HTTP service (FastAPI) **and** a pure, unit-tested core. Runs on mock
providers by default, so it needs **no API keys** — clone it and the server (or the
one-click deploy) is live immediately.

The demo plays the full incident lifecycle in one run:

```
Scene 1  normal traffic         -> served by the primary (openai)
Scene 2  team exceeds 5 req/min -> clean 429s with Retry-After
Scene 3  openai goes DOWN       -> 2 retries, then fallback to anthropic;
                                   circuit opens after 3 failures in 30s
Scene 4  openai recovers        -> cooldown -> half-open probe -> circuit closes
Scene 5  budget exhausted       -> requests blocked with a clear error
```

Circuit breaker log from the actual run:
```
t=  8.8s  openai: closed -> open       (3 failures in 30s)
t= 81.8s  openai: open -> half_open    (cooldown elapsed, probing)
t= 81.8s  openai: half_open -> closed  (probe succeeded)
```

## Why this exists

Every company with more than one team using LLMs ends up building this: teams need
isolation (one team's traffic spike shouldn't starve another), finance needs budget caps,
and reliability needs multi-provider failover that doesn't require anyone to be paged.
This is pure infrastructure engineering applied to AI.

## How it works

```
gateway/
  models.py     Typed shapes: TeamConfig, GatewayRequest/Response, ProviderHealth
  providers.py  Mock providers with CONTROLLABLE failure modes (forced_down flag)
                + fallback chains defined per tier, not per model
  ratelimit.py  Token-bucket limiter, one bucket per team, caller-supplied clock
                (production: same algorithm in Redis for distributed atomicity)
  circuit.py    Circuit breaker per provider: closed -> open (3 failures/30s)
                -> half-open (after 60s cooldown) -> closed on probe success.
                Every state change logged.
  gateway.py    The core: auth -> tier check -> rate limit -> budget -> routed
                call (retry x2 on primary, then fallback chain, breakers skip
                dead providers) -> metrics
  app.py        FastAPI HTTP layer: POST /v1/chat, GET /health, GET /metrics;
                maps gateway statuses to HTTP codes (429 + Retry-After, etc.)
  demo.py       The 5-scene incident lifecycle on a simulated clock
tests/          Deterministic pytest suite driving the fake clock (no network)
```

### Request lifecycle

```
handle(request, now)
  1. auth: API key -> TeamConfig (allowed tiers, limits, budget)
  2. tier check: team allowed to use this model tier?
  3. rate limit: token bucket per team; 429 + Retry-After when empty
  4. budget: daily spend cap; warn at 80%, block at 100%
  5. route: for each provider in the tier's fallback chain:
       - circuit open? skip instantly (no timeout burned)
       - primary gets up to 2 retries with backoff; fallbacks get 1 shot
       - success -> record cost, metrics, return
  6. all failed -> "all_providers_down" with detail
```

## Quickstart

```bash
git clone https://github.com/Anas-Amiar/llm-gateway.git
cd llm-gateway
pip install -r requirements.txt

python3 -m gateway.demo   # the full 5-scene incident lifecycle on a simulated clock
```

## Run the API

```bash
uvicorn gateway.app:app --reload      # http://localhost:8000  (interactive docs at /docs)
```

```bash
# a normal request — routed to the cheapest capable provider in the tier
curl -s -X POST localhost:8000/v1/chat \
  -H 'content-type: application/json' \
  -d '{"api_key":"demo-key","prompt":"summarize this","tier":"standard"}'
# -> {"status":"ok","provider_used":"openai","cost_usd":0.003, ...}
```

`batch-key` is capped at 5 req/min — fire it in a loop and you get clean `429`s with a real
`Retry-After` header. `GET /health` returns per-provider circuit-breaker state; `GET /metrics`
returns the aggregate counters.

## Deploy your own

Runs on mock providers with no secrets, so a public demo is one click:

- **Render** — New → Blueprint → point at this repo (`render.yaml` included, free tier).
- **Docker** — `docker build -t llm-gateway . && docker run -p 8000:8000 llm-gateway`

To make it live, swap `MockProvider` in `providers.py` for real provider SDK calls — the
interface (`call(prompt) -> (text, latency, cost)`) stays the same.

## Testing

```bash
pip install -r requirements-dev.txt
pytest -q        # 15 tests, deterministic, no network
```

The core takes a caller-supplied clock, so rate-limit refill, circuit-breaker windows, and
cooldowns are all asserted by advancing a fake clock — the suite runs in well under a second
and CI runs it on Python 3.10–3.12.

## Architecture decisions

**Why circuit breakers when you already have fallbacks?**
Fallbacks answer "where else can this request go?" Circuit breakers answer "why are we
still paying a timeout to ask a dead provider?" Without a breaker, every request during an
outage burns a full timeout on the primary before falling back — with one, the gateway
skips the dead provider instantly and probes it once per cooldown instead.

**Why fallback chains per tier instead of per model?**
"GPT-4o is down, use Claude Sonnet" hardcodes today's model lineup into your routing.
Chains per tier ("premium" → [anthropic, openai]) mean the policy survives model renames
and lineup changes — the same reasoning as the route map in the LLM Cost Autopilot.

**Why retries only on the primary?**
The fallback is already the recovery path. Retrying every provider in the chain multiplies
worst-case latency for a request that's probably doomed. Retry where recovery is likely
(transient blip on the healthy primary), fail fast everywhere else.

**Why a simulated clock?**
Rate-limit refills, breaker cooldowns, and budget windows are all time-based. A
caller-supplied `now` makes every one of them testable in milliseconds — the demo plays
an ~4-minute incident timeline instantly.

## What's deliberately out of scope for v1

- Real provider SDKs behind the provider abstraction (the interface is in place)
- Redis-backed distributed rate limiting (same algorithm, different store)
- Streaming passthrough with simultaneous logging
- OpenTelemetry traces + Prometheus/Grafana (metrics are collected; export is plumbing)
- Priority queues for realtime-vs-batch scheduling under contention
- The admin API for no-restart config changes
