"""bujji.mic_v0_validation.models_intraday — Phase 20.1C.

New models for intraday rolling-window validation. Additive to
`models.py` (Phase 20.1B) — that module's `DayClassification`/
`RegimeGroupStats`/`ValidationReport` are untouched, reused for the
session-level (full-day) results exactly as before. Nothing here
redesigns `bujji.mic_v0`'s own architecture; this package only adds
new VALIDATION capability.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

# Expanded vocabulary (charter Step 5) -- VALIDATION ONLY, never wired
# into bujji.mic_v0.engine or any decision path. Not every listed state
# is reachable from RegimeBrain's current output -- see
# intraday_validation.py's own disclosed mapping and the Limitations
# section of this phase's report for exactly which are and are not.
INTRADAY_TREND_UP = "TREND_UP"
INTRADAY_TREND_DOWN = "TREND_DOWN"
INTRADAY_RANGE = "RANGE"
INTRADAY_BREAKOUT_ATTEMPT = "BREAKOUT_ATTEMPT"          # not reachable today -- see disclosure
INTRADAY_VOLATILITY_EXPANSION = "VOLATILITY_EXPANSION"
INTRADAY_VOLATILITY_COMPRESSION = "VOLATILITY_COMPRESSION"
INTRADAY_TRANSITION = "TRANSITION"
INTRADAY_NO_TRADE = "NO_TRADE"                            # not reachable today -- see disclosure
INTRADAY_UNKNOWN = "UNKNOWN"

ALL_INTRADAY_REGIMES = (
    INTRADAY_TREND_UP, INTRADAY_TREND_DOWN, INTRADAY_RANGE, INTRADAY_BREAKOUT_ATTEMPT,
    INTRADAY_VOLATILITY_EXPANSION, INTRADAY_VOLATILITY_COMPRESSION, INTRADAY_TRANSITION,
    INTRADAY_NO_TRADE, INTRADAY_UNKNOWN,
)

WINDOW_LENGTHS_MINUTES = (30, 60, 90, 120)


@dataclass(frozen=True)
class IntradayWindowReading:
    """One rolling window's classification plus independent market-
    behavior metrics -- same "never derive validation evidence from
    the classification itself" discipline as Phase 20.1B's
    `DayClassification`."""

    date: str
    window_label: str              # e.g. "09:15-09:45"
    window_minutes: int            # one of WINDOW_LENGTHS_MINUTES
    intraday_regime: str           # ALL_INTRADAY_REGIMES
    efficiency_ratio: Optional[float]
    adx: Optional[float]
    realized_vol: Optional[float]
    compression_ratio: Optional[float]
    reversal_frequency: Optional[float]
    persistence: Optional[float]
    candles_used: int

    def to_dict(self) -> dict:
        return {
            "date": self.date, "window_label": self.window_label, "window_minutes": self.window_minutes,
            "intraday_regime": self.intraday_regime, "efficiency_ratio": self.efficiency_ratio,
            "adx": self.adx, "realized_vol": self.realized_vol, "compression_ratio": self.compression_ratio,
            "reversal_frequency": self.reversal_frequency, "persistence": self.persistence,
            "candles_used": self.candles_used,
        }


@dataclass(frozen=True)
class IntradayRegimeGroupStats:
    label: str
    window_minutes: int
    n: int
    avg_efficiency_ratio: Optional[float]
    avg_adx: Optional[float]
    avg_reversal_frequency: Optional[float]
    avg_persistence: Optional[float]

    def to_dict(self) -> dict:
        return {
            "label": self.label, "window_minutes": self.window_minutes, "n": self.n,
            "avg_efficiency_ratio": self.avg_efficiency_ratio, "avg_adx": self.avg_adx,
            "avg_reversal_frequency": self.avg_reversal_frequency, "avg_persistence": self.avg_persistence,
        }


@dataclass(frozen=True)
class WindowLengthReport:
    window_minutes: int
    total_windows: int
    group_stats: Tuple[IntradayRegimeGroupStats, ...]
    separation_conclusion: str
    separation_evidence: Tuple[str, ...]
    sufficient_sample_size: bool


@dataclass(frozen=True)
class IntradayValidationReport:
    window_reports: Tuple[WindowLengthReport, ...]
    total_days_analyzed: int
    overall_pass: bool
    overall_conclusion: str

    def render(self) -> str:
        lines = ["MIC Intraday Validation Report (Phase 20.1C)", "=" * 60, "",
                 f"Total real trading days analyzed: {self.total_days_analyzed}", ""]
        for wr in self.window_reports:
            lines.append(f"--- {wr.window_minutes}-minute rolling windows ({wr.total_windows} total) ---")
            for gs in wr.group_stats:
                lines.append(
                    f"  {gs.label}: n={gs.n} avg_ER={gs.avg_efficiency_ratio} avg_ADX={gs.avg_adx} "
                    f"avg_reversal={gs.avg_reversal_frequency} avg_persistence={gs.avg_persistence}"
                )
            lines.append(f"  Sufficient sample size: {wr.sufficient_sample_size}")
            lines.append(f"  Conclusion: {wr.separation_conclusion}")
            for e in wr.separation_evidence:
                lines.append(f"    - {e}")
            lines.append("")
        lines.append(f"OVERALL: {'PASS' if self.overall_pass else 'FAIL/INCONCLUSIVE'}")
        lines.append(self.overall_conclusion)
        return "\n".join(lines)
