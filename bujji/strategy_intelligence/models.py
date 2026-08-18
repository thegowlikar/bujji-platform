"""Phase 20.5 -- pure data contracts. No IO, no broker, no execution,
no order/position vocabulary anywhere in this module."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class StrategyEvidence:
    """Research-backed evidence for one strategy family -- exactly the
    shape `bujji.strategy_research.stats.PerformanceStats` (Phase
    20.4) already produces, plus the three period-split expectancies
    Phase 20.4's own stability check computes. Never derived here;
    always supplied by the caller from real research output."""

    strategy_name: str
    sample_size: int
    win_rate: Optional[float]
    profit_factor: Optional[float]           # None when there were no losing trades to divide by.
    gross_expectancy: Optional[float]        # mean theoretical (pre-cost) P&L per trade.
    net_expectancy: Optional[float]          # mean realized (post-cost) P&L per trade.
    train_expectancy: Optional[float]
    validation_expectancy: Optional[float]
    out_of_sample_expectancy: Optional[float]


@dataclass(frozen=True)
class MarketContext:
    """MIC's CURRENT reading -- consumed ONLY to modify confidence
    (see `scoring.score_strategy`), never the evidence-based score.
    `favorable_regimes`/`unfavorable_regimes` are the caller's own
    disclosed record of which regimes this strategy's evidence was
    actually validated in -- never inferred here."""

    mic_regime: str
    favorable_regimes: Tuple[str, ...] = ()
    unfavorable_regimes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class StrategyScore:
    """One strategy's score. `evidence_score` and its three components
    are computed from `evidence` ALONE -- provably identical across
    any two calls that differ only in `context` (see
    `test_mic_context_never_changes_evidence_score`). `confidence` and
    `effective_score` are the only fields `context` may change."""

    strategy_name: str
    evidence_score: float          # 0-100. Historical Edge + Execution Robustness + Stability. Context-independent.
    edge_component: float
    execution_component: float
    stability_component: float
    confidence: str                 # bujji.epistemics.uncertainty confidence label: HIGH/MODERATE/LOW/NONE.
    confidence_limiting_factor: Optional[str]
    context_note: Optional[str]     # human-readable explanation of what `context` did to confidence, if anything.
    effective_score: float          # evidence_score capped by confidence -- what ranking sorts by.
    evidence: StrategyEvidence

    def render(self) -> str:
        lines = [
            f"{self.strategy_name}",
            f"  Score: {self.effective_score:.0f}/100  (evidence_score={self.evidence_score:.0f}, "
            f"edge={self.edge_component:.0f}, execution={self.execution_component:.0f}, "
            f"stability={self.stability_component:.0f})",
            f"  Confidence: {self.confidence}"
            + (f"  (limiting factor: {self.confidence_limiting_factor})" if self.confidence_limiting_factor else ""),
        ]
        if self.context_note:
            lines.append(f"  Context: {self.context_note}")
        e = self.evidence
        lines.append(
            f"  Evidence: n={e.sample_size}, win_rate={e.win_rate}, profit_factor={e.profit_factor}, "
            f"net_expectancy={e.net_expectancy}, gross_expectancy={e.gross_expectancy}"
        )
        return "\n".join(lines)


@dataclass(frozen=True)
class StrategyRankingReport:
    """`scores`: sorted, highest `effective_score` first (ties broken
    by `sample_size` descending -- see `ranking.rank_strategies`)."""

    scores: Tuple[StrategyScore, ...]

    def render(self) -> str:
        lines = ["Strategy Ranking:", ""]
        for i, s in enumerate(self.scores, start=1):
            lines.append(f"{i}. {s.render()}")
            lines.append("")
        return "\n".join(lines)
