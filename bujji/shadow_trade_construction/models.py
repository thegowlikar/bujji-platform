"""Shadow Trade Construction — Phase 14, Tasks 1-4 (scoped first slice).
Pure models, no IO, no broker, no execution.

`ShadowTradeCandidate` is a NEW, dedicated, read-only concept -- deliberately
NOT layered onto `TradeIntentAssessment` (which stays a pure description with
no strike/expiry/quantity, per its own design) and NOT a duplicate of
`msi_trade_construction.models.TradeConstructionAssessment` (which this
package's engine.py REUSES unmodified via an input-shape adapter -- see
engine.py's module docstring for the full reuse rationale). This model exists
to carry the EXTRA provenance a live, decision-traceable shadow candidate
needs that the Bhavcopy-oriented TradeConstructionAssessment was never
designed to carry: per-leg bid/ask (not just a blended mid), the originating
intelligence cycle/snapshot ids, and an explicit construction status vocabulary
distinguishing WHY a candidate could or could not be built.

No value here is invented. Every field is either copied directly from a
real upstream record, deterministically derived from real fields, or
explicitly None/UNKNOWN.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

# --- Construction status vocabulary (Task 3) --------------------------------
STATUS_CONSTRUCTED = "CONSTRUCTED"
STATUS_PARTIALLY_CONSTRUCTIBLE = "PARTIALLY_CONSTRUCTIBLE"
STATUS_NOT_CONSTRUCTIBLE = "NOT_CONSTRUCTIBLE"
STATUS_INSUFFICIENT_MARKET_DATA = "INSUFFICIENT_MARKET_DATA"
STATUS_INVALID_INTENT = "INVALID_INTENT"
STATUS_INVALID_INSTRUMENT = "INVALID_INSTRUMENT"
STATUS_INVALID_QUOTE = "INVALID_QUOTE"

ALL_CONSTRUCTION_STATUSES = (
    STATUS_CONSTRUCTED, STATUS_PARTIALLY_CONSTRUCTIBLE, STATUS_NOT_CONSTRUCTIBLE,
    STATUS_INSUFFICIENT_MARKET_DATA, STATUS_INVALID_INTENT, STATUS_INVALID_INSTRUMENT,
    STATUS_INVALID_QUOTE,
)


@dataclass(frozen=True)
class ShadowTradeLeg:
    """One leg of a hypothetical position -- mirrors
    `msi_trade_construction.models.StrikeLeg`'s shape/role vocabulary
    (reused directly, not reinvented) plus the separate bid/ask this
    package additionally preserves."""

    role: str                      # bujji.msi_trade_construction.taxonomy.ALL_STRIKE_ROLES
    option_type: str               # "CE" | "PE"
    strike: float
    expiry: str
    side: str                      # "BUY" | "SELL"
    ratio: int
    entry_mid: Optional[float]     # what msi_trade_construction calls "premium" -- real quoted mid, never fabricated.
    entry_bid: Optional[float]
    entry_ask: Optional[float]
    open_interest: Optional[float]
    delta: Optional[float]         # solved live via the SAME pure Black-Scholes function msi_trade_construction reuses.
    reasoning: Tuple[str, ...]


@dataclass(frozen=True)
class ShadowTradeCandidate:
    candidate_id: str
    timestamp: str
    source_cycle_id: str                   # the intelligence_cycle record's own timestamp, used as its id (no separate cycle-id field exists upstream).
    strategy_family: str
    strategy_variant: Optional[str]        # None in this first slice -- no per-family variant taxonomy exists yet upstream.
    market_regime: Optional[str]
    direction: Optional[str]
    thesis: Optional[str]                  # trade_thesis.thesis_type, if present.
    selection_confidence: Optional[str]
    consensus_state: Optional[str]
    opportunity_state: Optional[str]
    underlying_symbol: str
    underlying_price: Optional[float]
    expiry: Optional[str]
    legs: Tuple[ShadowTradeLeg, ...]
    quantity: Optional[int]                # lot multiples; None in this first slice -- position sizing is explicitly out of scope (Task 2 said "do not invent values").
    lot_size: Optional[int]
    contracts: Optional[int]               # lot_size * quantity, only if both are real -- never guessed.
    construction_confidence: str           # "HIGH" | "LOW" | "NONE" -- HIGH only when every leg has a real bid+ask; LOW when mid-only for any leg; NONE when not constructed.
    construction_status: str               # one of ALL_CONSTRUCTION_STATUSES.
    construction_reason: str               # human-readable, always populated (even on success).
    required_evidence: Tuple[str, ...]     # domains this construction attempt needed.
    evidence_snapshot: Dict[str, Optional[str]]  # domain -> status, copied from the record's own fields at decision time.
    source_market_snapshot_id: str         # the MarketSnapshot's own timestamp, used as its id (no separate snapshot-id field exists upstream).

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "timestamp": self.timestamp,
            "source_cycle_id": self.source_cycle_id,
            "strategy_family": self.strategy_family,
            "strategy_variant": self.strategy_variant,
            "market_regime": self.market_regime,
            "direction": self.direction,
            "thesis": self.thesis,
            "selection_confidence": self.selection_confidence,
            "consensus_state": self.consensus_state,
            "opportunity_state": self.opportunity_state,
            "underlying_symbol": self.underlying_symbol,
            "underlying_price": self.underlying_price,
            "expiry": self.expiry,
            "legs": [
                {
                    "role": l.role, "option_type": l.option_type, "strike": l.strike, "expiry": l.expiry,
                    "side": l.side, "ratio": l.ratio, "entry_mid": l.entry_mid, "entry_bid": l.entry_bid,
                    "entry_ask": l.entry_ask, "open_interest": l.open_interest, "delta": l.delta,
                    "reasoning": list(l.reasoning),
                }
                for l in self.legs
            ],
            "quantity": self.quantity,
            "lot_size": self.lot_size,
            "contracts": self.contracts,
            "construction_confidence": self.construction_confidence,
            "construction_status": self.construction_status,
            "construction_reason": self.construction_reason,
            "required_evidence": list(self.required_evidence),
            "evidence_snapshot": self.evidence_snapshot,
            "source_market_snapshot_id": self.source_market_snapshot_id,
        }
