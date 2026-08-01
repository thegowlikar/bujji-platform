"""Market Regime Adapter -- BUJJI Options OS v3, Numeric Risk Governor
Gate D.5, Part 3.

PURPOSE: translate regime information the platform has ALREADY
computed into the small, stable vocabulary Adaptive Risk Memory keys
its lookups on. This module calculates NOTHING -- no candle math, no
statistics, no thresholds against raw price/IV data. Every function
here is a pure relabeling table over an already-computed value.

EXISTING REGIME MODELS FOUND AND REUSED, NOT DUPLICATED (verified via
inspection before writing anything):
  - bujji/intelligence/regime_brain.py -> RegimeBrain.analyze() already
    computes trend/noise character from real candles into
    bujji.intelligence.models.RegimeType: TRENDING, RANGING, VOLATILE,
    COMPRESSED, TRANSITIONING, UNKNOWN. It has no directional
    (up/down) axis of its own, but its own RegimeReading.evidence
    dict already carries a signed `net_move` figure (the net
    directional price move over the analysis window) as part of its
    published evidence -- adapt_trend_regime() below reads the SIGN of
    that already-computed number to split TRENDING into
    TRENDING_UP/TRENDING_DOWN. This is reading an existing computed
    value's sign, not deriving a new one.
  - bujji/msi_volatility_structure/taxonomy.py -> ALL_VOLATILITY_REGIMES
    already exist: HIGH_VOLATILITY, COMPRESSED, TRANSITIONING, STABLE,
    UNKNOWN (IV-based, a different signal from RegimeBrain's realized
    vol). adapt_volatility_regime() below is a straight relabeling
    table onto this module's own small vocabulary.

Neither source module is imported here (this stays a pure string-to-
string adapter with no coupling to intelligence/ or msi_volatility_
structure/'s own data types), so callers pass already-extracted plain
strings/floats -- keeping this module trivially testable and free of
any dependency on how those upstream regimes were computed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

TREND_TRENDING_UP = "TRENDING_UP"
TREND_TRENDING_DOWN = "TRENDING_DOWN"
TREND_SIDEWAYS = "SIDEWAYS"
TREND_UNKNOWN = "UNKNOWN"

VOL_HIGH = "HIGH_VOL"
VOL_LOW = "LOW_VOL"
VOL_EXPANSION = "EXPANSION"
VOL_CONTRACTION = "CONTRACTION"
VOL_UNKNOWN = "UNKNOWN"

ALL_TREND_REGIMES = (TREND_TRENDING_UP, TREND_TRENDING_DOWN, TREND_SIDEWAYS, TREND_UNKNOWN)
ALL_VOLATILITY_REGIMES = (VOL_HIGH, VOL_LOW, VOL_EXPANSION, VOL_CONTRACTION, VOL_UNKNOWN)

# Straight relabeling table from RegimeBrain's RegimeType values
# (passed here as plain strings, e.g. "TRENDING") onto this module's
# trend vocabulary. TRENDING is split further by net_move sign in
# adapt_trend_regime() itself, not in this table.
_TREND_REGIME_TYPE_MAP = {
    "RANGING": TREND_SIDEWAYS,
    "VOLATILE": TREND_UNKNOWN,        # a volatility read, not a trend read -- honestly unmapped here
    "COMPRESSED": TREND_UNKNOWN,
    "TRANSITIONING": TREND_UNKNOWN,
    "UNKNOWN": TREND_UNKNOWN,
}

# Straight relabeling table from msi_volatility_structure's own
# ALL_VOLATILITY_REGIMES strings onto this module's volatility
# vocabulary.
_VOLATILITY_REGIME_MAP = {
    "HIGH_VOLATILITY": VOL_HIGH,
    "STABLE": VOL_LOW,
    "TRANSITIONING": VOL_EXPANSION,
    "COMPRESSED": VOL_CONTRACTION,
    "UNKNOWN": VOL_UNKNOWN,
}


def adapt_trend_regime(source_regime_type: Optional[str], net_move: Optional[float] = None) -> str:
    """source_regime_type: RegimeBrain's already-computed RegimeType
    value, passed as a plain string (e.g. "TRENDING"). net_move: the
    already-computed signed net-move figure from that same
    RegimeReading's evidence dict, used ONLY to read its sign when
    source_regime_type == "TRENDING". No recomputation of either
    input occurs here."""
    if source_regime_type is None:
        return TREND_UNKNOWN
    if source_regime_type == "TRENDING":
        if net_move is None:
            return TREND_UNKNOWN
        return TREND_TRENDING_UP if net_move > 0 else TREND_TRENDING_DOWN
    return _TREND_REGIME_TYPE_MAP.get(source_regime_type, TREND_UNKNOWN)


def adapt_volatility_regime(source_volatility_regime: Optional[str]) -> str:
    """source_volatility_regime: an already-computed value from
    msi_volatility_structure.taxonomy.ALL_VOLATILITY_REGIMES, passed
    as a plain string."""
    if source_volatility_regime is None:
        return VOL_UNKNOWN
    return _VOLATILITY_REGIME_MAP.get(source_volatility_regime, VOL_UNKNOWN)


@dataclass(frozen=True)
class MarketRegimeContext:
    trend_regime: str
    volatility_regime: str
    source_trend_regime_type: Optional[str]
    source_volatility_regime: Optional[str]


def build_market_regime_context(
    source_regime_type: Optional[str], net_move: Optional[float], source_volatility_regime: Optional[str],
) -> MarketRegimeContext:
    return MarketRegimeContext(
        trend_regime=adapt_trend_regime(source_regime_type, net_move),
        volatility_regime=adapt_volatility_regime(source_volatility_regime),
        source_trend_regime_type=source_regime_type,
        source_volatility_regime=source_volatility_regime,
    )
