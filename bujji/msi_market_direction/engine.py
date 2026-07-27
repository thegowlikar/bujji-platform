"""Market Direction Intelligence engine — pure functions, no state, no
IO, no wall-clock reads.

---------------------------------------------------------------------
Step 0 investigation resolution (see docs/MARKET_DIRECTION_INTELLIGENCE.md
for the full writeup) — this is the single most important design
decision in this module:
---------------------------------------------------------------------
Per Series 84's investigation (docs/DIRECTIONAL_OWNERSHIP_INVESTIGATION.md),
`bujji.msi_price_structure.engine.derive_trend_state` computed a signed
value internally but never exposed it. Rather than re-deriving that
signal independently in this package (which would duplicate reasoning
already performed by Series 78), Series 85 added ONE purely-additive
field to `PriceStructureAssessment`: `trend_direction_signal`
(taxonomy.DIRECTION_UP / DIRECTION_DOWN / None), exposing exactly the
value `derive_trend_state` already computed. `derive_price_structure_lens`
below consumes that field directly — it does NOT re-read raw
Episode/MarketEvent evidence at all, because the signal already exists
on the real, unmodified `PriceStructureAssessment` object.

`bujji.msi_market_structure.engine`, by contrast, needed NO equivalent
change: `breakout_state`/`breakdown_state`/`structure_location`
(LOCATION_ABOVE_RESISTANCE / LOCATION_BELOW_SUPPORT specifically) were
ALREADY public and ALREADY genuinely directional — confirmed breaks of
resistance/support are directional facts by definition. For the
remaining, purely-spatial `structure_location` values (NEAR_SUPPORT /
NEAR_RESISTANCE / INSIDE_RANGE), Series 84's investigation found NO
internal signal is computed anywhere in Series 79 for those cases —
genuinely absent, not merely unexposed — so `derive_market_structure_lens`
honestly reports UNKNOWN for them rather than fabricating a lean or
attempting independent re-derivation from raw price evidence (which
would require inventing new interpretive logic Series 79 itself never
performed, exceeding this sprint's "reconcile existing lenses" scope).

This module imports `bujji.msi_price_structure.models.PriceStructureAssessment`
and `bujji.msi_market_structure.models.MarketStructureAssessment` DIRECTLY
(unlike bujji.msi_consensus's deliberately domain-agnostic design) —
this sprint's own Deliverable 2 names these two lenses concretely by
brain, not generically, so direct, typed consumption is the correct,
disclosed design here (mirroring Series 82's own "downstream consumer,
not sibling" exception).
"""
from __future__ import annotations

import hashlib
from typing import List, Optional, Tuple

from bujji.msi_price_structure.models import PriceStructureAssessment
from bujji.msi_price_structure import taxonomy as psi_taxonomy
from bujji.msi_market_structure.models import MarketStructureAssessment
from bujji.msi_market_structure import taxonomy as mssi_taxonomy

from . import config as _config
from . import taxonomy
from .models import Explanation, LensOpinion, MarketDirectionAssessment


# ---------------------------------------------------------------------------
# Lens A — Price Structure Direction. Consumes the already-computed,
# purely-additive `trend_direction_signal` field (see module docstring).
# ---------------------------------------------------------------------------
def derive_price_structure_lens(psi: PriceStructureAssessment) -> LensOpinion:
    signal = psi.trend_direction_signal
    trend_state = psi.trend_state

    if signal is None:
        if trend_state == psi_taxonomy.TREND_NONE and psi.balance_state == psi_taxonomy.BALANCE_IN_BALANCE:
            lean, confidence = taxonomy.NEUTRAL, taxonomy.CONFIDENCE_LOW
            reasoning = (
                f"trend_state={trend_state} (no directional run) and balance_state="
                f"{psi.balance_state} (net-cancelling price action) — genuine evidence of no bias."
            )
        else:
            lean, confidence = taxonomy.UNKNOWN, taxonomy.CONFIDENCE_NONE
            reasoning = (
                f"trend_direction_signal is None (trend_state={trend_state}) and no offsetting "
                f"balance evidence exists — insufficient evidence to form any directional opinion."
            )
    else:
        is_up = signal == psi_taxonomy.DIRECTION_UP
        if trend_state == psi_taxonomy.TREND_ESTABLISHED:
            lean = taxonomy.STRONG_BULLISH if is_up else taxonomy.STRONG_BEARISH
            confidence = taxonomy.CONFIDENCE_HIGH
        elif trend_state == psi_taxonomy.TREND_EMERGING:
            lean = taxonomy.BULLISH if is_up else taxonomy.BEARISH
            confidence = taxonomy.CONFIDENCE_MODERATE
        else:  # TREND_WEAKENING — momentum fading, weaker conviction in the same direction.
            lean = taxonomy.WEAK_BULLISH if is_up else taxonomy.WEAK_BEARISH
            confidence = taxonomy.CONFIDENCE_LOW
        reasoning = (
            f"trend_direction_signal={signal} at trend_state={trend_state} "
            f"(structure_state={psi.structure_state})."
        )

    return LensOpinion(
        lens_name=taxonomy.PRICE_STRUCTURE_DIRECTION,
        directional_lean=lean,
        confidence=confidence,
        supporting_evidence_ids=psi.supporting_observation_ids,
        reasoning=reasoning,
    )


# ---------------------------------------------------------------------------
# Lens B — Market Structure Direction. Uses breakout_state/breakdown_state
# / structure_location directly — no re-derivation needed or attempted.
# ---------------------------------------------------------------------------
def derive_market_structure_lens(mssi: MarketStructureAssessment) -> LensOpinion:
    loc = mssi.structure_location

    if loc == mssi_taxonomy.LOCATION_ABOVE_RESISTANCE:
        lean, confidence = taxonomy.STRONG_BULLISH, taxonomy.CONFIDENCE_HIGH
        reasoning = f"structure_location={loc}: a confirmed resistance breakout is a directional fact."
    elif loc == mssi_taxonomy.LOCATION_BELOW_SUPPORT:
        lean, confidence = taxonomy.STRONG_BEARISH, taxonomy.CONFIDENCE_HIGH
        reasoning = f"structure_location={loc}: a confirmed support breakdown is a directional fact."
    elif loc == mssi_taxonomy.LOCATION_AT_RETEST:
        breakout_confirmed = mssi.breakout_state == mssi_taxonomy.BREAKOUT_CONFIRMED
        breakdown_confirmed = mssi.breakdown_state == mssi_taxonomy.BREAKDOWN_CONFIRMED
        if breakout_confirmed and not breakdown_confirmed:
            lean, confidence = taxonomy.WEAK_BULLISH, taxonomy.CONFIDENCE_LOW
            reasoning = "structure_location=AT_RETEST of a confirmed resistance breakout — direction known, unresolved retest lowers confidence."
        elif breakdown_confirmed and not breakout_confirmed:
            lean, confidence = taxonomy.WEAK_BEARISH, taxonomy.CONFIDENCE_LOW
            reasoning = "structure_location=AT_RETEST of a confirmed support breakdown — direction known, unresolved retest lowers confidence."
        else:
            lean, confidence = taxonomy.UNKNOWN, taxonomy.CONFIDENCE_NONE
            reasoning = "structure_location=AT_RETEST with ambiguous/absent break evidence — genuinely undetermined."
    else:
        # NEAR_SUPPORT / NEAR_RESISTANCE / INSIDE_RANGE / UNKNOWN — per
        # Series 84's investigation, no internal directional signal is
        # computed anywhere in Series 79 for these purely-spatial
        # cases. Honest UNKNOWN, not fabricated.
        lean, confidence = taxonomy.UNKNOWN, taxonomy.CONFIDENCE_NONE
        reasoning = f"structure_location={loc} is purely spatial — Series 79 computes no directional signal for this case."

    return LensOpinion(
        lens_name=taxonomy.MARKET_STRUCTURE_DIRECTION,
        directional_lean=lean,
        confidence=confidence,
        supporting_evidence_ids=mssi.supporting_observation_ids,
        reasoning=reasoning,
    )


# ---------------------------------------------------------------------------
# Deliverable 5 — Reconciliation Engine. NEVER majority-vote blindly,
# NEVER discard minority evidence (every LensOpinion always survives
# in participating_lenses regardless of what this function concludes).
# Extensible to an arbitrary-length tuple of lenses (Deliverable 2's
# design mandate) — never hardcoded to exactly 2.
# ---------------------------------------------------------------------------
def reconcile_lenses(lens_opinions: Tuple[LensOpinion, ...]) -> Tuple[str, str, Tuple[str, ...]]:
    opinionated = [lo for lo in lens_opinions if lo.directional_lean != taxonomy.UNKNOWN]

    if not opinionated:
        return taxonomy.UNKNOWN, taxonomy.CONFIDENCE_NONE, ()

    ranks = {lo.lens_name: taxonomy.band_rank(lo.directional_lean) for lo in opinionated}
    bullish = [lo for lo in opinionated if ranks[lo.lens_name] > 0]
    bearish = [lo for lo in opinionated if ranks[lo.lens_name] < 0]

    if bullish and bearish:
        # Genuine disagreement — a real market state, never averaged away.
        overall_direction = taxonomy.MIXED
        overall_confidence = taxonomy.CONFIDENCE_LOW
        conflicting = tuple(sorted(lo.lens_name for lo in (bullish + bearish)))
        return overall_direction, overall_confidence, conflicting

    # All opinionated lenses agree in sign (or all are NEUTRAL, rank 0).
    avg_rank = round(sum(ranks.values()) / len(ranks))
    overall_direction = taxonomy.band_from_rank(avg_rank)

    agreeing_count = len(opinionated)
    min_lens_confidence_rank = min(taxonomy.confidence_rank(lo.confidence) for lo in opinionated)
    if agreeing_count >= _config.CONFIDENCE_ALL_AGREE_MIN_OPINIONATED and min_lens_confidence_rank >= taxonomy.confidence_rank(taxonomy.CONFIDENCE_MODERATE):
        overall_confidence = taxonomy.CONFIDENCE_HIGH
    else:
        overall_confidence = taxonomy.confidence_at_rank(min_lens_confidence_rank)

    conflicting = ()  # nobody disagrees in sign when we reach this branch.
    return overall_direction, overall_confidence, conflicting


def build_explanation(
    *,
    assessment_id: str,
    lens_opinions: Tuple[LensOpinion, ...],
    overall_direction: str,
    conflicting_lenses: Tuple[str, ...],
) -> Explanation:
    opinionated_lenses = [lo for lo in lens_opinions if lo.directional_lean != taxonomy.UNKNOWN]
    bullish = tuple(sorted(lo.lens_name for lo in opinionated_lenses if taxonomy.band_rank(lo.directional_lean) > 0))
    bearish = tuple(sorted(lo.lens_name for lo in opinionated_lenses if taxonomy.band_rank(lo.directional_lean) < 0))
    neutral_or_unknown = tuple(sorted(
        lo.lens_name for lo in lens_opinions
        if lo.directional_lean == taxonomy.UNKNOWN or lo.directional_lean == taxonomy.NEUTRAL
    ))
    per_lens_evidence = tuple(f"{lo.lens_name}: {lo.reasoning}" for lo in lens_opinions)

    if overall_direction == taxonomy.MIXED:
        why_not_vote = (
            f"Overall direction is MIXED, not a majority vote: {len(bullish)} lens(es) leaned bullish "
            f"({', '.join(bullish)}) and {len(bearish)} leaned bearish ({', '.join(bearish)}) — genuine "
            f"disagreement is preserved as its own outcome rather than averaged or resolved by count."
        )
    elif overall_direction == taxonomy.UNKNOWN:
        why_not_vote = "No participating lens formed a directional opinion at all — there is nothing to vote on."
    else:
        why_not_vote = (
            f"All opinionated lenses agreed in sign, so overall_direction reflects their averaged band-rank — "
            f"not a raw vote count, since a lens's own confidence and band strength (e.g. STRONG_BULLISH vs "
            f"WEAK_BULLISH) both factor into the resulting band and overall_confidence."
        )

    return Explanation(
        assessment_id=assessment_id,
        which_lenses_participated=tuple(lo.lens_name for lo in lens_opinions),
        which_bullish=bullish,
        which_bearish=bearish,
        which_neutral_or_unknown=neutral_or_unknown,
        per_lens_evidence=per_lens_evidence,
        why_not_a_simple_vote=why_not_vote,
        schema_version=_config.SCHEMA_VERSION,
    )


def _assessment_id(
    supporting_assessment_ids: Tuple[str, ...],
    lens_opinions: Tuple[LensOpinion, ...],
    overall_direction: str,
    schema_version: str,
) -> str:
    lens_parts = "||".join(
        f"{lo.lens_name}:{lo.directional_lean}:{lo.confidence}"
        for lo in sorted(lens_opinions, key=lambda x: x.lens_name)
    )
    seed = "###".join([
        "|".join(sorted(supporting_assessment_ids)),
        lens_parts,
        overall_direction,
        schema_version,
    ])
    return "MDA-" + hashlib.md5(seed.encode()).hexdigest()[:24]


# ---------------------------------------------------------------------------
# Top-level composition entrypoint — Deliverable 9 connects ONLY to
# Series 78/79 in this sprint. Internally extensible: reconcile_lenses
# itself accepts any-length tuple, so a future caller supplying more
# LensOpinions (once real Volatility/Options-Positioning/etc lenses
# exist) requires zero change to reconcile_lenses/build_explanation.
# ---------------------------------------------------------------------------
def determine_market_direction(
    psi: PriceStructureAssessment,
    mssi: MarketStructureAssessment,
    *,
    timestamp: str,
    provenance: str = _config.DEFAULT_PROVENANCE,
    schema_version: str = _config.SCHEMA_VERSION,
) -> MarketDirectionAssessment:
    price_lens = derive_price_structure_lens(psi)
    structure_lens = derive_market_structure_lens(mssi)
    lens_opinions: Tuple[LensOpinion, ...] = (price_lens, structure_lens)

    overall_direction, overall_confidence, conflicting_lenses = reconcile_lenses(lens_opinions)

    supporting_assessment_ids = tuple(sorted({psi.assessment_id, mssi.assessment_id}))

    assessment_id = _assessment_id(supporting_assessment_ids, lens_opinions, overall_direction, schema_version)

    explanation = build_explanation(
        assessment_id=assessment_id,
        lens_opinions=lens_opinions,
        overall_direction=overall_direction,
        conflicting_lenses=conflicting_lenses,
    )

    return MarketDirectionAssessment(
        assessment_id=assessment_id,
        timestamp=timestamp,
        overall_direction=overall_direction,
        overall_confidence=overall_confidence,
        participating_lenses=lens_opinions,
        conflicting_lenses=conflicting_lenses,
        supporting_assessment_ids=supporting_assessment_ids,
        explanation=explanation,
        provenance=provenance,
        schema_version=schema_version,
    )
