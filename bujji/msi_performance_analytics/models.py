"""Performance Analytics & Edge Validation models — Series 101. Frozen
dataclasses throughout (house convention)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class TradeAnalytics:
    """Deliverable 2 — one immutable record per completed Shadow Trade."""
    shadow_trade_id: str
    realised_return_pct: Optional[float]   # realised_pnl / |simulated_margin|, None if margin unavailable.
    max_favourable_excursion: Optional[float]   # Best real daily unrealised_pnl observed during the hold.
    max_adverse_excursion: Optional[float]      # Worst real daily unrealised_pnl observed during the hold.
    holding_period_days: int
    realised_volatility: Optional[float]        # Stdev of real daily log-returns of the MTM series.
    realised_direction: str                     # "UP" | "DOWN" | "FLAT", from real entry/exit spot.
    exit_efficiency: Optional[float]             # realised_pnl / MFE (only meaningful when MFE > 0).
    thesis_duration_days: int
    lifecycle_duration_days: int
    exit_reason: Optional[str]
    realised_pnl: Optional[float]
    explanation: Tuple[str, ...]
    provenance: str
    schema_version: str


@dataclass(frozen=True)
class ReliabilityNote:
    sample_size: int
    reliability: str            # taxonomy.ALL_RELIABILITY_STATES
    reasoning: Tuple[str, ...]


@dataclass(frozen=True)
class MetricEstimate:
    """One statistic, always paired with its own sample size and
    reliability note -- Deliverable 7's own mandate: never present a
    number without its uncertainty."""
    value: Optional[float]
    sample_size: int
    reliability: ReliabilityNote
    confidence_interval: Optional[Tuple[float, float]]  # None where a CI is not meaningful for this metric.


@dataclass(frozen=True)
class EdgeValidationReport:
    """Deliverable 4."""
    assessment_id: str
    timestamp: str
    win_rate: MetricEstimate
    expectancy: MetricEstimate
    profit_factor: MetricEstimate
    average_winner: MetricEstimate
    average_loser: MetricEstimate
    median_winner: MetricEstimate
    median_loser: MetricEstimate
    payoff_ratio: MetricEstimate
    max_drawdown: MetricEstimate
    longest_losing_streak: MetricEstimate
    longest_winning_streak: MetricEstimate
    risk_adjusted_return: MetricEstimate
    explanation: Tuple[str, ...]
    provenance: str
    schema_version: str


@dataclass(frozen=True)
class DecisionCategorySummary:
    """Deliverable 3 — one category's (APPROVED/REJECTED/NO_TRADE)
    descriptive statistics."""
    category: str
    frequency: int
    average_confidence_rank: Optional[float]
    thesis_distribution: Dict[str, int]
    strategy_distribution: Dict[str, int]
    market_regime_distribution: Dict[str, int]


@dataclass(frozen=True)
class CounterfactualRecord:
    """Deliverable 6 — one non-approved decision, judged ONLY against
    what actually happened (never against what SHOULD have been
    decided)."""
    decision_id: str
    date: str
    category: str                    # taxonomy.CATEGORY_REJECTED | CATEGORY_NO_TRADE
    realised_movement_pct: Optional[float]
    classification: str               # taxonomy.ALL_COUNTERFACTUAL_CLASSES
    reasoning: Tuple[str, ...]


@dataclass(frozen=True)
class CounterfactualReport:
    assessment_id: str
    timestamp: str
    total_non_approved: int
    rejected_winners: int
    rejected_losers: int
    unknown: int
    records: Tuple[CounterfactualRecord, ...]
    explanation: Tuple[str, ...]
    provenance: str
    schema_version: str
