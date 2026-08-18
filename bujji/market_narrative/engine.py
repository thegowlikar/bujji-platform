"""Market Narrative Engine -- pure functions, no state, no IO, no LLM.
Phase 11 Upgrade 2.

Reads the SAME per-cycle dicts `IntelligenceCycleRecorder` already
persists (price_structure, market_structure, participant_positioning,
volatility_structure, market_direction, consensus, liquidity) plus an
optional regime-memory snapshot dict (Upgrade 1), and composes a short,
rule-based story out of ONLY the fragments that resolved with real
evidence. A domain reporting UNKNOWN/None contributes nothing -- never
a fabricated sentence about it. Not part of the trading path: read-only,
never imported by any decision-making code.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from .models import NarrativeReport

_CONFIDENCE_RANK = {"NONE": 0, "LOW": 1, "MODERATE": 2, "HIGH": 3}
_CONSENSUS_RANK = {
    "NO_CONSENSUS": 0, "WEAK_CONSENSUS": 1, "MODERATE_CONSENSUS": 2,
    "STRONG_CONSENSUS": 3, "UNANIMOUS_CONSENSUS": 4,
}
_STRUCTURE_LOCATION_PHRASE = {
    "INSIDE_RANGE": "inside a structural range",
    "NEAR_SUPPORT": "near structural support",
    "NEAR_RESISTANCE": "near structural resistance",
}
_BULLISH_LEANS = {"STRONG_BULLISH", "BULLISH", "WEAK_BULLISH"}
_BEARISH_LEANS = {"STRONG_BEARISH", "BEARISH", "WEAK_BEARISH"}


def _safe_get(d: Optional[dict], key: str):
    return d.get(key) if isinstance(d, dict) else None


def _build_fragments(record: dict, regime_memory: Optional[dict]) -> Tuple[List[str], List[str]]:
    """Returns (sentence_fragments, dominant_factor_slugs)."""
    sentences: List[str] = []
    factors: List[str] = []

    vsb = record.get("volatility_structure")
    compression_state = _safe_get(vsb, "compression_state")
    expansion_state = _safe_get(vsb, "expansion_state")
    if compression_state == "CONFIRMED":
        sentences.append("Volatility remains compressed.")
        factors.append("compression")
    elif expansion_state == "CONFIRMED":
        sentences.append("Volatility is expanding.")
        factors.append("expansion")

    mssi = record.get("market_structure")
    location = _safe_get(mssi, "structure_location")
    if location in _STRUCTURE_LOCATION_PHRASE:
        sentences.append(f"Price remains {_STRUCTURE_LOCATION_PHRASE[location]}.")
        factors.append("range_bound_structure" if location == "INSIDE_RANGE" else "structural_boundary_test")

    consensus = record.get("consensus")
    consensus_level = _safe_get(consensus, "consensus_level")
    if consensus_level in ("STRONG_CONSENSUS", "UNANIMOUS_CONSENSUS"):
        sentences.append("Multiple intelligence domains agree on the current read.")
        factors.append("strong_cross_domain_consensus")
    elif consensus_level == "NO_CONSENSUS":
        sentences.append("Directional conviction across domains is weak.")
        factors.append("weak_directional_consensus")

    pp = record.get("participant_positioning")
    bias = _safe_get(pp, "positioning_bias")
    strength = _safe_get(pp, "positioning_strength")
    if bias and bias != "UNKNOWN":
        strength_phrase = f" ({strength.lower()})" if strength and strength != "UNKNOWN" else ""
        sentences.append(f"Participant positioning shows a {bias.lower()} bias{strength_phrase}.")
        factors.append("participant_bias_disclosed")

    liquidity = record.get("liquidity")
    tightness = _safe_get(liquidity, "tightness")
    if tightness == "TIGHT":
        sentences.append("Liquidity is currently favorable for entry or exit.")
        factors.append("tight_liquidity")
    elif tightness == "WIDE":
        sentences.append("Liquidity is currently unfavorable -- spreads are wide.")
        factors.append("wide_liquidity")

    if regime_memory and regime_memory.get("current_regime"):
        duration = regime_memory.get("duration_cycles")
        sentences.append(f"The {regime_memory['current_regime'].lower()} regime has persisted for {duration} cycles.")
        factors.append("regime_context")

    return sentences, factors


def _detect_contradictions(record: dict) -> List[str]:
    contradictions: List[str] = []

    mdi = record.get("market_direction")
    overall_direction = _safe_get(mdi, "overall_direction")
    pp = record.get("participant_positioning")
    bias = _safe_get(pp, "positioning_bias")
    if overall_direction in (_BULLISH_LEANS | _BEARISH_LEANS) and bias in ("NEUTRAL", "UNKNOWN", None):
        contradictions.append("price direction without participant confirmation")

    consensus = record.get("consensus")
    consensus_level = _safe_get(consensus, "consensus_level")
    overall_confidence = _safe_get(mdi, "overall_confidence")
    if consensus_level == "NO_CONSENSUS" and overall_confidence == "HIGH":
        contradictions.append("high directional confidence despite no cross-domain consensus")

    mssi = record.get("market_structure")
    mssi_contradictions = _safe_get(mssi, "contradictions") or ()
    contradictions.extend(f"market_structure: {c}" for c in mssi_contradictions)

    consensus_conflicts = _safe_get(consensus, "conflicting_domains") or ()
    contradictions.extend(f"consensus: conflicting domain {c}" for c in consensus_conflicts)

    return contradictions


def _overall_confidence(record: dict) -> str:
    ranks: List[int] = []

    mdi = record.get("market_direction")
    if _safe_get(mdi, "overall_confidence") in _CONFIDENCE_RANK:
        ranks.append(_CONFIDENCE_RANK[mdi["overall_confidence"]])

    mssi = record.get("market_structure")
    if _safe_get(mssi, "confidence") in _CONFIDENCE_RANK:
        ranks.append(_CONFIDENCE_RANK[mssi["confidence"]])

    vsb = record.get("volatility_structure")
    if _safe_get(vsb, "confidence") in _CONFIDENCE_RANK:
        ranks.append(_CONFIDENCE_RANK[vsb["confidence"]])

    consensus = record.get("consensus")
    level = _safe_get(consensus, "consensus_level")
    if level in _CONSENSUS_RANK:
        # Map consensus's own 0-4 rank onto the 0-3 confidence scale.
        ranks.append(min(3, round(_CONSENSUS_RANK[level] * 3 / 4)))

    if not ranks:
        return "NONE"
    avg_rank = round(sum(ranks) / len(ranks))
    return ("NONE", "LOW", "MODERATE", "HIGH")[avg_rank]


def build_narrative(record: dict, regime_memory: Optional[dict] = None) -> NarrativeReport:
    """Pure function: one intelligence_cycle record dict (+ optional
    regime_memory dict from Upgrade 1) -> one NarrativeReport. Never
    raises on missing/None domains."""
    sentences, factors = _build_fragments(record, regime_memory)
    story = " ".join(sentences) if sentences else "Insufficient evidence to construct a market narrative this cycle."
    contradictions = _detect_contradictions(record)
    confidence = _overall_confidence(record) if sentences else "NONE"

    return NarrativeReport(
        market_story=story,
        dominant_factors=tuple(factors),
        contradictions=tuple(contradictions),
        confidence=confidence,
    )
