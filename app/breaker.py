"""AgentQon — circuit breakers (gap-security).

When a provider or a tool fails repeatedly, the harness stops sending
it requests for a timed window, then runs ONE recovery probe before
resuming. A breaker that trips is not a crash: it is the harness
choosing not to waste the user's time (and money) on a failing
endpoint.

States: closed (working) → open (resting after failures) →
half-open (one probe allowed) → closed (probe worked) or open again.
Thresholds, window and cooldown are configurable per breaker; the
registry holds one breaker per provider/tool key. Breaker state is
surfaced in plain words for the mission minutes/usage surfaces.
"""
from __future__ import annotations

import threading
import time

DEFAULT_FAILURE_THRESHOLD = 5
DEFAULT_WINDOW_SECONDS = 60.0
DEFAULT_COOLDOWN_SECONDS = 120.0


class CircuitBreaker:
    def __init__(self, key: str, failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
                 window_seconds: float = DEFAULT_WINDOW_SECONDS,
                 cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS):
        self.key = key
        self.failure_threshold = failure_threshold
        self.window_seconds = window_seconds
        self.cooldown_seconds = cooldown_seconds
        self._failures: list[float] = []
        self._open_until: float | None = None
        self._probe_in_flight = False
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            if self._open_until is None:
                return "closed"
            if time.time() >= self._open_until:
                return "half_open"
            return "open"

    def allow(self) -> bool:
        """True when a request may be sent (closed, or one probe in
        half-open). A second concurrent probe is not allowed."""
        with self._lock:
            if self._open_until is None:
                return True
            if time.time() < self._open_until:
                return False
            if self._probe_in_flight:
                return False
            self._probe_in_flight = True
            return True

    def record_success(self) -> None:
        with self._lock:
            self._failures.clear()
            self._open_until = None
            self._probe_in_flight = False

    def record_failure(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            self._failures = [t for t in self._failures
                              if (now - t) <= self.window_seconds]
            self._failures.append(now)
            self._probe_in_flight = False
            if len(self._failures) >= self.failure_threshold:
                self._open_until = now + self.cooldown_seconds

    def seconds_until_retry(self, now: float | None = None) -> float:
        now = time.time() if now is None else now
        with self._lock:
            if self._open_until is None:
                return 0.0
            return max(0.0, self._open_until - now)

    def plain_words(self) -> str:
        state = self.state
        if state == "closed":
            return f"{self.key} is working normally"
        if state == "open":
            wait = int(self.seconds_until_retry())
            return (f"{self.key} failed repeatedly, so it is resting — "
                    f"no new requests for about {wait} seconds, then a "
                    f"single test request will check whether it is back")
        return (f"{self.key} is being tested with a single request to "
                f"see whether it has recovered")


class BreakerRegistry:
    """One breaker per provider/tool key, with shared defaults the
    operator can tune."""

    def __init__(self, failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
                 window_seconds: float = DEFAULT_WINDOW_SECONDS,
                 cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS):
        self.failure_threshold = failure_threshold
        self.window_seconds = window_seconds
        self.cooldown_seconds = cooldown_seconds
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> CircuitBreaker:
        with self._lock:
            breaker = self._breakers.get(key)
            if breaker is None:
                breaker = CircuitBreaker(
                    key, self.failure_threshold, self.window_seconds,
                    self.cooldown_seconds)
                self._breakers[key] = breaker
            return breaker

    def surface(self) -> list[dict]:
        """Plain-words breaker state for minutes/usage surfaces."""
        with self._lock:
            breakers = list(self._breakers.values())
        return [{"key": b.key, "state": b.state, "plain": b.plain_words()}
                for b in breakers]


# Process-wide registry used by the usage surface. Tests build their
# own registries; production code shares this one.
REGISTRY = BreakerRegistry()
