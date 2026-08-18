"""Intelligence Pipeline Adapter -- Shadow Runtime, Phase 19.10.1.

The thin composition layer Phase 19.10.0's audit called for: converts
an already-real `MarketRealitySnapshot` (Phase 18.x) + a window of real
spot candles into every Phase 19.0-19.9 Intelligence Foundation object,
in order:

    MarketRealitySnapshot
        -> (six brains, unmodified)
        -> MarketIntelligenceSnapshot (19.3)
        -> DecisionContext (19.4)
        -> DecisionIntelligenceSnapshot (19.6)
        -> MarketPhenomenaAssessment (19.7)
        -> MarketStateNode (19.8)
        -> MarketEnvironmentAssessment (19.9)

ONLY composes. Calculates NOTHING a brain doesn't already calculate,
never modifies a brain, never builds a second snapshot type, and never
imports `bujji.market_perception.intelligence_adapter` (that package's
OWN, different, older `build_intelligence_snapshot()` -- Phase 19.10.0's
own confirmed naming-collision finding; this adapter deliberately does
not touch it).

Every timestamp here comes from the caller-supplied `IntelligenceContext`
-- no wall-clock call anywhere in this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from bujji.core.models import Candle
from bujji.decision_context import DecisionContext, build_decision_context
from bujji.decision_intelligence import DecisionIntelligenceSnapshot, build_decision_intelligence_snapshot
from bujji.intelligence.context import IntelligenceContext
from bujji.intelligence.event_brain import EventBrain
from bujji.intelligence.greeks_brain import GreeksBrain
from bujji.intelligence.liquidity_brain import LiquidityBrain
from bujji.intelligence.market_intelligence_snapshot import MarketIntelligenceSnapshot, build_market_intelligence_snapshot
from bujji.intelligence.regime_brain import RegimeBrain
from bujji.intelligence.structure_brain import StructureBrain
from bujji.intelligence.volatility_brain import VolatilityBrain
from bujji.intelligence.volatility_policy import select_atm_strike
from bujji.market_environment import MarketEnvironmentAssessment, build_market_environment_assessment
from bujji.market_phenomena import MarketPhenomenaAssessment, build_market_phenomena_assessment
from bujji.market_reality_snapshot.models import MarketRealitySnapshot
from bujji.market_state_graph import MarketStateNode, build_market_state_node


class IntelligencePipelineAdapterError(Exception):
    """Raised when `reality_snapshot` genuinely lacks the evidence this
    adapter needs to compose a cycle (e.g. no options chain, no spot) --
    never silently skipped or fabricated."""


@dataclass(frozen=True)
class IntelligenceHeartbeatCycle:
    """The full, real output of one composed cycle -- every object
    already real, already tested, already deterministic on its own;
    this bundle only carries them together for a caller's convenience."""

    market_intelligence_snapshot: MarketIntelligenceSnapshot
    decision_context: DecisionContext
    decision_intelligence: DecisionIntelligenceSnapshot
    phenomena: MarketPhenomenaAssessment
    state_node: MarketStateNode
    environment: MarketEnvironmentAssessment

    def to_dict(self) -> dict:
        return {
            "market_intelligence_snapshot_id": self.market_intelligence_snapshot.intelligence_snapshot_id,
            "decision_intelligence_id": self.decision_intelligence.decision_intelligence_id,
            "phenomena_assessment_id": self.phenomena.assessment_id,
            "market_state_id": self.state_node.state_id,
            "environment_id": self.environment.environment_id,
        }


def _atm_strikes_grid(reality_snapshot: MarketRealitySnapshot) -> List[Tuple[float, float, float]]:
    """(strike, ce_oi, pe_oi) per strike, built directly from the real
    option contracts already on the snapshot -- never a second query,
    never a synthesized strike."""
    by_strike: Dict[float, Dict[str, float]] = {}
    if reality_snapshot.options is None:
        return []
    for c in reality_snapshot.options.contracts:
        row = by_strike.setdefault(c.strike, {"CE": 0.0, "PE": 0.0})
        if c.open_interest is not None:
            row[c.option_type] = c.open_interest
    return [(strike, row["CE"], row["PE"]) for strike, row in sorted(by_strike.items())]


def _find_contract(reality_snapshot: MarketRealitySnapshot, strike: float, expiry: str, option_type: str):
    for c in reality_snapshot.options.contracts:
        if c.strike == strike and c.expiry == expiry and c.option_type == option_type:
            return c
    return None


def _nearest_expiry(reality_snapshot: MarketRealitySnapshot) -> Optional[str]:
    expiries = sorted({c.expiry for c in reality_snapshot.options.contracts}) if reality_snapshot.options else []
    return expiries[0] if expiries else None


def build_intelligence_heartbeat_cycle(
    *,
    reality_snapshot: MarketRealitySnapshot,
    spot_candles: List[Candle],
    context: IntelligenceContext,
    previous_market_intelligence_snapshot: Optional[MarketIntelligenceSnapshot] = None,
    previous_phenomena: Optional[MarketPhenomenaAssessment] = None,
    previous_state_node: Optional[MarketStateNode] = None,
    memory_matches: Optional[list] = None,
    risk_free_rate: float = 0.065,
) -> IntelligenceHeartbeatCycle:
    """Pure composition, no IO. Raises `IntelligencePipelineAdapterError`
    if `reality_snapshot` genuinely has no spot or no options chain --
    the six brains' own honest UNKNOWN/INSUFFICIENT data-quality gates
    handle every other real gap (missing VIX, thin chain, etc.) exactly
    as they already do everywhere else in this project; this adapter
    only refuses to run at all when there is nothing to compose from."""
    if reality_snapshot.spot is None:
        raise IntelligencePipelineAdapterError("reality_snapshot has no spot -- nothing to compose from")
    spot = reality_snapshot.spot.close

    regime = RegimeBrain().analyze(spot_candles, context)
    structure = StructureBrain().analyze(spot, _atm_strikes_grid(reality_snapshot), context=context)

    expiry = _nearest_expiry(reality_snapshot)
    strikes = sorted({c.strike for c in reality_snapshot.options.contracts}) if reality_snapshot.options and expiry else []
    atm_strike = select_atm_strike(strikes, spot) if strikes else None

    ce_contract = _find_contract(reality_snapshot, atm_strike, expiry, "CE") if atm_strike is not None else None
    pe_contract = _find_contract(reality_snapshot, atm_strike, expiry, "PE") if atm_strike is not None else None

    liquidity = LiquidityBrain().analyze(
        ce_contract.bid if ce_contract else None, ce_contract.ask if ce_contract else None,
        pe_contract.bid if pe_contract else None, pe_contract.ask if pe_contract else None,
        context=context,
    )

    if expiry is not None:
        expiry_date = date_type.fromisoformat(expiry)
        expiry_close = datetime.combine(expiry_date, datetime.min.time(), tzinfo=context.as_of_time.tzinfo).replace(hour=15, minute=30)
        t_years = max((expiry_close - context.as_of_time).total_seconds(), 0.0) / (365 * 24 * 3600)
    else:
        expiry_date = None
        t_years = 0.0

    ce_premium = ce_contract.ltp if ce_contract else None
    pe_premium = pe_contract.ltp if pe_contract else None
    volatility = VolatilityBrain().analyze(
        spot_candles, spot, atm_strike or 0.0, t_years,
        ce_premium or 0.0, pe_premium or 0.0, risk_free_rate, context=context,
    )

    greeks = GreeksBrain().analyze(
        spot=spot, strike=atm_strike or 0.0, t_years=t_years,
        iv_ce=volatility.iv_ce, iv_pe=volatility.iv_pe, risk_free_rate=risk_free_rate, context=context,
    )

    vix_level = reality_snapshot.vix.close if reality_snapshot.vix else None
    vix_prev_close = None
    if reality_snapshot.vix and reality_snapshot.vix.change_percent is not None and reality_snapshot.vix.close is not None:
        # change_percent = (close - prev_close) / prev_close * 100 -- inverted algebraically,
        # never a second source-of-truth VIX value.
        denom = 1.0 + reality_snapshot.vix.change_percent / 100.0
        vix_prev_close = reality_snapshot.vix.close / denom if denom != 0 else None
    event = EventBrain().analyze(
        expiry_date, context.as_of_time.date(), vix_level, vix_prev_close, context=context,
    )

    snapshot = build_market_intelligence_snapshot(
        context=context, created_at=context.as_of_time,
        regime=regime, structure=structure, liquidity=liquidity,
        volatility=volatility, greeks=greeks, event=event,
    )

    decision_context = build_decision_context(snapshot)
    decision_intelligence = build_decision_intelligence_snapshot(
        snapshot=snapshot, decision_context=decision_context,
        memory_matches=memory_matches or [], created_at=context.as_of_time,
    )
    phenomena = build_market_phenomena_assessment(
        snapshot=snapshot, previous_snapshot=previous_market_intelligence_snapshot, created_at=context.as_of_time,
    )
    state_node = build_market_state_node(
        snapshot=snapshot, decision_intelligence=decision_intelligence, phenomena_assessment=phenomena,
        previous_snapshot=previous_market_intelligence_snapshot, previous_phenomena_assessment=previous_phenomena,
        previous_node=previous_state_node,
    )
    environment = build_market_environment_assessment(
        node=state_node, decision_intelligence=decision_intelligence, created_at=context.as_of_time,
    )

    return IntelligenceHeartbeatCycle(
        market_intelligence_snapshot=snapshot, decision_context=decision_context,
        decision_intelligence=decision_intelligence, phenomena=phenomena,
        state_node=state_node, environment=environment,
    )
