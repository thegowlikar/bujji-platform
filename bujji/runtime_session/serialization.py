"""JSON round-trip for RuntimeSession / RuntimeSessionPolicy."""
from __future__ import annotations

from typing import Any, Dict

from .models import RuntimeSession, RuntimeSessionPolicy


def session_policy_to_dict(p: RuntimeSessionPolicy) -> Dict[str, Any]:
    return {
        "policy_version": p.policy_version,
        "allow_pause_resume": p.allow_pause_resume,
        "version": p.version,
    }


def session_policy_from_dict(d: Dict[str, Any]) -> RuntimeSessionPolicy:
    return RuntimeSessionPolicy(
        policy_version=d["policy_version"],
        allow_pause_resume=d["allow_pause_resume"],
        version=d["version"],
    )


def session_to_dict(s: RuntimeSession) -> Dict[str, Any]:
    return {
        "session_id": s.session_id,
        "authorization_id": s.authorization_id,
        "session_state": s.session_state,
        "session_policy": session_policy_to_dict(s.session_policy) if s.session_policy else None,
        "lifecycle_trace": s.lifecycle_trace,
        "failure_reason": s.failure_reason,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
        "version": s.version,
    }


def session_from_dict(d: Dict[str, Any]) -> RuntimeSession:
    policy_dict = d.get("session_policy")
    return RuntimeSession(
        session_id=d["session_id"],
        authorization_id=d.get("authorization_id"),
        session_state=d["session_state"],
        session_policy=session_policy_from_dict(policy_dict) if policy_dict else None,
        lifecycle_trace=d["lifecycle_trace"],
        failure_reason=d.get("failure_reason"),
        created_at=d["created_at"],
        updated_at=d["updated_at"],
        version=d["version"],
    )
