"""JSON round-trip for BrokerSession / AuthenticationPolicy /
AuthenticationOutcome.
"""
from __future__ import annotations

from typing import Any, Dict

from .models import AuthenticationOutcome, AuthenticationPolicy, BrokerSession


def authentication_policy_to_dict(p: AuthenticationPolicy) -> Dict[str, Any]:
    return {
        "policy_version": p.policy_version,
        "session_ttl_seconds": p.session_ttl_seconds,
        "version": p.version,
    }


def authentication_policy_from_dict(d: Dict[str, Any]) -> AuthenticationPolicy:
    return AuthenticationPolicy(
        policy_version=d["policy_version"],
        session_ttl_seconds=d["session_ttl_seconds"],
        version=d["version"],
    )


def authentication_outcome_to_dict(o: AuthenticationOutcome) -> Dict[str, Any]:
    return {
        "success": o.success,
        "broker_identity": o.broker_identity,
        "expires_at": o.expires_at,
        "error": o.error,
    }


def authentication_outcome_from_dict(d: Dict[str, Any]) -> AuthenticationOutcome:
    return AuthenticationOutcome(
        success=d["success"],
        broker_identity=d.get("broker_identity"),
        expires_at=d.get("expires_at"),
        error=d.get("error"),
    )


def broker_session_to_dict(s: BrokerSession) -> Dict[str, Any]:
    return {
        "broker_session_id": s.broker_session_id,
        "runtime_session_id": s.runtime_session_id,
        "broker_identity": s.broker_identity,
        "authentication_state": s.authentication_state,
        "session_state": s.session_state,
        "authentication_trace": s.authentication_trace,
        "failure_reason": s.failure_reason,
        "created_at": s.created_at,
        "expires_at": s.expires_at,
        "updated_at": s.updated_at,
        "version": s.version,
    }


def broker_session_from_dict(d: Dict[str, Any]) -> BrokerSession:
    return BrokerSession(
        broker_session_id=d["broker_session_id"],
        runtime_session_id=d.get("runtime_session_id"),
        broker_identity=d.get("broker_identity"),
        authentication_state=d["authentication_state"],
        session_state=d["session_state"],
        authentication_trace=d["authentication_trace"],
        failure_reason=d.get("failure_reason"),
        created_at=d["created_at"],
        expires_at=d.get("expires_at"),
        updated_at=d["updated_at"],
        version=d["version"],
    )
