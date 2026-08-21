"""Portfolio Reality Engine -- BUJJI Options OS v3, Gate F.3 Parts 2-3.

PURPOSE: on every market update, compute each open position group's
MTM/PnL by calling the EXISTING, already-tested `portfolio_valuation.
engine.revalue()` -- once per group, filtering PaperBroker's flat
position list down to that group's own registered symbols via the
Position Reality Registry -- never re-implementing MTM mathematics.
Then refresh the account/portfolio-level risk context by calling D.1's
and D.2's own existing classification functions directly -- never a
new risk calculation, only assembling the same real functions' outputs
for D.4 to consume.

STEP 1 FINDING THIS RESTS ON: `portfolio_valuation.engine.revalue()`
is a pure function over `positions: List[dict]` (PaperBroker's own
shape) + `latest_prices`/`realized_pnl_by_symbol` -- reused verbatim,
called once per registered group.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, Optional, Tuple

from bujji.trading_brain.portfolio_valuation.engine import revalue
from bujji.trading_brain.portfolio_valuation.models import PortfolioValuation

from bujji.trading_brain.risk_governor.capital_safety_governor import (
    CapitalSafetySnapshot, CapitalSafetyThresholds, classify_capital_safety, compute_capital_metrics,
)
from bujji.trading_brain.risk_governor.portfolio_risk_aggregator import (
    PortfolioRiskSnapshot, PortfolioRiskThresholds, aggregate_portfolio_risk, classify_portfolio_risk,
)
from bujji.trading_brain.risk_governor.position_group_fold import PositionGroupState

from .position_reality_registry import PositionRealityRegistry

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class RiskContextSnapshot:
    capital_status: str
    portfolio_status: str
    portfolio_snapshot: Optional[PortfolioRiskSnapshot]
    risk_by_position_group_id: Dict[str, float]


class PortfolioRealityEngine:
    """Reuses portfolio_valuation.engine.revalue() and D.1/D.2's own
    existing classification functions -- computes no MTM/risk formula
    of its own.

    `event_bus`: optional (default None, so every existing caller is
    unaffected -- same established pattern as PaperBroker/F.4's
    TradeLifecycleExecutor). Gate V.0's own Step 1 inspection found
    this engine was the ONE place in F.0-F.5 that published nothing at
    all, meaning MTM/valuation data was otherwise unobservable to any
    passive recorder -- this is the one new, minimal event this whole
    Trading Brain runtime adds, reusing the existing HEALTH_CHANGED
    EventType (no new EventType), with the precise label carried in
    payload["stage"], exactly like every other Gate F module's own
    event-publishing convention."""

    def __init__(self, registry: PositionRealityRegistry, event_bus: Optional[object] = None) -> None:
        self._registry = registry
        self._event_bus = event_bus

    async def revalue_all(
        self, latest_prices: Dict[str, float], clock: Clock,
        price_timestamps: Optional[Dict[str, str]] = None, triggering_symbol: Optional[str] = None,
    ) -> Dict[str, PortfolioValuation]:
        """One PortfolioValuation per open registered group, each
        computed by a single call into the existing revalue()."""
        valuations: Dict[str, PortfolioValuation] = {}
        for position_group_id in await self._registry.open_group_ids():
            positions = await self._registry.positions_for_group(position_group_id)
            realized_by_symbol = self._registry.realized_pnl_by_symbol(position_group_id)
            valuation = revalue(
                positions, latest_prices, realized_by_symbol,
                price_timestamps=price_timestamps, triggering_symbol=triggering_symbol, clock=clock,
            )
            valuations[position_group_id] = valuation
            self._publish_valuation_updated(position_group_id, valuation, clock)
        return valuations

    def _publish_valuation_updated(self, position_group_id: str, valuation: PortfolioValuation, clock: Clock) -> None:
        if self._event_bus is None:
            return
        from bujji.core.event_bus import Event, EventType
        self._event_bus.publish_nowait(Event(
            type=EventType.HEALTH_CHANGED,
            payload={"stage": "POSITION_VALUATION_UPDATED", "position_group_id": position_group_id,
                     "valuation_id": valuation.valuation_id, "total_realized_pnl": valuation.total_realized_pnl,
                     "total_unrealized_pnl": valuation.total_unrealized_pnl, "total_pnl": valuation.total_pnl},
            timestamp=clock(),
        ))

    async def refresh_risk_context(
        self, capital_snapshot: CapitalSafetySnapshot,
        capital_safety_thresholds: Optional[CapitalSafetyThresholds],
        portfolio_risk_thresholds: Optional[PortfolioRiskThresholds],
        margin_snapshot: Optional[object], margin_explanation: Optional[object],
        position_groups: list, clock: Clock,
    ) -> RiskContextSnapshot:
        """Part 3: assembles current reality for D.1-D.6 -- no new
        risk calculation. `position_groups` is the caller-supplied
        List[PositionGroupState] (e.g. from the real Position Group
        Journal, exactly as E.2/E.3 already require) -- this engine
        does not gather it itself, matching those gates' own
        established ownership boundary."""
        active_thresholds = capital_safety_thresholds or CapitalSafetyThresholds()
        metrics = compute_capital_metrics(capital_snapshot, active_thresholds)
        capital_status, _ = classify_capital_safety(capital_snapshot, metrics)

        risk_by_position_group_id = {
            pg_id: self._registry.initial_risk(pg_id) for pg_id in await self._registry.open_group_ids()
        }

        portfolio_status = "INVALID"
        portfolio_snapshot = None
        if position_groups:
            portfolio_snapshot = aggregate_portfolio_risk(
                position_groups, margin_snapshot, margin_explanation, risk_by_position_group_id, clock,
            )
            portfolio_status, _ = classify_portfolio_risk(portfolio_snapshot, portfolio_risk_thresholds)

        return RiskContextSnapshot(
            capital_status=capital_status, portfolio_status=portfolio_status,
            portfolio_snapshot=portfolio_snapshot, risk_by_position_group_id=risk_by_position_group_id,
        )
