"""Adaptive Position Management -- Phase 15I. Pure models, no IO, no
broker, no execution.

Forensic finding: `bujji.position_intelligence.models.RECOMMEND_ADJUST`
has existed since Phase 15/15F but `evaluate_thesis()` never actually
produces it (THESIS_WEAKENING always maps to RECOMMEND_HOLD) -- another
instance of the "built but not connected" pattern this project keeps
finding (Phase 15E's dead Greeks import, Phase 15F's unused
`ShadowTradeCandidate.legs[i].delta`). This phase does not touch
Position Intelligence's own recommendation field; instead it builds a
SEPARATE, higher-level `PositionManagementAssessment` that CONSUMES
Position Intelligence's `ThesisEvaluation` as its single most
authoritative input (see engine.py's evidence-hierarchy docstring) and
produces the fuller HOLD/ADJUST/HEDGE/ROLL/EXIT/UNKNOWN vocabulary the
mission actually needs -- reusing the SAME string constants
(`RECOMMEND_HOLD`, `RECOMMEND_EXIT`, `RECOMMEND_UNKNOWN`) where the
meaning is identical, never inventing a parallel HOLD/EXIT/UNKNOWN
vocabulary.

ADJUST/HEDGE/ROLL are recommendation TYPES ONLY (Step 1 Q6) -- no
structured action plan (target strike, hedge ratio, roll destination)
is produced this phase. Building one without a proven need (no
downstream consumer, no PaperBroker integration yet) would repeat
exactly the mistake this project has spent five phases correcting.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

SCHEMA_VERSION = "1.0.0"

# Reuse Position Intelligence's own vocabulary where the meaning is identical.
RECOMMEND_HOLD = "HOLD"
RECOMMEND_ADJUST = "ADJUST"
RECOMMEND_HEDGE = "HEDGE"
RECOMMEND_ROLL = "ROLL"
RECOMMEND_EXIT = "EXIT"
RECOMMEND_UNKNOWN = "UNKNOWN"
ALL_RECOMMENDATIONS = (RECOMMEND_HOLD, RECOMMEND_ADJUST, RECOMMEND_HEDGE, RECOMMEND_ROLL, RECOMMEND_EXIT, RECOMMEND_UNKNOWN)

# Machine-readable reason codes -- every recommendation cites at least one.
REASON_THESIS_INTACT = "THESIS_INTACT"
REASON_THESIS_WEAKENING_RECOVERABLE = "THESIS_WEAKENING_RECOVERABLE"
REASON_THESIS_INVALIDATED = "THESIS_INVALIDATED"
REASON_EXPOSURE_DRIFT_SEVERE = "EXPOSURE_DRIFT_SEVERE"
REASON_EXPIRY_GEOMETRY_UNFAVORABLE = "EXPIRY_GEOMETRY_UNFAVORABLE"
REASON_INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
REASON_CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
REASON_POSITION_NOT_OPEN = "POSITION_NOT_OPEN"


@dataclass(frozen=True)
class EvidenceItem:
    """One input the assessment weighed -- dimension, its resolved
    value, and whether it was actually available (UNKNOWN is a first-
    class, explicit value here, never an absent key)."""

    dimension: str            # "thesis" / "exposure" / "expiry_geometry" / "premium_behaviour" / "liquidity".
    status: str                # dimension-specific: e.g. THESIS_INTACT, "SEVERE_DRIFT", "NORMAL", "UNKNOWN".
    detail: str

    def to_dict(self) -> dict:
        return {"dimension": self.dimension, "status": self.status, "detail": self.detail}


@dataclass(frozen=True)
class PositionManagementAssessment:
    position_id: str
    evaluation_timestamp: str
    recommendation: str                # one of ALL_RECOMMENDATIONS.
    reason_codes: Tuple[str, ...]       # machine-readable, one or more of the REASON_* constants.
    recommendation_reason: str          # human-readable summary.
    evidence: Tuple[EvidenceItem, ...]
    thesis_status: Optional[str]        # copied from the consumed ThesisEvaluation, for direct traceability.
    evidence_confidence: Optional[str]  # copied from the consumed ThesisEvaluation.
    previous_recommendation: Optional[str]  # the position's own prior management assessment, if any -- continuity, never re-derived.
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return {
            "position_id": self.position_id, "evaluation_timestamp": self.evaluation_timestamp,
            "recommendation": self.recommendation, "reason_codes": list(self.reason_codes),
            "recommendation_reason": self.recommendation_reason,
            "evidence": [e.to_dict() for e in self.evidence],
            "thesis_status": self.thesis_status, "evidence_confidence": self.evidence_confidence,
            "previous_recommendation": self.previous_recommendation, "schema_version": self.schema_version,
        }
