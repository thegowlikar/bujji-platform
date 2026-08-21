"""Outcome Memory -- Phase 15N. Pure models, no IO, no broker, no
execution, no strategy-selection import, no feedback path.

Architecture: `REMEMBER` in the mission's own progression -- Decision
-> Outcome -> MEMORY, deliberately NOT Memory -> Decision (that is an
explicitly out-of-scope future phase with a much higher trust bar).

Forensic audit findings driving this design (Step 1):
- `PositionOutcomeAttribution` (Phase 15J) is the canonical, already-
  complete causal record -- `readiness`, `outcome_direction`,
  `realized_pnl`, `primary_cause`, `contributing_factors`,
  `protective_factors`, `evidence` (per-dimension, itself carrying
  STRENGTH_UNKNOWN/IMPACT_UNKNOWN as first-class values), `narrative`.
  Never re-derived here -- embedded VERBATIM (`attribution_snapshot`).
- `PositionLifecycle` (Phase 15G-15K) is the canonical position record
  -- entry snapshot, legs, thesis/management history, `structured_exit`
  (itself carrying `PNL_COMPLETE`/`PNL_PARTIAL`/`PNL_UNKNOWN`). Never
  re-derived here -- embedded VERBATIM (`lifecycle_snapshot`). This is
  the most faithful way to "preserve the historical reasoning context"
  (Step 2): the exact, tamper-proof records that existed at attribution
  time, not a lossy re-summarization that could silently drift from
  the canonical source over time.
- A dedicated OUTCOME_MEMORY_RECORDED event is sufficient -- `EventStore`
  (Phase 15B) is fully generic (`PersistedEvent` has no position-
  lifecycle-specific shape) and needs NO new persistence architecture.
- CRITICAL architectural difference from every prior EventStore
  consumer: `PositionLifecycle`/`RegimeMemoryState`/`ObservationMemory`
  are all PER-SESSION (hydration takes a `session_id` and REJECTS any
  event from another session, `position_lifecycle/engine.py`'s own
  `apply_event`). Outcome Memory is explicitly CROSS-SESSION BY DESIGN
  -- the entire point of durable memory is to span many sessions.
  `hydrate_outcome_memory` therefore takes NO target `session_id` and
  accepts every real, well-formed event regardless of which session
  produced it -- `session_id` is preserved as a per-record IDENTITY/
  QUERY field, never used as a hydration-time isolation gate.
- No legacy `trading_brain` memory implementation exists to reuse --
  confirmed by direct inspection (`bujji/trading_brain/` has no
  `outcome_memory`/learning package at all, only the disconnected
  `portfolio_risk_aggregator`/`portfolio_valuation` found in Phase 15M).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

SCHEMA_VERSION = "1.0.0"

# --- Event type ---------------------------------------------------------------
EVENT_OUTCOME_MEMORY_RECORDED = "OUTCOME_MEMORY_RECORDED"

# --- Transition outcomes (same vocabulary as position_lifecycle) -----------
TRANSITION_ACCEPTED = "ACCEPTED"
TRANSITION_IDEMPOTENT = "IDEMPOTENT"
TRANSITION_REJECTED = "REJECTED"

# --- Epistemic status (Step 3 -- first-class, never a guess) --------------
STATUS_KNOWN = "KNOWN"                  # real evidence exists and was resolved.
STATUS_UNKNOWN = "UNKNOWN"              # evidence SHOULD exist for a record of this shape, but genuinely doesn't.
STATUS_NOT_APPLICABLE = "NOT_APPLICABLE"  # this record legitimately has no such evidence (e.g. no management event ever happened).
STATUS_NOT_AVAILABLE = "NOT_AVAILABLE"    # this record predates the feature/schema version that would capture it.
ALL_EPISTEMIC_STATUSES = (STATUS_KNOWN, STATUS_UNKNOWN, STATUS_NOT_APPLICABLE, STATUS_NOT_AVAILABLE)


def memory_id_for(session_id: str, position_id: str, attribution_version: str) -> str:
    """Deterministic, collision-resistant, replay-safe, independent of
    any broker order id -- same style as `position_id_for`/`leg_id_for`
    (Phase 15G) and `client_order_id_for` (Phase 15L). `attribution_version`
    (the attribution's own `evaluation_timestamp`) is included so a
    position that is somehow re-attributed later (e.g. a corrected
    replay) produces a genuinely DIFFERENT memory identity rather than
    silently overwriting history -- memory records are immutable facts,
    never updated in place."""
    return "MEM-" + hashlib.md5(f"{session_id}|{position_id}|{attribution_version}".encode()).hexdigest()[:24]


@dataclass(frozen=True)
class OutcomeMemoryRecord:
    """One immutable historical fact: a real, already-attributed
    position, remembered in full -- never summarized lossily, never
    re-derived, never mutated after creation. `lifecycle_snapshot`/
    `attribution_snapshot` are the VERBATIM `to_dict()` output of the
    real `PositionLifecycle`/`PositionOutcomeAttribution` that produced
    this memory -- the single source of truth for every underlying
    field; the top-level fields below exist purely as QUERY CONVENIENCE
    (fast filtering without re-parsing nested dicts), never as a second,
    competing copy of the truth."""

    memory_id: str
    session_id: str
    position_id: str
    candidate_id: str
    strategy_family: Optional[str]
    entry_timestamp: str
    exit_timestamp: Optional[str]
    recorded_at: str

    # --- Query-convenience extracts (verbatim copies, never re-derived) ---
    entry_regime: Optional[str]
    entry_direction: Optional[str]
    underlying_symbol: Optional[str]
    outcome_direction: str                 # OUTCOME_PROFIT/LOSS/BREAKEVEN/UNKNOWN, copied from attribution.
    realized_pnl: Optional[float]
    pnl_status: str                        # KNOWN/UNKNOWN/PARTIAL-as-UNKNOWN-for-querying -- see engine.py's own mapping.
    final_thesis_status: Optional[str]
    primary_cause: Optional[str]
    management_status: str                 # one of ALL_EPISTEMIC_STATUSES -- NOT_APPLICABLE if zero management events ever recorded.
    greeks_status: str                     # one of ALL_EPISTEMIC_STATUSES.
    premium_behaviour_status: str          # one of ALL_EPISTEMIC_STATUSES.
    portfolio_context_status: str          # one of ALL_EPISTEMIC_STATUSES -- NOT_AVAILABLE unless a PortfolioSnapshot was supplied at recording time.

    # --- Full verbatim snapshots -- the actual source of truth. -----------
    lifecycle_snapshot: Dict[str, Any]
    attribution_snapshot: Dict[str, Any]
    portfolio_context_snapshot: Optional[Dict[str, Any]]

    schema_version: str = SCHEMA_VERSION
    # Phase 5 (Nervous System Integration): query-convenience extracts of
    # PositionOutcomeAttribution.mfe/.mae -- same "verbatim copy, never
    # re-derived" discipline as every other top-level field above. Also
    # already present, unextracted, inside attribution_snapshot (this
    # dataclass's own to_dict()) -- these two fields exist only so a
    # caller can filter/sort without re-parsing that nested dict. See
    # outcome_attribution.models' own docstring for why they read None
    # on every real record today.
    mfe: Optional[float] = None
    mae: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "memory_id": self.memory_id, "session_id": self.session_id, "position_id": self.position_id,
            "candidate_id": self.candidate_id, "strategy_family": self.strategy_family,
            "entry_timestamp": self.entry_timestamp, "exit_timestamp": self.exit_timestamp,
            "recorded_at": self.recorded_at,
            "entry_regime": self.entry_regime, "entry_direction": self.entry_direction,
            "underlying_symbol": self.underlying_symbol, "outcome_direction": self.outcome_direction,
            "realized_pnl": self.realized_pnl, "pnl_status": self.pnl_status,
            "final_thesis_status": self.final_thesis_status, "primary_cause": self.primary_cause,
            "management_status": self.management_status, "greeks_status": self.greeks_status,
            "premium_behaviour_status": self.premium_behaviour_status,
            "portfolio_context_status": self.portfolio_context_status,
            "lifecycle_snapshot": self.lifecycle_snapshot, "attribution_snapshot": self.attribution_snapshot,
            "portfolio_context_snapshot": self.portfolio_context_snapshot,
            "schema_version": self.schema_version,
            "mfe": self.mfe, "mae": self.mae,
        }

    @staticmethod
    def from_dict(d: dict) -> "OutcomeMemoryRecord":
        return OutcomeMemoryRecord(
            memory_id=d["memory_id"], session_id=d["session_id"], position_id=d["position_id"],
            candidate_id=d["candidate_id"], strategy_family=d.get("strategy_family"),
            entry_timestamp=d["entry_timestamp"], exit_timestamp=d.get("exit_timestamp"),
            recorded_at=d["recorded_at"],
            entry_regime=d.get("entry_regime"), entry_direction=d.get("entry_direction"),
            underlying_symbol=d.get("underlying_symbol"), outcome_direction=d.get("outcome_direction", STATUS_UNKNOWN),
            realized_pnl=d.get("realized_pnl"), pnl_status=d.get("pnl_status", STATUS_UNKNOWN),
            final_thesis_status=d.get("final_thesis_status"), primary_cause=d.get("primary_cause"),
            management_status=d.get("management_status", STATUS_NOT_AVAILABLE),
            greeks_status=d.get("greeks_status", STATUS_NOT_AVAILABLE),
            premium_behaviour_status=d.get("premium_behaviour_status", STATUS_NOT_AVAILABLE),
            portfolio_context_status=d.get("portfolio_context_status", STATUS_NOT_AVAILABLE),
            lifecycle_snapshot=d.get("lifecycle_snapshot", {}), attribution_snapshot=d.get("attribution_snapshot", {}),
            portfolio_context_snapshot=d.get("portfolio_context_snapshot"),
            schema_version=d.get("schema_version", SCHEMA_VERSION),
            mfe=d.get("mfe"), mae=d.get("mae"),
        )


@dataclass(frozen=True)
class TransitionResult:
    outcome: str
    event_type: Optional[str]
    memory_id: Optional[str]
    reason: str

    def to_dict(self) -> dict:
        return {"outcome": self.outcome, "event_type": self.event_type, "memory_id": self.memory_id, "reason": self.reason}
