"""bujji.mic_v0.models — Phase 20.1.

Output shape matches the charter's own worked example exactly. Frozen,
no logic — construction lives in `engine.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

# --- market_regime ---------------------------------------------------------
REGIME_TREND = "TREND"
REGIME_RANGE = "RANGE"
REGIME_UNCLEAR = "UNCLEAR"
ALL_REGIMES = (REGIME_TREND, REGIME_RANGE, REGIME_UNCLEAR)

# --- volatility_state --------------------------------------------------------
VOLATILITY_LOW = "LOW"
VOLATILITY_NORMAL = "NORMAL"
VOLATILITY_HIGH = "HIGH"
ALL_VOLATILITY_STATES = (VOLATILITY_LOW, VOLATILITY_NORMAL, VOLATILITY_HIGH)

# --- risk_state --------------------------------------------------------------
RISK_NORMAL = "NORMAL"
RISK_ELEVATED = "ELEVATED"
RISK_EXTREME = "EXTREME"
ALL_RISK_STATES = (RISK_NORMAL, RISK_ELEVATED, RISK_EXTREME)

# --- recommended_environment (Cycle 1 universe only) -------------------------
ENV_TREND_FOLLOWING = "TREND_FOLLOWING"
ENV_MEAN_REVERSION = "MEAN_REVERSION"
ENV_NO_TRADE = "NO_TRADE"
ALL_ENVIRONMENTS = (ENV_TREND_FOLLOWING, ENV_MEAN_REVERSION, ENV_NO_TRADE)

# --- confidence level ---------------------------------------------------------
CONFIDENCE_NONE = "NONE"
CONFIDENCE_LOW = "LOW"
CONFIDENCE_MEDIUM = "MEDIUM"
CONFIDENCE_HIGH = "HIGH"
ALL_CONFIDENCE_LEVELS = (CONFIDENCE_NONE, CONFIDENCE_LOW, CONFIDENCE_MEDIUM, CONFIDENCE_HIGH)

EVENT_CONTEXT_NOT_AVAILABLE = "NOT_AVAILABLE"


@dataclass(frozen=True)
class ConfidenceInfo:
    """Never a bare number. Every confidence claim carries its own
    method, sample size, and evaluation window — the charter's own
    anti-fabrication rule, applied here exactly as it was applied to
    the data layer in Phase 17-19."""

    level: str                        # ALL_CONFIDENCE_LEVELS
    sample_size: int
    method: str                       # e.g. "pending Phase 20.1B validation", "historical frequency"
    evaluation_window: Optional[str] = None   # e.g. "2018-02-01 to 2025-12-31"

    def __post_init__(self) -> None:
        if self.level not in ALL_CONFIDENCE_LEVELS:
            raise ValueError(f"level={self.level!r} not in {ALL_CONFIDENCE_LEVELS}")
        if self.sample_size < 0:
            raise ValueError("sample_size cannot be negative")
        if self.sample_size == 0 and self.level not in (CONFIDENCE_NONE, CONFIDENCE_LOW):
            raise ValueError(
                f"sample_size=0 cannot justify confidence level {self.level!r} — "
                "a claim with zero evidence must be NONE or LOW, never MEDIUM/HIGH."
            )

    def to_dict(self) -> dict:
        return {
            "level": self.level, "sample_size": self.sample_size,
            "method": self.method, "evaluation_window": self.evaluation_window,
        }


@dataclass(frozen=True)
class EventContext:
    """Always NOT_AVAILABLE in Cycle 1 — see package docstring. A
    distinct type (not a bare string) so a future, real event module
    has an obvious place to plug in without changing MarketState's
    shape."""

    status: str = EVENT_CONTEXT_NOT_AVAILABLE

    def to_dict(self) -> dict:
        return {"status": self.status}


@dataclass(frozen=True)
class MarketState:
    """One classification of market conditions at one point in time.
    Never a prediction — a structured description of what the evidence
    shows, as of `as_of_time`, nothing more."""

    as_of_time: str                    # ISO 8601, caller-supplied, never wall-clock.
    market_regime: str                 # ALL_REGIMES
    volatility_state: str              # ALL_VOLATILITY_STATES
    risk_state: str                    # ALL_RISK_STATES
    recommended_environment: str       # ALL_ENVIRONMENTS
    evidence: Tuple[str, ...]
    confidence: ConfidenceInfo
    event_context: EventContext = field(default_factory=EventContext)
    data_quality: str = "SUFFICIENT"   # "SUFFICIENT" | "INSUFFICIENT" -- mirrors regime_brain.DataQuality

    def __post_init__(self) -> None:
        if self.market_regime not in ALL_REGIMES:
            raise ValueError(f"market_regime={self.market_regime!r} not in {ALL_REGIMES}")
        if self.volatility_state not in ALL_VOLATILITY_STATES:
            raise ValueError(f"volatility_state={self.volatility_state!r} not in {ALL_VOLATILITY_STATES}")
        if self.risk_state not in ALL_RISK_STATES:
            raise ValueError(f"risk_state={self.risk_state!r} not in {ALL_RISK_STATES}")
        if self.recommended_environment not in ALL_ENVIRONMENTS:
            raise ValueError(
                f"recommended_environment={self.recommended_environment!r} not in {ALL_ENVIRONMENTS}"
            )

    def to_dict(self) -> dict:
        return {
            "as_of_time": self.as_of_time,
            "market_regime": self.market_regime,
            "volatility_state": self.volatility_state,
            "risk_state": self.risk_state,
            "recommended_environment": self.recommended_environment,
            "evidence": list(self.evidence),
            "confidence": self.confidence.to_dict(),
            "event_context": self.event_context.to_dict(),
            "data_quality": self.data_quality,
        }
