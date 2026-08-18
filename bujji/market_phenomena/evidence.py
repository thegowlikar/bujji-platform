"""Evidence-item helpers -- Phase 19.7.

Every value here is copied verbatim from an already-real Reading field
or its `evidence_lineage` (Phase 19.2.2) -- never recomputed, never
fabricated. `detectors.py` uses these to build the
`supporting_evidence`/`contradicting_evidence` tuples every detected
phenomenon must carry.
"""
from __future__ import annotations

from typing import Any

from .models import PhenomenonEvidenceItem


def evidence(source: str, metric: str, value: Any) -> PhenomenonEvidenceItem:
    return PhenomenonEvidenceItem(metric=metric, value=value, source=source)


def regime_evidence(regime_reading, metric: str) -> PhenomenonEvidenceItem:
    value = regime_reading.evidence.get(metric) if metric in regime_reading.evidence else getattr(regime_reading, metric, None)
    return evidence("RegimeReading", metric, value)


def volatility_evidence(volatility_reading, metric: str) -> PhenomenonEvidenceItem:
    value = volatility_reading.evidence.get(metric) if metric in volatility_reading.evidence else getattr(volatility_reading, metric, None)
    return evidence("VolatilityReading", metric, value)


def liquidity_evidence(liquidity_reading, metric: str) -> PhenomenonEvidenceItem:
    value = liquidity_reading.evidence.get(metric) if metric in liquidity_reading.evidence else getattr(liquidity_reading, metric, None)
    return evidence("LiquidityReading", metric, value)


def event_evidence(event_reading, metric: str) -> PhenomenonEvidenceItem:
    value = event_reading.evidence.get(metric) if metric in event_reading.evidence else getattr(event_reading, metric, None)
    return evidence("EventReading", metric, value)
