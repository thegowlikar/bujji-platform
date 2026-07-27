"""Runtime Rate Limiter — BUJJI Options OS v3, Engineering Series 57.

Admission control only: given a Series 56 `CircuitDecision` and a
configured minimum interval, decide whether new work may be admitted
*right now*. It never retries, dispatches, authenticates, recovers, or
changes runtime state, and it never queries broker or market state.

Design note, consistent with `health.py` (Series 55) and
`circuit_breaker.py` (Series 56): this module is stateless. It does
not track "the last admission time" as hidden internal state -- the
caller supplies `previous_admission_timestamp` explicitly on every
call. This satisfies the specification's own "no hidden state"
traceability requirement literally, and keeps `RuntimeRateLimiter`
itself a pure function of its inputs, exactly like
`RuntimeHealthAggregator.snapshot()` and
`RuntimeCircuitBreaker.evaluate()` before it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from . import runtime as runtime_module
from .circuit_breaker import CIRCUIT_CLOSED, CircuitDecision

RATE_LIMIT_PERMITTED = "PERMITTED"
RATE_LIMIT_RATE_LIMITED = "RATE_LIMITED"
RATE_LIMIT_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

ALL_RATE_LIMIT_STATES = (RATE_LIMIT_PERMITTED, RATE_LIMIT_RATE_LIMITED, RATE_LIMIT_INSUFFICIENT_DATA)

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now()


@dataclass(frozen=True)
class RateLimiterConfig:
    """Immutable after construction. `min_interval_seconds` is the
    only decision-relevant field; `mode`/`qualification_mode` are
    carried through purely for traceability in `RateLimitDecision.reason`,
    exactly as `CircuitDecision` already does for `runtime_mode`/
    `qualification_mode`.
    """

    min_interval_seconds: float = 1.0
    mode: Optional[str] = None
    qualification_mode: Optional[str] = None

    def __post_init__(self) -> None:
        if self.min_interval_seconds < 0:
            raise ValueError(f"min_interval_seconds must be >= 0, got {self.min_interval_seconds!r}")


@dataclass(frozen=True)
class RateLimitDecision:
    """A single, immutable, deterministic decision, fully traceable
    back to the `CircuitDecision` it was derived from and the two
    timestamps that produced it.
    """

    state: str
    permitted: bool
    circuit_decision: Optional[CircuitDecision]
    timestamp: str
    previous_admission_timestamp: Optional[str]
    configured_interval_seconds: Optional[float]
    elapsed_seconds: Optional[float]
    reason: str


@dataclass(frozen=True)
class RuntimeRateLimiter:
    """Read-only, side-effect-free. `evaluate()` performs only
    arithmetic on timestamps it is given (or reads from its own
    injected clock) -- it never calls `connect()`, `authenticate()`,
    `dispatch()`, or `recover()` on anything, and it inspects no
    broker or market state.
    """

    config: Optional[RateLimiterConfig] = None
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("bujji.production_runtime.rate_limiter"))
    clock: Optional[Clock] = _real_clock

    def evaluate(
        self,
        circuit_decision: Optional[CircuitDecision],
        previous_admission_timestamp: Optional[datetime] = None,
    ) -> RateLimitDecision:
        if self.config is None or self.clock is None:
            return self._missing_configuration(circuit_decision, previous_admission_timestamp)

        timestamp_dt = self.clock()
        timestamp = timestamp_dt.isoformat()

        if circuit_decision is None or circuit_decision.state != CIRCUIT_CLOSED:
            circuit_state = circuit_decision.state if circuit_decision is not None else "UNKNOWN"
            decision = RateLimitDecision(
                state=RATE_LIMIT_RATE_LIMITED,
                permitted=False,
                circuit_decision=circuit_decision,
                timestamp=timestamp,
                previous_admission_timestamp=(
                    previous_admission_timestamp.isoformat() if previous_admission_timestamp else None
                ),
                configured_interval_seconds=self.config.min_interval_seconds,
                elapsed_seconds=None,
                reason=f"Circuit is {circuit_state}, not CLOSED; rate limiter never admits new work in that case.",
            )
            self._log(decision)
            return decision

        if previous_admission_timestamp is None:
            decision = RateLimitDecision(
                state=RATE_LIMIT_PERMITTED,
                permitted=True,
                circuit_decision=circuit_decision,
                timestamp=timestamp,
                previous_admission_timestamp=None,
                configured_interval_seconds=self.config.min_interval_seconds,
                elapsed_seconds=None,
                reason="No previous admission is on record; nothing to rate-limit against.",
            )
            self._log(decision)
            return decision

        elapsed_seconds = (timestamp_dt - previous_admission_timestamp).total_seconds()
        interval_satisfied = elapsed_seconds >= self.config.min_interval_seconds

        decision = RateLimitDecision(
            state=RATE_LIMIT_PERMITTED if interval_satisfied else RATE_LIMIT_RATE_LIMITED,
            permitted=interval_satisfied,
            circuit_decision=circuit_decision,
            timestamp=timestamp,
            previous_admission_timestamp=previous_admission_timestamp.isoformat(),
            configured_interval_seconds=self.config.min_interval_seconds,
            elapsed_seconds=elapsed_seconds,
            reason=(
                f"Elapsed {elapsed_seconds:.3f}s since last admission "
                f"{'>=' if interval_satisfied else '<'} configured minimum interval "
                f"{self.config.min_interval_seconds:.3f}s."
            ),
        )
        self._log(decision)
        return decision

    def _missing_configuration(
        self,
        circuit_decision: Optional[CircuitDecision],
        previous_admission_timestamp: Optional[datetime],
    ) -> RateLimitDecision:
        # A missing clock or configuration means we cannot even
        # produce a trustworthy timestamp -- fabricating one would
        # violate the "no fabricated evidence" discipline used
        # throughout this project, so `timestamp` itself is left
        # unset (empty string) rather than guessed.
        decision = RateLimitDecision(
            state=RATE_LIMIT_INSUFFICIENT_DATA,
            permitted=False,
            circuit_decision=circuit_decision,
            timestamp="",
            previous_admission_timestamp=(
                previous_admission_timestamp.isoformat() if previous_admission_timestamp else None
            ),
            configured_interval_seconds=self.config.min_interval_seconds if self.config else None,
            elapsed_seconds=None,
            reason="Missing rate limiter configuration or clock; insufficient data to decide safely.",
        )
        self._log(decision)
        return decision

    def _log(self, decision: RateLimitDecision) -> None:
        self.logger.info(
            "RateLimitDecision state=%s permitted=%s elapsed=%s interval=%s reason=%s timestamp=%s",
            decision.state,
            decision.permitted,
            decision.elapsed_seconds,
            decision.configured_interval_seconds,
            decision.reason,
            decision.timestamp,
        )


def guarded_run_read_only(
    circuit_decision: CircuitDecision,
    limiter: RuntimeRateLimiter,
    previous_admission_timestamp: Optional[datetime],
    root,
    pipeline_input,
    clock: Clock = _real_clock,
):
    """Gate Series 54's `run_read_only()` behind the Rate Limiter,
    which itself only ever runs when `circuit_decision.state ==
    CLOSED` (enforced inside `evaluate()`). Returns `(rate_decision,
    result)` -- `result` is `None` whenever admission is not
    `PERMITTED`.
    """
    rate_decision = limiter.evaluate(circuit_decision, previous_admission_timestamp)
    if not rate_decision.permitted:
        return rate_decision, None
    return rate_decision, runtime_module.run_read_only(root, pipeline_input, clock=clock)


def guarded_run_shadow(
    circuit_decision: CircuitDecision,
    limiter: RuntimeRateLimiter,
    previous_admission_timestamp: Optional[datetime],
    root,
    pipeline_input,
    spot_snapshot,
    option_chain,
    existing_session_ids=(),
    clock: Clock = _real_clock,
):
    """Gate Series 54's `run_shadow()` behind the Rate Limiter. Returns
    `(rate_decision, result)` -- `result` is `None` whenever admission
    is not `PERMITTED`; `run_shadow()`, and therefore every dispatch it
    could ever reach, is never called in that case.
    """
    rate_decision = limiter.evaluate(circuit_decision, previous_admission_timestamp)
    if not rate_decision.permitted:
        return rate_decision, None
    return rate_decision, runtime_module.run_shadow(
        root, pipeline_input, spot_snapshot, option_chain, existing_session_ids=existing_session_ids, clock=clock
    )
