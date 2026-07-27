"""Strategy Registry — BUJJI Options OS v3, Engineering Series 34.

Pure metadata. No strategy in this file contains executable trading
logic, no entry/exit rule, no position-sizing formula, no order
construction -- only declarative eligibility metadata that
engine.py's evaluation loop reads generically.

`ALL_STRATEGIES` declaration order IS the tie-break order: when more
than one strategy is ELIGIBLE for today's market, the first one
appearing in this tuple is selected. This is documented here precisely
because engine.py must never re-derive or invent an ordering of its
own -- the order lives in exactly one place.

Adding a new strategy means appending one `StrategyDefinition` to
`ALL_STRATEGIES` below. Nothing in engine.py, query.py, or
serialization.py needs to change.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class StrategyDefinition:
    strategy_id: str
    name: str
    family: str
    directional_bias: str  # NEUTRAL | BULLISH | BEARISH | VOLATILITY
    risk_profile: str  # DEFINED_RISK | UNDEFINED_RISK
    income_or_debit: str  # INCOME | DEBIT
    hedged: bool
    required_market_states: Tuple[str, ...]
    forbidden_market_states: Tuple[str, ...]
    minimum_market_character: str
    required_confidence: str
    # Declared for forward compatibility with a future, more granular
    # selector that can observe raw Governance/Calibration
    # classifications directly. NOT evaluated by this sprint's
    # engine.py: MarketStateAssessment (Series 33) only exposes the
    # *fused* market_character/confidence, not the underlying
    # per-layer Governance/Calibration values that produced them. See
    # docs/STRATEGY_SELECTOR_ARCHITECTURE.md for the full disclosure.
    required_governance: Optional[str] = None
    required_calibration: Optional[str] = None
    description: str = ""


ALL_STRATEGIES: Tuple[StrategyDefinition, ...] = (
    StrategyDefinition(
        strategy_id="PREMIUM_VWAP_STRADDLE",
        name="Premium VWAP Straddle",
        family="PREMIUM_SELLING",
        directional_bias="NEUTRAL",
        risk_profile="UNDEFINED_RISK",
        income_or_debit="INCOME",
        hedged=False,
        required_market_states=("RANGE", "QUIET"),
        forbidden_market_states=("TREND", "BREAKOUT", "VOLATILE", "EVENT_DRIVEN", "REVERSAL"),
        minimum_market_character="CLEAR",
        required_confidence="HIGH",
        description="Sells an at-the-money straddle around VWAP; wants a quiet, range-bound day with high-quality evidence.",
    ),
    StrategyDefinition(
        strategy_id="IRON_FLY",
        name="Iron Fly",
        family="PREMIUM_SELLING",
        directional_bias="NEUTRAL",
        risk_profile="DEFINED_RISK",
        income_or_debit="INCOME",
        hedged=True,
        required_market_states=("RANGE", "QUIET"),
        forbidden_market_states=("TREND", "BREAKOUT", "VOLATILE", "EVENT_DRIVEN", "REVERSAL"),
        minimum_market_character="CLEAR",
        required_confidence="HIGH",
        description="Defined-risk short straddle with wings; same market appetite as Premium VWAP Straddle but caps tail risk.",
    ),
    StrategyDefinition(
        strategy_id="IRON_CONDOR",
        name="Iron Condor",
        family="PREMIUM_SELLING",
        directional_bias="NEUTRAL",
        risk_profile="DEFINED_RISK",
        income_or_debit="INCOME",
        hedged=True,
        required_market_states=("RANGE", "QUIET"),
        forbidden_market_states=("TREND", "BREAKOUT", "VOLATILE", "EVENT_DRIVEN"),
        minimum_market_character="CONTESTED",
        required_confidence="MODERATE",
        description="Wider, lower-premium defined-risk range play; tolerates somewhat more contested evidence than Iron Fly.",
    ),
    StrategyDefinition(
        strategy_id="CALENDAR_SPREAD",
        name="Calendar Spread",
        family="VOLATILITY",
        directional_bias="NEUTRAL",
        risk_profile="DEFINED_RISK",
        income_or_debit="INCOME",
        hedged=True,
        required_market_states=("RANGE",),
        forbidden_market_states=("BREAKOUT", "VOLATILE", "EVENT_DRIVEN"),
        minimum_market_character="CONTESTED",
        required_confidence="MODERATE",
        description="Sells near-term, buys far-term same strike; benefits from time decay in a range-bound market.",
    ),
    StrategyDefinition(
        strategy_id="DIRECTIONAL_CALL_SPREAD",
        name="Directional Call Spread",
        family="DIRECTIONAL",
        directional_bias="BULLISH",
        risk_profile="DEFINED_RISK",
        income_or_debit="DEBIT",
        hedged=True,
        required_market_states=("TREND", "BREAKOUT"),
        forbidden_market_states=("RANGE", "QUIET", "EVENT_DRIVEN"),
        minimum_market_character="CLEAR",
        required_confidence="HIGH",
        description="Defined-risk bullish debit spread; wants a clean, well-evidenced trending or breakout market.",
    ),
    StrategyDefinition(
        strategy_id="DIRECTIONAL_PUT_SPREAD",
        name="Directional Put Spread",
        family="DIRECTIONAL",
        directional_bias="BEARISH",
        risk_profile="DEFINED_RISK",
        income_or_debit="DEBIT",
        hedged=True,
        required_market_states=("TREND", "BREAKOUT", "REVERSAL"),
        forbidden_market_states=("RANGE", "QUIET", "EVENT_DRIVEN"),
        minimum_market_character="CLEAR",
        required_confidence="HIGH",
        description="Defined-risk bearish debit spread; wants a clean trending, breakout, or reversal market.",
    ),
    StrategyDefinition(
        strategy_id="LONG_STRADDLE",
        name="Long Straddle",
        family="VOLATILITY",
        directional_bias="VOLATILITY",
        risk_profile="DEFINED_RISK",
        income_or_debit="DEBIT",
        hedged=False,
        required_market_states=("BREAKOUT", "VOLATILE", "EVENT_DRIVEN"),
        forbidden_market_states=("RANGE", "QUIET"),
        minimum_market_character="CONTESTED",
        required_confidence="MODERATE",
        description="Buys an at-the-money straddle; benefits from a large move in either direction.",
    ),
    StrategyDefinition(
        strategy_id="LONG_STRANGLE",
        name="Long Strangle",
        family="VOLATILITY",
        directional_bias="VOLATILITY",
        risk_profile="DEFINED_RISK",
        income_or_debit="DEBIT",
        hedged=False,
        required_market_states=("BREAKOUT", "VOLATILE", "EVENT_DRIVEN"),
        forbidden_market_states=("RANGE", "QUIET"),
        minimum_market_character="CONTESTED",
        required_confidence="LOW",
        description="Cheaper, wider version of Long Straddle; tolerates weaker evidence given its lower cost basis.",
    ),
    StrategyDefinition(
        strategy_id="SHORT_STRANGLE",
        name="Short Strangle",
        family="PREMIUM_SELLING",
        directional_bias="NEUTRAL",
        risk_profile="UNDEFINED_RISK",
        income_or_debit="INCOME",
        hedged=False,
        required_market_states=("RANGE", "QUIET"),
        forbidden_market_states=("TREND", "BREAKOUT", "VOLATILE", "EVENT_DRIVEN", "REVERSAL"),
        minimum_market_character="CLEAR",
        required_confidence="VERY_HIGH",
        description="Undefined-risk short strangle; the widest tail risk in the registry, so it demands the highest confidence bar.",
    ),
    StrategyDefinition(
        strategy_id="COVERED_CALL",
        name="Covered Call",
        family="INCOME",
        directional_bias="NEUTRAL",
        risk_profile="DEFINED_RISK",
        income_or_debit="INCOME",
        hedged=True,
        required_market_states=("RANGE", "TREND"),
        forbidden_market_states=("VOLATILE", "EVENT_DRIVEN"),
        minimum_market_character="CONTESTED",
        required_confidence="MODERATE",
        description="Sells a call against an existing holding; tolerant of moderately contested evidence.",
    ),
    StrategyDefinition(
        strategy_id="CASH_SECURED_PUT",
        name="Cash Secured Put",
        family="INCOME",
        directional_bias="BULLISH",
        risk_profile="DEFINED_RISK",
        income_or_debit="INCOME",
        hedged=False,
        required_market_states=("RANGE", "TREND"),
        forbidden_market_states=("VOLATILE", "EVENT_DRIVEN", "REVERSAL"),
        minimum_market_character="CONTESTED",
        required_confidence="MODERATE",
        description="Sells a cash-secured put; a mildly bullish income strategy tolerant of moderately contested evidence.",
    ),
)

BY_ID = {s.strategy_id: s for s in ALL_STRATEGIES}


def get_by_id(strategy_id: str):
    return BY_ID.get(strategy_id)
