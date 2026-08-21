"""Strategy Candidate Evaluator engine — BUJJI Options OS v3, Trading
Brain Intelligence Upgrade, Phase 1.

WHERE THIS SITS: strictly downstream of `strategy_selector.engine.
select()`. That module's own eligibility gate (market_state/
market_character/confidence thresholds) is untouched and remains the
FIRST pass -- a strategy it already rejected never reaches this
module and is never re-evaluated here (see `_carry_through_rejected`).
This module answers a different, narrower question than the
selector's binary eligibility gate: "of the strategies that already
passed, which one is the best expression of today's market?" -- via
named, disclosed, per-dimension scores, never a numeric weighted sum,
never ML.

THE "NERVOUS SYSTEM" GAP THIS MODULE CLOSES: the prior architecture
audit traced `evidence_interpreter.engine.interpret()` in full and
confirmed it only ever consumes MIC v2's four ontology-mapped layers
(Market Context / Context Stability / Calibration / Governance) --
it has no path for MSI's own real intelligence (MDI/PSI/MSSI/VSB/
Liquidity) at all, by construction. Rather than build a new bridge
translating MSI's rich vocabulary down into MIC v2's coarser ontology
fields (a real but separate, larger piece of work), this module takes
those MSI assessments DIRECTLY as caller-supplied, optional keyword
arguments -- the same "plain real object, caller-supplied" contract
`bujji.premium_structure_intelligence.select_structure(mdi, psi, mssi,
vsb, liquidity, ...)` already uses successfully elsewhere in this
codebase. Any dimension whose evidence was not supplied scores
UNKNOWN rather than guessing -- never silently defaulting to a
favorable or unfavorable read.

RISK_EFFICIENCY is deliberately NOT derived from a fresh strategy_id
-> MSI-family mapping. No such mapping has been verified for the
Trading Brain v3 registry's own strategy_id namespace this engagement
-- `registry.StrategyDefinition.risk_profile` (DEFINED_RISK /
UNDEFINED_RISK) is used instead, since it is exactly what `msi_entry_
bridge.py`'s own reviewed, disclosed formula-coverage table tracks:
every DEFINED_RISK family there either has a real bounded max-loss
formula or is architecturally boundable; every UNDEFINED_RISK family
is permanently, explicitly vetoed (see that module's own docstring).
This proxies real formula coverage honestly without inventing an
unverified cross-lineage mapping.

Capital/margin efficiency is NOT a Phase 1 dimension: Gate C's margin
certification has never produced a real result against a live
account (see project memory), so no honest score exists for it yet.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Callable, List, Optional, Tuple

from ..strategy_selector.models import StrategyDecision, StrategyEvaluation
from ..strategy_selector.registry import ALL_STRATEGIES, BY_ID, StrategyDefinition
from ..strategy_selector import taxonomy as selector_taxonomy
from . import taxonomy
from .models import DimensionScore, RankedCandidates, StrategyRanking

Clock = Callable[[], datetime]

_MDI_BULLISH_LEANS: Tuple[str, ...] = ("STRONG_BULLISH", "BULLISH", "WEAK_BULLISH")
_MDI_BEARISH_LEANS: Tuple[str, ...] = ("STRONG_BEARISH", "BEARISH", "WEAK_BEARISH")
_MDI_STRONG_LEANS: Tuple[str, ...] = ("STRONG_BULLISH", "STRONG_BEARISH")


def _real_clock() -> datetime:
    return datetime.now()


def _score_structure_fit(strategy: StrategyDefinition, mssi, psi, mdi) -> DimensionScore:
    if mssi is None or psi is None:
        return DimensionScore(
            taxonomy.DIMENSION_STRUCTURE_FIT, taxonomy.SCORE_UNKNOWN,
            "MSSI and/or PSI evidence was not supplied to this evaluation.",
        )

    bias = strategy.directional_bias

    if bias == "NEUTRAL":
        bounded = mssi.structural_balance == "RANGE_BOUND"
        centered = mssi.structure_location == "INSIDE_RANGE"
        if bounded and centered:
            return DimensionScore(
                taxonomy.DIMENSION_STRUCTURE_FIT, taxonomy.SCORE_HIGH,
                f"MSSI reads {mssi.structure_location} within a {mssi.structural_balance} market -- "
                f"a centered, range-bound read this strategy's neutral shape is built for.",
            )
        if bounded:
            return DimensionScore(
                taxonomy.DIMENSION_STRUCTURE_FIT, taxonomy.SCORE_MEDIUM,
                f"MSSI reads {mssi.structural_balance} but not centered ({mssi.structure_location}) -- "
                f"a real range exists but this strategy's ATM/near-ATM shape is not perfectly placed.",
            )
        return DimensionScore(
            taxonomy.DIMENSION_STRUCTURE_FIT, taxonomy.SCORE_LOW,
            f"MSSI does not read a bounded range ({mssi.structural_balance}) -- a neutral, range-dependent "
            f"structure has no real structural edge here.",
        )

    if bias in ("BULLISH", "BEARISH"):
        if mdi is None:
            return DimensionScore(
                taxonomy.DIMENSION_STRUCTURE_FIT, taxonomy.SCORE_UNKNOWN,
                "MDI evidence was not supplied to this evaluation.",
            )
        leans = _MDI_BULLISH_LEANS if bias == "BULLISH" else _MDI_BEARISH_LEANS
        if mdi.overall_direction in leans and mdi.overall_direction in _MDI_STRONG_LEANS:
            return DimensionScore(
                taxonomy.DIMENSION_STRUCTURE_FIT, taxonomy.SCORE_HIGH,
                f"MDI reads {mdi.overall_direction}, a strong lean matching this strategy's {bias} bias.",
            )
        if mdi.overall_direction in leans:
            return DimensionScore(
                taxonomy.DIMENSION_STRUCTURE_FIT, taxonomy.SCORE_MEDIUM,
                f"MDI reads {mdi.overall_direction}, a lean matching this strategy's {bias} bias but not a strong one.",
            )
        return DimensionScore(
            taxonomy.DIMENSION_STRUCTURE_FIT, taxonomy.SCORE_LOW,
            f"MDI reads {mdi.overall_direction}, which does not support this strategy's {bias} bias.",
        )

    # bias == "VOLATILITY": wants a compressed base (fuel) or an already-confirmed expansion, not a
    # calm, unremarkable structure.
    if psi.compression_state == "COMPRESSION_CONFIRMED" or psi.expansion_state == "EXPANSION_CONFIRMED":
        return DimensionScore(
            taxonomy.DIMENSION_STRUCTURE_FIT, taxonomy.SCORE_HIGH,
            f"PSI reads compression_state={psi.compression_state}, expansion_state={psi.expansion_state} -- "
            f"a real coiled or already-expanding structure this strategy's long-vega shape wants.",
        )
    if psi.compression_state == "COMPRESSION_EARLY":
        return DimensionScore(
            taxonomy.DIMENSION_STRUCTURE_FIT, taxonomy.SCORE_MEDIUM,
            f"PSI reads early compression ({psi.compression_state}) -- a possible but not yet confirmed setup.",
        )
    return DimensionScore(
        taxonomy.DIMENSION_STRUCTURE_FIT, taxonomy.SCORE_LOW,
        f"PSI shows no compression or expansion signal (compression_state={psi.compression_state}, "
        f"expansion_state={psi.expansion_state}) -- no structural basis for a long-vega expression today.",
    )


_SCORE_DOWNGRADE = {
    taxonomy.SCORE_HIGH: taxonomy.SCORE_MEDIUM,
    taxonomy.SCORE_MEDIUM: taxonomy.SCORE_LOW,
    taxonomy.SCORE_LOW: taxonomy.SCORE_LOW,
    taxonomy.SCORE_UNKNOWN: taxonomy.SCORE_UNKNOWN,
}

# Phase 2: when VSB evidence is entirely absent, volatility_intelligence's
# real iv_rank_state (India VIX percentile) is used as a fallback --
# deliberately capped one tier below what a direct VSB read could earn,
# since it is an index-level proxy, never the instrument's own solved IV.
_IV_RANK_FALLBACK_INCOME = {"HIGH": taxonomy.SCORE_MEDIUM, "NORMAL": taxonomy.SCORE_MEDIUM, "LOW": taxonomy.SCORE_LOW}
_IV_RANK_FALLBACK_DEBIT = {"LOW": taxonomy.SCORE_MEDIUM, "NORMAL": taxonomy.SCORE_MEDIUM, "HIGH": taxonomy.SCORE_LOW}


def _score_volatility_fit(strategy: StrategyDefinition, vsb, volatility_intelligence=None) -> DimensionScore:
    if vsb is None:
        if volatility_intelligence is not None and volatility_intelligence.iv_rank_state != "UNKNOWN":
            fallback = _IV_RANK_FALLBACK_INCOME if strategy.income_or_debit == "INCOME" else _IV_RANK_FALLBACK_DEBIT
            level = fallback[volatility_intelligence.iv_rank_state]
            return DimensionScore(
                taxonomy.DIMENSION_VOLATILITY_FIT, level,
                f"No direct VSB evidence; falling back to volatility_intelligence.iv_rank_state="
                f"{volatility_intelligence.iv_rank_state} (India VIX percentile, index-level proxy -- "
                f"capped below what a direct per-instrument VSB read could earn).",
            )
        return DimensionScore(
            taxonomy.DIMENSION_VOLATILITY_FIT, taxonomy.SCORE_UNKNOWN,
            "VSB evidence was not supplied to this evaluation.",
        )

    if strategy.income_or_debit == "INCOME":
        if vsb.iv_state == "IV_RICH":
            score = DimensionScore(
                taxonomy.DIMENSION_VOLATILITY_FIT, taxonomy.SCORE_HIGH,
                f"VSB reads {vsb.iv_state} -- premium is rich relative to VSB's own model, favoring a seller.",
            )
        elif vsb.iv_state == "IV_FAIR":
            score = DimensionScore(
                taxonomy.DIMENSION_VOLATILITY_FIT, taxonomy.SCORE_MEDIUM,
                f"VSB reads {vsb.iv_state} -- no volatility edge either way for a premium seller.",
            )
        else:
            score = DimensionScore(
                taxonomy.DIMENSION_VOLATILITY_FIT, taxonomy.SCORE_LOW,
                f"VSB reads {vsb.iv_state} -- premium looks cheap relative to VSB's own model, unfavorable for a seller.",
            )
    # income_or_debit == "DEBIT"
    elif vsb.iv_state == "IV_CHEAP" or vsb.expansion_state == "EXPANSION_CONFIRMED":
        score = DimensionScore(
            taxonomy.DIMENSION_VOLATILITY_FIT, taxonomy.SCORE_HIGH,
            f"VSB reads iv_state={vsb.iv_state}, expansion_state={vsb.expansion_state} -- "
            f"premium is cheap and/or volatility is already expanding, favoring a buyer.",
        )
    elif vsb.iv_state == "IV_FAIR":
        score = DimensionScore(
            taxonomy.DIMENSION_VOLATILITY_FIT, taxonomy.SCORE_MEDIUM,
            f"VSB reads {vsb.iv_state} -- no volatility edge either way for a premium buyer.",
        )
    else:
        score = DimensionScore(
            taxonomy.DIMENSION_VOLATILITY_FIT, taxonomy.SCORE_LOW,
            f"VSB reads {vsb.iv_state} -- premium looks rich relative to VSB's own model, unfavorable for a buyer.",
        )

    return _refine_with_volatility_intelligence(score, volatility_intelligence)


def _refine_with_volatility_intelligence(score: DimensionScore, volatility_intelligence) -> DimensionScore:
    """Never re-derives the base score -- only downgrades it, and only
    when volatility_intelligence itself already detected a real
    conflict between VSB's instrument-level iv_state and its own
    iv_rank_state's India VIX percentile (volatility_quality=WEAK,
    computed once inside volatility_intelligence, never recomputed
    here). Corroboration (STRONG) or single-source/no-evidence
    (MODERATE/UNKNOWN) leave the base score untouched."""
    if volatility_intelligence is None:
        return score
    if volatility_intelligence.volatility_quality == "WEAK":
        downgraded = _SCORE_DOWNGRADE[score.level]
        return DimensionScore(
            score.dimension, downgraded,
            score.reason + f" REFINED: volatility_intelligence reports volatility_quality=WEAK "
            f"(iv_state and iv_rank_state disagree) -- downgraded from {score.level} to reflect real uncertainty.",
        )
    return score


def _score_risk_efficiency(strategy: StrategyDefinition) -> DimensionScore:
    if strategy.risk_profile == "DEFINED_RISK":
        return DimensionScore(
            taxonomy.DIMENSION_RISK_EFFICIENCY, taxonomy.SCORE_HIGH,
            "risk_profile=DEFINED_RISK -- the numeric risk stack has (or can honestly derive) a bounded "
            "max-loss formula for this shape.",
        )
    return DimensionScore(
        taxonomy.DIMENSION_RISK_EFFICIENCY, taxonomy.SCORE_LOW,
        "risk_profile=UNDEFINED_RISK -- msi_entry_bridge.py permanently vetoes this shape today; no honest "
        "bounded max-loss formula exists for it (see that module's own docstring).",
    )


def _score_liquidity_fit(liquidity) -> DimensionScore:
    if liquidity is None:
        return DimensionScore(
            taxonomy.DIMENSION_LIQUIDITY_FIT, taxonomy.SCORE_UNKNOWN,
            "LiquidityReading was not supplied to this evaluation.",
        )
    tightness = str(liquidity.tightness).rsplit(".", 1)[-1]
    if tightness == "TIGHT":
        return DimensionScore(
            taxonomy.DIMENSION_LIQUIDITY_FIT, taxonomy.SCORE_HIGH,
            "LiquidityReading.tightness=TIGHT -- efficient entry/exit expected.",
        )
    if tightness == "NORMAL":
        return DimensionScore(
            taxonomy.DIMENSION_LIQUIDITY_FIT, taxonomy.SCORE_MEDIUM,
            "LiquidityReading.tightness=NORMAL -- workable but not ideal entry/exit conditions.",
        )
    if tightness == "WIDE":
        return DimensionScore(
            taxonomy.DIMENSION_LIQUIDITY_FIT, taxonomy.SCORE_LOW,
            "LiquidityReading.tightness=WIDE -- entry/exit likely to leak edge through spread.",
        )
    return DimensionScore(
        taxonomy.DIMENSION_LIQUIDITY_FIT, taxonomy.SCORE_UNKNOWN,
        f"LiquidityReading.tightness={tightness!r} is not a recognized TIGHT/NORMAL/WIDE reading.",
    )


def _score_strategy(
    strategy: StrategyDefinition, *, mdi, psi, mssi, vsb, liquidity, volatility_intelligence=None,
) -> Tuple[DimensionScore, ...]:
    return (
        _score_structure_fit(strategy, mssi, psi, mdi),
        _score_volatility_fit(strategy, vsb, volatility_intelligence),
        _score_risk_efficiency(strategy),
        _score_liquidity_fit(liquidity),
    )


def _registry_order_index(strategy_id: str) -> int:
    for i, s in enumerate(ALL_STRATEGIES):
        if s.strategy_id == strategy_id:
            return i
    return len(ALL_STRATEGIES)


def _build_ranking(strategy_id: str, scores: Tuple[DimensionScore, ...], rank_position: int) -> StrategyRanking:
    high_count = sum(1 for s in scores if s.level == taxonomy.SCORE_HIGH)
    unknown_count = sum(1 for s in scores if s.level == taxonomy.SCORE_UNKNOWN)
    parts = [f"{s.dimension}={s.level}" for s in scores]
    summary = f"{strategy_id}: " + ", ".join(parts)
    return StrategyRanking(
        strategy_id=strategy_id, dimension_scores=scores, high_count=high_count,
        unknown_count=unknown_count, rank_position=rank_position, summary=summary,
    )


def _build_trace(status: str, winner: Optional[str], rankings: Tuple[StrategyRanking, ...]) -> str:
    if status == taxonomy.RANKING_STATUS_UNKNOWN:
        return "UNKNOWN because no StrategyDecision was supplied."
    if status == taxonomy.RANKING_STATUS_NO_ELIGIBLE_CANDIDATES:
        return "NO_ELIGIBLE_CANDIDATES -- strategy_selector found nothing eligible; there is nothing to rank."

    lines = [f"Selected {winner}."]
    for r in rankings:
        if r.strategy_id == winner:
            lines.append(f"Winner {r.strategy_id}: {r.summary}.")
        else:
            lines.append(f"Ranked below winner, {r.strategy_id}: {r.summary}.")
    return " ".join(lines)


def rank(
    decision: Optional[StrategyDecision],
    *,
    mdi=None, psi=None, mssi=None, vsb=None, liquidity=None, volatility_intelligence=None,
    clock: Clock = _real_clock,
) -> RankedCandidates:
    """Score and rank strategy_selector's already-ELIGIBLE candidates.

    Never re-gates: a strategy strategy_selector rejected is carried
    through unscored in `rejected_ineligible`, with its own original
    rejection reason still intact in `decision.all_evaluations` --
    this function does not repeat or override that verdict.

    Pure apart from the injectable clock: the same decision + evidence,
    given the same clock, always produces a byte-identical ranking.
    """
    timestamp = clock().isoformat()

    if decision is None:
        trace = _build_trace(taxonomy.RANKING_STATUS_UNKNOWN, None, ())
        seed = "|".join(["NONE", taxonomy.RANKING_STATUS_UNKNOWN, timestamp])
        ranking_id = "RC-" + hashlib.md5(seed.encode()).hexdigest()[:16]
        return RankedCandidates(
            ranking_id=ranking_id, strategy_decision_id=None, status=taxonomy.RANKING_STATUS_UNKNOWN,
            winner=None, rankings=(), rejected_ineligible=(), decision_trace=trace,
            timestamp=timestamp, version=taxonomy.STRATEGY_EVALUATOR_VERSION,
        )

    eligible_evals: List[StrategyEvaluation] = [
        ev for ev in decision.all_evaluations if ev.eligibility == selector_taxonomy.ELIGIBILITY_ELIGIBLE
    ]
    rejected_ids = tuple(
        ev.strategy_id for ev in decision.all_evaluations
        if ev.eligibility != selector_taxonomy.ELIGIBILITY_ELIGIBLE
    )

    if not eligible_evals:
        trace = _build_trace(taxonomy.RANKING_STATUS_NO_ELIGIBLE_CANDIDATES, None, ())
        seed = "|".join([decision.decision_id, taxonomy.RANKING_STATUS_NO_ELIGIBLE_CANDIDATES, timestamp])
        ranking_id = "RC-" + hashlib.md5(seed.encode()).hexdigest()[:16]
        return RankedCandidates(
            ranking_id=ranking_id, strategy_decision_id=decision.decision_id,
            status=taxonomy.RANKING_STATUS_NO_ELIGIBLE_CANDIDATES, winner=None, rankings=(),
            rejected_ineligible=rejected_ids, decision_trace=trace,
            timestamp=timestamp, version=taxonomy.STRATEGY_EVALUATOR_VERSION,
        )

    scored: List[Tuple[str, Tuple[DimensionScore, ...]]] = []
    for ev in eligible_evals:
        definition = BY_ID.get(ev.strategy_id)
        if definition is None:
            continue
        scores = _score_strategy(
            definition, mdi=mdi, psi=psi, mssi=mssi, vsb=vsb, liquidity=liquidity,
            volatility_intelligence=volatility_intelligence,
        )
        scored.append((ev.strategy_id, scores))

    # Deterministic ordering: highest high_count first, fewest unknowns
    # next, registry declaration order as the final tie-break -- the
    # SAME tie-break convention strategy_selector.engine.select() itself
    # already uses, never a new ordering invented here.
    def _sort_key(item):
        strategy_id, scores = item
        high_count = sum(1 for s in scores if s.level == taxonomy.SCORE_HIGH)
        unknown_count = sum(1 for s in scores if s.level == taxonomy.SCORE_UNKNOWN)
        return (-high_count, unknown_count, _registry_order_index(strategy_id))

    scored.sort(key=_sort_key)

    rankings = tuple(
        _build_ranking(strategy_id, scores, position + 1)
        for position, (strategy_id, scores) in enumerate(scored)
    )
    winner = rankings[0].strategy_id

    trace = _build_trace(taxonomy.RANKING_STATUS_RANKED, winner, rankings)
    seed = "|".join([decision.decision_id, winner, timestamp])
    ranking_id = "RC-" + hashlib.md5(seed.encode()).hexdigest()[:16]

    return RankedCandidates(
        ranking_id=ranking_id, strategy_decision_id=decision.decision_id,
        status=taxonomy.RANKING_STATUS_RANKED, winner=winner, rankings=rankings,
        rejected_ineligible=rejected_ids, decision_trace=trace,
        timestamp=timestamp, version=taxonomy.STRATEGY_EVALUATOR_VERSION,
    )
