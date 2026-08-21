"""Market Intelligence Core — shared models (Volatility Brain addition)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from .evidence import IntelligenceEvidence


class RegimeType(str, Enum):
    TRENDING = "TRENDING"
    RANGING = "RANGING"
    VOLATILE = "VOLATILE"
    COMPRESSED = "COMPRESSED"
    TRANSITIONING = "TRANSITIONING"
    UNKNOWN = "UNKNOWN"


class DataQuality(str, Enum):
    SUFFICIENT = "SUFFICIENT"
    INSUFFICIENT = "INSUFFICIENT"


class Richness(str, Enum):
    """Is implied volatility rich or cheap relative to what the underlying
    is actually realizing? This is the core "does premium selling have an
    edge today" signal."""

    IV_RICH = "IV_RICH"
    IV_CHEAP = "IV_CHEAP"
    IV_FAIR = "IV_FAIR"
    UNKNOWN = "UNKNOWN"  # Insufficient/unreliable data -- never guessed.


@dataclass(frozen=True)
class RegimeReading:
    regime: RegimeType
    confidence: float
    data_quality: DataQuality
    evidence: dict[str, Any] = field(default_factory=dict)
    evidence_lineage: Dict[str, IntelligenceEvidence] = field(default_factory=dict)
    reason: str = ""
    candles_used: int = 0
    as_of: Optional[datetime] = None

    def render(self) -> str:
        lines = [
            "==========================",
            "REGIME BRAIN",
            "==========================",
            f"Regime            : {self.regime.value}",
            f"Confidence        : {self.confidence:.0%}",
            f"Data Quality      : {self.data_quality.value}",
            f"Candles Used      : {self.candles_used}",
            f"Reason            : {self.reason}",
        ]
        if self.evidence:
            lines.append("Evidence:")
            for k, v in self.evidence.items():
                lines.append(f"  {k:<24}: {v}")
        lines.append("==========================")
        return "\n".join(lines)

    def to_log(self) -> dict[str, Any]:
        return {
            "brain": "regime",
            "regime": self.regime.value,
            "confidence": self.confidence,
            "data_quality": self.data_quality.value,
            "candles_used": self.candles_used,
            "reason": self.reason,
            **{f"evidence.{k}": v for k, v in self.evidence.items()},
        }

    def to_dashboard(self) -> dict[str, Any]:
        return {
            "regime": self.regime.value,
            "confidence": self.confidence,
            "data_quality": self.data_quality.value,
            "reason": self.reason,
            "evidence": dict(self.evidence),
            "as_of": self.as_of.isoformat() if self.as_of else None,
        }


@dataclass(frozen=True)
class VolatilityReading:
    """Volatility Brain output.

    IV rank/percentile are ALWAYS None in this build -- they require
    weeks/months of historical IV that BUJJI does not yet have (FYERS
    serves no historical data for expired option contracts, verified live
    earlier this session). Reporting them as None rather than a fabricated
    0/50/whatever is the same fail-safe discipline as the Capital
    Management Engine's UNVERIFIED status -- never guess from missing data.
    """

    iv_ce: Optional[float]           # Implied vol solved from the CE premium.
    iv_pe: Optional[float]           # Implied vol solved from the PE premium.
    iv_average: Optional[float]      # Mean of iv_ce/iv_pe (None if either failed to solve).
    realized_vol: Optional[float]    # Annualized realized vol from real spot candles.
    richness: Richness
    richness_ratio: Optional[float]  # iv_average / realized_vol.
    expected_move_points: Optional[float]  # 1-sigma expected move to expiry, in index points.
    expected_move_pct: Optional[float]     # Same, as a percent of spot.
    iv_rank: Optional[float] = None        # Always None -- see class docstring.
    iv_percentile: Optional[float] = None  # Always None -- see class docstring.
    confidence: float = 0.0
    data_quality: DataQuality = DataQuality.INSUFFICIENT
    evidence: dict[str, Any] = field(default_factory=dict)
    evidence_lineage: Dict[str, IntelligenceEvidence] = field(default_factory=dict)
    reason: str = ""
    as_of: Optional[datetime] = None

    def render(self) -> str:
        def fmt(v, suffix=""):
            return "NOT AVAILABLE" if v is None else f"{v:.4f}{suffix}"

        lines = [
            "==========================",
            "VOLATILITY BRAIN",
            "==========================",
            f"IV (CE)           : {fmt(self.iv_ce)}",
            f"IV (PE)           : {fmt(self.iv_pe)}",
            f"IV (average)      : {fmt(self.iv_average)}",
            f"Realized Vol      : {fmt(self.realized_vol)}",
            f"Richness          : {self.richness.value} "
            f"({fmt(self.richness_ratio)} IV/RV ratio)",
            f"Expected Move     : {fmt(self.expected_move_points, ' pts')} "
            f"({fmt(self.expected_move_pct, '%')})",
            f"IV Rank           : {fmt(self.iv_rank)}  [always NOT AVAILABLE -- see docs]",
            f"IV Percentile     : {fmt(self.iv_percentile)}  [always NOT AVAILABLE -- see docs]",
            f"Confidence        : {self.confidence:.0%}",
            f"Data Quality      : {self.data_quality.value}",
            f"Reason            : {self.reason}",
        ]
        if self.evidence:
            lines.append("Evidence:")
            for k, v in self.evidence.items():
                lines.append(f"  {k:<24}: {v}")
        lines.append("==========================")
        return "\n".join(lines)

    def to_log(self) -> dict[str, Any]:
        return {
            "brain": "volatility",
            "iv_ce": self.iv_ce,
            "iv_pe": self.iv_pe,
            "iv_average": self.iv_average,
            "realized_vol": self.realized_vol,
            "richness": self.richness.value,
            "richness_ratio": self.richness_ratio,
            "expected_move_points": self.expected_move_points,
            "expected_move_pct": self.expected_move_pct,
            "iv_rank": self.iv_rank,
            "iv_percentile": self.iv_percentile,
            "confidence": self.confidence,
            "data_quality": self.data_quality.value,
            "reason": self.reason,
            **{f"evidence.{k}": v for k, v in self.evidence.items()},
        }

    def to_dashboard(self) -> dict[str, Any]:
        return {
            "iv_ce": self.iv_ce,
            "iv_pe": self.iv_pe,
            "iv_average": self.iv_average,
            "realized_vol": self.realized_vol,
            "richness": self.richness.value,
            "richness_ratio": self.richness_ratio,
            "expected_move_points": self.expected_move_points,
            "expected_move_pct": self.expected_move_pct,
            "iv_rank": self.iv_rank,
            "iv_percentile": self.iv_percentile,
            "confidence": self.confidence,
            "data_quality": self.data_quality.value,
            "reason": self.reason,
            "evidence": dict(self.evidence),
            "as_of": self.as_of.isoformat() if self.as_of else None,
        }


class PremiumBehavior(str, Enum):
    """Is the combined straddle premium moving the way a seller wants --
    decaying with time -- or is something else (spot movement, IV
    expansion) fighting that decay?"""

    DECAYING_FASTER_THAN_THETA = "DECAYING_FASTER_THAN_THETA"  # Favorable: extra decay beyond pure time value.
    DECAYING_AS_EXPECTED = "DECAYING_AS_EXPECTED"               # Roughly tracking pure time decay.
    RISING_AGAINST_THETA = "RISING_AGAINST_THETA"               # Unfavorable: premium not falling despite time passing.
    UNKNOWN = "UNKNOWN"  # Insufficient/unreliable data -- never guessed.


@dataclass(frozen=True)
class PremiumReading:
    """Premium Brain output.

    Core idea: hold spot and IV fixed at their ENTRY values and compute
    what the combined premium would be at the current point in time from
    time decay (theta) alone -- a pure counterfactual baseline. Compare
    that baseline against the REAL current combined premium. The gap
    between the two is explained by everything that isn't pure time decay
    (spot movement, IV expansion/contraction) -- exactly the information a
    premium seller needs: is decay happening because time is passing, or
    is something else eating into it / helping it along.
    """

    entry_combined_premium: Optional[float]
    current_combined_premium: Optional[float]
    theoretical_time_decay_only_premium: Optional[float]  # Same spot/IV as entry, only time moved forward.
    behavior: PremiumBehavior
    behavior_ratio: Optional[float]  # current_combined_premium / theoretical_time_decay_only_premium.
    premium_captured_pct: Optional[float]  # (entry - current) / entry * 100 -- seller's paper P&L as % of entry credit.
    time_elapsed_pct: Optional[float]  # Fraction of entry-to-expiry time that has passed, as a %.
    confidence: float = 0.0
    data_quality: DataQuality = DataQuality.INSUFFICIENT
    evidence: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    as_of: Optional[datetime] = None

    def render(self) -> str:
        def fmt(v, suffix=""):
            return "NOT AVAILABLE" if v is None else f"{v:.4f}{suffix}"

        lines = [
            "==========================",
            "PREMIUM BRAIN",
            "==========================",
            f"Entry Combined Premium   : {fmt(self.entry_combined_premium)}",
            f"Current Combined Premium : {fmt(self.current_combined_premium)}",
            f"Theoretical (theta-only) : {fmt(self.theoretical_time_decay_only_premium)}",
            f"Behavior                 : {self.behavior.value} ({fmt(self.behavior_ratio)} ratio)",
            f"Premium Captured         : {fmt(self.premium_captured_pct, '%')}",
            f"Time Elapsed             : {fmt(self.time_elapsed_pct, '%')}",
            f"Confidence               : {self.confidence:.0%}",
            f"Data Quality             : {self.data_quality.value}",
            f"Reason                   : {self.reason}",
        ]
        if self.evidence:
            lines.append("Evidence:")
            for k, v in self.evidence.items():
                lines.append(f"  {k:<24}: {v}")
        lines.append("==========================")
        return "\n".join(lines)

    def to_log(self) -> dict[str, Any]:
        return {
            "brain": "premium",
            "entry_combined_premium": self.entry_combined_premium,
            "current_combined_premium": self.current_combined_premium,
            "theoretical_time_decay_only_premium": self.theoretical_time_decay_only_premium,
            "behavior": self.behavior.value,
            "behavior_ratio": self.behavior_ratio,
            "premium_captured_pct": self.premium_captured_pct,
            "time_elapsed_pct": self.time_elapsed_pct,
            "confidence": self.confidence,
            "data_quality": self.data_quality.value,
            "reason": self.reason,
            **{f"evidence.{k}": v for k, v in self.evidence.items()},
        }

    def to_dashboard(self) -> dict[str, Any]:
        return {
            "entry_combined_premium": self.entry_combined_premium,
            "current_combined_premium": self.current_combined_premium,
            "theoretical_time_decay_only_premium": self.theoretical_time_decay_only_premium,
            "behavior": self.behavior.value,
            "behavior_ratio": self.behavior_ratio,
            "premium_captured_pct": self.premium_captured_pct,
            "time_elapsed_pct": self.time_elapsed_pct,
            "confidence": self.confidence,
            "data_quality": self.data_quality.value,
            "reason": self.reason,
            "evidence": dict(self.evidence),
            "as_of": self.as_of.isoformat() if self.as_of else None,
        }


class GreeksExposure(str, Enum):
    """Net directional exposure of the SHORT straddle position, from
    position delta (CE+PE deltas, sign-flipped for a short position)."""

    DELTA_NEUTRAL = "DELTA_NEUTRAL"          # |position_delta| small -- position P&L roughly indifferent to small spot moves.
    NET_LONG_EXPOSURE = "NET_LONG_EXPOSURE"  # Position now benefits from spot rising further.
    NET_SHORT_EXPOSURE = "NET_SHORT_EXPOSURE"  # Position now benefits from spot falling further.
    UNKNOWN = "UNKNOWN"  # Insufficient/unreliable data -- never guessed.


@dataclass(frozen=True)
class GreeksReading:
    """Greeks Brain output.

    All per-leg Greeks are for a LONG position in that leg (textbook
    Black-Scholes convention). Position Greeks are for the actual SHORT
    straddle BUJJI holds -- i.e. sign-flipped and summed:
    position_x = -(leg_x_ce + leg_x_pe).

    Theta is reported PER CALENDAR DAY (annualized Black-Scholes theta
    divided by 365), not per year -- the number a seller actually cares
    about is "how much does this position earn from time decay today".
    Vega is reported PER 1% CHANGE IN IV (annualized vega divided by
    100), for the same reason -- "IV up 1 point" is the intuitive unit,
    not "IV up 100 points (i.e. to +100% absolute)".
    """

    delta_ce: Optional[float]
    delta_pe: Optional[float]
    gamma_ce: Optional[float]
    gamma_pe: Optional[float]
    theta_ce_per_day: Optional[float]
    theta_pe_per_day: Optional[float]
    vega_ce_per_pct: Optional[float]
    vega_pe_per_pct: Optional[float]
    position_delta: Optional[float]
    position_gamma: Optional[float]
    position_theta_per_day: Optional[float]
    position_vega_per_pct: Optional[float]
    exposure: GreeksExposure
    confidence: float = 0.0
    data_quality: DataQuality = DataQuality.INSUFFICIENT
    evidence: dict[str, Any] = field(default_factory=dict)
    evidence_lineage: Dict[str, IntelligenceEvidence] = field(default_factory=dict)
    reason: str = ""
    as_of: Optional[datetime] = None

    def render(self) -> str:
        def fmt(v, suffix=""):
            return "NOT AVAILABLE" if v is None else f"{v:.4f}{suffix}"

        lines = [
            "==========================",
            "GREEKS BRAIN",
            "==========================",
            f"Delta (CE / PE)          : {fmt(self.delta_ce)} / {fmt(self.delta_pe)}",
            f"Gamma (CE / PE)          : {fmt(self.gamma_ce)} / {fmt(self.gamma_pe)}",
            f"Theta/day (CE / PE)      : {fmt(self.theta_ce_per_day)} / {fmt(self.theta_pe_per_day)}",
            f"Vega/1% (CE / PE)        : {fmt(self.vega_ce_per_pct)} / {fmt(self.vega_pe_per_pct)}",
            f"Position Delta           : {fmt(self.position_delta)}",
            f"Position Gamma           : {fmt(self.position_gamma)}",
            f"Position Theta/day       : {fmt(self.position_theta_per_day)}",
            f"Position Vega/1%         : {fmt(self.position_vega_per_pct)}",
            f"Exposure                 : {self.exposure.value}",
            f"Confidence               : {self.confidence:.0%}",
            f"Data Quality             : {self.data_quality.value}",
            f"Reason                   : {self.reason}",
        ]
        if self.evidence:
            lines.append("Evidence:")
            for k, v in self.evidence.items():
                lines.append(f"  {k:<24}: {v}")
        lines.append("==========================")
        return "\n".join(lines)

    def to_log(self) -> dict[str, Any]:
        return {
            "brain": "greeks",
            "delta_ce": self.delta_ce, "delta_pe": self.delta_pe,
            "gamma_ce": self.gamma_ce, "gamma_pe": self.gamma_pe,
            "theta_ce_per_day": self.theta_ce_per_day, "theta_pe_per_day": self.theta_pe_per_day,
            "vega_ce_per_pct": self.vega_ce_per_pct, "vega_pe_per_pct": self.vega_pe_per_pct,
            "position_delta": self.position_delta, "position_gamma": self.position_gamma,
            "position_theta_per_day": self.position_theta_per_day,
            "position_vega_per_pct": self.position_vega_per_pct,
            "exposure": self.exposure.value,
            "confidence": self.confidence,
            "data_quality": self.data_quality.value,
            "reason": self.reason,
            **{f"evidence.{k}": v for k, v in self.evidence.items()},
        }

    def to_dashboard(self) -> dict[str, Any]:
        return {
            "delta_ce": self.delta_ce, "delta_pe": self.delta_pe,
            "gamma_ce": self.gamma_ce, "gamma_pe": self.gamma_pe,
            "theta_ce_per_day": self.theta_ce_per_day, "theta_pe_per_day": self.theta_pe_per_day,
            "vega_ce_per_pct": self.vega_ce_per_pct, "vega_pe_per_pct": self.vega_pe_per_pct,
            "position_delta": self.position_delta, "position_gamma": self.position_gamma,
            "position_theta_per_day": self.position_theta_per_day,
            "position_vega_per_pct": self.position_vega_per_pct,
            "exposure": self.exposure.value,
            "confidence": self.confidence,
            "data_quality": self.data_quality.value,
            "reason": self.reason,
            "evidence": dict(self.evidence),
            "as_of": self.as_of.isoformat() if self.as_of else None,
        }


class SpreadTightness(str, Enum):
    """Is the combined straddle's top-of-book bid/ask spread tight enough
    to enter/exit without meaningful slippage right now?"""

    TIGHT = "TIGHT"
    NORMAL = "NORMAL"
    WIDE = "WIDE"
    UNKNOWN = "UNKNOWN"  # Insufficient/unreliable quote -- never guessed.


@dataclass(frozen=True)
class LiquidityReading:
    """Liquidity Brain output.

    LIVE-VERIFIED DATA SOURCE (2026-07-20): the real FYERS `quotes`
    response includes per-symbol `bid`, `ask`, and `spread` fields for
    option contracts -- confirmed live against real NIFTY weekly ATM CE/PE
    quotes, with `spread == ask - bid` holding exactly on both legs. This
    brain uses ONLY those three fields.

    NOT used: the quote's `volume` field. Its value (400M+ for a single
    option symbol) does not look like a plausible per-symbol traded
    quantity and was not independently corroborated -- rather than guess
    at what it represents, it is simply not consumed here. Full multi-level
    market depth (beyond top-of-book bid/ask) was not observed in the raw
    quote response and is NOT assumed available.
    """

    ce_bid: Optional[float]
    ce_ask: Optional[float]
    pe_bid: Optional[float]
    pe_ask: Optional[float]
    ce_spread_pct: Optional[float]
    pe_spread_pct: Optional[float]
    combined_spread: Optional[float]
    combined_spread_pct: Optional[float]
    tightness: SpreadTightness
    confidence: float = 0.0
    data_quality: DataQuality = DataQuality.INSUFFICIENT
    evidence: dict[str, Any] = field(default_factory=dict)
    evidence_lineage: Dict[str, IntelligenceEvidence] = field(default_factory=dict)
    reason: str = ""
    as_of: Optional[datetime] = None

    def render(self) -> str:
        def fmt(v, suffix=""):
            return "NOT AVAILABLE" if v is None else f"{v:.4f}{suffix}"

        lines = [
            "==========================",
            "LIQUIDITY BRAIN",
            "==========================",
            f"CE Bid / Ask             : {fmt(self.ce_bid)} / {fmt(self.ce_ask)}",
            f"PE Bid / Ask             : {fmt(self.pe_bid)} / {fmt(self.pe_ask)}",
            f"CE / PE Spread %         : {fmt(self.ce_spread_pct, '%')} / {fmt(self.pe_spread_pct, '%')}",
            f"Combined Spread          : {fmt(self.combined_spread)} ({fmt(self.combined_spread_pct, '%')})",
            f"Tightness                : {self.tightness.value}",
            f"Confidence               : {self.confidence:.0%}",
            f"Data Quality             : {self.data_quality.value}",
            f"Reason                   : {self.reason}",
        ]
        if self.evidence:
            lines.append("Evidence:")
            for k, v in self.evidence.items():
                lines.append(f"  {k:<24}: {v}")
        lines.append("==========================")
        return "\n".join(lines)

    def to_log(self) -> dict[str, Any]:
        return {
            "brain": "liquidity",
            "ce_bid": self.ce_bid, "ce_ask": self.ce_ask,
            "pe_bid": self.pe_bid, "pe_ask": self.pe_ask,
            "ce_spread_pct": self.ce_spread_pct, "pe_spread_pct": self.pe_spread_pct,
            "combined_spread": self.combined_spread, "combined_spread_pct": self.combined_spread_pct,
            "tightness": self.tightness.value,
            "confidence": self.confidence,
            "data_quality": self.data_quality.value,
            "reason": self.reason,
            **{f"evidence.{k}": v for k, v in self.evidence.items()},
        }

    def to_dashboard(self) -> dict[str, Any]:
        return {
            "ce_bid": self.ce_bid, "ce_ask": self.ce_ask,
            "pe_bid": self.pe_bid, "pe_ask": self.pe_ask,
            "ce_spread_pct": self.ce_spread_pct, "pe_spread_pct": self.pe_spread_pct,
            "combined_spread": self.combined_spread, "combined_spread_pct": self.combined_spread_pct,
            "tightness": self.tightness.value,
            "confidence": self.confidence,
            "data_quality": self.data_quality.value,
            "reason": self.reason,
            "evidence": dict(self.evidence),
            "as_of": self.as_of.isoformat() if self.as_of else None,
        }


class StructureProximity(str, Enum):
    """Is spot sitting right at a real open-interest wall -- a strike
    with unusually heavy CE or PE open interest that tends to act as
    resistance/support -- or comfortably between walls?"""

    NEAR_RESISTANCE_WALL = "NEAR_RESISTANCE_WALL"
    NEAR_SUPPORT_WALL = "NEAR_SUPPORT_WALL"
    MID_RANGE = "MID_RANGE"
    UNKNOWN = "UNKNOWN"  # Insufficient/unreliable OI data -- never guessed.


@dataclass(frozen=True)
class StructureReading:
    """Structure Brain output.

    LIVE-VERIFIED DATA SOURCE (2026-07-20): the real FYERS `optionchain`
    endpoint (`client.optionchain(...)`, distinct from the plain `quotes`
    endpoint the other brains use) returns per-strike `oi`, `prev_oi`, and
    `oich` (OI change) for both CE and PE. Confirmed live against real
    NIFTY strikes with `oich == oi - prev_oi` holding exactly -- genuine,
    internally consistent open-interest data, not a placeholder.

    Resistance = the strike ABOVE spot with the highest CE open interest
    (heavy call writing above spot -- the classic "call wall"). Support =
    the strike BELOW spot with the highest PE open interest ("put wall").
    Both are real market positioning, not price action -- this is a
    genuinely different signal from the Regime Brain's pure price-action
    view.
    """

    spot: Optional[float]
    resistance_strike: Optional[float]
    resistance_oi: Optional[float]
    support_strike: Optional[float]
    support_oi: Optional[float]
    distance_to_resistance_pct: Optional[float]
    distance_to_support_pct: Optional[float]
    put_call_oi_ratio: Optional[float]  # Total PE OI / total CE OI across strikes provided -- informational only, not yet calibrated for classification.
    proximity: StructureProximity
    confidence: float = 0.0
    data_quality: DataQuality = DataQuality.INSUFFICIENT
    evidence: dict[str, Any] = field(default_factory=dict)
    evidence_lineage: Dict[str, IntelligenceEvidence] = field(default_factory=dict)
    reason: str = ""
    as_of: Optional[datetime] = None

    def render(self) -> str:
        def fmt(v, suffix=""):
            return "NOT AVAILABLE" if v is None else f"{v:.4f}{suffix}"

        lines = [
            "==========================",
            "STRUCTURE BRAIN",
            "==========================",
            f"Spot                     : {fmt(self.spot)}",
            f"Resistance (strike / OI) : {fmt(self.resistance_strike)} / {fmt(self.resistance_oi)}",
            f"Support (strike / OI)    : {fmt(self.support_strike)} / {fmt(self.support_oi)}",
            f"Distance to Resistance   : {fmt(self.distance_to_resistance_pct, '%')}",
            f"Distance to Support      : {fmt(self.distance_to_support_pct, '%')}",
            f"Put/Call OI Ratio        : {fmt(self.put_call_oi_ratio)}",
            f"Proximity                : {self.proximity.value}",
            f"Confidence               : {self.confidence:.0%}",
            f"Data Quality             : {self.data_quality.value}",
            f"Reason                   : {self.reason}",
        ]
        if self.evidence:
            lines.append("Evidence:")
            for k, v in self.evidence.items():
                lines.append(f"  {k:<24}: {v}")
        lines.append("==========================")
        return "\n".join(lines)

    def to_log(self) -> dict[str, Any]:
        return {
            "brain": "structure",
            "spot": self.spot,
            "resistance_strike": self.resistance_strike, "resistance_oi": self.resistance_oi,
            "support_strike": self.support_strike, "support_oi": self.support_oi,
            "distance_to_resistance_pct": self.distance_to_resistance_pct,
            "distance_to_support_pct": self.distance_to_support_pct,
            "put_call_oi_ratio": self.put_call_oi_ratio,
            "proximity": self.proximity.value,
            "confidence": self.confidence,
            "data_quality": self.data_quality.value,
            "reason": self.reason,
            **{f"evidence.{k}": v for k, v in self.evidence.items()},
        }

    def to_dashboard(self) -> dict[str, Any]:
        return {
            "spot": self.spot,
            "resistance_strike": self.resistance_strike, "resistance_oi": self.resistance_oi,
            "support_strike": self.support_strike, "support_oi": self.support_oi,
            "distance_to_resistance_pct": self.distance_to_resistance_pct,
            "distance_to_support_pct": self.distance_to_support_pct,
            "put_call_oi_ratio": self.put_call_oi_ratio,
            "proximity": self.proximity.value,
            "confidence": self.confidence,
            "data_quality": self.data_quality.value,
            "reason": self.reason,
            "evidence": dict(self.evidence),
            "as_of": self.as_of.isoformat() if self.as_of else None,
        }


class ExpiryProximity(str, Enum):
    """How close is today to the straddle's own expiry -- pure date
    arithmetic, always computable, never requires an external source."""

    EXPIRY_DAY = "EXPIRY_DAY"
    EXPIRY_EVE = "EXPIRY_EVE"
    NORMAL = "NORMAL"
    UNKNOWN = "UNKNOWN"  # Invalid/inconsistent dates -- never guessed.


class VixRegime(str, Enum):
    """Is India VIX (the market's own priced-in expectation of near-term
    volatility) sitting at a calm, normal, or elevated level right now?"""

    LOW = "LOW"
    MODERATE = "MODERATE"
    ELEVATED = "ELEVATED"
    UNKNOWN = "UNKNOWN"  # Insufficient/unreliable VIX data -- never guessed.


@dataclass(frozen=True)
class EventReading:
    """Event Brain output.

    Deliberately narrower than the MIC's original "Event Brain" concept.
    The calendar half of that design (FOMC, RBI policy, Union Budget, and
    similar scheduled macro events) needs an external economic-calendar
    data source -- BUJJI has no such integration, and none of the FYERS
    endpoints already in use (quotes, historical, optionchain) provide
    one. Rather than fabricate event dates from general knowledge (which
    would silently go stale and could be wrong for any given contract
    cycle), that half is NOT built here and remains an open item.

    What IS built, on two genuinely verifiable sources:
      - expiry_proximity: pure date arithmetic against the straddle's own
        real expiry date -- always available, needs no external data.
      - vix_regime / vix_change_pct: India VIX, live-verified against the
        real FYERS quote for NSE:INDIAVIX-INDEX (lp=13.02 at capture
        time, sane historical range) -- the market's own priced
        near-term volatility expectation, which does reflect anticipated
        event risk even without knowing the specific calendar event.
    """

    days_to_expiry: Optional[int]
    expiry_proximity: ExpiryProximity
    vix_level: Optional[float]
    vix_change_pct: Optional[float]
    vix_regime: VixRegime
    confidence: float = 0.0
    data_quality: DataQuality = DataQuality.INSUFFICIENT
    evidence: dict[str, Any] = field(default_factory=dict)
    evidence_lineage: Dict[str, IntelligenceEvidence] = field(default_factory=dict)
    reason: str = ""
    as_of: Optional[datetime] = None

    def render(self) -> str:
        def fmt(v, suffix=""):
            return "NOT AVAILABLE" if v is None else f"{v}{suffix}"

        lines = [
            "==========================",
            "EVENT BRAIN",
            "==========================",
            f"Days to Expiry           : {fmt(self.days_to_expiry)}",
            f"Expiry Proximity         : {self.expiry_proximity.value}",
            f"VIX Level                : {fmt(self.vix_level)}",
            f"VIX Change               : {fmt(self.vix_change_pct, '%')}",
            f"VIX Regime               : {self.vix_regime.value}",
            f"Confidence               : {self.confidence:.0%}",
            f"Data Quality             : {self.data_quality.value}",
            f"Reason                   : {self.reason}",
            "NOTE: economic-calendar events (FOMC/RBI/Budget/etc.) are",
            "NOT covered -- no verified data source exists for them.",
        ]
        if self.evidence:
            lines.append("Evidence:")
            for k, v in self.evidence.items():
                lines.append(f"  {k:<24}: {v}")
        lines.append("==========================")
        return "\n".join(lines)

    def to_log(self) -> dict[str, Any]:
        return {
            "brain": "event",
            "days_to_expiry": self.days_to_expiry,
            "expiry_proximity": self.expiry_proximity.value,
            "vix_level": self.vix_level,
            "vix_change_pct": self.vix_change_pct,
            "vix_regime": self.vix_regime.value,
            "confidence": self.confidence,
            "data_quality": self.data_quality.value,
            "reason": self.reason,
            **{f"evidence.{k}": v for k, v in self.evidence.items()},
        }

    def to_dashboard(self) -> dict[str, Any]:
        return {
            "days_to_expiry": self.days_to_expiry,
            "expiry_proximity": self.expiry_proximity.value,
            "vix_level": self.vix_level,
            "vix_change_pct": self.vix_change_pct,
            "vix_regime": self.vix_regime.value,
            "confidence": self.confidence,
            "data_quality": self.data_quality.value,
            "reason": self.reason,
            "evidence": dict(self.evidence),
            "as_of": self.as_of.isoformat() if self.as_of else None,
        }


class StreakSignal(str, Enum):
    """Is the recent trade sequence on an unusual winning or losing run
    worth a human's attention, or within normal variance?"""

    NORMAL = "NORMAL"
    WINNING_STREAK = "WINNING_STREAK"
    LOSING_STREAK = "LOSING_STREAK"
    UNKNOWN = "UNKNOWN"  # Insufficient real trade history -- never guessed.


@dataclass(frozen=True)
class BehaviourReading:
    """Behaviour Brain output.

    DATA REALITY, CHECKED BEFORE BUILDING THIS BRAIN (2026-07-20): the
    real trade journal (`bujji/journal/journal.py`, backed by
    `data/bujji.db`) currently has ZERO completed live trades -- BUJJI has
    never closed a live position. Even counting this session's real-data
    backtest runs, there are only ~5 correlated real trading days on
    file (2026-07-13 to 2026-07-17, re-run several times), nowhere near
    independent enough or numerous enough to detect a genuine behavioural
    pattern (day-of-week effects, exit-rule performance, streak
    significance, etc.) rather than noise.

    This brain is therefore built INERT BY DESIGN: `MIN_TRADES_REQUIRED`
    (see `behaviour_brain.py`) gates every single output behind a hard
    trade-count floor. Below that floor, EVERY field is None/UNKNOWN --
    not a partial reading, not a cautious guess -- because with this few
    trades any computed "pattern" would be indistinguishable from random
    noise, and reporting one anyway would be exactly the kind of
    fabrication this codebase's discipline exists to prevent. The
    real-data validation section every other brain in the MIC has is
    deliberately absent from this one's documentation until enough real
    trades exist to write it honestly.
    """

    total_trades: Optional[int]
    win_rate: Optional[float]
    avg_pnl: Optional[float]
    current_streak: Optional[int]  # Positive = consecutive wins, negative = consecutive losses.
    streak_signal: StreakSignal
    exit_reason_breakdown: dict[str, Any] = field(default_factory=dict)  # {reason: {"count": n, "win_rate": x, "avg_pnl": y}}
    confidence: float = 0.0
    data_quality: DataQuality = DataQuality.INSUFFICIENT
    evidence: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    as_of: Optional[datetime] = None

    def render(self) -> str:
        def fmt(v, suffix=""):
            return "NOT AVAILABLE" if v is None else f"{v}{suffix}"

        lines = [
            "==========================",
            "BEHAVIOUR BRAIN",
            "==========================",
            f"Total Trades On File     : {fmt(self.total_trades)}",
            f"Win Rate                 : {fmt(self.win_rate, '%')}",
            f"Avg PnL                  : {fmt(self.avg_pnl)}",
            f"Current Streak           : {fmt(self.current_streak)}",
            f"Streak Signal            : {self.streak_signal.value}",
            f"Confidence               : {self.confidence:.0%}",
            f"Data Quality             : {self.data_quality.value}",
            f"Reason                   : {self.reason}",
        ]
        if self.exit_reason_breakdown:
            lines.append("Exit Reason Breakdown:")
            for k, v in self.exit_reason_breakdown.items():
                lines.append(f"  {k:<24}: {v}")
        if self.evidence:
            lines.append("Evidence:")
            for k, v in self.evidence.items():
                lines.append(f"  {k:<24}: {v}")
        lines.append("==========================")
        return "\n".join(lines)

    def to_log(self) -> dict[str, Any]:
        return {
            "brain": "behaviour",
            "total_trades": self.total_trades,
            "win_rate": self.win_rate,
            "avg_pnl": self.avg_pnl,
            "current_streak": self.current_streak,
            "streak_signal": self.streak_signal.value,
            "confidence": self.confidence,
            "data_quality": self.data_quality.value,
            "reason": self.reason,
            "exit_reason_breakdown": dict(self.exit_reason_breakdown),
            **{f"evidence.{k}": v for k, v in self.evidence.items()},
        }

    def to_dashboard(self) -> dict[str, Any]:
        return {
            "total_trades": self.total_trades,
            "win_rate": self.win_rate,
            "avg_pnl": self.avg_pnl,
            "current_streak": self.current_streak,
            "streak_signal": self.streak_signal.value,
            "confidence": self.confidence,
            "data_quality": self.data_quality.value,
            "reason": self.reason,
            "exit_reason_breakdown": dict(self.exit_reason_breakdown),
            "evidence": dict(self.evidence),
            "as_of": self.as_of.isoformat() if self.as_of else None,
        }
