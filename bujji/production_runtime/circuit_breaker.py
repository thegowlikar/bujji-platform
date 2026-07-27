"""Runtime Circuit Breaker — BUJJI Options OS v3, Engineering Series 56.

The Circuit Breaker observes. It authorizes. It never repairs. Given a
`RuntimeHealthSnapshot` (Series 55), it makes exactly one deterministic
decision -- permit or deny new work -- and never itself retries,
recovers, authenticates, dispatches, or connects to anything.

This module deliberately does not modify `bujji/production_runtime/
runtime.py` (Series 54), which is frozen. Instead it provides its own
gate, `authorize_new_work()`, and thin guarded wrappers
(`guarded_run_read_only()`, `guarded_run_shadow()`) that call the
Circuit Breaker before delegating to the existing, unmodified Series
54 entry points -- "before accepting any new pipeline execution" is
satisfied at the call site, not by editing the frozen module.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from . import runtime as runtime_module
from .health import (
    HEALTH_DEGRADED,
    HEALTH_HEALTHY,
    HEALTH_INSUFFICIENT_DATA,
    HEALTH_UNHEALTHY,
    HEALTH_UNKNOWN,
    RuntimeHealthSnapshot,
)

CIRCUIT_CLOSED = "CLOSED"
CIRCUIT_OPEN = "OPEN"
CIRCUIT_HALF_OPEN = "HALF_OPEN"
CIRCUIT_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

ALL_CIRCUIT_STATES = (CIRCUIT_CLOSED, CIRCUIT_OPEN, CIRCUIT_HALF_OPEN, CIRCUIT_INSUFFICIENT_DATA)

# The one, deterministic, no-heuristics mapping from Series 55's five
# overall health values to a circuit state. HALF_OPEN is classifiable
# by this sprint's own vocabulary but this map never produces it --
# per the specification, automatic transitions into HALF_OPEN are
# explicitly out of scope; it is reserved for a future operational
# recovery workflow, not decided here.
_HEALTH_TO_CIRCUIT_STATE = {
    HEALTH_HEALTHY: CIRCUIT_CLOSED,
    HEALTH_DEGRADED: CIRCUIT_OPEN,
    HEALTH_UNHEALTHY: CIRCUIT_OPEN,
    HEALTH_INSUFFICIENT_DATA: CIRCUIT_INSUFFICIENT_DATA,
    # HEALTH_UNKNOWN is not one of the four worked examples in the
    # specification. It arises only when RuntimeHealthAggregator was
    # given no evidence at all (see Series 55). Treated the same as
    # INSUFFICIENT_DATA -- a direct, disclosed extension of the
    # specification's own conservative default, not a new heuristic:
    # "not enough evidence exists to decide safely" describes UNKNOWN
    # exactly as much as it describes INSUFFICIENT_DATA.
    HEALTH_UNKNOWN: CIRCUIT_INSUFFICIENT_DATA,
}

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now()


@dataclass(frozen=True)
class CircuitDecision:
    """A single, immutable, deterministic decision. `health_snapshot`
    is the exact `RuntimeHealthSnapshot` the decision was derived from
    -- never a copy, never a summary -- so the decision remains fully
    traceable to its evidence.
    """

    state: str
    permit_new_work: bool
    health_snapshot: RuntimeHealthSnapshot
    evidence_used: str
    reason: str
    timestamp: str


@dataclass(frozen=True)
class RuntimeCircuitBreaker:
    """Read-only, side-effect-free. `evaluate()` performs a pure
    dictionary lookup against `health_snapshot.overall` -- it never
    calls `connect()`, `authenticate()`, `dispatch()`, or `recover()`
    on anything, and it never mutates the snapshot it is given.
    """

    logger: logging.Logger = field(
        default_factory=lambda: logging.getLogger("bujji.production_runtime.circuit_breaker")
    )
    clock: Clock = _real_clock

    def evaluate(
        self,
        health_snapshot: RuntimeHealthSnapshot,
        runtime_mode: Optional[str] = None,
        qualification_mode: Optional[str] = None,
    ) -> CircuitDecision:
        timestamp = self.clock().isoformat()
        overall = health_snapshot.overall
        state = _HEALTH_TO_CIRCUIT_STATE.get(overall, CIRCUIT_INSUFFICIENT_DATA)
        permit = state == CIRCUIT_CLOSED

        evidence_used = f"RuntimeHealthSnapshot.overall={overall} (as of {health_snapshot.timestamp})"
        reason = _reason_for(state, overall, runtime_mode, qualification_mode)

        decision = CircuitDecision(
            state=state,
            permit_new_work=permit,
            health_snapshot=health_snapshot,
            evidence_used=evidence_used,
            reason=reason,
            timestamp=timestamp,
        )

        self.logger.info(
            "CircuitDecision state=%s permit_new_work=%s health=%s reason=%s timestamp=%s",
            decision.state,
            decision.permit_new_work,
            overall,
            decision.reason,
            decision.timestamp,
        )
        return decision


def _reason_for(
    state: str, overall_health: str, runtime_mode: Optional[str], qualification_mode: Optional[str]
) -> str:
    mode_suffix = ""
    if runtime_mode is not None:
        mode_suffix += f", runtime_mode={runtime_mode}"
    if qualification_mode is not None:
        mode_suffix += f", qualification_mode={qualification_mode}"

    if state == CIRCUIT_CLOSED:
        return f"Overall health is HEALTHY; new work is permitted{mode_suffix}."
    if state == CIRCUIT_OPEN:
        return f"Overall health is {overall_health}; new work is refused until health recovers{mode_suffix}."
    return (
        f"Overall health is {overall_health}; insufficient evidence to decide safely, "
        f"new work is refused as a conservative default{mode_suffix}."
    )


def authorize_new_work(
    breaker: RuntimeCircuitBreaker,
    health_snapshot: RuntimeHealthSnapshot,
    runtime_mode: Optional[str] = None,
    qualification_mode: Optional[str] = None,
) -> CircuitDecision:
    """The single gate every new pipeline execution must pass through.
    A pure delegation to `breaker.evaluate()` -- kept as a free
    function so call sites don't need to know the breaker is a
    dataclass with a method.
    """
    return breaker.evaluate(health_snapshot, runtime_mode=runtime_mode, qualification_mode=qualification_mode)


def guarded_run_read_only(
    breaker: RuntimeCircuitBreaker,
    health_snapshot: RuntimeHealthSnapshot,
    root,
    pipeline_input,
    clock: Clock = _real_clock,
):
    """Gate Series 54's `run_read_only()` behind the Circuit Breaker.
    Returns `(decision, result)` -- `result` is `None` whenever the
    circuit is not `CLOSED`, and `run_read_only()` is never called in
    that case (no pipeline work is performed at all).
    """
    decision = authorize_new_work(breaker, health_snapshot, runtime_mode=getattr(root.config, "mode", None))
    if not decision.permit_new_work:
        return decision, None
    return decision, runtime_module.run_read_only(root, pipeline_input, clock=clock)


def guarded_run_shadow(
    breaker: RuntimeCircuitBreaker,
    health_snapshot: RuntimeHealthSnapshot,
    root,
    pipeline_input,
    spot_snapshot,
    option_chain,
    existing_session_ids=(),
    clock: Clock = _real_clock,
):
    """Gate Series 54's `run_shadow()` behind the Circuit Breaker.
    Returns `(decision, result)` -- `result` is `None` whenever the
    circuit is not `CLOSED`; `run_shadow()`, and therefore every
    dispatch it could ever reach, is never called in that case.
    """
    decision = authorize_new_work(breaker, health_snapshot, runtime_mode=getattr(root.config, "mode", None))
    if not decision.permit_new_work:
        return decision, None
    return decision, runtime_module.run_shadow(
        root, pipeline_input, spot_snapshot, option_chain, existing_session_ids=existing_session_ids, clock=clock
    )
