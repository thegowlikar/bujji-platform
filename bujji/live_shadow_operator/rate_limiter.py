"""bujji.live_shadow_operator.rate_limiter — Sprint 112 Deliverable 2.

A reusable, generic rate-limiting wrapper for live FYERS API calls
(`fetch_live_premium`/`fetch_live_atm_premiums`, Sprint 105 follow-up 3;
any future live broker call this operator makes).

Reuses, never duplicates, the REAL retry pattern already proven in
production: `bujji.execution.engine.ExecutionEngine._with_retry`
(confirmed by reading it directly) --
  (a) exponential-style backoff (`delay * attempt`),
  (b) `AuthenticationError` is a PERMANENT failure, re-raised
      immediately on first occurrence, never retried (the exact same
      "credentials are invalid, a human must act" vs. "transient,
      retrying may help" distinction `_with_retry` already makes).
This module adds only what `_with_retry` does NOT have, per this
sprint's own explicit ask: a configurable requests/second throttle,
jitter, and real operational metrics (average wait, retries, dropped
requests). It never touches `_with_retry` itself (frozen, unmodified).
"""
from __future__ import annotations

import asyncio
import logging
import random as _random
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from bujji.broker.errors import AuthenticationError


@dataclass(frozen=True)
class RateLimiterConfig:
    requests_per_second: float = 3.0
    max_retries: int = 3
    base_backoff_seconds: float = 1.5   # matches config.yaml's own retry_backoff_seconds default
    jitter_seconds: float = 0.25
    retry_budget: int = 20              # total retries allowed across the WHOLE session, not per-call

    def __post_init__(self) -> None:
        if self.requests_per_second <= 0:
            raise ValueError("requests_per_second must be > 0")
        if self.max_retries < 0 or self.retry_budget < 0:
            raise ValueError("max_retries/retry_budget must be >= 0")


@dataclass(frozen=True)
class RateLimitedCallResult:
    success: bool
    value: Any
    attempts: int
    retries: int
    wait_seconds: float
    dropped: bool                        # True if the retry budget was exhausted (session-wide)
    permanent_failure: bool              # True if an AuthenticationError short-circuited retries
    error: Optional[str]


@dataclass
class RateLimiterMetrics:
    """Real, session-wide counters -- every field is incremented only
    from an actual call outcome, never estimated."""
    total_calls: int = 0
    total_retries: int = 0
    total_wait_seconds: float = 0.0
    dropped_requests: int = 0
    permanent_failures: int = 0

    @property
    def average_wait_seconds(self) -> float:
        return (self.total_wait_seconds / self.total_calls) if self.total_calls else 0.0


class RateLimitedCaller:
    """Wraps any async broker call with throttling + retry/backoff/
    jitter + permanent-failure detection + real metrics. Never wraps a
    decision function -- this class knows nothing about MSI/trading
    logic, only about calling an async function safely."""

    def __init__(self, config: RateLimiterConfig = RateLimiterConfig(),
                 logger: Optional[logging.Logger] = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 monotonic: Callable[[], float] = time.monotonic,
                 rng: Optional[_random.Random] = None) -> None:
        self._config = config
        self._log = logger or logging.getLogger("bujji.live_shadow_operator.rate_limiter")
        self._sleep = sleep
        self._monotonic = monotonic
        self._rng = rng or _random.Random()
        self._last_call_monotonic: Optional[float] = None
        self.metrics = RateLimiterMetrics()

    async def _throttle(self) -> float:
        min_interval = 1.0 / self._config.requests_per_second
        now = self._monotonic()
        if self._last_call_monotonic is None:
            self._last_call_monotonic = now
            return 0.0
        elapsed = now - self._last_call_monotonic
        wait = max(min_interval - elapsed, 0.0)
        if wait > 0:
            await self._sleep(wait)
        self._last_call_monotonic = self._monotonic()
        return wait

    async def call(self, name: str, fn: Callable[..., Awaitable[Any]], *args, **kwargs) -> RateLimitedCallResult:
        self.metrics.total_calls += 1
        total_wait = await self._throttle()
        retries = 0
        last_exc: Optional[Exception] = None

        for attempt in range(1, self._config.max_retries + 1):
            try:
                value = await fn(*args, **kwargs)
                self.metrics.total_wait_seconds += total_wait
                return RateLimitedCallResult(
                    success=True, value=value, attempts=attempt, retries=retries,
                    wait_seconds=total_wait, dropped=False, permanent_failure=False, error=None,
                )
            except AuthenticationError as exc:
                # Permanent failure -- same fast-fail distinction
                # `ExecutionEngine._with_retry` already makes: never
                # burn retry budget on a credentials problem.
                self.metrics.permanent_failures += 1
                self._log.warning("rate_limited_call_permanent_failure call=%s err=%s", name, exc)
                self.metrics.total_wait_seconds += total_wait
                return RateLimitedCallResult(
                    success=False, value=None, attempts=attempt, retries=retries,
                    wait_seconds=total_wait, dropped=False, permanent_failure=True, error=str(exc),
                )
            except Exception as exc:  # noqa: BLE001 -- transient broker/network faults
                last_exc = exc
                if attempt >= self._config.max_retries or self.metrics.total_retries >= self._config.retry_budget:
                    break
                retries += 1
                self.metrics.total_retries += 1
                backoff = self._config.base_backoff_seconds * attempt
                jitter = self._rng.uniform(0, self._config.jitter_seconds)
                delay = backoff + jitter
                self._log.info("rate_limited_call_retry call=%s attempt=%d delay=%.3f err=%s", name, attempt, delay, exc)
                await self._sleep(delay)
                total_wait += delay

        dropped = last_exc is not None
        if dropped:
            self.metrics.dropped_requests += 1
        self.metrics.total_wait_seconds += total_wait
        return RateLimitedCallResult(
            success=False, value=None, attempts=self._config.max_retries, retries=retries,
            wait_seconds=total_wait, dropped=dropped, permanent_failure=False,
            error=str(last_exc) if last_exc else None,
        )
