"""Volatility Intelligence engine — BUJJI Options OS v3, Trading Brain
Intelligence Upgrade, Phase 2.

Composes exactly two already-real, already-verified sources. Adds NO
new volatility math anywhere in this module:

  1. `bujji.msi_volatility_structure.engine.assess_volatility_structure`
     (VSB) — real IV richness (vs. realized vol), realized volatility,
     expected move, a disclosed price-based volatility-regime proxy,
     and independent expansion_state/compression_state dimensions.
     This module never recomputes any of this — it only reads the
     real `VolatilityStructureAssessment` object a caller already
     built and translates/buckets its fields into this package's own
     vocabulary (see the field-by-field mapping below).

  2. `bujji.mic_v0.volatility_classifier.classify_volatility_state` —
     real, deterministic trailing-window India VIX percentile
     classifier. Called directly, unmodified. Its LOW/NORMAL/HIGH
     output is index-level (India VIX), not a per-instrument
     option-chain-derived IV rank — that distinction is carried into
     every reason string that touches `iv_rank_state`, since
     option-derived IV rank remains genuinely missing codebase-wide
     (confirmed by direct trace; see the Phase 2 architecture audit).

WHAT THIS MODULE ANSWERS: "what is volatility telling us?" WHAT IT
NEVER ANSWERS: "should we sell premium?" No field, branch, or reason
string here recommends a trade or a strategy — that judgment belongs
entirely to `strategy_evaluator`, which may (optionally) read this
assessment's fields as additional evidence.

Field-by-field provenance (all pure translation/bucketing of an
already-real value, or a small disclosed synthesis rule -- never new
math):
  iv_state              <- VSB.iv_state (IV_RICH/IV_CHEAP/IV_FAIR/UNKNOWN), prefix stripped.
  iv_rank_state          <- classify_volatility_state()'s real LOW/NORMAL/HIGH/None(->UNKNOWN).
  volatility_state        <- bucketed from VSB.volatility_regime (a disclosed price-proxy itself).
  volatility_regime        <- combined from VSB.expansion_state + VSB.compression_state.
  expected_move_state      <- renamed from VSB.expected_move_state (NARROW/MODERATE/WIDE).
  skew_state / term_structure_state <- always UNKNOWN (Phase 2 explicitly out of scope).
  volatility_quality        <- NEW synthesis: corroboration between iv_state and iv_rank_state.
  confidence               <- directly derived from volatility_quality.
"""
from __future__ import annotations

import hashlib
from typing import List, Optional, Sequence, Tuple

from bujji.mic_v0.volatility_classifier import classify_volatility_state
from bujji.msi_volatility_structure.models import VolatilityStructureAssessment

from . import taxonomy
from .models import VolatilityIntelligenceAssessment

PROVENANCE = "bujji.volatility_intelligence.engine.assess"

# Agreement table for volatility_quality: which iv_rank_state a given
# iv_state is considered to corroborate. Symmetric, deterministic,
# disclosed -- not a statistical calibration.
_AGREEING_RANK_FOR_IV_STATE = {
    taxonomy.IV_RICH: taxonomy.IV_RANK_HIGH,
    taxonomy.IV_CHEAP: taxonomy.IV_RANK_LOW,
    taxonomy.IV_FAIR: taxonomy.IV_RANK_NORMAL,
}

_QUALITY_TO_CONFIDENCE = {
    taxonomy.QUALITY_STRONG: taxonomy.CONFIDENCE_HIGH,
    taxonomy.QUALITY_MODERATE: taxonomy.CONFIDENCE_MODERATE,
    taxonomy.QUALITY_WEAK: taxonomy.CONFIDENCE_LOW,
    taxonomy.QUALITY_UNKNOWN: taxonomy.CONFIDENCE_NONE,
}

_VSB_IV_STATE_MAP = {"IV_RICH": taxonomy.IV_RICH, "IV_CHEAP": taxonomy.IV_CHEAP, "IV_FAIR": taxonomy.IV_FAIR}
_VSB_EXPECTED_MOVE_MAP = {
    "NARROW": taxonomy.EXPECTED_MOVE_LOW, "MODERATE": taxonomy.EXPECTED_MOVE_NORMAL, "WIDE": taxonomy.EXPECTED_MOVE_HIGH,
}
_VSB_REGIME_TO_VOLATILITY_STATE = {
    "HIGH_VOLATILITY": taxonomy.VOLATILITY_ELEVATED,
    "COMPRESSED": taxonomy.VOLATILITY_LOW,
    "STABLE": taxonomy.VOLATILITY_NORMAL,
    "TRANSITIONING": taxonomy.VOLATILITY_NORMAL,  # disclosed: treated as a non-extreme reading, distinct from the two extremes.
}


def _derive_iv_state(vsb: Optional[VolatilityStructureAssessment]) -> Tuple[str, str]:
    if vsb is None:
        return taxonomy.IV_UNKNOWN, "No VolatilityStructureAssessment supplied."
    mapped = _VSB_IV_STATE_MAP.get(vsb.iv_state)
    if mapped is None:
        return taxonomy.IV_UNKNOWN, f"VSB iv_state={vsb.iv_state} carries no real richness read."
    return mapped, f"VSB iv_state={vsb.iv_state} (instrument-level IV vs. realized volatility)."


def _derive_iv_rank_state(
    current_vix: Optional[float], trailing_vix_history: Sequence[float],
) -> Tuple[str, List[str]]:
    if current_vix is None or not trailing_vix_history:
        return taxonomy.IV_RANK_UNKNOWN, ["No current VIX value and/or trailing VIX history supplied."]
    state, evidence = classify_volatility_state(current_vix, list(trailing_vix_history))
    if state is None:
        return taxonomy.IV_RANK_UNKNOWN, list(evidence)
    return state, list(evidence) + [
        "iv_rank_state is India VIX (index-level) percentile, not per-instrument option-chain-derived IV rank "
        "-- that remains genuinely missing codebase-wide."
    ]


def _derive_volatility_state(vsb: Optional[VolatilityStructureAssessment]) -> Tuple[str, str]:
    if vsb is None:
        return taxonomy.VOLATILITY_UNKNOWN, "No VolatilityStructureAssessment supplied."
    mapped = _VSB_REGIME_TO_VOLATILITY_STATE.get(vsb.volatility_regime)
    if mapped is None:
        return taxonomy.VOLATILITY_UNKNOWN, f"VSB volatility_regime={vsb.volatility_regime} carries no real read."
    return mapped, (
        f"VSB volatility_regime={vsb.volatility_regime} (a disclosed price-statistics proxy, "
        f"not a genuine IV-based signal -- see VSB's own module docstring)."
    )


def _derive_volatility_regime(vsb: Optional[VolatilityStructureAssessment]) -> Tuple[str, str]:
    if vsb is None:
        return taxonomy.REGIME_UNKNOWN, "No VolatilityStructureAssessment supplied."
    expanding = vsb.expansion_state == "CONFIRMED"
    contracting = vsb.compression_state == "CONFIRMED"
    if expanding and contracting:
        return taxonomy.REGIME_UNKNOWN, (
            "VSB reports both expansion_state=CONFIRMED and compression_state=CONFIRMED simultaneously "
            "-- contradictory evidence from a single-ratio derivation that should not occur; refusing "
            "to guess which one is real rather than silently picking one."
        )
    if expanding:
        return taxonomy.REGIME_EXPANDING, "VSB expansion_state=CONFIRMED."
    if contracting:
        return taxonomy.REGIME_CONTRACTING, "VSB compression_state=CONFIRMED."
    if vsb.expansion_state == "NOT_DETECTED" and vsb.compression_state == "NOT_DETECTED":
        return taxonomy.REGIME_STABLE, "VSB reports neither expansion nor compression confirmed."
    return taxonomy.REGIME_UNKNOWN, (
        f"VSB expansion_state={vsb.expansion_state}, compression_state={vsb.compression_state} "
        f"-- insufficient evidence for either extreme or a stable read."
    )


def _derive_expected_move_state(vsb: Optional[VolatilityStructureAssessment]) -> Tuple[str, str]:
    if vsb is None:
        return taxonomy.EXPECTED_MOVE_UNKNOWN, "No VolatilityStructureAssessment supplied."
    mapped = _VSB_EXPECTED_MOVE_MAP.get(vsb.expected_move_state)
    if mapped is None:
        return taxonomy.EXPECTED_MOVE_UNKNOWN, f"VSB expected_move_state={vsb.expected_move_state} carries no real read."
    return mapped, f"VSB expected_move_state={vsb.expected_move_state} (expected_move_pct={vsb.expected_move_pct})."


def _derive_quality_and_confidence(iv_state: str, iv_rank_state: str) -> Tuple[str, str, Optional[str]]:
    iv_real = iv_state != taxonomy.IV_UNKNOWN
    rank_real = iv_rank_state != taxonomy.IV_RANK_UNKNOWN

    if not iv_real and not rank_real:
        quality = taxonomy.QUALITY_UNKNOWN
        note = None
    elif iv_real and rank_real:
        agrees = _AGREEING_RANK_FOR_IV_STATE.get(iv_state) == iv_rank_state
        if agrees:
            quality = taxonomy.QUALITY_STRONG
            note = f"iv_state={iv_state} and iv_rank_state={iv_rank_state} corroborate each other."
        else:
            quality = taxonomy.QUALITY_WEAK
            note = (
                f"iv_state={iv_state} and iv_rank_state={iv_rank_state} DISAGREE -- instrument-level and "
                f"index-level volatility reads point different ways; confidence reduced, not averaged."
            )
    else:
        quality = taxonomy.QUALITY_MODERATE
        source = "iv_state" if iv_real else "iv_rank_state"
        note = f"Only {source} produced real evidence; the other source was not supplied or was UNKNOWN."

    confidence = _QUALITY_TO_CONFIDENCE[quality]
    return quality, confidence, note


def _assessment_id(fields: Tuple[str, ...], timestamp: str) -> str:
    seed = "###".join(fields) + f"###{timestamp}"
    return "VIA-" + hashlib.md5(seed.encode()).hexdigest()[:24]


def assess(
    volatility_structure: Optional[VolatilityStructureAssessment] = None,
    current_vix: Optional[float] = None,
    trailing_vix_history: Sequence[float] = (),
    *,
    timestamp: str,
    provenance: str = PROVENANCE,
    schema_version: str = taxonomy.VOLATILITY_INTELLIGENCE_VERSION,
) -> VolatilityIntelligenceAssessment:
    """Pure function of its inputs. Either real source may be omitted
    independently -- each dimension that depends on a missing source
    reports UNKNOWN honestly, never a guess."""
    iv_state, iv_state_reason = _derive_iv_state(volatility_structure)
    iv_rank_state, iv_rank_evidence = _derive_iv_rank_state(current_vix, trailing_vix_history)
    volatility_state, volatility_state_reason = _derive_volatility_state(volatility_structure)
    volatility_regime, volatility_regime_reason = _derive_volatility_regime(volatility_structure)
    expected_move_state, expected_move_reason = _derive_expected_move_state(volatility_structure)
    volatility_quality, confidence, quality_note = _derive_quality_and_confidence(iv_state, iv_rank_state)

    reasons: List[str] = [
        f"iv_state={iv_state}: {iv_state_reason}",
        f"iv_rank_state={iv_rank_state}: " + "; ".join(iv_rank_evidence),
        f"volatility_state={volatility_state}: {volatility_state_reason}",
        f"volatility_regime={volatility_regime}: {volatility_regime_reason}",
        f"expected_move_state={expected_move_state}: {expected_move_reason}",
        "skew_state=UNKNOWN, term_structure_state=UNKNOWN: genuinely missing codebase-wide, out of Phase 2 scope.",
    ]
    if quality_note is not None:
        reasons.append(f"volatility_quality={volatility_quality}: {quality_note}")
    else:
        reasons.append(f"volatility_quality={volatility_quality}: neither source produced real evidence.")

    supporting_assessment_ids: Tuple[str, ...] = (
        (volatility_structure.assessment_id,) if volatility_structure is not None else ()
    )

    assessment_id = _assessment_id(
        (
            iv_state, iv_rank_state, volatility_state, volatility_regime, expected_move_state,
            taxonomy.SKEW_UNKNOWN, taxonomy.TERM_STRUCTURE_UNKNOWN, volatility_quality, confidence, schema_version,
        ),
        timestamp,
    )

    return VolatilityIntelligenceAssessment(
        assessment_id=assessment_id, timestamp=timestamp,
        iv_state=iv_state, iv_rank_state=iv_rank_state, volatility_state=volatility_state,
        volatility_regime=volatility_regime, expected_move_state=expected_move_state,
        skew_state=taxonomy.SKEW_UNKNOWN, term_structure_state=taxonomy.TERM_STRUCTURE_UNKNOWN,
        volatility_quality=volatility_quality, confidence=confidence, reasons=tuple(reasons),
        supporting_assessment_ids=supporting_assessment_ids, provenance=provenance, schema_version=schema_version,
    )
