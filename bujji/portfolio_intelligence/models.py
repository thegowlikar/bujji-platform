"""Portfolio Intelligence -- Phase 15M. Pure models, no IO, no broker,
no execution, no new state machine.

Forensic audit findings driving this design:
- Multiple `PositionLifecycle` instances ALREADY coexist naturally --
  `bujji.position_lifecycle.recovery.hydrate_position_lifecycles`
  returns `Dict[position_id, PositionLifecycle]` for every position in
  a session (Phase 15G, unchanged). Portfolio Intelligence needs no new
  discovery mechanism -- it consumes that dict directly.
- `PositionLifecycle` remains the ONLY source of truth for a position's
  state. `PortfolioSnapshot` is a pure, read-only AGGREGATION over
  already-hydrated/already-replayed lifecycle state -- never a second
  reducer that consumes raw events itself, and never mutates or
  re-derives any individual position's own fields.
- Greeks: `LegRecord.entry_delta` is the real, leg-accurate delta
  (Phase 14's `ShadowTradeLeg.delta`, strike-correct). Gamma/theta/
  vega exist ONLY on `EntrySnapshot.entry_greeks` -- a single ATM
  CE/PE snapshot captured once at position-entry time, NOT resolved
  per-leg. For a leg whose own strike differs from that ATM snapshot's
  `strike` field, gamma/theta/vega are genuinely UNKNOWN (a strike
  mismatch, not a missing value) -- never approximated from the wrong
  strike's greeks.
- `underlying_symbol` was captured newly this phase onto `EntrySnapshot`
  (Phase 15M additive change, mirrors Phase 15K's own entry_bid/
  entry_ask/lot_size precedent) -- real data one layer up
  (`ShadowTradeCandidate.underlying_symbol`, Phase 14) that had never
  been carried into the lifecycle before, needed for concentration-by-
  underlying.
- Unrealized P&L requires a LIVE market price. A pure, replay-safe
  snapshot built only from persisted `PositionLifecycle` state has no
  such price for OPEN positions -- unrealized P&L is therefore ALWAYS
  `UNKNOWN` in the pure snapshot; a caller may optionally supply a
  live-price overlay (never fabricated here).
- Account equity / available margin: exist only on a LIVE `PaperBroker`
  instance (`get_funds()`), never persisted into any lifecycle event
  -- genuinely `MISSING` from a pure, replay-safe snapshot. Represented
  here as an explicit optional overlay field, `None` unless the caller
  supplies real broker-read values (never fabricated).
- Legacy `bujji.trading_brain.risk_governor.portfolio_risk_aggregator`
  / `bujji.trading_brain.portfolio_valuation` exist and DO implement a
  portfolio concept -- but on the disconnected, `position_group_id`-
  keyed legacy identity scheme (same finding repeated since Phase 13).
  NOT connected here, per the mission's own explicit instruction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

SCHEMA_VERSION = "1.0.0"

# --- Portfolio-level P&L/exposure status (UNKNOWN first-class) --------------
STATUS_KNOWN = "KNOWN"
STATUS_PARTIAL = "PARTIAL"   # some, but not all, contributing positions/legs resolved.
STATUS_UNKNOWN = "UNKNOWN"   # no real evidence exists at all.
ALL_AGGREGATE_STATUSES = (STATUS_KNOWN, STATUS_PARTIAL, STATUS_UNKNOWN)

# --- Portfolio conflict findings ---------------------------------------------
CONFLICT_NO_CONFLICT = "NO_CONFLICT"
CONFLICT_DIRECTIONAL = "DIRECTIONAL_CONFLICT"
CONFLICT_UNDERLYING_CONCENTRATION = "UNDERLYING_CONCENTRATION"
CONFLICT_EXPIRY_CONCENTRATION = "EXPIRY_CONCENTRATION"
CONFLICT_GREEK_CONCENTRATION = "GREEK_CONCENTRATION"
CONFLICT_CORRELATED_EXPOSURE = "CORRELATED_EXPOSURE"
CONFLICT_UNKNOWN = "UNKNOWN"
ALL_CONFLICT_FINDINGS = (
    CONFLICT_NO_CONFLICT, CONFLICT_DIRECTIONAL, CONFLICT_UNDERLYING_CONCENTRATION,
    CONFLICT_EXPIRY_CONCENTRATION, CONFLICT_GREEK_CONCENTRATION, CONFLICT_CORRELATED_EXPOSURE, CONFLICT_UNKNOWN,
)

# --- Portfolio thesis health -------------------------------------------------
THESIS_HEALTH_ALL_INTACT = "ALL_INTACT"
THESIS_HEALTH_MIXED = "MIXED"
THESIS_HEALTH_DETERIORATING = "DETERIORATING"
THESIS_HEALTH_NO_OPEN_POSITIONS = "NO_OPEN_POSITIONS"
THESIS_HEALTH_UNKNOWN = "UNKNOWN"
ALL_THESIS_HEALTH_STATUSES = (
    THESIS_HEALTH_ALL_INTACT, THESIS_HEALTH_MIXED, THESIS_HEALTH_DETERIORATING,
    THESIS_HEALTH_NO_OPEN_POSITIONS, THESIS_HEALTH_UNKNOWN,
)


@dataclass(frozen=True)
class GreekExposure:
    """Net exposure for ONE greek across the whole book. `status`
    reflects whether EVERY contributing leg resolved -- a partial sum
    is never silently presented as complete."""

    net: Optional[float]
    status: str  # one of ALL_AGGREGATE_STATUSES.

    def to_dict(self) -> dict:
        return {"net": self.net, "status": self.status}


@dataclass(frozen=True)
class PremiumExposure:
    """Premium bought vs. sold across the whole book -- kept separate
    (never netted into one number), same discipline as Phase 15K's own
    gross/net P&L separation."""

    total_premium_bought: Optional[float]
    total_premium_sold: Optional[float]
    status: str

    def to_dict(self) -> dict:
        return {
            "total_premium_bought": self.total_premium_bought,
            "total_premium_sold": self.total_premium_sold, "status": self.status,
        }


@dataclass(frozen=True)
class ConcentrationEntry:
    key: str            # underlying symbol, expiry, or strategy_family value.
    position_count: int
    position_ids: Tuple[str, ...]

    def to_dict(self) -> dict:
        return {"key": self.key, "position_count": self.position_count, "position_ids": list(self.position_ids)}


@dataclass(frozen=True)
class PortfolioPnL:
    """Realized/gross/net kept explicitly separate, same discipline as
    Phase 15K's `StructuredExit`. Unrealized P&L is ALWAYS UNKNOWN in a
    pure, replay-safe snapshot -- it requires a live market price this
    snapshot never has (Step 7's own explicit instruction)."""

    realized_gross_pnl: Optional[float]
    realized_net_pnl: Optional[float]
    total_fees: Optional[float]
    total_slippage: Optional[float]
    unrealized_pnl: Optional[float]        # ALWAYS None from a pure snapshot; only a caller-supplied overlay can populate it.
    unrealized_pnl_status: str             # always STATUS_UNKNOWN unless a live-price overlay was supplied.
    status: str                            # realized P&L aggregate status.
    per_position: Dict[str, Optional[float]] = field(default_factory=dict)  # position_id -> best-known realized_pnl.

    def to_dict(self) -> dict:
        return {
            "realized_gross_pnl": self.realized_gross_pnl, "realized_net_pnl": self.realized_net_pnl,
            "total_fees": self.total_fees, "total_slippage": self.total_slippage,
            "unrealized_pnl": self.unrealized_pnl, "unrealized_pnl_status": self.unrealized_pnl_status,
            "status": self.status, "per_position": dict(self.per_position),
        }


@dataclass(frozen=True)
class PortfolioConflictFinding:
    finding: str            # one of ALL_CONFLICT_FINDINGS.
    detail: str             # human-readable explanation.
    involved_position_ids: Tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "finding": self.finding, "detail": self.detail,
            "involved_position_ids": list(self.involved_position_ids),
        }


@dataclass(frozen=True)
class CapitalOverlay:
    """Optional, caller-supplied real broker read (`PaperBroker.
    get_funds()`) -- NEVER fabricated or derived here. `None` fields
    mean genuinely not supplied; this dataclass exists so the snapshot
    always has an explicit, honest place for this data rather than
    silently omitting it."""

    account_equity: Optional[float] = None
    available_margin: Optional[float] = None
    source: str = "NOT_SUPPLIED"  # "NOT_SUPPLIED" | "LIVE_BROKER_READ".

    def to_dict(self) -> dict:
        return {"account_equity": self.account_equity, "available_margin": self.available_margin, "source": self.source}


@dataclass(frozen=True)
class PortfolioSnapshot:
    """The whole-book view -- a pure, deterministic function of an
    already-hydrated `Dict[position_id, PositionLifecycle]`. Never
    stored as an event itself (Step 3: aggregation layer only, no new
    persistence format) -- recomputed on demand from the SAME lifecycle
    state every caller already has, so it is automatically as
    replay-safe as `PositionLifecycle` itself."""

    session_id: str
    as_of: Optional[str]
    schema_version: str

    position_count: int
    open_position_ids: Tuple[str, ...]
    closed_position_ids: Tuple[str, ...]

    net_delta: GreekExposure
    net_gamma: GreekExposure
    net_theta: GreekExposure
    net_vega: GreekExposure
    premium_exposure: PremiumExposure
    capital_deployed: Optional[float]        # sum of |entry_premium * quantity * lot_size| for OPEN legs -- real, derivable.
    capital_deployed_status: str

    concentration_by_underlying: Tuple[ConcentrationEntry, ...]
    concentration_by_strategy_family: Tuple[ConcentrationEntry, ...]
    concentration_by_expiry: Tuple[ConcentrationEntry, ...]

    pnl: PortfolioPnL
    capital: CapitalOverlay

    conflicts: Tuple[PortfolioConflictFinding, ...]
    thesis_health: str                        # one of ALL_THESIS_HEALTH_STATUSES.
    thesis_health_detail: Dict[str, Any]       # counts: intact/weakening/invalidated/unknown.
    management_recommendation_summary: Dict[str, int]  # recommendation -> count, across OPEN positions only.

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id, "as_of": self.as_of, "schema_version": self.schema_version,
            "position_count": self.position_count,
            "open_position_ids": list(self.open_position_ids), "closed_position_ids": list(self.closed_position_ids),
            "net_delta": self.net_delta.to_dict(), "net_gamma": self.net_gamma.to_dict(),
            "net_theta": self.net_theta.to_dict(), "net_vega": self.net_vega.to_dict(),
            "premium_exposure": self.premium_exposure.to_dict(),
            "capital_deployed": self.capital_deployed, "capital_deployed_status": self.capital_deployed_status,
            "concentration_by_underlying": [c.to_dict() for c in self.concentration_by_underlying],
            "concentration_by_strategy_family": [c.to_dict() for c in self.concentration_by_strategy_family],
            "concentration_by_expiry": [c.to_dict() for c in self.concentration_by_expiry],
            "pnl": self.pnl.to_dict(), "capital": self.capital.to_dict(),
            "conflicts": [c.to_dict() for c in self.conflicts],
            "thesis_health": self.thesis_health, "thesis_health_detail": dict(self.thesis_health_detail),
            "management_recommendation_summary": dict(self.management_recommendation_summary),
        }
