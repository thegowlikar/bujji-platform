"""build_market_phenomena_assessment() -- Phase 19.7.

The one entry point. Runs every detector in `detectors.PHENOMENON_DETECTORS`
against an already-built `MarketIntelligenceSnapshot` (Phase 19.3, optionally
with an already-built `DecisionIntelligenceSnapshot` reference, Phase 19.6,
and an optional `previous` snapshot for transition/trend detection). Never
calls a brain's `.analyze(`, never queries a datastore -- every input is
handed in by the caller. No wall-clock call anywhere in this module.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Optional

from bujji.intelligence.market_intelligence_snapshot.models import MarketIntelligenceSnapshot

from .detectors import PHENOMENON_DETECTORS
from .models import ALL_PHENOMENON_TYPES, MarketPhenomenaAssessment, MarketPhenomenonAssessment


def _phenomenon_id_for(intelligence_snapshot_id: str, phenomenon_type: str) -> str:
    """Deterministic, collision-resistant, replay-safe -- same MD5-based
    convention `market_understanding.memory_models.market_memory_id_for()`
    and `outcome_memory.models.memory_id_for()` already established."""
    return "PHEN-" + hashlib.md5(f"{intelligence_snapshot_id}|{phenomenon_type}".encode()).hexdigest()[:24]


def build_market_phenomena_assessment(
    *,
    snapshot: MarketIntelligenceSnapshot,
    decision_intelligence_id: Optional[str] = None,
    previous_snapshot: Optional[MarketIntelligenceSnapshot] = None,
    created_at: datetime,
) -> MarketPhenomenaAssessment:
    detected = []
    not_detected = []

    for phenomenon_type in ALL_PHENOMENON_TYPES:
        detector = PHENOMENON_DETECTORS[phenomenon_type]
        result = detector(snapshot, previous_snapshot)
        if result is None:
            not_detected.append(phenomenon_type)
            continue
        detected.append(MarketPhenomenonAssessment(
            phenomenon_id=_phenomenon_id_for(snapshot.intelligence_snapshot_id, phenomenon_type),
            phenomenon_type=phenomenon_type, confidence=result.confidence, state=result.state,
            supporting_evidence=result.supporting_evidence, contradicting_evidence=result.contradicting_evidence,
            intelligence_snapshot_id=snapshot.intelligence_snapshot_id,
            decision_intelligence_id=decision_intelligence_id,
        ))

    not_detected_reason = (
        "no real evidence in this snapshot met the detector's rule for these phenomena -- "
        "not fabricated, honestly absent"
        if not_detected else ""
    )

    from dataclasses import replace
    from bujji.replay_engine.engine import fingerprint_state

    provisional = MarketPhenomenaAssessment(
        assessment_id="", created_at=created_at,
        intelligence_snapshot_id=snapshot.intelligence_snapshot_id,
        decision_intelligence_id=decision_intelligence_id,
        phenomena=tuple(detected), not_detected=tuple(not_detected),
        not_detected_reason=not_detected_reason,
    )
    assessment_id = fingerprint_state(provisional.fingerprint_payload())
    return replace(provisional, assessment_id=assessment_id)
