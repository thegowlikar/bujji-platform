"""JSON round-trip for RuntimeAuthorization / QualificationPolicy /
RuntimeSafetyPolicy.
"""
from __future__ import annotations

from typing import Any, Dict

from .models import QualificationPolicy, RuntimeAuthorization, RuntimeSafetyPolicy


def qualification_policy_to_dict(p: QualificationPolicy) -> Dict[str, Any]:
    return {
        "replay_status": p.replay_status,
        "qualification_fingerprint": p.qualification_fingerprint,
        "chain_valid": p.chain_valid,
        "deterministic": p.deterministic,
        "version": p.version,
    }


def qualification_policy_from_dict(d: Dict[str, Any]) -> QualificationPolicy:
    return QualificationPolicy(
        replay_status=d["replay_status"],
        qualification_fingerprint=d.get("qualification_fingerprint"),
        chain_valid=d["chain_valid"],
        deterministic=d["deterministic"],
        version=d["version"],
    )


def runtime_safety_policy_to_dict(p: RuntimeSafetyPolicy) -> Dict[str, Any]:
    return {
        "policy_version": p.policy_version,
        "allow_authorized_with_warnings": p.allow_authorized_with_warnings,
        "version": p.version,
    }


def runtime_safety_policy_from_dict(d: Dict[str, Any]) -> RuntimeSafetyPolicy:
    return RuntimeSafetyPolicy(
        policy_version=d["policy_version"],
        allow_authorized_with_warnings=d["allow_authorized_with_warnings"],
        version=d["version"],
    )


def authorization_to_dict(a: RuntimeAuthorization) -> Dict[str, Any]:
    return {
        "authorization_id": a.authorization_id,
        "execution_session_id": a.execution_session_id,
        "authorization_state": a.authorization_state,
        "decision": a.decision,
        "failed_checks": list(a.failed_checks),
        "passed_checks": list(a.passed_checks),
        "failure_reasons": list(a.failure_reasons),
        "warnings": list(a.warnings),
        "authorization_trace": a.authorization_trace,
        "timestamp": a.timestamp,
        "version": a.version,
    }


def authorization_from_dict(d: Dict[str, Any]) -> RuntimeAuthorization:
    return RuntimeAuthorization(
        authorization_id=d["authorization_id"],
        execution_session_id=d.get("execution_session_id"),
        authorization_state=d["authorization_state"],
        decision=d["decision"],
        failed_checks=tuple(d["failed_checks"]),
        passed_checks=tuple(d["passed_checks"]),
        failure_reasons=tuple(d["failure_reasons"]),
        warnings=tuple(d["warnings"]),
        authorization_trace=d["authorization_trace"],
        timestamp=d["timestamp"],
        version=d["version"],
    )
