# LLM Gateway — the pitch

*A 2-minute walkthrough for presenting this project in an interview.*

## The 30-second version

"Every company with more than one team using LLMs eventually builds the same thing: a
gateway in front of all LLM calls. I built one with the full production feature set —
per-team API keys and rate limits, daily budget caps that warn at 80% and block at 100%,
retry-then-fallback routing across providers, and circuit breakers that stop wasting
timeouts on a dead provider. My demo plays a complete incident lifecycle in one run: the
primary provider goes down, requests retry then fail over, the circuit opens after 3
failures in 30 seconds, and when the provider recovers, a half-open probe closes the
circuit and traffic returns — all visible in the breaker's state log."

## The problem, in plain terms

Three teams share an OpenAI account. The batch team's backfill job eats the entire rate
limit and the customer-facing chatbot starts throwing errors. Finance discovers the month's
LLM bill doubled and nobody knows which team did it. Then OpenAI has a bad hour, and every
feature in the company goes down with it, because nothing knows how to fail over. Every one
of these is a gateway problem, and every scaled AI org ends up building this exact layer.

## The idea

One choke point, four policies:
1. **Isolation** — per-team token buckets: one team's spike can't starve another.
2. **Cost control** — per-team daily budgets computed from actual per-call costs.
3. **Resilience** — retry the primary briefly, then fail over along a per-tier chain.
4. **Fast failure detection** — circuit breakers so an outage costs one detection window,
   not a timeout per request.

## How I built it (in order, and why that order)

1. **Providers with controllable failure modes** (`gateway/providers.py`) — mock providers
   with a `forced_down` switch. Built first because resilience features you can't trigger
   on demand are features you can't demonstrate or test. Fallback chains are defined per
   *tier*, not per model, so routing policy survives model lineup changes.

2. **The token-bucket rate limiter** (`gateway/ratelimit.py`) — per-team buckets against
   a caller-supplied clock. Same algorithm you'd run in Redis; the demo proves 5 req/min
   means exactly 5 succeed and 3 get clean 429s with Retry-After.

3. **The circuit breaker** (`gateway/circuit.py`) — the classic three-state machine:
   closed → open (3 failures in 30s) → half-open (after 60s cooldown) → closed on probe
   success. Every transition logged with timestamp and reason.

4. **The gateway core** (`gateway/gateway.py`) — the policy pipeline: auth → tier check →
   rate limit → budget → routed call. Retries (2, with backoff) only on the primary;
   fallbacks get one shot each; open circuits are skipped instantly.

5. **The incident demo** (`gateway/demo.py`) — five scenes on a simulated clock: normal
   traffic, rate limiting, the outage with fallback + circuit opening, recovery via
   half-open probe, and a budget exhaustion block.

## The result

- Outage handled with **zero failed user requests**: every request during the openai
  outage was served by anthropic via fallback
- Circuit opened after exactly 3 failures, skipping the dead provider for the rest of
  the outage — no more timeouts burned
- Recovery was automatic: cooldown → probe → circuit closed → traffic back on the primary
- Rate limits enforced exactly (5/8 rapid requests passed, 3 got 429s)
- Budget cap blocked the over-spending team with a clear, actionable error

## What I'd highlight if asked "what was the hardest design decision?"

Where to put retries. The obvious design retries every provider in the chain — but that
multiplies worst-case latency for a request that's probably doomed, and during a real
outage it turns your gateway into a latency amplifier. The right answer: retry only the
primary (where a transient blip is plausible), give fallbacks one shot each, and let the
circuit breaker eliminate even the first attempt once a provider is known-dead. The demo
shows this working: the first request during the outage pays 2 retries; after the circuit
opens, subsequent requests go straight to the fallback with zero retries.

## What I'd build next

- Redis-backed rate limiting for multi-instance deployment
- Streaming passthrough (forward chunks in real time while buffering for logs)
- OpenTelemetry spans + Prometheus metrics + the three Grafana dashboards
  (operations / business / performance)
- Priority scheduling: realtime requests pre-empt batch under contention
- Integration with the semantic cache (Project 8): cache first, gateway on miss

## Companion projects

This gateway is the operational shell around everything else in the portfolio: the
[LLM Cost Autopilot](https://github.com/Anas-Amiar/Project-2-llm-cost-autopilot) decides
*which model* a request deserves; the [semantic cache](https://github.com/Anas-Amiar/Project-8-semantic-cache)
eliminates repeat calls; this gateway enforces *who may call, how often, at what cost, and
what happens when providers fail*. Together they're the LLM serving stack.
