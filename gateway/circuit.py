"""
Circuit breaker per provider: closed -> open after N failures in a window;
open -> half-open after a cooldown; half-open -> closed on one success, back
to open on failure.  Every state change is logged.
"""

FAILURE_THRESHOLD = 3      # failures within the window to open the circuit
WINDOW_SECONDS = 30.0
COOLDOWN_SECONDS = 60.0


class CircuitBreaker:
    def __init__(self, provider: str):
        self.provider = provider
        self.state = "closed"
        self.failure_times: list[float] = []
        self.opened_at = 0.0
        self.total_calls = 0
        self.total_failures = 0
        self.state_log: list[str] = []

    def _transition(self, new_state: str, now: float, reason: str) -> None:
        if new_state != self.state:
            self.state_log.append(
                f"t={now:6.1f}s  {self.provider}: {self.state} -> {new_state}  ({reason})")
            self.state = new_state

    def can_call(self, now: float) -> bool:
        if self.state == "open":
            if now - self.opened_at >= COOLDOWN_SECONDS:
                self._transition("half_open", now, "cooldown elapsed, probing")
                return True     # allow ONE probe request
            return False
        return True             # closed or half_open

    def record_success(self, now: float) -> None:
        self.total_calls += 1
        if self.state == "half_open":
            self._transition("closed", now, "probe succeeded")
        self.failure_times = []

    def record_failure(self, now: float) -> None:
        self.total_calls += 1
        self.total_failures += 1
        if self.state == "half_open":
            self.opened_at = now
            self._transition("open", now, "probe failed")
            return
        self.failure_times = [t for t in self.failure_times if now - t < WINDOW_SECONDS]
        self.failure_times.append(now)
        if len(self.failure_times) >= FAILURE_THRESHOLD:
            self.opened_at = now
            self._transition("open", now,
                             f"{len(self.failure_times)} failures in {WINDOW_SECONDS:.0f}s")
