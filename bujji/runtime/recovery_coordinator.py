"""Runtime Recovery Coordinator — BUJJI Options OS v3, Engineering
Series 53, Sprint 1.

Production remains the source of truth. The Runtime reconstructs
itself from Production. Never the reverse.

This module never calls, imports, or modifies
`bujji.core.orchestrator.Orchestrator._recover()`. That method is
private, stateful, side-effecting, `async`, returns nothing, and is
deeply coupled to the rest of `Orchestrator` (`SignalEngine`,
`TradeManager`, `EventBus`, `CapitalManagementEngine`) -- reaching into
it directly from here would either duplicate that coupling or require
reaching into `Orchestrator`'s private attributes after the fact,
neither of which this project has ever done to any other production
module. Instead, this coordinator defines `ProductionRecoverySource`,
a structural `typing.Protocol` capturing exactly the five
already-recovered facts this coordinator needs
(`is_healthy`, `broker_connected`, `has_open_position`,
`position_state`, `recovery_error`) -- the same seam pattern already
used for `ExecutionEngineInterface` (Series 45) and
`AuthenticationProviderInterface` (Series 49). A future wiring layer,
holding a real `Orchestrator` instance and having already called its
own `startup()`/`_recover()`, is responsible for reading that
instance's own public state and constructing a `ProductionRecoverySnapshot`
from it -- this coordinator never does that reading itself.

Runtime objects are reconstructed using ONLY the finite state values
already defined by their own frozen modules (Series 45/48/49) -- no
new lifecycle state is invented anywhere in this file. The one new,
disclosed vocabulary is `recovery_status`, describing this
coordinator's own top-level outcome (there was no pre-existing concept
for that to reuse).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional, Protocol, runtime_checkable

from ..authentication.models import BrokerSession
from ..runtime_execution.models import ExecutionSession
from ..runtime_session.models import RuntimeSession

Clock = Callable[[], datetime]

RECOVERY_COORDINATOR_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Recovery Status -- this coordinator's own top-level outcome. New,
# because no equivalent concept existed anywhere before this sprint.
# Disclosed explicitly, per the specification's own instruction not to
# invent new vocabulary silently.
# ---------------------------------------------------------------------------
RECOVERY_STATUS_RECOVERED = "RECOVERED"
RECOVERY_STATUS_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
RECOVERY_STATUS_FAILED = "FAILED"

ALL_RECOVERY_STATUSES = (
    RECOVERY_STATUS_RECOVERED,
    RECOVERY_STATUS_INSUFFICIENT_DATA,
    RECOVERY_STATUS_FAILED,
)

# Recognized production position_state values this coordinator knows
# how to map. Anything else yields INSUFFICIENT_DATA -- never a guess.
_ACTIVE_POSITION_STATES = ("IN_POSITION", "EXITING", "CONFIRMED")
_IDLE_POSITION_STATES = ("WAITING", "READY")
_DONE_POSITION_STATES = ("DONE_FOR_DAY",)


@dataclass(frozen=True)
class ProductionRecoverySnapshot:
    """The exact, narrow set of already-recovered facts this
    coordinator needs from production. Constructed by a future wiring
    layer from a real, already-recovered `Orchestrator` instance --
    never by this module.
    """

    is_healthy: bool
    broker_connected: bool
    has_open_position: bool
    position_state: Optional[str]
    recovery_error: Optional[str]


@runtime_checkable
class ProductionRecoverySource(Protocol):
    """The abstract shape this coordinator consumes. A future wiring
    layer implements this around a real, already-recovered
    `Orchestrator` instance -- this coordinator never imports
    `Orchestrator` itself.
    """

    def get_recovery_snapshot(self) -> ProductionRecoverySnapshot: ...


@dataclass(frozen=True)
class RuntimeRecoveryResult:
    recovery_id: str
    recovery_status: str
    runtime_session: Optional[RuntimeSession]
    broker_session: Optional[BrokerSession]
    execution_session: Optional[ExecutionSession]
    execution_session_gap_reason: Optional[str]
    production_snapshot_summary: str
    recovery_trace: str
    failure_reason: Optional[str]
    timestamp: str
    version: str


def _real_clock() -> datetime:
    return datetime.now()


def _recovery_id(source_present: bool, timestamp: str) -> str:
    seed = "|".join(["source" if source_present else "NONE", timestamp])
    return "RR-" + hashlib.md5(seed.encode()).hexdigest()[:16]


def _session_state_for(position_state: Optional[str]) -> Optional[str]:
    """Map production's own position_state (from `core.state_machine.State`,
    unchanged) onto RuntimeSession's own EXISTING session_state
    vocabulary (Series 48) -- never a new state. Returns None if the
    value is not recognized (never guessed).
    """
    if position_state in _ACTIVE_POSITION_STATES:
        return "ACTIVE"
    if position_state in _IDLE_POSITION_STATES:
        return "READY"
    if position_state in _DONE_POSITION_STATES:
        return "COMPLETED"
    return None


def _insufficient_data(timestamp: str, reason_note: str) -> RuntimeRecoveryResult:
    trace = f"Restart detected. {reason_note} INSUFFICIENT_DATA."
    return RuntimeRecoveryResult(
        recovery_id=_recovery_id(False, timestamp),
        recovery_status=RECOVERY_STATUS_INSUFFICIENT_DATA,
        runtime_session=None,
        broker_session=None,
        execution_session=None,
        execution_session_gap_reason=None,
        production_snapshot_summary="No usable production recovery snapshot.",
        recovery_trace=trace,
        failure_reason="INSUFFICIENT_DATA",
        timestamp=timestamp,
        version=RECOVERY_COORDINATOR_VERSION,
    )


def _failed(timestamp: str, recovery_error: str, summary: str) -> RuntimeRecoveryResult:
    trace = f"Restart detected. Production recovery invoked. {summary} FAILED: {recovery_error}."
    return RuntimeRecoveryResult(
        recovery_id=_recovery_id(True, timestamp),
        recovery_status=RECOVERY_STATUS_FAILED,
        runtime_session=None,
        broker_session=None,
        execution_session=None,
        execution_session_gap_reason=None,
        production_snapshot_summary=summary,
        recovery_trace=trace,
        failure_reason=recovery_error,
        timestamp=timestamp,
        version=RECOVERY_COORDINATOR_VERSION,
    )


def recover(
    source: Optional[ProductionRecoverySource],
    clock: Clock = _real_clock,
) -> RuntimeRecoveryResult:
    """Reconstruct Runtime artifacts solely from an already-recovered
    production snapshot.

    Never recovers broker state itself, never recreates an order or a
    position, never retries execution, never authenticates, and never
    modifies production recovery -- it only reads the narrow snapshot
    it is handed and reconstructs Runtime bookkeeping using existing,
    unmodified state vocabularies.
    """
    timestamp = clock().isoformat()

    if source is None:
        return _insufficient_data(timestamp, "No ProductionRecoverySource was supplied.")

    snapshot = source.get_recovery_snapshot()
    summary = (
        f"is_healthy={snapshot.is_healthy}, broker_connected={snapshot.broker_connected}, "
        f"has_open_position={snapshot.has_open_position}, position_state={snapshot.position_state}."
    )

    # Rule: production itself reports a hard recovery failure (e.g. the
    # broker connection could never be established at all) -- surfaced,
    # never retried, never papered over.
    if snapshot.recovery_error is not None:
        return _failed(timestamp, snapshot.recovery_error, summary)

    session_state = _session_state_for(snapshot.position_state)

    # Rule: an unrecognized position_state is never guessed at.
    if session_state is None:
        return _insufficient_data(
            timestamp,
            f"Production recovery invoked. {summary} position_state was not recognized.",
        )

    # Rule: broker not connected -- runtime cannot honestly claim
    # readiness even if a position_state was recognized.
    if not snapshot.broker_connected:
        return _insufficient_data(
            timestamp,
            f"Production recovery invoked. {summary} Broker is not connected.",
        )

    runtime_session_id = "RS-" + hashlib.md5(f"recovery|{timestamp}".encode()).hexdigest()[:16]
    runtime_session = RuntimeSession(
        session_id=runtime_session_id,
        authorization_id=None,  # Recovery reconstructs continuity; it never repeats a fresh authorization decision.
        session_state=session_state,
        session_policy=None,
        lifecycle_trace=f"Reconstructed from production recovery. {session_state}.",
        failure_reason=None,
        created_at=timestamp,
        updated_at=timestamp,
        version="1.0.0",
    )

    broker_auth_state = "AUTHENTICATED"
    broker_session_state = "READY"
    broker_session_id = "BS-" + hashlib.md5(f"recovery|{timestamp}".encode()).hexdigest()[:16]
    broker_session = BrokerSession(
        broker_session_id=broker_session_id,
        runtime_session_id=runtime_session_id,
        broker_identity=None,
        authentication_state=broker_auth_state,
        session_state=broker_session_state,
        authentication_trace=f"Reconstructed from production recovery. {broker_auth_state}. {broker_session_state}.",
        failure_reason=None,
        created_at=timestamp,
        expires_at=None,
        updated_at=timestamp,
        version="1.0.0",
    )

    # An honest, disclosed gap: reconstructing a full ExecutionSession
    # (Series 45) would require the original OrderRequests and
    # dispatch plan, which no data available through
    # ProductionRecoverySnapshot provides -- fabricating them to fill
    # in the model's required fields would violate "never infer
    # missing information". execution_session therefore always stays
    # None this sprint, whether or not a position exists; the gap
    # reason names exactly what production capability is missing to
    # close it, rather than silently omitting the field.
    execution_session_gap_reason = (
        "Reconstructing a full ExecutionSession requires the original OrderRequests "
        "and dispatch plan; ProductionRecoverySnapshot does not expose order-level "
        "detail, and this coordinator never fabricates it. See "
        "docs/RUNTIME_RECOVERY_INTEGRATION.md for the full disclosure."
        if snapshot.has_open_position
        else "No open position was reported; there is nothing to reconstruct an "
        "ExecutionSession for."
    )

    trace = (
        f"Restart detected. Production recovery invoked. {summary} "
        f"Runtime reconstruction performed: RuntimeSession={session_state}, "
        f"BrokerSession={broker_auth_state}/{broker_session_state}. RECOVERED."
    )

    return RuntimeRecoveryResult(
        recovery_id=_recovery_id(True, timestamp),
        recovery_status=RECOVERY_STATUS_RECOVERED,
        runtime_session=runtime_session,
        broker_session=broker_session,
        execution_session=None,
        execution_session_gap_reason=execution_session_gap_reason,
        production_snapshot_summary=summary,
        recovery_trace=trace,
        failure_reason=None,
        timestamp=timestamp,
        version=RECOVERY_COORDINATOR_VERSION,
    )
