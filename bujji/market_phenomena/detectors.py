"""Phenomenon detection rules -- Phase 19.7.

One `_detect_<phenomenon>()` function per phenomenon in
`models.ALL_PHENOMENON_TYPES` -- each a plain, documented, deterministic
rule over already-real `MarketIntelligenceSnapshot` fields (Phase 19.3),
optionally comparing against a `previous` snapshot for transition/trend
detection. Returns `None` (never a fabricated low-confidence guess) when
the snapshot genuinely lacks the evidence to say anything. Mirrors
`bujji.msi_market_phenomena.engine`'s own `_rule_<phenomenon>() -> bool`
pattern and its `_PHENOMENON_RULES` dict + consistency-assert technique
(Phase 19.7's own audit finding) -- reused as a design technique, never
as shared code (different input pipeline).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

from bujji.intelligence.market_intelligence_snapshot.models import MarketIntelligenceSnapshot
from bujji.intelligence.models import DataQuality, ExpiryProximity, Richness, RegimeType, SpreadTightness, VixRegime

from .evidence import event_evidence, liquidity_evidence, regime_evidence
from .models import (
    ALL_PHENOMENON_TYPES,
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MODERATE,
    PHENOMENON_EVENT_RISK,
    PHENOMENON_LIQUIDITY_STRESS,
    PHENOMENON_REGIME_TRANSITION,
    PHENOMENON_VOLATILITY_COMPRESSION,
    PHENOMENON_VOLATILITY_EXPANSION,
    PhenomenonEvidenceItem,
)


@dataclass(frozen=True)
class DetectionResult:
    confidence: str
    state: str
    supporting_evidence: Tuple[PhenomenonEvidenceItem, ...]
    contradicting_evidence: Tuple[PhenomenonEvidenceItem, ...]


def _detect_volatility_compression(
    current: MarketIntelligenceSnapshot, previous: Optional[MarketIntelligenceSnapshot],
) -> Optional[DetectionResult]:
    """Primary evidence: RegimeBrain's own COMPRESSED classification --
    its own docstring already defines this as "range visibly narrowing
    within the session," the exact real-world meaning this phenomenon
    names. Corroborating evidence: a real realized-vol decrease versus
    `previous`, when a previous snapshot is available."""
    regime = current.regime
    if regime.data_quality != DataQuality.SUFFICIENT or regime.regime != RegimeType.COMPRESSED:
        return None

    supporting = [
        regime_evidence(regime, "compression_ratio"),
        regime_evidence(regime, "realized_vol"),
    ]
    contradicting = []
    confidence = CONFIDENCE_MODERATE

    if previous is not None and previous.regime.data_quality == DataQuality.SUFFICIENT:
        prev_vol = previous.regime.evidence.get("realized_vol")
        cur_vol = regime.evidence.get("realized_vol")
        if prev_vol is not None and cur_vol is not None:
            if cur_vol < prev_vol:
                confidence = CONFIDENCE_HIGH
                supporting.append(regime_evidence(previous.regime, "realized_vol"))
            elif cur_vol > prev_vol:
                # Real tension: RegimeBrain still says COMPRESSED this
                # cycle, but realized vol actually rose versus last cycle.
                contradicting.append(regime_evidence(previous.regime, "realized_vol"))

    return DetectionResult(
        confidence=confidence, state="range narrowing, realized volatility compressed",
        supporting_evidence=tuple(supporting), contradicting_evidence=tuple(contradicting),
    )


def _detect_volatility_expansion(
    current: MarketIntelligenceSnapshot, previous: Optional[MarketIntelligenceSnapshot],
) -> Optional[DetectionResult]:
    """Primary evidence: RegimeBrain's own VOLATILE classification (high
    realized vol, its own documented meaning) OR a TRANSITIONING reading
    whose own `compression_ratio` evidence shows intra-session
    expansion (RegimeBrain's own EXPANSION_RATIO_THRESHOLD branch)."""
    regime = current.regime
    if regime.data_quality != DataQuality.SUFFICIENT:
        return None

    is_volatile = regime.regime == RegimeType.VOLATILE
    compression_ratio = regime.evidence.get("compression_ratio")
    is_expanding_transition = (
        regime.regime == RegimeType.TRANSITIONING and compression_ratio is not None and compression_ratio >= 1.0
    )
    if not (is_volatile or is_expanding_transition):
        return None

    supporting = [regime_evidence(regime, "realized_vol"), regime_evidence(regime, "compression_ratio")]
    contradicting = []
    confidence = CONFIDENCE_HIGH if is_volatile else CONFIDENCE_MODERATE

    if previous is not None and previous.regime.data_quality == DataQuality.SUFFICIENT:
        prev_vol = previous.regime.evidence.get("realized_vol")
        cur_vol = regime.evidence.get("realized_vol")
        if prev_vol is not None and cur_vol is not None and cur_vol < prev_vol:
            # Real tension: an expansion signal this cycle, but realized
            # vol actually fell versus last cycle.
            contradicting.append(regime_evidence(previous.regime, "realized_vol"))

    return DetectionResult(
        confidence=confidence, state="volatility expanding" + (" after compression" if is_expanding_transition else ""),
        supporting_evidence=tuple(supporting), contradicting_evidence=tuple(contradicting),
    )


def _detect_liquidity_stress(
    current: MarketIntelligenceSnapshot, previous: Optional[MarketIntelligenceSnapshot],
) -> Optional[DetectionResult]:
    """Primary evidence: LiquidityBrain's own WIDE classification --
    its own docstring already defines this as widened top-of-book
    bid/ask spread, the exact real-world meaning this phenomenon
    names."""
    liquidity = current.liquidity
    if liquidity.data_quality != DataQuality.SUFFICIENT or liquidity.tightness != SpreadTightness.WIDE:
        return None

    supporting = [
        liquidity_evidence(liquidity, "combined_spread"),
        liquidity_evidence(liquidity, "combined_mid"),
    ]
    contradicting = []
    confidence = CONFIDENCE_HIGH

    if current.structure.data_quality != DataQuality.SUFFICIENT:
        contradicting.append(PhenomenonEvidenceItem(
            metric="data_quality", value=current.structure.data_quality.value, source="StructureReading",
        ))
        confidence = CONFIDENCE_MODERATE

    return DetectionResult(
        confidence=confidence, state="liquidity conditions deteriorated -- spread wider than threshold",
        supporting_evidence=tuple(supporting), contradicting_evidence=tuple(contradicting),
    )


def _detect_event_risk(
    current: MarketIntelligenceSnapshot, previous: Optional[MarketIntelligenceSnapshot],
) -> Optional[DetectionResult]:
    """Directly reuses EventBrain's own output -- per this phase's own
    instruction, no new evidence is computed here."""
    event = current.event
    is_expiry_risk = event.expiry_proximity in (ExpiryProximity.EXPIRY_DAY, ExpiryProximity.EXPIRY_EVE)
    is_vix_risk = event.vix_regime == VixRegime.ELEVATED
    if not (is_expiry_risk or is_vix_risk):
        return None

    supporting = []
    if is_expiry_risk:
        supporting.append(event_evidence(event, "expiry_proximity"))
        supporting.append(event_evidence(event, "days_to_expiry"))
    if is_vix_risk:
        supporting.append(event_evidence(event, "vix_level"))
        supporting.append(event_evidence(event, "vix_regime"))

    confidence = CONFIDENCE_HIGH if (is_expiry_risk and is_vix_risk) else CONFIDENCE_MODERATE
    reasons = []
    if is_expiry_risk:
        reasons.append(event.expiry_proximity.value.lower())
    if is_vix_risk:
        reasons.append("elevated VIX")
    state = "event risk: " + " and ".join(reasons)

    return DetectionResult(
        confidence=confidence, state=state,
        supporting_evidence=tuple(supporting), contradicting_evidence=(),
    )


def _detect_regime_transition(
    current: MarketIntelligenceSnapshot, previous: Optional[MarketIntelligenceSnapshot],
) -> Optional[DetectionResult]:
    """Requires a real `previous` snapshot -- structurally cannot detect
    a transition from a single instant. `current.regime.regime !=
    previous.regime.regime` is the entire rule, per this phase's own
    spec."""
    if previous is None:
        return None
    if current.regime.data_quality != DataQuality.SUFFICIENT or previous.regime.data_quality != DataQuality.SUFFICIENT:
        return None
    if current.regime.regime == previous.regime.regime:
        return None

    supporting = [
        PhenomenonEvidenceItem(metric="regime", value=previous.regime.regime.value, source="RegimeReading(previous)"),
        PhenomenonEvidenceItem(metric="regime", value=current.regime.regime.value, source="RegimeReading(current)"),
    ]
    contradicting = []
    confidence = CONFIDENCE_HIGH
    if current.regime.confidence < 0.5:
        contradicting.append(regime_evidence(current.regime, "confidence"))
        confidence = CONFIDENCE_LOW

    return DetectionResult(
        confidence=confidence, state=f"{previous.regime.regime.value} -> {current.regime.regime.value}",
        supporting_evidence=tuple(supporting), contradicting_evidence=tuple(contradicting),
    )


_DetectorFn = Callable[[MarketIntelligenceSnapshot, Optional[MarketIntelligenceSnapshot]], Optional[DetectionResult]]

PHENOMENON_DETECTORS: Dict[str, _DetectorFn] = {
    PHENOMENON_VOLATILITY_COMPRESSION: _detect_volatility_compression,
    PHENOMENON_VOLATILITY_EXPANSION: _detect_volatility_expansion,
    PHENOMENON_LIQUIDITY_STRESS: _detect_liquidity_stress,
    PHENOMENON_EVENT_RISK: _detect_event_risk,
    PHENOMENON_REGIME_TRANSITION: _detect_regime_transition,
}

assert set(PHENOMENON_DETECTORS) == set(ALL_PHENOMENON_TYPES)
