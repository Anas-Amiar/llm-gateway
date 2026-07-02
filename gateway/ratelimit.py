"""
Token-bucket rate limiter, one bucket per team, against a caller-supplied
clock (simulated in demos/tests).  In production this lives in Redis for
distributed atomicity; the algorithm is identical.
"""


class TokenBucket:
    def __init__(self, rate_per_minute: int):
        self.capacity = float(rate_per_minute)
        self.tokens = float(rate_per_minute)
        self.rate_per_sec = rate_per_minute / 60.0
        self.last_refill = 0.0

    def allow(self, now: float) -> bool:
        # Refill based on elapsed simulated time
        elapsed = max(0.0, now - self.last_refill)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate_per_sec)
        self.last_refill = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False

    def retry_after_seconds(self) -> float:
        return round((1.0 - self.tokens) / self.rate_per_sec, 2)


class RateLimiter:
    def __init__(self):
        self._buckets: dict[str, TokenBucket] = {}

    def allow(self, team_id: str, rate_per_minute: int, now: float) -> tuple[bool, float]:
        bucket = self._buckets.setdefault(team_id, TokenBucket(rate_per_minute))
        ok = bucket.allow(now)
        return ok, 0.0 if ok else bucket.retry_after_seconds()
