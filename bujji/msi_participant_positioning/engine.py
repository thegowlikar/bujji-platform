"""Market Participant Positioning Intelligence engine — pure functions,
no state, no IO, no wall-clock reads.

---------------------------------------------------------------------
Deliverable 1 investigation — real findings (see
docs/MARKET_PARTICIPANT_POSITIONING_INTELLIGENCE.md for the full
writeup):
---------------------------------------------------------------------
- `bujji.options_observation.models.OptionObservation.open_interest` /
  `.change_in_open_interest` are REAL, populated fields (Series 73C,
  from real NSE Bhavcopy) — already exists, no duplication needed.
- No intraday OI history exists anywhere in this codebase — Bhavcopy is
  end-of-day only. Lens C/D therefore require TWO chain snapshots
  (two different days' Bhavcopy ingestions) to compute anything at
  all; given only one snapshot, they honestly report UNKNOWN.
- `bid`/`ask`/`bid_quantity`/`ask_quantity` are ALWAYS None from
  Bhavcopy (Series 73C's own disclosed finding) — no lens in this
  module depends on them.
- `bujji/intelligence/structure_brain.py` already implements real
  wall-proximity (Lens B's technique) and PCR computation (Lens A's
  technique) against LIVE data — but it calls `now_ist()` internally
  (a wall-clock read) and is not a pure/deterministic function, so it
  is NOT imported here (would break this arc's replay-determinism
  discipline). Its TECHNIQUE (not its code) is reimplemented below as
  pure functions over `OptionObservation` tuples, with the same
  disclosed, non-statistically-calibrated caution it already applies
  to PCR (see taxonomy.py's module docstring).

This module imports `bujji.options_observation.models.OptionObservation`
directly (mirrors Series 78/79's own direct consumption of
Episode/MarketEvent — the raw evidence layer this brain reasons over,
not a peer brain).
"""
from __future__ import annotations

import hashlib
from typing import Dict, List, Optional, Tuple

from bujji.options_observation.models import OptionObservation

from . import config as _config
from . import taxonomy
from .models import Explanation, LensOpinion, MarketParticipantPositioningAssessment

ChainSnapshot = Tuple[OptionObservation, ...]


# ---------------------------------------------------------------------------
# Shared chain-reading helpers.
# ---------------------------------------------------------------------------
def _valid_rows(chain: ChainSnapshot) -> List[OptionObservation]:
    return [
        row for row in chain
        if row.strike is not None and row.option_type in ("CE", "PE") and row.open_interest is not None
    ]


def _total_oi_by_type(chain: ChainSnapshot) -> Tuple[Optional[float], Optional[float]]:
    valid = _valid_rows(chain)
    if not valid:
        return None, None
    ce = sum(r.open_interest for r in valid if r.option_type == "CE")
    pe = sum(r.open_interest for r in valid if r.option_type == "PE")
    return ce, pe


def _spot_price(chain: ChainSnapshot) -> Optional[float]:
    for row in chain:
        if row.underlying_price is not None:
            return row.underlying_price
    return None


def _evidence_ids(rows: List[OptionObservation]) -> Tuple[str, ...]:
    return tuple(sorted({r.observation_id for r in rows}))


# ---------------------------------------------------------------------------
# Lens A — Put/Call OI Ratio. Needs only the current snapshot.
# Capped confidence per taxonomy.py's disclosed caution (never HIGH).
# ---------------------------------------------------------------------------
def derive_put_call_oi_ratio_lens(current_chain: ChainSnapshot) -> LensOpinion:
    valid = _valid_rows(current_chain)
    if len(valid) < _config.MIN_CONTRACTS_FOR_ANY_LENS:
        return LensOpinion(
            lens_name=taxonomy.LENS_PUT_CALL_OI_RATIO, positioning_lean=taxonomy.UNKNOWN_POSITIONING,
            confidence=taxonomy.CONFIDENCE_NONE, supporting_evidence_ids=_evidence_ids(valid),
            reasoning=f"Only {len(valid)} contract(s) with usable OI — below the minimum of {_config.MIN_CONTRACTS_FOR_ANY_LENS} required.",
        )
    ce, pe = _total_oi_by_type(current_chain)
    if not ce:
        return LensOpinion(
            lens_name=taxonomy.LENS_PUT_CALL_OI_RATIO, positioning_lean=taxonomy.UNKNOWN_POSITIONING,
            confidence=taxonomy.CONFIDENCE_NONE, supporting_evidence_ids=_evidence_ids(valid),
            reasoning="Total call-side OI is zero or absent — PCR undefined.",
        )
    pcr = pe / ce
    if pcr >= _config.PCR_BULLISH_MIN_RATIO:
        lean, confidence = taxonomy.BULLISH_POSITIONING, taxonomy.CONFIDENCE_MODERATE
    elif pcr <= _config.PCR_BEARISH_MAX_RATIO:
        lean, confidence = taxonomy.BEARISH_POSITIONING, taxonomy.CONFIDENCE_MODERATE
    else:
        lean, confidence = taxonomy.NEUTRAL_POSITIONING, taxonomy.CONFIDENCE_LOW
    return LensOpinion(
        lens_name=taxonomy.LENS_PUT_CALL_OI_RATIO, positioning_lean=lean, confidence=confidence,
        supporting_evidence_ids=_evidence_ids(valid),
        reasoning=f"put_call_oi_ratio={pcr:.4f} (total_pe_oi={pe}, total_ce_oi={ce}); "
                  f"thresholds bullish>={_config.PCR_BULLISH_MIN_RATIO}, bearish<={_config.PCR_BEARISH_MAX_RATIO} "
                  f"(disclosed, non-statistically-calibrated first pass — see taxonomy.py).",
    )


# ---------------------------------------------------------------------------
# Lens B — OI Concentration / wall proximity. Reimplements
# structure_brain.py's proximity TECHNIQUE as a pure function. Needs
# only the current snapshot plus a real spot price.
# ---------------------------------------------------------------------------
def derive_oi_concentration_lens(current_chain: ChainSnapshot) -> LensOpinion:
    valid = _valid_rows(current_chain)
    spot = _spot_price(current_chain)
    if len(valid) < _config.MIN_CONTRACTS_FOR_ANY_LENS or spot is None or spot <= 0:
        return LensOpinion(
            lens_name=taxonomy.LENS_OI_CONCENTRATION, positioning_lean=taxonomy.UNKNOWN_POSITIONING,
            confidence=taxonomy.CONFIDENCE_NONE, supporting_evidence_ids=_evidence_ids(valid),
            reasoning="Insufficient contracts or no real underlying_price available to locate spot relative to strikes.",
        )

    above = [r for r in valid if r.option_type == "CE" and r.strike > spot]
    below = [r for r in valid if r.option_type == "PE" and r.strike < spot]

    resistance = max(above, key=lambda r: r.open_interest) if above else None
    support = max(below, key=lambda r: r.open_interest) if below else None

    if resistance is None and support is None:
        return LensOpinion(
            lens_name=taxonomy.LENS_OI_CONCENTRATION, positioning_lean=taxonomy.UNKNOWN_POSITIONING,
            confidence=taxonomy.CONFIDENCE_NONE, supporting_evidence_ids=_evidence_ids(valid),
            reasoning="No call strikes above spot or put strikes below spot with usable OI.",
        )

    dist_resistance = ((resistance.strike - spot) / spot * 100.0) if resistance is not None else None
    dist_support = ((spot - support.strike) / spot * 100.0) if support is not None else None

    near_resistance = dist_resistance is not None and dist_resistance <= _config.NEAR_WALL_THRESHOLD_PCT
    near_support = dist_support is not None and dist_support <= _config.NEAR_WALL_THRESHOLD_PCT

    evidence = _evidence_ids([r for r in (resistance, support) if r is not None])

    if near_resistance and (not near_support or dist_resistance <= dist_support):
        return LensOpinion(
            lens_name=taxonomy.LENS_OI_CONCENTRATION, positioning_lean=taxonomy.BEARISH_POSITIONING,
            confidence=taxonomy.CONFIDENCE_MODERATE, supporting_evidence_ids=evidence,
            reasoning=f"Spot is within {dist_resistance:.3f}% of a call-OI wall at strike {resistance.strike} "
                      f"(ce_oi={resistance.open_interest}) — heavy call writing above spot reads as resistance-leaning.",
        )
    if near_support:
        return LensOpinion(
            lens_name=taxonomy.LENS_OI_CONCENTRATION, positioning_lean=taxonomy.BULLISH_POSITIONING,
            confidence=taxonomy.CONFIDENCE_MODERATE, supporting_evidence_ids=evidence,
            reasoning=f"Spot is within {dist_support:.3f}% of a put-OI wall at strike {support.strike} "
                      f"(pe_oi={support.open_interest}) — heavy put writing below spot reads as support-leaning.",
        )
    return LensOpinion(
        lens_name=taxonomy.LENS_OI_CONCENTRATION, positioning_lean=taxonomy.NEUTRAL_POSITIONING,
        confidence=taxonomy.CONFIDENCE_LOW, supporting_evidence_ids=evidence,
        reasoning=f"Spot sits mid-range between the nearest call wall ({dist_resistance}%) and put wall ({dist_support}%) — neither near enough to lean.",
    )


# ---------------------------------------------------------------------------
# Lens C — OI Migration. Requires TWO snapshots (see module docstring —
# daily resolution only, no intraday history exists). Honest UNKNOWN
# when no previous_chain is supplied.
# ---------------------------------------------------------------------------
def derive_oi_migration_lens(current_chain: ChainSnapshot, previous_chain: Optional[ChainSnapshot]) -> LensOpinion:
    if previous_chain is None:
        return LensOpinion(
            lens_name=taxonomy.LENS_OI_MIGRATION, positioning_lean=taxonomy.UNKNOWN_POSITIONING,
            confidence=taxonomy.CONFIDENCE_NONE, supporting_evidence_ids=(),
            reasoning="No previous chain snapshot supplied — OI migration requires comparing two points in time; "
                      "no intraday OI history exists in this codebase (Bhavcopy is end-of-day only), so this "
                      "would need a second day's ingestion, not a fabricated estimate.",
        )
    ce_now, pe_now = _total_oi_by_type(current_chain)
    ce_prev, pe_prev = _total_oi_by_type(previous_chain)
    if not ce_now or not ce_prev:
        return LensOpinion(
            lens_name=taxonomy.LENS_OI_MIGRATION, positioning_lean=taxonomy.UNKNOWN_POSITIONING,
            confidence=taxonomy.CONFIDENCE_NONE, supporting_evidence_ids=(),
            reasoning="Total call-side OI is zero or absent in the current or previous snapshot — PCR migration undefined.",
        )
    pcr_now = pe_now / ce_now
    pcr_prev = pe_prev / ce_prev
    delta = pcr_now - pcr_prev
    evidence = _evidence_ids(_valid_rows(current_chain) + _valid_rows(previous_chain))
    if abs(delta) < _config.MIGRATION_MEANINGFUL_PCR_DELTA:
        return LensOpinion(
            lens_name=taxonomy.LENS_OI_MIGRATION, positioning_lean=taxonomy.NEUTRAL_POSITIONING,
            confidence=taxonomy.CONFIDENCE_LOW, supporting_evidence_ids=evidence,
            reasoning=f"PCR moved from {pcr_prev:.4f} to {pcr_now:.4f} (delta={delta:.4f}) — below the "
                      f"{_config.MIGRATION_MEANINGFUL_PCR_DELTA} meaningful-change threshold.",
        )
    lean = taxonomy.BULLISH_POSITIONING if delta > 0 else taxonomy.BEARISH_POSITIONING
    return LensOpinion(
        lens_name=taxonomy.LENS_OI_MIGRATION, positioning_lean=lean, confidence=taxonomy.CONFIDENCE_LOW,
        supporting_evidence_ids=evidence,
        reasoning=f"PCR moved from {pcr_prev:.4f} to {pcr_now:.4f} (delta={delta:.4f}) — positioning migrating "
                  f"toward {'puts' if delta > 0 else 'calls'} (same disclosed, capped-confidence caution as Lens A).",
    )


# ---------------------------------------------------------------------------
# Lens D — OI Expansion vs Contraction. No directional valence of its
# own (always NEUTRAL_POSITIONING) — it informs positioning_strength,
# not positioning_bias. Requires two snapshots; returns (LensOpinion,
# expansion_ratio) so the top-level entrypoint can use the ratio for
# strength without re-deriving it.
# ---------------------------------------------------------------------------
def derive_oi_expansion_contraction_lens(
    current_chain: ChainSnapshot, previous_chain: Optional[ChainSnapshot]
) -> Tuple[LensOpinion, Optional[float]]:
    if previous_chain is None:
        return LensOpinion(
            lens_name=taxonomy.LENS_OI_EXPANSION_CONTRACTION, positioning_lean=taxonomy.UNKNOWN_POSITIONING,
            confidence=taxonomy.CONFIDENCE_NONE, supporting_evidence_ids=(),
            reasoning="No previous chain snapshot supplied — expansion/contraction requires two points in time.",
        ), None
    ce_now, pe_now = _total_oi_by_type(current_chain)
    ce_prev, pe_prev = _total_oi_by_type(previous_chain)
    total_now = (ce_now or 0) + (pe_now or 0)
    total_prev = (ce_prev or 0) + (pe_prev or 0)
    if total_prev <= 0:
        return LensOpinion(
            lens_name=taxonomy.LENS_OI_EXPANSION_CONTRACTION, positioning_lean=taxonomy.UNKNOWN_POSITIONING,
            confidence=taxonomy.CONFIDENCE_NONE, supporting_evidence_ids=(),
            reasoning="Total OI in the previous snapshot is zero or absent — expansion ratio undefined.",
        ), None
    ratio = (total_now - total_prev) / total_prev
    evidence = _evidence_ids(_valid_rows(current_chain) + _valid_rows(previous_chain))
    label = "STABLE"
    if ratio >= _config.EXPANSION_MEANINGFUL_TOTAL_OI_DELTA_RATIO:
        label = "EXPANDING"
    elif ratio <= -_config.EXPANSION_MEANINGFUL_TOTAL_OI_DELTA_RATIO:
        label = "CONTRACTING"
    confidence = taxonomy.CONFIDENCE_MODERATE if label != "STABLE" else taxonomy.CONFIDENCE_LOW
    return LensOpinion(
        lens_name=taxonomy.LENS_OI_EXPANSION_CONTRACTION,
        positioning_lean=taxonomy.NEUTRAL_POSITIONING,  # No directional valence — informs strength only.
        confidence=confidence, supporting_evidence_ids=evidence,
        reasoning=f"Total OI moved from {total_prev} to {total_now} (ratio={ratio:.4f}) — {label}. "
                  f"This lens never votes bullish/bearish; it informs positioning_strength only.",
    ), ratio


# ---------------------------------------------------------------------------
# Lens E — Writer Dominance. Uses change_in_open_interest (a
# single-snapshot field) — needs only the current snapshot.
# ---------------------------------------------------------------------------
def derive_writer_dominance_lens(current_chain: ChainSnapshot) -> LensOpinion:
    valid = [r for r in _valid_rows(current_chain) if r.change_in_open_interest is not None]
    if len(valid) < _config.MIN_CONTRACTS_FOR_ANY_LENS:
        return LensOpinion(
            lens_name=taxonomy.LENS_WRITER_DOMINANCE, positioning_lean=taxonomy.UNKNOWN_POSITIONING,
            confidence=taxonomy.CONFIDENCE_NONE, supporting_evidence_ids=_evidence_ids(valid),
            reasoning=f"Only {len(valid)} contract(s) with a real change_in_open_interest value.",
        )
    ce_change = sum(r.change_in_open_interest for r in valid if r.option_type == "CE")
    pe_change = sum(r.change_in_open_interest for r in valid if r.option_type == "PE")
    evidence = _evidence_ids(valid)

    if ce_change == 0 and pe_change == 0:
        return LensOpinion(
            lens_name=taxonomy.LENS_WRITER_DOMINANCE, positioning_lean=taxonomy.NEUTRAL_POSITIONING,
            confidence=taxonomy.CONFIDENCE_LOW, supporting_evidence_ids=evidence,
            reasoning=f"writer_dominance={taxonomy.WRITER_BALANCED}: no net change in OI on either side "
                      f"(ce_change=0, pe_change=0).",
        )

    ratio_threshold = _config.WRITER_DOMINANCE_MIN_RATIO
    if ce_change > 0 and (pe_change <= 0 or ce_change >= pe_change * ratio_threshold):
        dominance, lean = taxonomy.CALL_WRITERS_DOMINANT, taxonomy.BEARISH_POSITIONING
    elif pe_change > 0 and (ce_change <= 0 or pe_change >= ce_change * ratio_threshold):
        dominance, lean = taxonomy.PUT_WRITERS_DOMINANT, taxonomy.BULLISH_POSITIONING
    else:
        dominance, lean = taxonomy.WRITER_BALANCED, taxonomy.NEUTRAL_POSITIONING

    confidence = taxonomy.CONFIDENCE_MODERATE if dominance != taxonomy.WRITER_BALANCED else taxonomy.CONFIDENCE_LOW
    return LensOpinion(
        lens_name=taxonomy.LENS_WRITER_DOMINANCE, positioning_lean=lean, confidence=confidence,
        supporting_evidence_ids=evidence,
        reasoning=f"writer_dominance={dominance} (ce_change_in_oi={ce_change}, pe_change_in_oi={pe_change}, "
                  f"dominance threshold ratio={ratio_threshold}).",
    )


# ---------------------------------------------------------------------------
# Deliverable 5 — Reconciliation. Mirrors bujji.msi_market_direction
# .engine.reconcile_lenses exactly (same NEUTRAL/MIXED/UNKNOWN
# three-way discipline). Lens D never votes (always NEUTRAL_POSITIONING
# by construction), so it never contributes to disagreement.
# ---------------------------------------------------------------------------
def reconcile_lenses(lens_opinions: Tuple[LensOpinion, ...]) -> Tuple[str, Tuple[str, ...]]:
    opinionated = [lo for lo in lens_opinions if lo.positioning_lean != taxonomy.UNKNOWN_POSITIONING]
    if not opinionated:
        return taxonomy.UNKNOWN_POSITIONING, ()

    ranks = {lo.lens_name: taxonomy.bias_rank(lo.positioning_lean) for lo in opinionated}
    bullish = [lo for lo in opinionated if ranks[lo.lens_name] > 0]
    bearish = [lo for lo in opinionated if ranks[lo.lens_name] < 0]

    if bullish and bearish:
        conflicting = tuple(sorted(lo.lens_name for lo in (bullish + bearish)))
        return taxonomy.MIXED_POSITIONING, conflicting

    avg_rank = sum(ranks.values()) / len(ranks)
    positioning_bias = taxonomy.bias_from_rank(round(avg_rank))
    return positioning_bias, ()


def compute_positioning_strength(expansion_ratio: Optional[float], opinionated_count: int) -> str:
    if opinionated_count == 0:
        return taxonomy.STRENGTH_UNKNOWN
    if expansion_ratio is None:
        return taxonomy.STRENGTH_WEAK
    magnitude = abs(expansion_ratio)
    if magnitude >= _config.EXPANSION_MEANINGFUL_TOTAL_OI_DELTA_RATIO * 3:
        return taxonomy.STRENGTH_STRONG
    if magnitude >= _config.EXPANSION_MEANINGFUL_TOTAL_OI_DELTA_RATIO:
        return taxonomy.STRENGTH_MODERATE
    return taxonomy.STRENGTH_WEAK


def build_explanation(
    *,
    assessment_id: str,
    lens_opinions: Tuple[LensOpinion, ...],
    positioning_bias: str,
    has_previous_snapshot: bool,
) -> Explanation:
    bullish = tuple(sorted(lo.lens_name for lo in lens_opinions if lo.positioning_lean == taxonomy.BULLISH_POSITIONING))
    bearish = tuple(sorted(lo.lens_name for lo in lens_opinions if lo.positioning_lean == taxonomy.BEARISH_POSITIONING))
    neutral_or_unknown = tuple(sorted(
        lo.lens_name for lo in lens_opinions
        if lo.positioning_lean in (taxonomy.NEUTRAL_POSITIONING, taxonomy.UNKNOWN_POSITIONING)
    ))
    per_lens_evidence = tuple(f"{lo.lens_name}: {lo.reasoning}" for lo in lens_opinions)

    missing_evidence: List[str] = ["bid/ask/bid_quantity/ask_quantity are always None from Bhavcopy-sourced data — no lens depends on them."]
    if not has_previous_snapshot:
        missing_evidence.append(
            "No previous chain snapshot was supplied — OI Migration and Expansion/Contraction lenses "
            "genuinely could not form an opinion (UNKNOWN), not merely a weak one."
        )

    if positioning_bias == taxonomy.MIXED_POSITIONING:
        why = (
            f"positioning_bias is MIXED: {len(bullish)} lens(es) leaned bullish ({', '.join(bullish)}) and "
            f"{len(bearish)} leaned bearish ({', '.join(bearish)}) — genuine disagreement is preserved, never averaged."
        )
    elif positioning_bias == taxonomy.UNKNOWN_POSITIONING:
        why = "No participating lens formed a directional opinion at all — there is nothing to reconcile."
    else:
        why = (
            f"All opinionated lenses agreed in sign; positioning_bias={positioning_bias} reflects their "
            f"averaged rank, not a raw vote count."
        )

    return Explanation(
        assessment_id=assessment_id,
        which_lenses_participated=tuple(lo.lens_name for lo in lens_opinions),
        which_bullish=bullish,
        which_bearish=bearish,
        which_neutral_or_unknown=neutral_or_unknown,
        per_lens_evidence=per_lens_evidence,
        missing_evidence=tuple(missing_evidence),
        why_positioning_was_chosen=why,
        schema_version=_config.SCHEMA_VERSION,
    )


def _assessment_id(
    observation_ids: Tuple[str, ...],
    lens_opinions: Tuple[LensOpinion, ...],
    positioning_bias: str,
    positioning_strength: str,
    schema_version: str,
) -> str:
    lens_parts = "||".join(
        f"{lo.lens_name}:{lo.positioning_lean}:{lo.confidence}"
        for lo in sorted(lens_opinions, key=lambda x: x.lens_name)
    )
    seed = "###".join([
        "|".join(sorted(observation_ids)),
        lens_parts, positioning_bias, positioning_strength, schema_version,
    ])
    return "MPPA-" + hashlib.md5(seed.encode()).hexdigest()[:24]


# ---------------------------------------------------------------------------
# Top-level composition entrypoint.
# ---------------------------------------------------------------------------
def assess_participant_positioning(
    current_chain: ChainSnapshot,
    previous_chain: Optional[ChainSnapshot] = None,
    *,
    timestamp: str,
    provenance: str = _config.DEFAULT_PROVENANCE,
    schema_version: str = _config.SCHEMA_VERSION,
) -> MarketParticipantPositioningAssessment:
    pcr_lens = derive_put_call_oi_ratio_lens(current_chain)
    concentration_lens = derive_oi_concentration_lens(current_chain)
    migration_lens = derive_oi_migration_lens(current_chain, previous_chain)
    expansion_lens, expansion_ratio = derive_oi_expansion_contraction_lens(current_chain, previous_chain)
    writer_lens = derive_writer_dominance_lens(current_chain)

    lens_opinions: Tuple[LensOpinion, ...] = (
        pcr_lens, concentration_lens, migration_lens, expansion_lens, writer_lens,
    )

    positioning_bias, conflicting_lenses = reconcile_lenses(lens_opinions)
    opinionated_count = sum(1 for lo in lens_opinions if lo.positioning_lean != taxonomy.UNKNOWN_POSITIONING)
    positioning_strength = compute_positioning_strength(expansion_ratio, opinionated_count)

    all_observation_ids: List[str] = []
    for lo in lens_opinions:
        all_observation_ids.extend(lo.supporting_evidence_ids)
    supporting_observation_ids = tuple(sorted(set(all_observation_ids)))

    assessment_id = _assessment_id(
        supporting_observation_ids, lens_opinions, positioning_bias, positioning_strength, schema_version,
    )

    explanation = build_explanation(
        assessment_id=assessment_id, lens_opinions=lens_opinions,
        positioning_bias=positioning_bias, has_previous_snapshot=previous_chain is not None,
    )

    return MarketParticipantPositioningAssessment(
        assessment_id=assessment_id,
        timestamp=timestamp,
        positioning_bias=positioning_bias,
        positioning_strength=positioning_strength,
        participating_lenses=lens_opinions,
        conflicting_lenses=conflicting_lenses,
        supporting_observation_ids=supporting_observation_ids,
        explanation=explanation,
        provenance=provenance,
        schema_version=schema_version,
    )
