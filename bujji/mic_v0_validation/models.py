"""bujji.mic_v0_validation.models — Phase 20.1B. Frozen, no logic."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class DayClassification:
    """One real trading day's MIC v0 regime label plus INDEPENDENT
    market-behavior metrics computed from the SAME candles, for
    cross-checking -- never derived from the classification itself."""

    date: str
    market_regime: str          # mic_v0.models.ALL_REGIMES
    adx: Optional[float]
    efficiency_ratio: Optional[float]     # from regime_brain's own evidence, not recomputed
    reversal_frequency: Optional[float]   # fraction of candle-to-candle sign changes
    persistence: Optional[float]          # average consecutive same-direction run length
    candles_used: int


@dataclass(frozen=True)
class RegimeGroupStats:
    label: str
    n: int
    avg_adx: Optional[float]
    avg_efficiency_ratio: Optional[float]
    avg_reversal_frequency: Optional[float]
    avg_persistence: Optional[float]

    def to_dict(self) -> dict:
        return {
            "label": self.label, "n": self.n, "avg_adx": self.avg_adx,
            "avg_efficiency_ratio": self.avg_efficiency_ratio,
            "avg_reversal_frequency": self.avg_reversal_frequency,
            "avg_persistence": self.avg_persistence,
        }


@dataclass(frozen=True)
class ValidationReport:
    trend_stats: RegimeGroupStats
    range_stats: RegimeGroupStats
    unclear_stats: RegimeGroupStats
    total_days: int
    separation_conclusion: str
    separation_evidence: Tuple[str, ...]

    def render(self) -> str:
        lines = [
            "MIC Validation Report", "=" * 60, "",
            f"Total real trading days analyzed: {self.total_days}", "",
            "TREND samples:",
            f"  n = {self.trend_stats.n}",
            f"  Average ADX: {self.trend_stats.avg_adx}",
            f"  Average efficiency ratio: {self.trend_stats.avg_efficiency_ratio}",
            f"  Average reversal frequency: {self.trend_stats.avg_reversal_frequency}",
            f"  Average persistence: {self.trend_stats.avg_persistence}",
            "",
            "RANGE samples:",
            f"  n = {self.range_stats.n}",
            f"  Average ADX: {self.range_stats.avg_adx}",
            f"  Average efficiency ratio: {self.range_stats.avg_efficiency_ratio}",
            f"  Average reversal frequency: {self.range_stats.avg_reversal_frequency}",
            f"  Average persistence: {self.range_stats.avg_persistence}",
            "",
            "UNCLEAR samples:",
            f"  n = {self.unclear_stats.n}",
            "",
            "Conclusion:", f"  {self.separation_conclusion}", "",
            "Evidence:",
        ] + [f"  - {e}" for e in self.separation_evidence]
        return "\n".join(lines)
