"""Shadow Observatory record models -- BUJJI Options OS v3, Gate V.0.

PURPOSE: JSON-serializable schemas only -- plain dataclasses of str/
int/float/bool/None/dict/list. Never a Python object, dataclass
instance, enum member, or broker/strategy object is written directly;
every field here is already a primitive by the time it reaches these
models (every EventBus payload published across F.0-F.5 is already
plain-primitive by construction -- verified in Step 1 inspection).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class HeartbeatArtifact:
    time: str
    runtime_state: str
    positions: int
    broker_connected: bool
    market_feed_status: str
    last_decision_time: Optional[str]
    last_execution_time: Optional[str]


@dataclass(frozen=True)
class DecisionArtifact:
    timestamp: str
    decision_owner: str          # e.g. "STRATEGY_ENGINE" | "RISK_GOVERNOR"
    output_decision: str          # e.g. "STRATEGY_PROPOSED" | "APPROVED" | "BLOCKED"
    explanation: Dict[str, Any]    # the ORIGINAL event payload, verbatim -- no interpretation added.
    trace_id: str


@dataclass(frozen=True)
class OrderArtifact:
    timestamp: str
    stage: str
    position_group_id: Optional[str]
    detail: Dict[str, Any]


@dataclass(frozen=True)
class ExecutionArtifact:
    timestamp: str
    stage: str
    detail: Dict[str, Any]


@dataclass(frozen=True)
class PositionArtifact:
    timestamp: str
    stage: str
    position_group_id: Optional[str]
    detail: Dict[str, Any]


@dataclass(frozen=True)
class LifecycleArtifact:
    timestamp: str
    stage: str
    position_group_id: Optional[str]
    detail: Dict[str, Any]


@dataclass(frozen=True)
class StateChangeArtifact:
    timestamp: str
    from_state: Optional[str]
    to_state: Optional[str]
    reason: Optional[str]


@dataclass(frozen=True)
class ErrorArtifact:
    timestamp: str
    exception_type: str
    module: str
    runtime_state: Optional[str]
    recovery_action: str
    detail: str


@dataclass(frozen=True)
class SessionMetadata:
    session_id: str
    started_at: str
    initial_state: str


@dataclass(frozen=True)
class SessionManifest:
    """The 'identity card' of a shadow session -- forensic evidence-
    chain metadata, distinct from `SessionMetadata` (which only
    records when/how the session's own runtime state machine started).
    Every field is caller-supplied, never fabricated: `code_version`/
    `config_hash` are deployment-level facts this package cannot know
    on its own (it never inspects git or config files itself), so a
    missing value stays `None` rather than being guessed."""
    session_id: str
    start_time: str
    mode: str
    strategy_engine: str
    risk_engine: str
    broker: str
    code_version: Optional[str]
    config_hash: Optional[str]
    market: str
    symbols: Tuple[str, ...]


@dataclass(frozen=True)
class SessionSummaryArtifact:
    session_id: str
    session_start: Optional[str]
    session_end: Optional[str]
    session_duration_seconds: Optional[float]
    state_transitions: int
    orders_count: int
    fills_count: int
    reject_count: int
    final_positions: Tuple[str, ...]
    realized_pnl: float
    unrealized_pnl: Optional[float]
    error_count: int
