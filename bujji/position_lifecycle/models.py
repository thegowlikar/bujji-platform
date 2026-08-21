"""Position Lifecycle -- Phase 15G. Pure models, no IO, no broker, no
execution.

Canonical identity decision (forensic finding): `ShadowTradeCandidate.
candidate_id` (Phase 14, deterministic `md5(source_cycle_id|family)`)
is NOT reused as the position identity -- it identifies "what a given
cycle WOULD construct," recomputed identically every time that same
cycle/family pair is observed, regardless of whether Bujji ever acts
on it. A position identity must instead identify the deliberate MOMENT
of entry -- a distinct, one-time event. `position_id` is therefore
derived from (session_id, candidate_id, entry_timestamp): deterministic
(replay-safe), globally unique (session-scoped), independent of any
broker order id, independent of symbol/timestamp alone, and NEVER
reused (a second POSITION_OPENED for the same candidate_id at a
DIFFERENT entry_timestamp is a genuinely different position).

Lifecycle scope, deliberately minimal (per Step 2's explicit "smallest
safe state machine" instruction): CANDIDATE -> OPEN -> CLOSED. MONITORING
is not a stored transition -- it's a DERIVED display label (OPEN with at
least one thesis evaluation recorded), avoiding an unnecessary event
type for something that isn't actually a state change. ADJUSTING/
RISK_REDUCING/EXIT_PENDING/OUTCOME_RECORDED are explicitly deferred to
a future phase, per the mission's own instruction not to assume every
state belongs in the first implementation.

Reuses `bujji.state_persistence.EventStore`/`PersistedEvent` DIRECTLY
(Phase 15B) -- no new persistence mechanism was needed or built.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

SCHEMA_VERSION = "1.0.0"

# --- Lifecycle status (stored) ----------------------------------------------
STATUS_OPEN = "OPEN"
STATUS_CLOSED = "CLOSED"
ALL_STATUSES = (STATUS_OPEN, STATUS_CLOSED)

# --- Lifecycle status (derived, display-only, never stored) ----------------
DISPLAY_MONITORING = "MONITORING"  # OPEN + >=1 thesis evaluation recorded.

# --- Event types -------------------------------------------------------------
EVENT_POSITION_OPENED = "POSITION_OPENED"
EVENT_THESIS_EVALUATED = "THESIS_EVALUATED"
# Phase 15I: records an ADVISORY management assessment
# (HOLD/ADJUST/HEDGE/ROLL/EXIT/UNKNOWN, bujji.position_management) --
# same guard discipline as THESIS_EVALUATED (only accepted while OPEN).
# This event NEVER implies an adjustment/hedge/roll actually happened --
# no POSITION_ADJUSTED/POSITION_HEDGED/POSITION_ROLLED event exists yet
# (deliberately not invented without a proven need, per the mission's
# own "do not invent events unnecessarily" instruction).
EVENT_MANAGEMENT_ASSESSED = "MANAGEMENT_ASSESSED"
EVENT_POSITION_CLOSED = "POSITION_CLOSED"
# Phase 15J: records an immutable, post-close ANALYTICAL attribution
# (bujji.outcome_attribution) -- opposite guard direction from
# THESIS_EVALUATED/MANAGEMENT_ASSESSED: only accepted while CLOSED
# (an open position has no outcome yet -- see engine.py's own
# NOT_READY handling). Never mutates status, legs, or any decision
# field -- explanation only.
EVENT_OUTCOME_ATTRIBUTED = "OUTCOME_ATTRIBUTED"
ALL_EVENT_TYPES = (
    EVENT_POSITION_OPENED, EVENT_THESIS_EVALUATED, EVENT_MANAGEMENT_ASSESSED,
    EVENT_POSITION_CLOSED, EVENT_OUTCOME_ATTRIBUTED,
)

# --- Transition outcomes ------------------------------------------------------
TRANSITION_ACCEPTED = "ACCEPTED"
TRANSITION_IDEMPOTENT = "IDEMPOTENT"   # exact duplicate event_id -- a real no-op, not an error.
TRANSITION_REJECTED = "REJECTED"       # invalid transition / unknown position / conflicting payload / wrong session.

# --- P&L status (Phase 15K) ---------------------------------------------------
# Forensic finding: `entry_bid`/`entry_ask` exist on the real, upstream
# `ShadowTradeLeg` (Phase 14) but were never carried onto `LegRecord`
# (Phase 15G) -- confirmed DERIVABLE (real data exists one layer up),
# not MISSING, and captured additively below. `lot_size`/multiplier
# exists only on `ShadowTradeCandidate` (Phase 14) and was never
# persisted into the lifecycle at all -- also DERIVABLE, now captured
# on `EntrySnapshot`. Exit price/premium/bid/ask are genuinely MISSING
# system-wide until this phase (no PaperBroker linkage exists, Phase
# 15B's own disclosed limitation) -- `PNL_UNKNOWN` is the honest
# default whenever real exit price evidence does not exist, NEVER a
# fabricated zero.
PNL_COMPLETE = "COMPLETE"   # every leg's exit price is real -- P&L is a real, deterministic calculation.
PNL_PARTIAL = "PARTIAL"     # some, but not all, legs have real exit prices.
PNL_UNKNOWN = "UNKNOWN"     # no real exit price evidence exists for this leg/position at all.
ALL_PNL_STATUSES = (PNL_COMPLETE, PNL_PARTIAL, PNL_UNKNOWN)


@dataclass(frozen=True)
class LegRecord:
    """One leg of a (possibly multi-leg) position -- its OWN identity,
    linked to (never merged into) the parent position_id."""

    leg_id: str
    role: str                      # msi_trade_construction.taxonomy strike role, copied from the candidate leg.
    option_type: str               # "CE" | "PE"
    strike: float
    expiry: str
    side: str                      # "BUY" | "SELL"
    quantity: Optional[int]
    entry_premium: Optional[float]
    entry_delta: Optional[float]
    # Phase 15K additive fields -- real data one layer up (ShadowTradeLeg,
    # Phase 14), captured here for the first time. Default None so every
    # pre-Phase-15K persisted record hydrates safely (backward compatible).
    entry_bid: Optional[float] = None
    entry_ask: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "leg_id": self.leg_id, "role": self.role, "option_type": self.option_type,
            "strike": self.strike, "expiry": self.expiry, "side": self.side,
            "quantity": self.quantity, "entry_premium": self.entry_premium, "entry_delta": self.entry_delta,
            "entry_bid": self.entry_bid, "entry_ask": self.entry_ask,
        }

    @staticmethod
    def from_dict(d: dict) -> "LegRecord":
        return LegRecord(
            leg_id=d["leg_id"], role=d["role"], option_type=d["option_type"], strike=d["strike"],
            expiry=d["expiry"], side=d["side"], quantity=d.get("quantity"),
            entry_premium=d.get("entry_premium"), entry_delta=d.get("entry_delta"),
            entry_bid=d.get("entry_bid"), entry_ask=d.get("entry_ask"),
        )


@dataclass(frozen=True)
class ExitLegRecord:
    """One leg's structured exit evidence + computed P&L -- immutable,
    recorded once via a structured POSITION_CLOSED payload (Phase 15K).
    `gross_leg_pnl`/`net_leg_pnl` are computed by
    `bujji.position_lifecycle.pnl` (a pure function, never re-derived
    here) and stored as the replay result, same discipline as every
    other accumulated field in this module."""

    leg_id: str
    exit_price: Optional[float]      # per-unit exit premium (mid), same units as LegRecord.entry_premium.
    exit_bid: Optional[float]
    exit_ask: Optional[float]
    exit_quantity: Optional[int]      # None -> assumed equal to the leg's own entry quantity (full exit; see PARTIAL_EXIT=DEFERRED).
    gross_leg_pnl: Optional[float]
    fees: Optional[float]
    net_leg_pnl: Optional[float]
    pnl_status: str                   # one of ALL_PNL_STATUSES.

    def to_dict(self) -> dict:
        return {
            "leg_id": self.leg_id, "exit_price": self.exit_price, "exit_bid": self.exit_bid,
            "exit_ask": self.exit_ask, "exit_quantity": self.exit_quantity,
            "gross_leg_pnl": self.gross_leg_pnl, "fees": self.fees, "net_leg_pnl": self.net_leg_pnl,
            "pnl_status": self.pnl_status,
        }

    @staticmethod
    def from_dict(d: dict) -> "ExitLegRecord":
        return ExitLegRecord(
            leg_id=d["leg_id"], exit_price=d.get("exit_price"), exit_bid=d.get("exit_bid"),
            exit_ask=d.get("exit_ask"), exit_quantity=d.get("exit_quantity"),
            gross_leg_pnl=d.get("gross_leg_pnl"), fees=d.get("fees"), net_leg_pnl=d.get("net_leg_pnl"),
            pnl_status=d.get("pnl_status", PNL_UNKNOWN),
        )


@dataclass(frozen=True)
class StructuredExit:
    """Position-level structured exit -- gross/fees/slippage/net kept
    EXPLICITLY separate (Step 4), never collapsed into one number.
    `gross_realized_pnl` may be known even when `net_realized_pnl` is
    not (fees/slippage genuinely unavailable) -- never silently
    assumed zero."""

    exit_timestamp: str
    exit_reason: str
    legs: Tuple[ExitLegRecord, ...]
    gross_realized_pnl: Optional[float]
    fees: Optional[float]
    slippage: Optional[float]
    net_realized_pnl: Optional[float]
    pnl_status: str   # one of ALL_PNL_STATUSES -- COMPLETE only when EVERY leg's pnl_status is COMPLETE.

    def to_dict(self) -> dict:
        return {
            "exit_timestamp": self.exit_timestamp, "exit_reason": self.exit_reason,
            "legs": [l.to_dict() for l in self.legs], "gross_realized_pnl": self.gross_realized_pnl,
            "fees": self.fees, "slippage": self.slippage, "net_realized_pnl": self.net_realized_pnl,
            "pnl_status": self.pnl_status,
        }

    @staticmethod
    def from_dict(d: dict) -> "StructuredExit":
        return StructuredExit(
            exit_timestamp=d["exit_timestamp"], exit_reason=d["exit_reason"],
            legs=tuple(ExitLegRecord.from_dict(l) for l in d.get("legs", [])),
            gross_realized_pnl=d.get("gross_realized_pnl"), fees=d.get("fees"), slippage=d.get("slippage"),
            net_realized_pnl=d.get("net_realized_pnl"), pnl_status=d.get("pnl_status", PNL_UNKNOWN),
        )


@dataclass(frozen=True)
class EntrySnapshot:
    """What Bujji believed at the moment this position became OPEN --
    compact by reference, not a duplicated MarketSnapshot: `source_cycle_id`
    points back at the real, already-persisted intelligence_cycle record
    (Phase 8+) that produced this candidate; deterministic replay can
    always recover the full context from there. Only small, genuinely
    useful scalars are captured here directly, mirroring
    PositionEntrySnapshot's own precedent (Phase 15/15F)."""

    candidate_id: str
    source_cycle_id: str
    strategy_family: Optional[str]
    entry_timestamp: str
    underlying_price: Optional[float]
    entry_direction: Optional[str]
    entry_regime: Optional[str]
    entry_thesis: Optional[str]
    entry_confidence: Optional[str]
    entry_greeks: Optional[dict]              # Phase 15E field, verbatim, same source cycle -- None if unavailable.
    entry_premium_behaviour: Optional[dict]    # Phase 15E field, verbatim -- None if unavailable.
    # Phase 15K additive field: the real contract multiplier (shares per
    # lot), copied from ShadowTradeCandidate.lot_size (Phase 14) at open
    # time -- never persisted anywhere in the lifecycle before this
    # phase. Default None so every pre-Phase-15K record hydrates safely.
    lot_size: Optional[int] = None
    # Phase 15M additive field: the real underlying ticker, copied from
    # ShadowTradeCandidate.underlying_symbol (Phase 14) -- existed one
    # layer up but was never carried into the lifecycle before now
    # (only underlying_PRICE was). Needed for portfolio-level
    # concentration-by-underlying; default None so every pre-Phase-15M
    # record hydrates safely.
    underlying_symbol: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id, "source_cycle_id": self.source_cycle_id,
            "strategy_family": self.strategy_family, "entry_timestamp": self.entry_timestamp,
            "underlying_price": self.underlying_price, "entry_direction": self.entry_direction,
            "entry_regime": self.entry_regime, "entry_thesis": self.entry_thesis,
            "entry_confidence": self.entry_confidence, "entry_greeks": self.entry_greeks,
            "entry_premium_behaviour": self.entry_premium_behaviour, "lot_size": self.lot_size,
            "underlying_symbol": self.underlying_symbol,
        }

    @staticmethod
    def from_dict(d: dict) -> "EntrySnapshot":
        return EntrySnapshot(
            candidate_id=d["candidate_id"], source_cycle_id=d["source_cycle_id"],
            strategy_family=d.get("strategy_family"), entry_timestamp=d["entry_timestamp"],
            underlying_price=d.get("underlying_price"), entry_direction=d.get("entry_direction"),
            entry_regime=d.get("entry_regime"), entry_thesis=d.get("entry_thesis"),
            entry_confidence=d.get("entry_confidence"), entry_greeks=d.get("entry_greeks"),
            entry_premium_behaviour=d.get("entry_premium_behaviour"), lot_size=d.get("lot_size"),
            underlying_symbol=d.get("underlying_symbol"),
        )


@dataclass(frozen=True)
class PositionLifecycle:
    """The current, replay-derived state of ONE position -- never
    mutated in place (every apply_event() call returns a NEW instance),
    matching every other frozen cross-cycle state in this codebase
    (RegimeMemoryState, ObservationMemory, PremiumBehaviourState)."""

    position_id: str
    session_id: str
    status: str
    entry: EntrySnapshot
    legs: Tuple[LegRecord, ...]
    opened_at: str
    # Thesis history -- each entry is a real ThesisEvaluation.to_dict()
    # (Phase 15F), tagged with the cycle_id it was evaluated against.
    # Persisted as EVENTS (Step 7: "persist events/evaluations, not
    # redundant mutable state") -- this tuple is the REPLAY RESULT of
    # those events, not a second source of truth.
    thesis_evaluations: Tuple[Dict[str, Any], ...] = ()
    # Phase 15I: management assessment history -- each entry a real
    # PositionManagementAssessment.to_dict() (bujji.position_management).
    # Same "persist events, not redundant mutable state" discipline as
    # thesis_evaluations above -- this tuple is the REPLAY RESULT of
    # MANAGEMENT_ASSESSED events, never a second source of truth. An
    # assessment recorded here is ALWAYS advisory -- it never implies
    # an adjustment/hedge/roll actually happened.
    management_assessments: Tuple[Dict[str, Any], ...] = ()
    closed_at: Optional[str] = None
    exit_reason: Optional[str] = None
    final_thesis_status: Optional[str] = None
    latest_management_recommendation: Optional[str] = None
    realized_pnl: Optional[float] = None  # Honestly None -- no PaperBroker linkage exists yet (Step 11/12/13).
    # Phase 15J: a real PositionOutcomeAttribution.to_dict()
    # (bujji.outcome_attribution), recorded once via an
    # OUTCOME_ATTRIBUTED event -- immutable historical evidence, never
    # re-derived on read, never influences status/legs/any decision field.
    outcome_attribution: Optional[Dict[str, Any]] = None
    # Phase 15K additive field: a real StructuredExit.to_dict()
    # (this module), recorded once as part of a structured
    # POSITION_CLOSED payload (see Step 7's forensic decision to enrich
    # the EXISTING event rather than add a second one). `realized_pnl`
    # above is still populated for backward compatibility with Phase
    # 15J's existing Outcome Attribution consumer (net if known, else
    # gross, else None) -- `structured_exit` carries the FULL gross/
    # fees/slippage/net/per-leg breakdown. Default None so every
    # pre-Phase-15K record hydrates safely.
    structured_exit: Optional[Dict[str, Any]] = None

    @property
    def display_status(self) -> str:
        if self.status == STATUS_OPEN and self.thesis_evaluations:
            return DISPLAY_MONITORING
        return self.status

    def to_dict(self) -> dict:
        return {
            "position_id": self.position_id, "session_id": self.session_id, "status": self.status,
            "display_status": self.display_status, "entry": self.entry.to_dict(),
            "legs": [l.to_dict() for l in self.legs], "opened_at": self.opened_at,
            "thesis_evaluations": list(self.thesis_evaluations),
            "management_assessments": list(self.management_assessments),
            "closed_at": self.closed_at,
            "exit_reason": self.exit_reason, "final_thesis_status": self.final_thesis_status,
            "latest_management_recommendation": self.latest_management_recommendation,
            "realized_pnl": self.realized_pnl, "outcome_attribution": self.outcome_attribution,
            "structured_exit": self.structured_exit,
        }


@dataclass(frozen=True)
class TransitionResult:
    """The outcome of applying ONE event to lifecycle state -- always
    returned alongside the (possibly unchanged) state dict, so a caller
    can distinguish a real state change from a no-op/rejection instead
    of silently guessing from before/after equality."""

    outcome: str            # TRANSITION_ACCEPTED / TRANSITION_IDEMPOTENT / TRANSITION_REJECTED.
    event_type: Optional[str]
    position_id: Optional[str]
    reason: str

    def to_dict(self) -> dict:
        return {
            "outcome": self.outcome, "event_type": self.event_type,
            "position_id": self.position_id, "reason": self.reason,
        }
