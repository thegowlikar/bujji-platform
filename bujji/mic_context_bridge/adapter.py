"""Phase 20.23 -- the composition adapter. Combines already-real
`bujji.intelligence.*` brain outputs (RegimeReading, VolatilityReading,
PremiumReading, LiquidityReading, StructureReading, GreeksReading)
into one `MarketUnderstandingContext`. Zero calculation: no candle
math, no IV solving, no OI aggregation, no Greeks computation happens
anywhere in this file -- every sentence in `supporting_factors`/
`uncertainties`/`conflicts` is built directly from a field already
present on a caller-supplied Reading object.

`bujji.mic_v0` (core) is NEVER imported or modified by this module --
this package is a pure downstream consumer of the same
`bujji.intelligence.*` brains MIC v0 itself already partially reads
from (`RegimeBrain`, `event_brain`'s VIX threshold).
"""
from __future__ import annotations

from typing import Optional

from bujji.intelligence.models import (
    DataQuality, GreeksExposure, GreeksReading, LiquidityReading, PremiumBehavior, PremiumReading,
    RegimeReading, RegimeType, Richness, SpreadTightness, StructureProximity, StructureReading,
    VolatilityReading,
)

from .models import MarketUnderstandingContext

# The one conflict rule this phase implements, disclosed here rather
# than guessed at run time: a regime classified as COMPRESSED (low
# realized-volatility price action) while implied volatility is
# simultaneously priced RICH relative to realized vol is a genuine,
# observable tension -- the options market is pricing more movement
# than price action has actually shown. This is the ONLY conflict rule
# implemented; it is not exhaustive, and no other pairing is inferred.
_COMPRESSED_VS_RICH_CONFLICT = (
    "regime classified as COMPRESSED (low realized-volatility price action) while implied "
    "volatility is priced RICH relative to realized volatility -- the options market is "
    "pricing more movement than recent price action has shown"
)


def build_market_understanding_context(
    regime: Optional[RegimeReading] = None,
    volatility: Optional[VolatilityReading] = None,
    premium: Optional[PremiumReading] = None,
    liquidity: Optional[LiquidityReading] = None,
    structure: Optional[StructureReading] = None,
    greeks: Optional[GreeksReading] = None,
) -> MarketUnderstandingContext:
    """Every argument is independently optional -- a caller supplies
    whichever real brain outputs it has for this cycle; a `None` is
    honestly reported as an uncertainty, never fabricated."""
    supporting_factors = []
    uncertainties = []
    conflicts = []

    # -- Regime -------------------------------------------------------
    if regime is not None and regime.data_quality == DataQuality.SUFFICIENT and regime.regime != RegimeType.UNKNOWN:
        market_state = regime.regime.value
        supporting_factors.append(f"regime classified as {regime.regime.value} ({regime.reason or 'no reason given'})")
    else:
        market_state = "UNKNOWN"
        uncertainties.append("Regime observation unavailable or insufficient.")

    # -- Volatility -----------------------------------------------------
    if volatility is not None and volatility.data_quality == DataQuality.SUFFICIENT and volatility.richness != Richness.UNKNOWN:
        supporting_factors.append(
            f"implied volatility {volatility.richness.value.replace('IV_', '').lower()} relative to realized "
            f"volatility (ratio={volatility.richness_ratio:.2f})" if volatility.richness_ratio is not None
            else f"implied volatility {volatility.richness.value.replace('IV_', '').lower()} relative to realized volatility"
        )
    else:
        uncertainties.append("Volatility observation unavailable or insufficient.")

    # -- Premium --------------------------------------------------------
    if premium is not None and premium.data_quality == DataQuality.SUFFICIENT and premium.behavior != PremiumBehavior.UNKNOWN:
        _PREMIUM_TEXT = {
            PremiumBehavior.DECAYING_FASTER_THAN_THETA: "premium decay present, faster than pure time decay",
            PremiumBehavior.DECAYING_AS_EXPECTED: "premium decay present, tracking pure time decay",
            PremiumBehavior.RISING_AGAINST_THETA: "premium rising against time decay",
        }
        supporting_factors.append(_PREMIUM_TEXT[premium.behavior])
    else:
        uncertainties.append("Premium behaviour observation unavailable or insufficient.")

    # -- Liquidity --------------------------------------------------------
    if liquidity is not None and liquidity.data_quality == DataQuality.SUFFICIENT and liquidity.tightness != SpreadTightness.UNKNOWN:
        supporting_factors.append(f"liquidity {liquidity.tightness.value.lower()} (combined spread {liquidity.combined_spread_pct:.2f}%)"
                                    if liquidity.combined_spread_pct is not None
                                    else f"liquidity {liquidity.tightness.value.lower()}")
    else:
        uncertainties.append("Liquidity observation unavailable.")

    # -- Structure ------------------------------------------------------
    if structure is not None and structure.data_quality == DataQuality.SUFFICIENT and structure.proximity != StructureProximity.UNKNOWN:
        _STRUCTURE_TEXT = {
            StructureProximity.NEAR_RESISTANCE_WALL: "resistance concentration detected (call writing above spot)",
            StructureProximity.NEAR_SUPPORT_WALL: "support concentration detected (put writing below spot)",
            StructureProximity.MID_RANGE: "no strong OI concentration near spot",
        }
        supporting_factors.append(_STRUCTURE_TEXT[structure.proximity])
    else:
        uncertainties.append("Structure/OI observation unavailable or insufficient.")

    # -- Greeks -----------------------------------------------------------
    if greeks is not None and greeks.data_quality == DataQuality.SUFFICIENT and greeks.exposure != GreeksExposure.UNKNOWN:
        _GREEKS_TEXT = {
            GreeksExposure.DELTA_NEUTRAL: "position exposure delta-neutral",
            GreeksExposure.NET_LONG_EXPOSURE: "position exposure net long (benefits from spot rising)",
            GreeksExposure.NET_SHORT_EXPOSURE: "position exposure net short (benefits from spot falling)",
        }
        supporting_factors.append(_GREEKS_TEXT[greeks.exposure])
    else:
        uncertainties.append("Greeks observation unavailable or insufficient.")

    # -- Conflict detection (the one disclosed rule) -----------------------
    if (
        regime is not None and regime.data_quality == DataQuality.SUFFICIENT and regime.regime == RegimeType.COMPRESSED
        and volatility is not None and volatility.data_quality == DataQuality.SUFFICIENT
        and volatility.richness == Richness.IV_RICH
    ):
        conflicts.append(_COMPRESSED_VS_RICH_CONFLICT)

    if supporting_factors:
        explanation = f"{market_state}: " + "; ".join(supporting_factors) + "."
    else:
        explanation = f"{market_state}: no sufficient supporting intelligence available this cycle."
    if conflicts:
        explanation += " Conflicting signals: " + "; ".join(conflicts) + "."
    if uncertainties:
        explanation += " Uncertain: " + "; ".join(uncertainties)

    return MarketUnderstandingContext(
        market_state=market_state, supporting_factors=tuple(supporting_factors),
        uncertainties=tuple(uncertainties), conflicts=tuple(conflicts), explanation=explanation,
    )
