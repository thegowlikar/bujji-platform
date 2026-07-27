"""Authentication & Broker Session Manager models — frozen, immutable
records.

`AuthenticationOutcome` is the contract every injected authentication
provider (a production wrapper, or a test stub) must return -- this
module owns that contract, never production's own token/session
types. Nothing here submits an order, retries, or reconciles.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class AuthenticationPolicy:
    policy_version: str
    session_ttl_seconds: int = 28800
    version: str = "1.0.0"


@dataclass(frozen=True)
class AuthenticationOutcome:
    """The result contract every AuthenticationProviderInterface
    implementation must return from `authenticate()`. Owned by this
    module -- never production's own token/session response shape.
    """

    success: bool
    broker_identity: Optional[str] = None
    expires_at: Optional[str] = None
    error: Optional[str] = None


@dataclass(frozen=True)
class BrokerSession:
    broker_session_id: str
    runtime_session_id: Optional[str]
    broker_identity: Optional[str]
    authentication_state: str
    session_state: str
    authentication_trace: str
    failure_reason: Optional[str]
    created_at: str
    expires_at: Optional[str]
    updated_at: str
    version: str
