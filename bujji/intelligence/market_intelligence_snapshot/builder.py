"""build_market_intelligence_snapshot() -- Phase 19.3.

Pure composition: no broker call, no new indicator, no trading rule, no
signal. Every function here either (a) copies a value straight out of an
already-real brain Reading, or (b) applies a single, explicitly documented,
deterministic mapping from existing enums to existing enums. Nothing here
computes a NEW number from raw market data -- that is the six brains' job,
already done, before this module ever runs.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Tuple

from bujji.replay_engine.engine import fingerprint_state

from ..context import IntelligenceContext
from ..evidence import IntelligenceEvidence
from ..models import (
    DataQuality,
    EventReading,
    ExpiryProximity,
    GreeksReading,
    LiquidityReading,
    RegimeReading,
    RegimeType,
    StructureReading,
    VixRegime,
    VolatilityReading,
)
from .models import (
    ContradictionScore,
    IntelligenceEvidenceBundle,
    MarketIntelligenceSnapshot,
    MarketPosture,
    MarketThesis,
)

RECONSTRUCTION_VERSION = "19.3.0"  # This package's own composition-logic version, independent of Reality's 18.3.0.


def _build_evidence_bundle(
    regime: RegimeReading, structure: StructureReading, liquidity: LiquidityReading,
    volatility: VolatilityReading, greeks: GreeksReading, event: EventReading,
) -> IntelligenceEvidenceBundle:
    named_readings = (
        ("regime", regime), ("structure", structure), ("liquidity", liquidity),
        ("volatility", volatility), ("greeks", greeks), ("event", event),
    )
    items = []
    source_refs = set()
    observation_ids = set()
    confidences = []
    for brain_name, reading in named_readings:
        confidences.append(reading.confidence)
        for metric_name, evidence_item in reading.evidence_lineage.items():
            prefixed = IntelligenceEvidence(
                metric_name=f"{brain_name}.{metric_name}",
                value=evidence_item.value,
                source_reference=evidence_item.source_reference,
                observation_references=evidence_item.observation_references,
                observed_at=evidence_item.observed_at,
            )
            items.append(prefixed)
            if evidence_item.source_reference:
                source_refs.add(evidence_item.source_reference)
            observation_ids.update(evidence_item.observation_references)

    return IntelligenceEvidenceBundle(
        items=tuple(items),
        source_references=tuple(sorted(source_refs)),
        observation_ids=tuple(sorted(observation_ids)),
        confidence=min(confidences) if confidences else 0.0,
    )


def _build_contradiction_score(
    regime: RegimeReading, structure: StructureReading, liquidity: LiquidityReading,
    volatility: VolatilityReading, greeks: GreeksReading, event: EventReading,
) -> ContradictionScore:
    readings = (regime, structure, liquidity, volatility, greeks, event)
    supporting = sum(1 for r in readings if r.data_quality == DataQuality.SUFFICIENT)
    contradicting = sum(1 for r in readings if r.data_quality == DataQuality.INSUFFICIENT)
    total = supporting + contradicting
    overall = round(contradicting / total, 4) if total > 0 else 0.0
    return ContradictionScore(
        supporting_domain_count=supporting, contradicting_domain_count=contradicting, overall=overall,
    )


# Direct, documented, one-to-one mapping -- RegimeType/EventBrain's own
# existing classifications to MarketPosture. Event risk takes priority
# over regime shape (an expiry-day session is EVENT_RISK regardless of
# how its price action happens to be behaving).
_REGIME_TO_POSTURE = {
    RegimeType.TRENDING: MarketPosture.TRENDING,
    RegimeType.RANGING: MarketPosture.RANGING,
    RegimeType.VOLATILE: MarketPosture.EXPANSION,
    RegimeType.COMPRESSED: MarketPosture.COMPRESSION,
    RegimeType.TRANSITIONING: MarketPosture.UNCERTAIN,
    RegimeType.UNKNOWN: MarketPosture.UNCERTAIN,
}


def _derive_posture(regime: RegimeReading, event: EventReading) -> MarketPosture:
    if event.expiry_proximity in (ExpiryProximity.EXPIRY_DAY, ExpiryProximity.EXPIRY_EVE):
        return MarketPosture.EVENT_RISK
    if event.vix_regime == VixRegime.ELEVATED:
        return MarketPosture.EVENT_RISK
    return _REGIME_TO_POSTURE.get(regime.regime, MarketPosture.UNCERTAIN)


def _build_thesis(
    regime: RegimeReading, structure: StructureReading, liquidity: LiquidityReading,
    volatility: VolatilityReading, greeks: GreeksReading, event: EventReading,
) -> MarketThesis:
    named_readings = (
        ("regime", regime), ("structure", structure), ("liquidity", liquidity),
        ("volatility", volatility), ("greeks", greeks), ("event", event),
    )

    # primary_thesis: a plain, literal composition of the regime and
    # event classifications -- the only two in-scope brains that describe
    # the OVERALL session shape rather than one specific dimension of it.
    # Never a directional (bullish/bearish) claim -- no in-scope brain
    # produces one to honestly report.
    primary_thesis = f"{regime.regime.value} regime ({regime.reason})"
    if event.expiry_proximity != ExpiryProximity.UNKNOWN:
        primary_thesis += f"; {event.expiry_proximity.value.lower()} expiry"

    supporting_factors = tuple(
        f"{name}: {reading.reason}" for name, reading in named_readings
        if reading.data_quality == DataQuality.SUFFICIENT and reading.reason
    )
    contradictions = tuple(
        f"{name}: INSUFFICIENT ({reading.reason})" for name, reading in named_readings
        if reading.data_quality == DataQuality.INSUFFICIENT
    )

    # invalidation_conditions: real numeric levels already present on the
    # Reading objects, never invented thresholds.
    invalidation = []
    if structure.resistance_strike is not None:
        invalidation.append(f"spot closes above resistance {structure.resistance_strike}")
    if structure.support_strike is not None:
        invalidation.append(f"spot closes below support {structure.support_strike}")
    if event.days_to_expiry is not None and event.days_to_expiry <= 1:
        invalidation.append("expiry reached (pin risk / accelerating gamma)")

    derived_from = tuple(name for name, reading in named_readings if reading.data_quality == DataQuality.SUFFICIENT)

    return MarketThesis(
        primary_thesis=primary_thesis,
        supporting_factors=supporting_factors,
        contradictions=contradictions,
        invalidation_conditions=tuple(invalidation),
        derived_from=derived_from,
    )


def build_market_intelligence_snapshot(
    *,
    context: IntelligenceContext,
    created_at: datetime,
    regime: RegimeReading,
    structure: StructureReading,
    liquidity: LiquidityReading,
    volatility: VolatilityReading,
    greeks: GreeksReading,
    event: EventReading,
) -> MarketIntelligenceSnapshot:
    """The one entry point. Takes already-built brain Readings (never
    calls a brain itself -- that stays the caller's responsibility,
    exactly as `runner.py` already does it) and composes them, unchanged,
    into one deterministic snapshot.
    """
    evidence_bundle = _build_evidence_bundle(regime, structure, liquidity, volatility, greeks, event)
    contradiction = _build_contradiction_score(regime, structure, liquidity, volatility, greeks, event)
    posture = _derive_posture(regime, event)
    thesis = _build_thesis(regime, structure, liquidity, volatility, greeks, event)

    # Build with a placeholder id, hash the real content, then rebuild
    # frozen with that id -- fingerprint_payload() never includes
    # intelligence_snapshot_id itself, so this two-step construction
    # cannot self-reference or drift.
    provisional = MarketIntelligenceSnapshot(
        intelligence_snapshot_id="",
        created_at=created_at,
        as_of_time=context.as_of_time,
        execution_mode=context.execution_mode,
        reality_fingerprint=context.reality_snapshot_reference,
        dataset_artifact_id=context.dataset_artifact_reference,
        reconstruction_version=RECONSTRUCTION_VERSION,
        regime=regime, structure=structure, liquidity=liquidity,
        volatility=volatility, greeks=greeks, event=event,
        evidence_bundle=evidence_bundle, thesis=thesis,
        contradiction=contradiction, posture=posture,
    )
    intelligence_snapshot_id = fingerprint_state(provisional.fingerprint_payload())

    from dataclasses import replace
    return replace(provisional, intelligence_snapshot_id=intelligence_snapshot_id)
