"""Runtime Health Aggregation — BUJJI Options OS v3, Engineering Series 55.

Health is evidence, not control. This module observes already-produced
state objects from other subsystems and classifies them into one
overall verdict. It never reconnects, retries, recovers, authenticates,
dispatches, trades, or mutates any object it is given. Every field on
`RuntimeHealthSnapshot` is either an inspection result or the identity
of the evidence used to produce it -- nothing here is guessed.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional, Tuple

HEALTH_UNKNOWN = "UNKNOWN"
HEALTH_HEALTHY = "HEALTHY"
HEALTH_DEGRADED = "DEGRADED"
HEALTH_UNHEALTHY = "UNHEALTHY"
HEALTH_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

ALL_HEALTH_STATES = (
    HEALTH_UNKNOWN,
    HEALTH_HEALTHY,
    HEALTH_DEGRADED,
    HEALTH_UNHEALTHY,
    HEALTH_INSUFFICIENT_DATA,
)

# States that mean "this subsystem is fine, right now, on its own terms" --
# never inferred, only ever compared literally against each subsystem's own
# existing taxonomy value.
_HEALTHY_RUNTIME_SESSION_STATES = ("READY", "ACTIVE")
_HEALTHY_BROKER_AUTH_STATES = ("AUTHENTICATED",)
_HEALTHY_BROKER_SESSION_STATES = ("READY", "CONNECTED")
_HEALTHY_EXECUTION_STATES = ("READY", "DISPATCH_PENDING", "DISPATCHED")

_FAILED_RUNTIME_SESSION_STATES = ("FAILED", "ABORTED")
_FAILED_BROKER_AUTH_STATES = ("FAILED", "EXPIRED")
_FAILED_BROKER_SESSION_STATES = ("FAILED", "EXPIRED", "DISCONNECTED")
_FAILED_EXECUTION_STATES = ("FAILED_VALIDATION", "ABORTED")

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now()


class _NotApplicable:
    """Sentinel distinguishing 'this evidence was never requested for
    this run' (subsystem omitted entirely -- e.g. no restart occurred,
    so recovery is not applicable) from 'this evidence was requested
    but is missing' (caller passes `None` explicitly -> subsystem
    evaluated as INSUFFICIENT_DATA). Never fabricate applicability
    either way -- the caller states it explicitly."""

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return "NOT_APPLICABLE"


_NOT_APPLICABLE = _NotApplicable()


@dataclass(frozen=True)
class SubsystemHealth:
    """One subsystem's own classification, plus exactly the evidence
    that produced it -- never a summary that hides the underlying
    fact.
    """

    name: str
    status: str
    evidence: str


@dataclass(frozen=True)
class RuntimeHealthSnapshot:
    """A single, immutable, deterministic health report. Every field is
    either inspection output or evidence identity -- constructing this
    object performs no I/O and changes nothing.
    """

    overall: str
    subsystems: Tuple[SubsystemHealth, ...]
    startup_verified: Optional[bool]
    configuration_valid: Optional[bool]
    runtime_mode: Optional[str]
    broker_mode: Optional[str]
    qualification_mode: Optional[str]
    evidence_used: Tuple[str, ...]
    issues: Tuple[str, ...]
    timestamp: str


def _state_of(obj: Any, attr: str) -> Optional[str]:
    if obj is None:
        return None
    return getattr(obj, attr, None)


def _classify_runtime_session(runtime_session: Any) -> SubsystemHealth:
    if runtime_session is None:
        return SubsystemHealth("runtime_session", HEALTH_INSUFFICIENT_DATA, "no RuntimeSession supplied")
    state = _state_of(runtime_session, "session_state")
    if state in _HEALTHY_RUNTIME_SESSION_STATES:
        return SubsystemHealth("runtime_session", HEALTH_HEALTHY, f"session_state={state}")
    if state in _FAILED_RUNTIME_SESSION_STATES:
        return SubsystemHealth("runtime_session", HEALTH_UNHEALTHY, f"session_state={state}")
    if state is None:
        return SubsystemHealth("runtime_session", HEALTH_INSUFFICIENT_DATA, "RuntimeSession has no session_state")
    return SubsystemHealth("runtime_session", HEALTH_DEGRADED, f"session_state={state}")


def _classify_authentication(broker_session: Any) -> SubsystemHealth:
    if broker_session is None:
        return SubsystemHealth("authentication", HEALTH_INSUFFICIENT_DATA, "no BrokerSession supplied")
    auth_state = _state_of(broker_session, "authentication_state")
    session_state = _state_of(broker_session, "session_state")
    if auth_state is None or session_state is None:
        return SubsystemHealth(
            "authentication",
            HEALTH_INSUFFICIENT_DATA,
            f"authentication_state={auth_state}, session_state={session_state}",
        )
    if auth_state in _FAILED_BROKER_AUTH_STATES or session_state in _FAILED_BROKER_SESSION_STATES:
        return SubsystemHealth(
            "authentication",
            HEALTH_UNHEALTHY,
            f"authentication_state={auth_state}, session_state={session_state}",
        )
    if auth_state in _HEALTHY_BROKER_AUTH_STATES and session_state in _HEALTHY_BROKER_SESSION_STATES:
        return SubsystemHealth(
            "authentication",
            HEALTH_HEALTHY,
            f"authentication_state={auth_state}, session_state={session_state}",
        )
    return SubsystemHealth(
        "authentication",
        HEALTH_DEGRADED,
        f"authentication_state={auth_state}, session_state={session_state}",
    )


def _classify_execution(execution_session: Any) -> SubsystemHealth:
    if execution_session is None:
        return SubsystemHealth("execution", HEALTH_INSUFFICIENT_DATA, "no ExecutionSession supplied")
    state = _state_of(execution_session, "execution_state")
    if state in _HEALTHY_EXECUTION_STATES:
        return SubsystemHealth("execution", HEALTH_HEALTHY, f"execution_state={state}")
    if state in _FAILED_EXECUTION_STATES:
        return SubsystemHealth("execution", HEALTH_UNHEALTHY, f"execution_state={state}")
    if state is None:
        return SubsystemHealth("execution", HEALTH_INSUFFICIENT_DATA, "ExecutionSession has no execution_state")
    return SubsystemHealth("execution", HEALTH_DEGRADED, f"execution_state={state}")


def _classify_recovery(recovery_result: Any) -> SubsystemHealth:
    if recovery_result is None:
        return SubsystemHealth("recovery", HEALTH_INSUFFICIENT_DATA, "no RuntimeRecoveryResult supplied")
    status = _state_of(recovery_result, "recovery_status")
    if status == "RECOVERED":
        return SubsystemHealth("recovery", HEALTH_HEALTHY, f"recovery_status={status}")
    if status == "FAILED":
        return SubsystemHealth("recovery", HEALTH_UNHEALTHY, f"recovery_status={status}")
    if status == "INSUFFICIENT_DATA":
        return SubsystemHealth("recovery", HEALTH_INSUFFICIENT_DATA, f"recovery_status={status}")
    return SubsystemHealth("recovery", HEALTH_INSUFFICIENT_DATA, f"recovery_status={status!r} unrecognized")


def _classify_startup(startup_report: Any) -> SubsystemHealth:
    if startup_report is None:
        return SubsystemHealth("startup", HEALTH_INSUFFICIENT_DATA, "no StartupReport supplied")
    ready = _state_of(startup_report, "ready")
    if ready is True:
        return SubsystemHealth("startup", HEALTH_HEALTHY, "ready=True")
    if ready is False:
        reason = _state_of(startup_report, "failure_reason") or "unspecified"
        return SubsystemHealth("startup", HEALTH_UNHEALTHY, f"ready=False, failure_reason={reason}")
    return SubsystemHealth("startup", HEALTH_INSUFFICIENT_DATA, "StartupReport has no ready field")


def _classify_configuration(startup_report: Any) -> SubsystemHealth:
    if startup_report is None:
        return SubsystemHealth("configuration", HEALTH_INSUFFICIENT_DATA, "no StartupReport supplied")
    valid = _state_of(startup_report, "config_valid")
    if valid is True:
        return SubsystemHealth("configuration", HEALTH_HEALTHY, "config_valid=True")
    if valid is False:
        return SubsystemHealth("configuration", HEALTH_UNHEALTHY, "config_valid=False")
    return SubsystemHealth("configuration", HEALTH_INSUFFICIENT_DATA, "StartupReport has no config_valid field")


# "Critical" subsystems: without a valid configuration or a successful
# startup, nothing else can be trusted, so either one being UNHEALTHY
# always dominates the overall verdict. "Operational" subsystems
# (runtime session, authentication, execution, recovery) being
# unavailable degrades the system but does not necessarily mean the
# whole runtime is unhealthy -- matching the specification's own worked
# example ("Authentication unavailable, Execution unavailable ->
# DEGRADED").
_CRITICAL_SUBSYSTEMS = ("configuration", "startup")
_OPERATIONAL_SUBSYSTEMS = ("runtime_session", "authentication", "execution", "recovery")


def _aggregate_overall(subsystems: Tuple[SubsystemHealth, ...]) -> str:
    """Deterministic, order-independent aggregation:

    1. No subsystems supplied at all -> UNKNOWN.
    2. Any critical subsystem (configuration, startup) UNHEALTHY ->
       overall UNHEALTHY -- nothing else can be trusted.
    3. Any critical subsystem INSUFFICIENT_DATA -> overall
       INSUFFICIENT_DATA -- we cannot even confirm the system started.
    4. Otherwise, any operational subsystem INSUFFICIENT_DATA -> overall
       INSUFFICIENT_DATA (missing subsystem information).
    5. Otherwise, any operational subsystem UNHEALTHY or DEGRADED ->
       overall DEGRADED (the system runs, but not at full capability).
    6. All HEALTHY -> overall HEALTHY.
    """
    if not subsystems:
        return HEALTH_UNKNOWN

    by_name = {s.name: s for s in subsystems}

    critical = [by_name[n] for n in _CRITICAL_SUBSYSTEMS if n in by_name]
    operational = [by_name[n] for n in _OPERATIONAL_SUBSYSTEMS if n in by_name]

    if any(s.status == HEALTH_UNHEALTHY for s in critical):
        return HEALTH_UNHEALTHY
    if any(s.status == HEALTH_INSUFFICIENT_DATA for s in critical):
        return HEALTH_INSUFFICIENT_DATA
    if any(s.status == HEALTH_INSUFFICIENT_DATA for s in operational):
        return HEALTH_INSUFFICIENT_DATA
    if any(s.status in (HEALTH_UNHEALTHY, HEALTH_DEGRADED) for s in operational):
        return HEALTH_DEGRADED

    statuses = {s.status for s in subsystems}
    if statuses == {HEALTH_HEALTHY}:
        return HEALTH_HEALTHY
    return HEALTH_UNKNOWN


@dataclass(frozen=True)
class RuntimeHealthAggregator:
    """Read-only. Every method here inspects the objects it is given
    and returns a new, immutable snapshot -- it never calls a method on
    any subsystem object that could change state (no `.connect()`, no
    `.authenticate()`, no `.dispatch()`, no `.recover()`).
    """

    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("bujji.production_runtime.health"))
    clock: Clock = _real_clock

    def snapshot(
        self,
        runtime_session: Any = _NOT_APPLICABLE,
        broker_session: Any = _NOT_APPLICABLE,
        execution_session: Any = _NOT_APPLICABLE,
        recovery_result: Any = _NOT_APPLICABLE,
        startup_report: Any = _NOT_APPLICABLE,
        runtime_mode: Optional[str] = None,
        broker_mode: Optional[str] = None,
        qualification_mode: Optional[str] = None,
    ) -> RuntimeHealthSnapshot:
        """Build a snapshot from whatever evidence is supplied.

        Any argument left at its default (`_NOT_APPLICABLE`) is treated
        as "not applicable to this run" and omitted from the snapshot
        entirely -- it is never scored, and never drags the overall
        verdict down. Passing `None` explicitly means "this evidence
        was expected but is missing," which the corresponding
        subsystem classifies as `INSUFFICIENT_DATA`. `configuration`
        and `startup` are always evaluated (from `startup_report`,
        itself defaulting to not-applicable) since every run has a
        startup outcome to report.
        """
        timestamp = self.clock().isoformat()

        subsystem_results = []
        if startup_report is not _NOT_APPLICABLE:
            subsystem_results.append(_classify_configuration(startup_report))
            subsystem_results.append(_classify_startup(startup_report))
        if runtime_session is not _NOT_APPLICABLE:
            subsystem_results.append(_classify_runtime_session(runtime_session))
        if broker_session is not _NOT_APPLICABLE:
            subsystem_results.append(_classify_authentication(broker_session))
        if execution_session is not _NOT_APPLICABLE:
            subsystem_results.append(_classify_execution(execution_session))
        if recovery_result is not _NOT_APPLICABLE:
            subsystem_results.append(_classify_recovery(recovery_result))
        subsystems = tuple(subsystem_results)

        overall = _aggregate_overall(subsystems)

        evidence_used = tuple(f"{s.name}: {s.evidence}" for s in subsystems)
        issues = tuple(
            f"{s.name} is {s.status} ({s.evidence})"
            for s in subsystems
            if s.status in (HEALTH_UNHEALTHY, HEALTH_DEGRADED, HEALTH_INSUFFICIENT_DATA)
        )

        startup_verified = _state_of(startup_report, "ready")
        configuration_valid = _state_of(startup_report, "config_valid")

        snap = RuntimeHealthSnapshot(
            overall=overall,
            subsystems=subsystems,
            startup_verified=startup_verified,
            configuration_valid=configuration_valid,
            runtime_mode=runtime_mode,
            broker_mode=broker_mode,
            qualification_mode=qualification_mode,
            evidence_used=evidence_used,
            issues=issues,
            timestamp=timestamp,
        )

        self.logger.info(
            "RuntimeHealthSnapshot overall=%s subsystems=%s issues=%s timestamp=%s",
            snap.overall,
            {s.name: s.status for s in snap.subsystems},
            list(snap.issues),
            snap.timestamp,
        )
        return snap

    def snapshot_from_composition_root(
        self,
        root: Any,
        startup_report: Any = _NOT_APPLICABLE,
        runtime_session: Any = _NOT_APPLICABLE,
        broker_session: Any = _NOT_APPLICABLE,
        execution_session: Any = _NOT_APPLICABLE,
        recovery_result: Any = _NOT_APPLICABLE,
    ) -> RuntimeHealthSnapshot:
        """Convenience entry point for `CompositionRoot` (Series 54).
        Reads only already-public, already-constructed fields off
        `root` (its `config`) -- never constructs or connects
        anything.
        """
        config = getattr(root, "config", None)
        return self.snapshot(
            runtime_session=runtime_session,
            broker_session=broker_session,
            execution_session=execution_session,
            recovery_result=recovery_result,
            startup_report=startup_report,
            runtime_mode=_state_of(config, "mode"),
            broker_mode=_state_of(config, "broker_name"),
            qualification_mode=_state_of(config, "replay_status"),
        )
