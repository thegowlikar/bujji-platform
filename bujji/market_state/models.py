"""MarketState model -- Shadow Campaign v2 Phase 3C.

The final immutable market-UNDERSTANDING object. No strategy/trade/
entry/exit/position/order/risk field exists here or ever will --
enforced by tests/test_market_state_safety_phase3c.py's dataclass-field
check, mirroring every earlier phase's same discipline.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class MarketDirectionSummary:
    """Phase 3D. ONLY direction/confidence/evidence/uncertainties --
    never trade_bias/buy_signal/sell_signal/entry, enforced by
    tests/test_market_direction_safety_phase3d.py."""

    direction: Optional[str]
    confidence: Optional[str]
    evidence: Tuple[str, ...]
    uncertainties: Tuple[str, ...]


@dataclass(frozen=True)
class MarketState:
    timestamp: str
    regime: Optional[str]
    volatility_state: Optional[str]
    liquidity_state: Optional[str]
    price_structure: Optional[str]
    market_structure: Optional[str]
    participant_positioning: Optional[str]
    active_events: Tuple[str, ...]
    active_episodes: Tuple[str, ...]
    overall_confidence: str
    uncertainties: Tuple[str, ...]
    evidence_ids: Tuple[str, ...]
    market_direction: Optional[MarketDirectionSummary] = None
