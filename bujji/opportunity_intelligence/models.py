"""Phase 20.6 -- pure data contracts. No IO, no broker, no execution,
no order/position vocabulary anywhere in this module."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from bujji.strategy_intelligence import StrategyScore

ELIGIBLE = "ELIGIBLE"
WATCH = "WATCH"
BLOCKED = "BLOCKED"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
ALL_QUALIFICATION_STATES = (ELIGIBLE, WATCH, BLOCKED, INSUFFICIENT_EVIDENCE)


@dataclass(frozen=True)
class MarketEnvironment:
    """The CURRENT read -- everything here is a real, caller-supplied
    observation, never inferred. `mic_regime` uses the intraday
    vocabulary (`bujji.mic_v0_validation.models_intraday`, Phase
    20.1C's validated granularity) -- never MIC v0's raw session-level
    TREND/RANGE/UNCLEAR, which Phase 20.1 found INCONCLUSIVE at that
    coarser horizon. `risk_state`/`volatility_state` use MIC v0's own
    session-level VIX-derived vocabulary (`bujji.mic_v0.models`),
    since those (unlike regime) were never found horizon-dependent.
    `execution_profile_name` is one of `bujji.execution_profiles`'
    own names (NORMAL/STRESS/EXTREME, Phase 20.2)."""

    mic_regime: str
    risk_state: str
    volatility_state: str
    execution_profile_name: str
    data_quality_ok: bool = True


@dataclass(frozen=True)
class QualificationReason:
    code: str
    detail: str


@dataclass(frozen=True)
class QualificationDecision:
    state: str
    reasons: Tuple[QualificationReason, ...]

    def __post_init__(self):
        if self.state not in ALL_QUALIFICATION_STATES:
            raise ValueError(f"unknown qualification state {self.state!r}")
        if not self.reasons:
            raise ValueError("a QualificationDecision must always carry at least one reason -- never a bare verdict")


@dataclass(frozen=True)
class OpportunityAssessment:
    """One strategy's qualification for the CURRENT environment.
    `strategy_score` is carried through byte-for-byte from `bujji.
    strategy_intelligence.score_strategy()` -- this package never
    recomputes `evidence_score`/`confidence`/`effective_score`; see
    `evaluator.evaluate_opportunity`'s own docstring for the direct
    proof this holds."""

    strategy_name: str
    decision: QualificationDecision
    strategy_score: StrategyScore
    environment: MarketEnvironment

    def render(self) -> str:
        lines = [
            f"{self.strategy_name}",
            f"  Decision: {self.decision.state}",
            f"  Strategy evidence_score={self.strategy_score.evidence_score:.0f} "
            f"confidence={self.strategy_score.confidence}",
            f"  Environment: regime={self.environment.mic_regime} risk={self.environment.risk_state} "
            f"volatility={self.environment.volatility_state} execution={self.environment.execution_profile_name}",
        ]
        for r in self.decision.reasons:
            lines.append(f"  Reason [{r.code}]: {r.detail}")
        return "\n".join(lines)
