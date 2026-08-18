"""Phase 20.6 -- the qualification evaluator. One function, composing
`rules.py`'s pure checks in a fixed order -- no scoring logic lives
here (that stays in `bujji.strategy_intelligence`, Phase 20.5,
untouched)."""
from __future__ import annotations

from typing import Tuple

from bujji.strategy_intelligence import StrategyScore

from . import rules
from .models import BLOCKED, ELIGIBLE, INSUFFICIENT_EVIDENCE, WATCH, MarketEnvironment, OpportunityAssessment, QualificationDecision, QualificationReason


def evaluate_opportunity(
    strategy_score: StrategyScore, environment: MarketEnvironment,
    favorable_regimes: Tuple[str, ...] = (), unfavorable_regimes: Tuple[str, ...] = (),
) -> OpportunityAssessment:
    """`strategy_score` is passed straight through, unmodified, into
    the returned `OpportunityAssessment.strategy_score` -- this
    function reads it (evidence_score, confidence, effective_score)
    but never writes a new value into any of its fields. `favorable_
    regimes`/`unfavorable_regimes` are the caller's own disclosed
    record of what this strategy's research (Phase 20.3/20.4) actually
    validated -- e.g. `bujji.strategy_research.TREND_FOLLOWING.
    market_conditions_required` -- never inferred here."""
    if not rules.has_sufficient_evidence(strategy_score):
        reason = rules.insufficient_evidence_reason(strategy_score)
        decision = QualificationDecision(state=INSUFFICIENT_EVIDENCE, reasons=(reason,))
        return OpportunityAssessment(
            strategy_name=strategy_score.strategy_name, decision=decision,
            strategy_score=strategy_score, environment=environment,
        )

    block_reason = rules.hard_block_reason(strategy_score, environment, unfavorable_regimes)
    if block_reason is not None:
        decision = QualificationDecision(state=BLOCKED, reasons=(block_reason,))
        return OpportunityAssessment(
            strategy_name=strategy_score.strategy_name, decision=decision,
            strategy_score=strategy_score, environment=environment,
        )

    if rules.is_ideal_environment(environment, favorable_regimes) and strategy_score.effective_score >= rules.MIN_ELIGIBLE_EFFECTIVE_SCORE:
        reason = QualificationReason(
            code="IDEAL_MATCH",
            detail=f"regime={environment.mic_regime!r} in favorable set {favorable_regimes!r}, "
                   f"risk/volatility/execution normal, effective_score={strategy_score.effective_score}",
        )
        decision = QualificationDecision(state=ELIGIBLE, reasons=(reason,))
    else:
        reason = rules.suboptimal_environment_reason(environment, favorable_regimes)
        decision = QualificationDecision(state=WATCH, reasons=(reason,))

    return OpportunityAssessment(
        strategy_name=strategy_score.strategy_name, decision=decision,
        strategy_score=strategy_score, environment=environment,
    )
