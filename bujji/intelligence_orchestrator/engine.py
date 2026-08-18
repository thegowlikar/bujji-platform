"""Intelligence Decision Orchestrator engine — BUJJI Options OS v3,
Trading Brain Intelligence Upgrade, Phase 4.

A THIN CONDUCTOR over four already-real, already-verified engines.
Nothing in this module re-derives market intelligence, strategy
suitability, or candidate scoring -- it calls each real engine exactly
once, in order, and narrates the result:

  1. `bujji.market_thesis.engine.assess` (Phase 3) -- itself a thin
     composition of `msi_trade_thesis.derive_trade_thesis` and
     `msi_strategy_selection_foundation.assess_all_families`. Called
     here unmodified.
  2. `bujji.trading_brain.strategy_selector.engine.select` -- real,
     unmodified, Trading Brain v3's own eligibility gate. Requires a
     real `MarketStateAssessment`, which this orchestrator does NOT
     fabricate from MSI evidence (see DecisionContext's own docstring
     for why: no verified translation path exists). If the caller
     does not supply one, strategy evaluation is honestly skipped --
     never guessed.
  3. `bujji.trading_brain.strategy_evaluator.engine.rank` (Phase 1/2)
     -- real, unmodified, scores the eligible candidates on real
     evidence. Called here unmodified.
  4. This module's own small, disclosed should-we-trade synthesis
     (`_derive_outcome`) -- the ONE genuinely new piece of logic in
     this package, and it is trivial: TRADE iff the evaluator produced
     a real winner, confidence is a deterministic ladder over the
     winner's own real `high_count`.

THE ONE OPEN SEAM, DISCLOSED RATHER THAN PAPERED OVER: Trading Brain
v3's strategy_selector registry (11 strategy_ids: PREMIUM_VWAP_
STRADDLE, IRON_FLY, IRON_CONDOR, DIRECTIONAL_CALL_SPREAD, ...) and
MSI's strategy_selection_foundation family taxonomy (13 families:
LONG_DIRECTIONAL, NEUTRAL_PREMIUM_SELLING, IRON_CONDOR, IRON_FLY, ...)
are two different, unreconciled vocabularies -- confirmed by direct
comparison, not assumed. No verified mapping between them exists
anywhere in this codebase EXCEPT `bujji.construction_shape_bridge.
bridge.STRUCTURE_TO_FAMILY` (SHORT_STRANGLE->NEUTRAL_PREMIUM_SELLING,
IRON_CONDOR->IRON_CONDOR, IRON_FLY->IRON_FLY) -- and even that table
was built for a THIRD vocabulary (`premium_structure_intelligence`'s
StructureType), reused here only because it happens to share literal
string values with three of strategy_selector's own strategy_ids
(confirmed by direct string comparison). This module cross-references
the evaluator's winner against the market thesis's preferred/rejected
families ONLY through that same narrow, already-tested table -- for
any other strategy_id, it honestly reports "no verified MSI family
mapping" rather than guessing one.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Callable, List, Optional, Tuple

from bujji.construction_shape_bridge.bridge import STRUCTURE_TO_FAMILY
from bujji.market_thesis.engine import assess as assess_market_thesis
from bujji.market_thesis.models import MarketThesisAssessment
from bujji.trading_brain.strategy_evaluator.engine import rank as rank_strategies
from bujji.trading_brain.strategy_evaluator.models import RankedCandidates
from bujji.trading_brain.strategy_selector.engine import select as select_strategy_eligibility
from bujji.trading_brain.strategy_selector.models import StrategyDecision

from . import taxonomy
from .models import DecisionContext, DecisionOutcome, DecisionTrace

PROVENANCE = "bujji.intelligence_orchestrator.engine.orchestrate"

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now()


def assess_market(context: DecisionContext) -> DecisionContext:
    """Stage 1, named to match the requested pipeline shape. Performs
    no computation of its own -- `DecisionContext` already IS the
    assembled market evidence, supplied by the caller. Returns it
    unchanged; exists as an explicit, testable seam in case a future
    caller wants to validate/normalize evidence before the pipeline
    runs, without this orchestrator inventing that logic today."""
    return context


def generate_thesis(context: DecisionContext) -> MarketThesisAssessment:
    """Stage 2. Calls `market_thesis.assess()` -- itself a thin
    composition of two real engines -- exactly as its own real
    signature requires."""
    return assess_market_thesis(
        psi=context.psi, mssi=context.mssi, mdi=context.mdi, mppi=context.mppi,
        vsb=context.vsb, consensus=context.consensus, liquidity=context.liquidity,
        volatility_intelligence=context.volatility_intelligence, timestamp=context.timestamp,
    )


def evaluate_strategies(
    context: DecisionContext, clock: Clock = _real_clock,
) -> Tuple[Optional[StrategyDecision], Optional[RankedCandidates]]:
    """Stage 3. Requires a real `MarketStateAssessment` -- honestly
    returns (None, None) when the caller did not supply one, rather
    than fabricating one from MSI evidence (see module docstring).
    `clock` is threaded through to both real engines explicitly --
    without it, each would default to its own wall-clock read,
    breaking determinism between two calls a few milliseconds apart."""
    if context.market_state_assessment is None:
        return None, None
    decision = select_strategy_eligibility(context.market_state_assessment, clock=clock)
    ranked = rank_strategies(
        decision, mdi=context.mdi, psi=context.psi, mssi=context.mssi, vsb=context.vsb,
        liquidity=context.liquidity, volatility_intelligence=context.volatility_intelligence, clock=clock,
    )
    return decision, ranked


def _confidence_from_high_count(high_count: int) -> str:
    if high_count >= 3:
        return taxonomy.CONFIDENCE_HIGH
    if high_count == 2:
        return taxonomy.CONFIDENCE_MODERATE
    if high_count == 1:
        return taxonomy.CONFIDENCE_LOW
    return taxonomy.CONFIDENCE_NONE


def _msi_cross_reference(winner: Optional[str], thesis: MarketThesisAssessment) -> str:
    if winner is None:
        return "No strategy was selected; no cross-reference to make."
    family = STRUCTURE_TO_FAMILY.get(winner)
    if family is None:
        return (
            f"No verified MSI family mapping exists for strategy_id={winner!r} "
            f"(construction_shape_bridge.STRUCTURE_TO_FAMILY only covers SHORT_STRANGLE/IRON_CONDOR/IRON_FLY) "
            f"-- the market thesis's preferred/rejected family lists cannot be honestly compared to this pick."
        )
    if family in thesis.preferred_strategy_families:
        return f"strategy_id={winner!r} maps to MSI family={family!r}, which the market thesis marks PREFERRED."
    if family in thesis.rejected_strategy_families:
        return f"strategy_id={winner!r} maps to MSI family={family!r}, which the market thesis marks REJECTED -- the two lineages disagree."
    if family in thesis.insufficient_evidence_families:
        return f"strategy_id={winner!r} maps to MSI family={family!r}, for which the market thesis had INSUFFICIENT_EVIDENCE."
    return f"strategy_id={winner!r} maps to MSI family={family!r}, which was not assessed by the market thesis this cycle."


def _derive_outcome(ranked: Optional[RankedCandidates], thesis: MarketThesisAssessment) -> DecisionOutcome:
    if ranked is None or ranked.winner is None:
        reason = (
            "No MarketStateAssessment was supplied; strategy evaluation was skipped." if ranked is None
            else f"strategy_evaluator produced no winner (status={ranked.status})."
        )
        return DecisionOutcome(
            decision_status=taxonomy.DECISION_NO_TRADE, selected_strategy=None, confidence=taxonomy.CONFIDENCE_NONE,
            msi_cross_reference=_msi_cross_reference(None, thesis), reasons=(reason,),
        )

    winning_ranking = next(r for r in ranked.rankings if r.strategy_id == ranked.winner)
    confidence = _confidence_from_high_count(winning_ranking.high_count)
    return DecisionOutcome(
        decision_status=taxonomy.DECISION_TRADE, selected_strategy=ranked.winner, confidence=confidence,
        msi_cross_reference=_msi_cross_reference(ranked.winner, thesis),
        reasons=(f"strategy_evaluator selected {ranked.winner}: {winning_ranking.summary}.",),
    )


def _decision_id(fields: Tuple[str, ...], timestamp: str) -> str:
    seed = "###".join(fields) + f"###{timestamp}"
    return "DT-" + hashlib.md5(seed.encode()).hexdigest()[:24]


def select_strategy(
    context: DecisionContext, thesis: MarketThesisAssessment,
    decision: Optional[StrategyDecision], ranked: Optional[RankedCandidates],
) -> DecisionTrace:
    """Stage 4. Composes stages 2 and 3's real outputs into one
    disclosed, ordered narration -- adds only the small, documented
    TRADE/NO_TRADE + confidence synthesis in `_derive_outcome`."""
    outcome = _derive_outcome(ranked, thesis)

    steps: List[str] = []
    supplied = [name for name, val in (
        ("psi", context.psi), ("mssi", context.mssi), ("mdi", context.mdi), ("mppi", context.mppi),
        ("vsb", context.vsb), ("consensus", context.consensus), ("liquidity", context.liquidity),
        ("volatility_intelligence", context.volatility_intelligence), ("premium_behaviour", context.premium_behaviour),
        ("market_state_assessment", context.market_state_assessment),
    ) if val is not None]
    steps.append(f"Market evidence assembled: {', '.join(supplied) if supplied else 'none supplied'}.")
    if context.premium_behaviour is not None:
        steps.append(
            "premium_behaviour was supplied but is not consumed by market_thesis, strategy_selector, or "
            "strategy_evaluator today -- carried through in DecisionContext, not silently dropped, but "
            "does not influence this decision."
        )
    steps.append(
        f"Market Thesis: market_regime={thesis.market_regime}, directional_bias={thesis.directional_bias}, "
        f"volatility_environment={thesis.volatility_environment}, premium_environment={thesis.premium_environment} "
        f"-- confidence={thesis.confidence}."
    )
    steps.append(
        f"MSI Strategy Family Suitability: {len(thesis.preferred_strategy_families)} preferred "
        f"{thesis.preferred_strategy_families}, {len(thesis.rejected_strategy_families)} rejected "
        f"{thesis.rejected_strategy_families}, {len(thesis.insufficient_evidence_families)} insufficient evidence."
    )
    if decision is None:
        steps.append("Strategy Selector (Trading Brain v3): skipped -- no MarketStateAssessment supplied.")
    else:
        steps.append(f"Strategy Selector (Trading Brain v3): {decision.selection_status} -- {decision.selection_reason}")
    if ranked is None:
        steps.append("Strategy Evaluator: skipped -- no eligibility decision to rank.")
    else:
        steps.append(
            f"Strategy Evaluator: {len(ranked.rankings)} eligible candidate(s) scored; winner={ranked.winner}."
        )
    steps.append(f"Cross-reference: {outcome.msi_cross_reference}")
    steps.append(f"Final decision: {outcome.decision_status} ({outcome.selected_strategy}), confidence={outcome.confidence}.")

    supporting_ids = set()
    supporting_ids.add(thesis.assessment_id)
    if decision is not None:
        supporting_ids.add(decision.decision_id)
    if ranked is not None:
        supporting_ids.add(ranked.ranking_id)
    supporting_ids.update(thesis.supporting_assessment_ids)

    decision_id = _decision_id(
        (thesis.assessment_id, decision.decision_id if decision else "NONE", ranked.ranking_id if ranked else "NONE",
         outcome.decision_status, outcome.selected_strategy or "NONE", outcome.confidence),
        context.timestamp,
    )

    return DecisionTrace(
        decision_id=decision_id, timestamp=context.timestamp,
        market_thesis_assessment_id=thesis.assessment_id,
        strategy_decision_id=decision.decision_id if decision else None,
        ranked_candidates_id=ranked.ranking_id if ranked else None,
        steps=tuple(steps), outcome=outcome, supporting_assessment_ids=tuple(sorted(supporting_ids)),
        provenance=PROVENANCE, schema_version=taxonomy.ORCHESTRATOR_VERSION,
    )


def orchestrate(context: DecisionContext, clock: Clock = _real_clock) -> DecisionTrace:
    """The full conductor: assess_market -> generate_thesis ->
    evaluate_strategies -> select_strategy, in order. This is the only
    function most callers need."""
    context = assess_market(context)
    thesis = generate_thesis(context)
    decision, ranked = evaluate_strategies(context, clock=clock)
    return select_strategy(context, thesis, decision, ranked)
