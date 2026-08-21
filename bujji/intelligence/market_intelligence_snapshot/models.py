"""MarketIntelligenceSnapshot -- Phase 19.3.

The composition layer, not a new intelligence layer. Assembles an already
validated `MarketRealitySnapshot` (via its `.fingerprint()`), an already
validated `IntelligenceContext`, and the six already validated in-scope
brain Readings (Phase 19.2.2's clock injection + evidence lineage) into
one deterministic object -- "what Bujji believed about the market at
one point in time, and exactly why."

Per this phase's own explicit scope: no new indicators, no trading rules,
no buy/sell signals, no brain modification, no execution logic, no direct
`mil_next` code reuse (concepts only -- `MarketThesis`/`ContradictionScore`/
posture vocabulary are modeled on `mil_next`'s own already-designed shapes,
per Phase 19.1.2's evaluation, but every field here is computed fresh from
this package's own real inputs).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional, Tuple

from ..evidence import IntelligenceEvidence
from ..models import (
    EventReading,
    GreeksReading,
    LiquidityReading,
    RegimeReading,
    StructureReading,
    VolatilityReading,
)


class MarketPosture(str, Enum):
    """What KIND of environment this is -- never an action, never a
    direction. A direct, documented, one-to-one mapping from RegimeBrain's
    own RegimeType + EventBrain's own expiry/VIX classification (see
    `posture.py`'s `derive_posture()` for the exact mapping table) --
    not a new trading rule, an aggregation/renaming layer over labels
    that already exist."""

    TRENDING = "TRENDING"
    RANGING = "RANGING"
    EXPANSION = "EXPANSION"
    COMPRESSION = "COMPRESSION"
    UNCERTAIN = "UNCERTAIN"
    EVENT_RISK = "EVENT_RISK"


@dataclass(frozen=True)
class ContradictionScore:
    """Markets are not "bullish = 90%" -- they are "trending, but a
    volatility warning, and liquidity deteriorating." This score reports
    how many of the six in-scope domains actually have usable evidence
    this cycle, not a synthesized directional confidence.

    SCOPED HONESTLY: `mil_next`'s own `ContradictionScore` concept (Phase
    19.1.2's finding) also carries `evidence_freshness_penalty` and
    `regime_consistency_penalty` sub-scores. Those are deliberately NOT
    included here -- this package has no real basis to compute either
    honestly yet (no multi-cycle history is threaded through a single
    snapshot build, and "regime consistency" would require comparing
    against a PRIOR snapshot, which does not exist in this phase's scope).
    Only `overall`, computed from a single real, documented formula, is
    included. Adding the other two sub-scores is a real, separate future
    step once multi-snapshot history exists -- not silently faked here.
    """

    supporting_domain_count: int   # in-scope brains with data_quality == SUFFICIENT
    contradicting_domain_count: int  # in-scope brains with data_quality == INSUFFICIENT
    overall: float  # contradicting / (supporting + contradicting); 0.0 if no domains at all

    def to_dict(self) -> dict:
        return {
            "supporting_domain_count": self.supporting_domain_count,
            "contradicting_domain_count": self.contradicting_domain_count,
            "overall": self.overall,
        }


@dataclass(frozen=True)
class MarketThesis:
    """An INTERPRETATION, not a fact -- explicitly distinguished from the
    Reading objects it is built from, which are fact-adjacent (real
    computed statistics). Every field here is mechanically composed from
    the six brains' own already-existing, already-validated `reason`
    strings and real numeric fields (resistance_strike, support_strike,
    days_to_expiry) -- never a new classification rule, never an invented
    directional (bullish/bearish) claim, since none of the six in-scope
    brains produce a directional signal to honestly report one from."""

    primary_thesis: str
    supporting_factors: Tuple[str, ...]
    contradictions: Tuple[str, ...]
    invalidation_conditions: Tuple[str, ...]
    derived_from: Tuple[str, ...]  # which brains' reason/evidence this thesis was built from

    def to_dict(self) -> dict:
        return {
            "primary_thesis": self.primary_thesis,
            "supporting_factors": list(self.supporting_factors),
            "contradictions": list(self.contradictions),
            "invalidation_conditions": list(self.invalidation_conditions),
            "derived_from": list(self.derived_from),
        }


@dataclass(frozen=True)
class IntelligenceEvidenceBundle:
    """The first real consumer of Phase 19.2.2's `evidence_lineage` field
    (Phase 19.2.3's audit finding #2 -- lineage existed but nothing read
    it yet). One flat, deduplicated view across all six brains' evidence,
    each item's `metric_name` prefixed with its owning brain so two
    brains' same-named metrics never collide."""

    items: Tuple[IntelligenceEvidence, ...]
    source_references: Tuple[str, ...]     # deduplicated, non-None only
    observation_ids: Tuple[str, ...]       # deduplicated, non-empty only
    confidence: float                       # min() across the six brains' own confidence -- a chain is as strong as its weakest link, never averaged into false reassurance

    def to_dict(self) -> dict:
        return {
            "items": [i.to_dict() for i in self.items],
            "source_references": list(self.source_references),
            "observation_ids": list(self.observation_ids),
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class MarketIntelligenceSnapshot:
    """"What Bujji believed about the market at one point in time, and
    exactly why." Direct composition over already-validated pieces --
    never reinterprets a brain's own Reading, never recomputes a metric.

    IDENTITY: `intelligence_snapshot_id` deliberately avoids the bare
    "snapshot_id" name (Phase 19.1.1's confirmed 3-way collision risk
    with `mic_adapter`/`mil_next`/`trading_brain.ontology`). Unlike
    `MarketRealitySnapshot.fingerprint()` (a computed method, never
    stored -- Phase 18.3's design), this field IS stored, set once at
    build time by `build_market_intelligence_snapshot()` -- the same
    precedent as `DatasetArtifact.artifact_id` (Phase 18.12), since this
    object, once built, is treated as a finished, immutable artifact
    (even though Phase 19.3 does not yet persist it -- see the phase doc's
    "Persistence" section). `.fingerprint()` below independently recomputes
    the same value for verification, so a hand-constructed object with
    mismatched content can never silently disagree with its own id.
    """

    # --- identity ---
    intelligence_snapshot_id: str
    created_at: datetime               # audit metadata -- EXCLUDED from the fingerprint payload
    as_of_time: datetime
    execution_mode: str
    reality_fingerprint: Optional[str]
    dataset_artifact_id: Optional[str]
    reconstruction_version: str

    # --- brain outputs (preserved verbatim, never reinterpreted) ---
    regime: RegimeReading
    structure: StructureReading
    liquidity: LiquidityReading
    volatility: VolatilityReading
    greeks: GreeksReading
    event: EventReading

    # --- evidence ---
    evidence_bundle: IntelligenceEvidenceBundle

    # --- interpretation ---
    thesis: MarketThesis
    contradiction: ContradictionScore
    posture: MarketPosture

    @property
    def invalidation_conditions(self) -> Tuple[str, ...]:
        """Convenience mirror of `thesis.invalidation_conditions` -- kept
        as a single source of truth on `MarketThesis` (per the phase
        spec's own worked MarketThesis example already including it)
        rather than a second, independently-settable field that could
        drift out of sync with the thesis it belongs to."""
        return self.thesis.invalidation_conditions

    def fingerprint_payload(self) -> Dict[str, Any]:
        """Everything that defines "the same market understanding" --
        excludes `created_at`, `intelligence_snapshot_id` itself, and any
        other pure audit metadata, per this phase's explicit requirement.
        Uses each Reading's own already-existing `to_dashboard()` (Phase
        19.2.2's own serialization, already excludes nothing this
        snapshot needs to include) rather than inventing a second
        serialization of the same data.

        `execution_mode` is DELIBERATELY EXCLUDED: it describes HOW this
        snapshot was produced (LIVE vs HISTORICAL_REPLAY vs PAPER), not
        WHAT Bujji understood about the market -- the same real inputs
        replayed later must produce the identical fingerprint regardless
        of which mode built them (the phase's own explicit replay-test
        requirement: "same fingerprint" across LIVE and
        HISTORICAL_REPLAY). It remains a real, stored field on the
        object itself, just not part of the content hash -- the same
        treatment as `created_at`."""
        return {
            "as_of_time": self.as_of_time.isoformat(),
            "reality_fingerprint": self.reality_fingerprint,
            "dataset_artifact_id": self.dataset_artifact_id,
            "reconstruction_version": self.reconstruction_version,
            "regime": self.regime.to_dashboard(),
            "structure": self.structure.to_dashboard(),
            "liquidity": self.liquidity.to_dashboard(),
            "volatility": self.volatility.to_dashboard(),
            "greeks": self.greeks.to_dashboard(),
            "event": self.event.to_dashboard(),
            "evidence_bundle": self.evidence_bundle.to_dict(),
            "thesis": self.thesis.to_dict(),
            "contradiction": self.contradiction.to_dict(),
            "posture": self.posture.value,
        }

    def fingerprint(self) -> str:
        """Recomputes the same SHA-256 content fingerprint
        `build_market_intelligence_snapshot()` used to set
        `intelligence_snapshot_id` -- reuses
        `replay_engine.engine.fingerprint_state()` verbatim, the same
        hashing mechanism `MarketRealitySnapshot.fingerprint()` already
        uses (Phase 18.3), never a new one."""
        from bujji.replay_engine.engine import fingerprint_state
        return fingerprint_state(self.fingerprint_payload())

    def to_dict(self) -> dict:
        d = self.fingerprint_payload()
        d["intelligence_snapshot_id"] = self.intelligence_snapshot_id
        d["created_at"] = self.created_at.isoformat()
        d["invalidation_conditions"] = list(self.invalidation_conditions)
        return d
