"""Evidence Assembly — Production Pipeline Entry 11, Layer between Broker
and Strategy.

Pure, side-effect-free packaging of already-fetched data into the formal
boundary objects the Strategy Layer is allowed to see. Introduces no new
computation, no new decision, and no new broker call — every value here was
already being fetched by the Orchestrator before this module existed; this
only makes the boundary explicit in code structure rather than implicit in
call order.

Strategy Layer code must receive its inputs exclusively through the objects
built here, and must never receive a Broker, ExecutionEngine, or Position
object directly.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from .models import Candle, Signal, TradeIntention

STRATEGY_TYPE = "PREMIUM_VWAP_STRADDLE"  # Descriptive label only -- not a behavioural switch.


def build_trade_intention(signal: Signal, decision_id: str = "") -> Optional[TradeIntention]:
    """Lossless reframing of the Signal Engine's existing broker-free output
    into the formal Strategy-Layer boundary object. Returns None when the
    signal is not a trade signal -- identical to today's `signal.is_trade`
    gate, just expressed as an explicit return rather than an implicit one.

    `decision_id` (Sprint 2, Decision Lineage) is optional and defaulted so
    every Sprint 1 call site/test keeps working unchanged -- when supplied,
    it ties this intention to the same id its DecisionSnapshot/ExecutionPlan/
    Position/journal row will also carry.
    """
    if not signal.is_trade:
        return None
    return TradeIntention(
        direction=signal.direction,
        strategy_type=STRATEGY_TYPE,
        thesis=signal.thesis.narrative if signal.thesis else "",
        evidence_refs={
            "spot": signal.spot,
            "vwap": signal.vwap,
            "orb_high": signal.orb.high if signal.orb else None,
            "orb_low": signal.orb.low if signal.orb else None,
        },
        as_of=signal.timestamp,
        decision_id=decision_id,
    )


def market_observations_snapshot(candle: Candle) -> dict:
    """Plain-dict snapshot of the candle a decision was made on -- used by
    DecisionSnapshot, never by any live decision path."""
    return {
        "timestamp": candle.timestamp.isoformat(),
        "open": candle.open, "high": candle.high,
        "low": candle.low, "close": candle.close,
        "volume": candle.volume,
    }
