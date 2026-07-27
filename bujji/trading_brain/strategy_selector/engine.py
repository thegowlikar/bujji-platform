"""Strategy Selector engine — BUJJI Options OS v3, Engineering Series
34, Sprint 1.

This is the first component in the Trading Brain permitted to answer
"what should we do?" -- and even here, only "which single existing
strategy, if any, honestly fits today's market?" It never asks "which
would make the most money?"; that question is forbidden by the
specification and never appears anywhere in this module.

Input is exactly one `MarketStateAssessment` (or `None`) -- never MIC
v2, never `EvidenceInterpretation`, never a Publication/Consumer
record, never a broker, replay, or candle. The firewall established in
Series 32 and respected by Series 33 remains absolute here too.

Evaluation is a plain deterministic loop over `registry.ALL_STRATEGIES`
with no scoring, no ranking, no machine learning, and no randomness.
Tie-breaking, when more than one strategy is eligible, is simply "the
first eligible strategy in registry declaration order" -- documented
in registry.py, never re-derived here.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Callable, List, Optional, Tuple

from ..market_state.models import MarketStateAssessment
from . import taxonomy
from .models import StrategyDecision, StrategyEvaluation
from .registry import ALL_STRATEGIES, StrategyDefinition

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now()


def _character_meets_minimum(actual: str, minimum: str) -> bool:
    return taxonomy.CHARACTER_TOLERANCE_ORDER.index(actual) >= taxonomy.CHARACTER_TOLERANCE_ORDER.index(minimum)


def _confidence_meets_minimum(actual: str, minimum: str) -> bool:
    return taxonomy.CONFIDENCE_ORDER.index(actual) >= taxonomy.CONFIDENCE_ORDER.index(minimum)


def _downgrade_one_step(level: str) -> str:
    idx = taxonomy.CONFIDENCE_ORDER.index(level)
    if level == "UNKNOWN":
        return "UNKNOWN"
    return taxonomy.CONFIDENCE_ORDER[max(idx - 1, 1)]


def evaluate_strategy(
    strategy: StrategyDefinition, assessment: MarketStateAssessment
) -> StrategyEvaluation:
    """Ask, honestly: can this strategy operate under today's market?

    Never asks whether it would be the most profitable choice -- that
    question has no representation anywhere in this function.
    """
    if assessment.market_state == "UNKNOWN":
        return StrategyEvaluation(
            strategy_id=strategy.strategy_id,
            eligibility=taxonomy.ELIGIBILITY_UNKNOWN,
            supporting_conditions=(),
            rejecting_conditions=("Market state is UNKNOWN; eligibility cannot be assessed.",),
        )

    supporting: List[str] = []
    rejecting: List[str] = []

    if assessment.market_state in strategy.forbidden_market_states:
        rejecting.append(
            f"Market state {assessment.market_state} is forbidden for {strategy.name}."
        )
        return StrategyEvaluation(
            strategy_id=strategy.strategy_id,
            eligibility=taxonomy.ELIGIBILITY_NOT_ELIGIBLE,
            supporting_conditions=tuple(supporting),
            rejecting_conditions=tuple(rejecting),
        )

    if assessment.market_state not in strategy.required_market_states:
        rejecting.append(
            f"Market state {assessment.market_state} is not among the required states "
            f"{strategy.required_market_states} for {strategy.name}."
        )
        return StrategyEvaluation(
            strategy_id=strategy.strategy_id,
            eligibility=taxonomy.ELIGIBILITY_NOT_ELIGIBLE,
            supporting_conditions=tuple(supporting),
            rejecting_conditions=tuple(rejecting),
        )

    supporting.append(f"Market state {assessment.market_state} matches required states.")

    if not _character_meets_minimum(assessment.market_character, strategy.minimum_market_character):
        rejecting.append(
            f"Market character {assessment.market_character} does not meet the minimum "
            f"{strategy.minimum_market_character} required by {strategy.name}."
        )
        return StrategyEvaluation(
            strategy_id=strategy.strategy_id,
            eligibility=taxonomy.ELIGIBILITY_NOT_ELIGIBLE,
            supporting_conditions=tuple(supporting),
            rejecting_conditions=tuple(rejecting),
        )

    supporting.append(
        f"Market character {assessment.market_character} meets minimum "
        f"{strategy.minimum_market_character}."
    )

    if not _confidence_meets_minimum(assessment.confidence, strategy.required_confidence):
        rejecting.append(
            f"Confidence {assessment.confidence} is below the {strategy.required_confidence} "
            f"required by {strategy.name}."
        )
        return StrategyEvaluation(
            strategy_id=strategy.strategy_id,
            eligibility=taxonomy.ELIGIBILITY_NOT_ELIGIBLE,
            supporting_conditions=tuple(supporting),
            rejecting_conditions=tuple(rejecting),
        )

    supporting.append(f"Confidence {assessment.confidence} meets required {strategy.required_confidence}.")

    return StrategyEvaluation(
        strategy_id=strategy.strategy_id,
        eligibility=taxonomy.ELIGIBILITY_ELIGIBLE,
        supporting_conditions=tuple(supporting),
        rejecting_conditions=(),
    )


def _build_trace(
    selection_status: str,
    selected: Optional[StrategyDefinition],
    winning_eval: Optional[StrategyEvaluation],
    all_evals: Tuple[StrategyEvaluation, ...],
    tie_break_applied: bool,
) -> str:
    lines = []
    if selection_status == taxonomy.SELECTION_STATUS_UNKNOWN:
        return "UNKNOWN because no MarketStateAssessment was supplied."
    if selection_status == taxonomy.SELECTION_STATUS_NO_STRATEGY:
        lines.append("NO_STRATEGY -- no registered strategy honestly qualified.")
        for ev in all_evals:
            reason = "; ".join(ev.rejecting_conditions) or "no reason recorded."
            lines.append(f"Rejected {ev.strategy_id}: {reason}")
        return " ".join(lines)

    assert selected is not None and winning_eval is not None
    lines.append(f"Selected {selected.name} ({selected.strategy_id}).")
    lines.append("Reason: " + "; ".join(winning_eval.supporting_conditions))
    if tie_break_applied:
        lines.append(
            "Multiple strategies were eligible; selected via deterministic registry-order tie-break."
        )
    for ev in all_evals:
        if ev.strategy_id == selected.strategy_id:
            continue
        if ev.eligibility == taxonomy.ELIGIBILITY_NOT_ELIGIBLE:
            reason = "; ".join(ev.rejecting_conditions) or "no reason recorded."
            lines.append(f"Rejected {ev.strategy_id}: {reason}")
    return " ".join(lines)


def select(
    assessment: Optional[MarketStateAssessment],
    clock: Clock = _real_clock,
) -> StrategyDecision:
    """Select at most one strategy for today's market.

    Never sizes a position, never places an order, never estimates
    PnL, never ranks a portfolio. Deterministic apart from the
    injectable clock: the same assessment, given the same clock,
    always produces a byte-identical decision.
    """
    timestamp = clock().isoformat()

    if assessment is None:
        trace = _build_trace(taxonomy.SELECTION_STATUS_UNKNOWN, None, None, (), False)
        seed = "|".join(["NONE", taxonomy.SELECTION_STATUS_UNKNOWN, timestamp])
        decision_id = "SD-" + hashlib.md5(seed.encode()).hexdigest()[:16]
        return StrategyDecision(
            decision_id=decision_id,
            selected_strategy=None,
            selection_status=taxonomy.SELECTION_STATUS_UNKNOWN,
            selection_confidence="UNKNOWN",
            selection_reason="No MarketStateAssessment was supplied.",
            supporting_conditions=(),
            rejecting_conditions=(),
            alternative_candidates=(),
            all_evaluations=(),
            decision_trace=trace,
            market_state_assessment_id=None,
            timestamp=timestamp,
            version=taxonomy.STRATEGY_SELECTOR_VERSION,
        )

    evaluations = tuple(evaluate_strategy(s, assessment) for s in ALL_STRATEGIES)
    eligible_ids = [
        ev.strategy_id for ev in evaluations if ev.eligibility == taxonomy.ELIGIBILITY_ELIGIBLE
    ]

    if not eligible_ids:
        trace = _build_trace(taxonomy.SELECTION_STATUS_NO_STRATEGY, None, None, evaluations, False)
        seed = "|".join([assessment.assessment_id, taxonomy.SELECTION_STATUS_NO_STRATEGY, timestamp])
        decision_id = "SD-" + hashlib.md5(seed.encode()).hexdigest()[:16]
        return StrategyDecision(
            decision_id=decision_id,
            selected_strategy=None,
            selection_status=taxonomy.SELECTION_STATUS_NO_STRATEGY,
            selection_confidence="UNKNOWN" if assessment.market_state == "UNKNOWN" else assessment.confidence,
            selection_reason="No registered strategy honestly qualified under today's market state.",
            supporting_conditions=(),
            rejecting_conditions=tuple(
                "; ".join(ev.rejecting_conditions) for ev in evaluations if ev.rejecting_conditions
            ),
            alternative_candidates=(),
            all_evaluations=evaluations,
            decision_trace=trace,
            market_state_assessment_id=assessment.assessment_id,
            timestamp=timestamp,
            version=taxonomy.STRATEGY_SELECTOR_VERSION,
        )

    # Deterministic tie-break: first eligible strategy in registry
    # declaration order (see registry.py::ALL_STRATEGIES).
    selected_id = eligible_ids[0]
    selected = next(s for s in ALL_STRATEGIES if s.strategy_id == selected_id)
    winning_eval = next(ev for ev in evaluations if ev.strategy_id == selected_id)
    tie_break_applied = len(eligible_ids) > 1
    alternatives = tuple(sid for sid in eligible_ids if sid != selected_id)

    selection_confidence = assessment.confidence
    if tie_break_applied:
        selection_confidence = _downgrade_one_step(selection_confidence)

    trace = _build_trace(
        taxonomy.SELECTION_STATUS_SELECTED, selected, winning_eval, evaluations, tie_break_applied
    )

    seed = "|".join([assessment.assessment_id, selected_id, timestamp])
    decision_id = "SD-" + hashlib.md5(seed.encode()).hexdigest()[:16]

    return StrategyDecision(
        decision_id=decision_id,
        selected_strategy=selected_id,
        selection_status=taxonomy.SELECTION_STATUS_SELECTED,
        selection_confidence=selection_confidence,
        selection_reason="; ".join(winning_eval.supporting_conditions),
        supporting_conditions=winning_eval.supporting_conditions,
        rejecting_conditions=(),
        alternative_candidates=alternatives,
        all_evaluations=evaluations,
        decision_trace=trace,
        market_state_assessment_id=assessment.assessment_id,
        timestamp=timestamp,
        version=taxonomy.STRATEGY_SELECTOR_VERSION,
    )
