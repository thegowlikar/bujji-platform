"""Runtime Safety Gate engine — BUJJI Options OS v3, Engineering
Series 47, Sprint 1.

The Trading Brain decides. The Runtime Execution Orchestrator
prepares. The Runtime Safety Gate authorizes. Nothing reaches
authentication or broker connectivity without passing this gate.

Inputs are exactly an `ExecutionSession` (Series 45), a
`QualificationPolicy` (a summary of a Series 46 replay result), and a
`RuntimeSafetyPolicy` -- never a broker, an authentication module, a
token manager, a network call, `ExecutionEngine`, or a runtime
connector. This module performs authorization, not execution: it
never authenticates, refreshes a token, connects to a broker, retries,
polls, or reconciles.

All nine safety checks run unconditionally whenever every input is
present -- never short-circuiting after the first failure, so both
`passed_checks` and `failed_checks` are always complete and every
authorization decision can fully explain itself. A session is never
partially authorized: either every critical check passes, or the
authorization is denied.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from ..runtime_execution.models import ExecutionSession
from . import taxonomy
from .models import QualificationPolicy, RuntimeAuthorization, RuntimeSafetyPolicy

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now()


def _run_checks(
    session: ExecutionSession,
    qualification_policy: QualificationPolicy,
    runtime_safety_policy: RuntimeSafetyPolicy,
) -> Dict[str, bool]:
    results: Dict[str, bool] = {}

    order_count_consistent = len(session.dispatch_plan) == len(session.order_requests)
    session_valid = (
        session.execution_state in ("READY", "DISPATCH_PENDING", "DISPATCHED")
        and order_count_consistent
    )
    results[taxonomy.CHECK_EXECUTION_SESSION_VALID] = session_valid
    results[taxonomy.CHECK_ORDER_COUNT_CONSISTENT] = order_count_consistent

    results[taxonomy.CHECK_QUALIFICATION_FINGERPRINT_PRESENT] = bool(
        qualification_policy.qualification_fingerprint
    )

    results[taxonomy.CHECK_PIPELINE_COMPLETED_SUCCESSFULLY] = qualification_policy.chain_valid is True

    results[taxonomy.CHECK_DISPATCH_PLAN_NOT_EMPTY] = len(session.dispatch_plan) > 0

    ids = [o.client_order_id for o in session.order_requests]
    results[taxonomy.CHECK_CLIENT_ORDER_IDS_UNIQUE] = len(set(ids)) == len(ids)

    results[taxonomy.CHECK_REPLAY_QUALIFICATION_PASSED] = (
        qualification_policy.replay_status == "PASSED" and qualification_policy.deterministic is True
    )

    results[taxonomy.CHECK_CONFIGURATION_VERSION_RECOGNIZED] = (
        session.version in taxonomy.RECOGNIZED_CONFIG_VERSIONS
    )

    results[taxonomy.CHECK_RUNTIME_POLICY_RECOGNIZED] = (
        runtime_safety_policy.policy_version in taxonomy.RECOGNIZED_RUNTIME_POLICY_VERSIONS
    )

    return results


def _trace(
    state: str,
    passed: Tuple[str, ...],
    failed: Tuple[str, ...],
    warnings: Tuple[str, ...],
) -> str:
    lines = []
    if failed:
        lines.append("Failed checks: " + ", ".join(failed) + ".")
    if passed:
        lines.append("Passed checks: " + ", ".join(passed) + ".")
    if warnings:
        lines.append("Warnings: " + ", ".join(warnings) + ".")
    lines.append(f"Authorization: {state}.")
    return " ".join(lines)


def _authorization_id(
    session: Optional[ExecutionSession], state: str, timestamp: str
) -> str:
    seed = "|".join([session.session_id if session else "NONE", state, timestamp])
    return "RAUTH-" + hashlib.md5(seed.encode()).hexdigest()[:16]


def authorize(
    session: Optional[ExecutionSession],
    qualification_policy: Optional[QualificationPolicy],
    runtime_safety_policy: Optional[RuntimeSafetyPolicy],
    clock: Clock = _real_clock,
) -> RuntimeAuthorization:
    """Determine whether an ExecutionSession is safe to advance toward
    live infrastructure.

    Never authenticates, never connects to a broker, never places an
    order -- only runs nine finite, deterministic safety checks and
    reports the result. A session is never partially authorized.
    """
    timestamp = clock().isoformat()

    if session is None or qualification_policy is None or runtime_safety_policy is None:
        return RuntimeAuthorization(
            authorization_id=_authorization_id(session, taxonomy.STATE_INSUFFICIENT_DATA, timestamp),
            execution_session_id=session.session_id if session else None,
            authorization_state=taxonomy.STATE_INSUFFICIENT_DATA,
            decision=taxonomy.DECISION_UNKNOWN,
            failed_checks=(),
            passed_checks=(),
            failure_reasons=(taxonomy.FAILURE_REASON_INSUFFICIENT_DATA,),
            warnings=(),
            authorization_trace=f"INSUFFICIENT_DATA because a required input was missing. Authorization: {taxonomy.STATE_INSUFFICIENT_DATA}.",
            timestamp=timestamp,
            version=taxonomy.RUNTIME_SAFETY_VERSION,
        )

    check_results = _run_checks(session, qualification_policy, runtime_safety_policy)
    passed_checks = tuple(name for name in taxonomy.ALL_SAFETY_CHECKS if check_results[name])
    failed_checks = tuple(name for name in taxonomy.ALL_SAFETY_CHECKS if not check_results[name])

    failure_reasons: Tuple[str, ...] = tuple(
        sorted({taxonomy.FAILURE_REASON_BY_CHECK[name] for name in failed_checks})
    )

    warnings: List[str] = []

    if failed_checks:
        state = taxonomy.STATE_DENIED
    elif (
        session.execution_state != "DISPATCHED"
        and runtime_safety_policy.allow_authorized_with_warnings
    ):
        state = taxonomy.STATE_AUTHORIZED_WITH_WARNINGS
        warnings.append(taxonomy.WARNING_SESSION_NOT_YET_DISPATCHED)
    else:
        state = taxonomy.STATE_AUTHORIZED

    decision = taxonomy.DECISION_BY_STATE[state]
    trace = _trace(state, passed_checks, failed_checks, tuple(warnings))

    return RuntimeAuthorization(
        authorization_id=_authorization_id(session, state, timestamp),
        execution_session_id=session.session_id,
        authorization_state=state,
        decision=decision,
        failed_checks=failed_checks,
        passed_checks=passed_checks,
        failure_reasons=failure_reasons,
        warnings=tuple(warnings),
        authorization_trace=trace,
        timestamp=timestamp,
        version=taxonomy.RUNTIME_SAFETY_VERSION,
    )
