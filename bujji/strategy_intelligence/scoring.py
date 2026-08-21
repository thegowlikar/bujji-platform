"""Phase 20.5 -- scoring engine. Pure functions, no IO, no ML, no
parameter optimization. Every constant below is a disclosed, round
reference point (not fit to any specific strategy's numbers) --
sample-size tiers follow ordinary statistical practice, profit-factor/
win-rate reference points are round, defensible thresholds picked
before this phase's real-data run, not after.
"""
from __future__ import annotations

from typing import Optional, Tuple

from bujji.epistemics import uncertainty as epi

from .models import MarketContext, StrategyEvidence, StrategyScore

# Sample-size confidence tiers -- ordinary statistical rules of thumb,
# not tuned to Phase 20.4's specific n=11,278 / n=150 results.
SAMPLE_SIZE_HIGH = 500
SAMPLE_SIZE_MODERATE = 100
SAMPLE_SIZE_LOW = 20

# Reference points for the Historical Edge component -- a profit
# factor of 2.0 and a win rate of 0.55 are round, pre-registered
# "solid edge" reference levels, not fit to any evidence this phase
# scores.
_EDGE_PROFIT_FACTOR_REFERENCE = 2.0
_EDGE_WIN_RATE_REFERENCE = 0.55

# Confidence -> effective-score ceiling. This is the mechanism that
# prevents a small-sample, high-apparent-return strategy from
# outranking a large-sample, stable one (Phase 20.5's explicit "no
# overfitting" requirement) -- confidence caps the score BEFORE
# ranking ever compares two strategies.
_EFFECTIVE_SCORE_CAP = {epi.HIGH: 100.0, epi.MODERATE: 70.0, epi.LOW: 45.0, epi.NONE: 30.0}


def _edge_component(evidence: StrategyEvidence) -> float:
    """0-40. Zero whenever net_expectancy is not positive -- a losing
    (or breakeven) strategy earns no edge points regardless of profit
    factor or win rate in isolation."""
    if evidence.net_expectancy is None or evidence.net_expectancy <= 0:
        return 0.0
    pf_term = min((evidence.profit_factor or 0.0) / _EDGE_PROFIT_FACTOR_REFERENCE, 1.0) * 20.0
    wr_term = min((evidence.win_rate or 0.0) / _EDGE_WIN_RATE_REFERENCE, 1.0) * 20.0
    return round(pf_term + wr_term, 2)


def _execution_component(evidence: StrategyEvidence) -> float:
    """0-25. Compares gross (theoretical, pre-cost) vs net (realized,
    post-cost) expectancy -- Phase 20.2's own gross-vs-net distinction,
    reused as this component's entire basis. Zero whenever the
    strategy isn't net-positive, or gross was never positive to begin
    with (nothing for costs to have eaten)."""
    if evidence.net_expectancy is None or evidence.net_expectancy <= 0:
        return 0.0
    if not evidence.gross_expectancy or evidence.gross_expectancy <= 0:
        return 0.0
    drag_pct = max(0.0, (evidence.gross_expectancy - evidence.net_expectancy) / evidence.gross_expectancy)
    return round(25.0 * (1.0 - min(drag_pct, 1.0)), 2)


def _stability_component(evidence: StrategyEvidence) -> float:
    """0-25. Fraction of the three Phase 20.4 periods (train/
    validation/out-of-sample) with positive expectancy -- rewards
    consistency, penalizes degradation into a negative period, exactly
    as this phase's own spec asks. A period with no data (None) is
    excluded from the denominator, never counted as a failure."""
    periods = (evidence.train_expectancy, evidence.validation_expectancy, evidence.out_of_sample_expectancy)
    known = [p for p in periods if p is not None]
    if not known:
        return 0.0
    positive = sum(1 for p in known if p > 0)
    return round(25.0 * positive / len(known), 2)


def _sample_size_uncertainty(n: int) -> epi.Uncertainty:
    if n < SAMPLE_SIZE_LOW:
        return epi.insufficient(f"sample_size={n} < minimum {SAMPLE_SIZE_LOW}")
    if n >= SAMPLE_SIZE_HIGH:
        conf = epi.HIGH
    elif n >= SAMPLE_SIZE_MODERATE:
        conf = epi.MODERATE
    else:
        conf = epi.LOW
    return epi.Uncertainty(state=epi.KNOWN, confidence=conf)


def _apply_mic_context(base_confidence: str, context: Optional[MarketContext]) -> Tuple[str, Optional[str]]:
    """The ONLY place `context` is consulted. Demotes confidence by
    ONE band (`epi.demote`, reused unmodified) when the current MIC
    regime is either explicitly disclosed as unfavorable for this
    strategy, or was never validated either way -- never promotes
    above the evidence-based confidence, and never touches the score.
    This is the direct, disclosed implementation of "MIC modifies
    confidence, not the strategy exists/trade decision"."""
    if context is None:
        return base_confidence, None
    if context.mic_regime in context.unfavorable_regimes:
        return (
            epi.demote(base_confidence, 1),
            f"current MIC regime {context.mic_regime!r} is outside this strategy's validated favorable set "
            f"{context.favorable_regimes!r} -- confidence demoted, score unchanged",
        )
    if context.mic_regime in context.favorable_regimes:
        return base_confidence, f"current MIC regime {context.mic_regime!r} matches this strategy's validated favorable set -- confidence unchanged"
    return (
        epi.demote(base_confidence, 1),
        f"current MIC regime {context.mic_regime!r} was never validated for this strategy -- confidence demoted, score unchanged",
    )


def score_strategy(evidence: StrategyEvidence, context: Optional[MarketContext] = None) -> StrategyScore:
    """`evidence_score` (and its three components) depends ONLY on
    `evidence` -- `context` is consulted strictly after, and only for
    `confidence`/`effective_score`/`context_note`. See
    `test_mic_context_never_changes_evidence_score` for the direct
    proof."""
    edge = _edge_component(evidence)
    execution = _execution_component(evidence)
    stability = _stability_component(evidence)
    evidence_score = round(edge + execution + stability, 2)

    sample_u = _sample_size_uncertainty(evidence.sample_size)
    composed = epi.compose(
        [epi.Input(name="sample_size", uncertainty=sample_u, critical=True)], base_confidence=epi.HIGH,
    )
    base_confidence = composed.confidence
    limiting_factor = composed.limiting_factor

    final_confidence, context_note = _apply_mic_context(base_confidence, context)
    if context_note is not None and final_confidence != base_confidence:
        limiting_factor = f"mic_context:{context.mic_regime}"

    cap_value = _EFFECTIVE_SCORE_CAP.get(final_confidence, 30.0)
    effective_score = round(min(evidence_score, cap_value), 2)

    return StrategyScore(
        strategy_name=evidence.strategy_name, evidence_score=evidence_score,
        edge_component=edge, execution_component=execution, stability_component=stability,
        confidence=final_confidence, confidence_limiting_factor=limiting_factor,
        context_note=context_note, effective_score=effective_score, evidence=evidence,
    )
