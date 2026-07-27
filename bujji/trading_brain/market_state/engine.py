"""Market State Builder engine — BUJJI Options OS v3, Engineering Series
33, Sprint 1.

The Evidence Interpreter (Series 32) answered "what does each
intelligence layer mean?" This module answers a different question:
"taking all of that translated intelligence together, what kind of
market am I actually looking at, and how much do I trust that read?"

This is the first module in the Trading Brain permitted to fuse
multiple signals into one conclusion. It is still forbidden from
answering "what should we do about it" -- there is no strategy field,
no risk allocation, no capital sizing, no execution intent decision
anywhere in this package's output.

Its only input is a single `EvidenceInterpretation` (Series 32's
output) -- never MIC v2 directly, never a Publication or Consumer
record, never a candle, never a broker call. The four signals fused
here are exactly the four the Evidence Interpreter derived from a
single MIC v2 layer each:

    market_state       <- Market Context (trend)
    confidence          <- Context Stability   (used here as "stability")
    opportunity_state   <- Calibration
    risk_state          <- Governance

Fusion is a finite decision table, nothing else: no probabilities, no
machine learning, no numeric scores. Confidence is derived from
agreement/completeness/governance/calibration/stability by stepping
along the same finite Confidence scale the ontology already defines --
never invented, never a raw float.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Callable, List, Tuple

from ..evidence_interpreter.models import EvidenceInterpretation
from . import taxonomy
from .models import MarketStateAssessment

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now()


def _downgrade_one_step(level: str) -> str:
    """Step one position toward VERY_LOW on the finite Confidence scale.

    Never produces UNKNOWN through downgrading alone -- UNKNOWN is
    reserved for cases this engine has already decided, elsewhere, that
    nothing can honestly be claimed. Downgrading only ever expresses
    "less sure than before," never "no idea."
    """
    idx = taxonomy.CONFIDENCE_ORDER.index(level)
    if level == "UNKNOWN":
        return "UNKNOWN"
    return taxonomy.CONFIDENCE_ORDER[max(idx - 1, 1)]


def _provenance_by_field(interpretation: EvidenceInterpretation, field: str):
    for p in interpretation.translation_provenance:
        if p.ontology_field == field:
            return p
    raise KeyError(f"EvidenceInterpretation has no provenance for field '{field}'")


def assess(
    interpretation: EvidenceInterpretation,
    clock: Clock = _real_clock,
) -> MarketStateAssessment:
    """Fuse one EvidenceInterpretation into a MarketStateAssessment.

    Pure apart from the injectable clock. Deterministic: the same
    interpretation, given the same clock, always yields byte-identical
    output.
    """
    snap = interpretation.ontology_snapshot

    market_state_prov = _provenance_by_field(interpretation, "market_state")
    stability_prov = _provenance_by_field(interpretation, "confidence")
    opportunity_prov = _provenance_by_field(interpretation, "opportunity_state")
    risk_prov = _provenance_by_field(interpretation, "risk_state")

    missing_count = sum(
        1
        for p in (market_state_prov, stability_prov, opportunity_prov, risk_prov)
        if p.source_value is None
    )

    supporting: List[str] = []
    contradicting: List[str] = []

    market_state_defined = snap.market_state != "UNKNOWN"

    is_stability_strong = snap.confidence in ("HIGH", "VERY_HIGH")
    is_stability_weak = snap.confidence in ("LOW", "VERY_LOW")
    is_opportunity_good = snap.opportunity_state in ("MEDIUM_EDGE", "HIGH_EDGE")
    is_opportunity_bad = snap.opportunity_state == "AVOID"
    is_risk_elevated = snap.risk_state in ("HIGH", "EXTREME")
    is_risk_normal = snap.risk_state == "NORMAL"

    if market_state_defined:
        if is_stability_strong:
            supporting.append(
                f"Context Stability ({stability_prov.source_value}) supports a defined "
                f"Market State ({market_state_prov.source_value})."
            )
        if is_opportunity_good:
            supporting.append(
                f"Calibration ({opportunity_prov.source_value}) supports a defined "
                f"Market State ({market_state_prov.source_value})."
            )
        if is_risk_normal:
            supporting.append(
                f"Governance ({risk_prov.source_value}) supports a defined "
                f"Market State ({market_state_prov.source_value})."
            )
        if is_risk_elevated:
            contradicting.append(
                f"Governance ({risk_prov.source_value}) contradicts a clean Market State "
                f"reading of ({market_state_prov.source_value})."
            )
        if is_opportunity_bad:
            contradicting.append(
                f"Calibration ({opportunity_prov.source_value}) contradicts a clean Market State "
                f"reading of ({market_state_prov.source_value})."
            )
        if is_stability_weak:
            contradicting.append(
                f"Context Stability ({stability_prov.source_value}) contradicts a clean Market "
                f"State reading of ({market_state_prov.source_value})."
            )

    n_support = len(supporting)
    n_contradict = len(contradicting)

    # ------------------------------------------------------------------
    # Deterministic decision table. Priority order matters: evaluated
    # top to bottom, first match wins.
    # ------------------------------------------------------------------
    if missing_count >= 3:
        character = taxonomy.MARKET_CHARACTER_INSUFFICIENT_EVIDENCE
        final_market_state = "UNKNOWN"
        confidence = "UNKNOWN"
        reasoning_trace = (
            f"UNKNOWN because {missing_count} of 4 input layers "
            f"(Market Context, Context Stability, Calibration, Governance) were not "
            f"supplied. Insufficient evidence to reason about market structure."
        )
    elif not market_state_defined:
        character = taxonomy.MARKET_CHARACTER_MIXED
        final_market_state = "UNKNOWN"
        confidence = snap.confidence
        reasoning_trace = (
            f"UNKNOWN because Market Context ({market_state_prov.source_value}) did not "
            f"resolve to a defined market state. No active contradiction among the "
            f"remaining signals -- evidence is simply silent on structure."
        )
    elif n_contradict == 0:
        character = taxonomy.MARKET_CHARACTER_CLEAR
        final_market_state = snap.market_state
        confidence = snap.confidence
        reasoning_trace = (
            f"{final_market_state} because {market_state_prov.reason} "
            + (" ".join(supporting) if supporting else "No supporting signals were available, but no contradictions were found either.")
            + " No contradictions."
        )
    elif n_contradict >= 2:
        character = taxonomy.MARKET_CHARACTER_UNCERTAIN
        final_market_state = "UNKNOWN"
        confidence = "UNKNOWN"
        reasoning_trace = (
            f"UNKNOWN because {n_contradict} signals contradict a Market State reading of "
            f"{snap.market_state}: " + " ".join(contradicting) + " "
            f"The contradictions outnumber or match the supporting evidence, so the "
            f"reading is withheld rather than trusted."
        )
    else:  # exactly one contradiction
        character = taxonomy.MARKET_CHARACTER_CONTESTED
        final_market_state = snap.market_state
        confidence = _downgrade_one_step(snap.confidence)
        reasoning_trace = (
            f"{final_market_state} because {market_state_prov.reason} "
            + (" ".join(supporting) + " " if supporting else "")
            + "However, this is contested: " + contradicting[0]
        )

    market_phase = taxonomy.PHASE_BY_CONFIDENCE.get(snap.confidence, taxonomy.MARKET_PHASE_UNKNOWN)

    # market_conviction is intentionally identical to confidence in this
    # sprint -- see taxonomy.py's note on REUSED_CONFIDENCE_LEVELS.
    market_conviction = confidence

    timestamp = clock().isoformat()

    seed = "|".join(
        [
            interpretation.interpretation_id,
            final_market_state,
            character,
            confidence,
            market_phase,
            timestamp,
        ]
    )
    assessment_id = "MSA-" + hashlib.md5(seed.encode()).hexdigest()[:16]

    return MarketStateAssessment(
        assessment_id=assessment_id,
        market_state=final_market_state,
        market_phase=market_phase,
        market_character=character,
        market_conviction=market_conviction,
        confidence=confidence,
        supporting_evidence=tuple(supporting),
        contradicting_evidence=tuple(contradicting),
        reasoning_trace=reasoning_trace,
        interpretation_id=interpretation.interpretation_id,
        timestamp=timestamp,
        version=taxonomy.MARKET_STATE_BUILDER_VERSION,
    )
