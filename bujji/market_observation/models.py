"""Market Observation Contract models — frozen, immutable records.

Implements MOF v1's (`docs/MOF_V1_FOUNDATION.md`) Observation-layer
ontology (Deliverable 3): `ObservationSnapshot` is realized here as
`Observation`; `ObservationSeries`, `ObservationSource`,
`ObservationQuality`, `ObservationFreshness`, `ObservationConfidence`,
`ObservationCompleteness`, `ObservationWindow`, `ObservationResolution`,
and `ObservationVersion` are realized as fields/dataclasses below.

Every dataclass here is `frozen=True` and carries no logic -- construction
lives in `engine.py`, never here. Nothing in this file authenticates,
parses a raw feed, connects to a broker, or interprets a value into a
Derived Evidence / Intelligence conclusion (per MOF Deliverable 1's
four-layer discipline: Observation -> Derived Evidence -> Intelligence
-> Decision -- this module implements Observation only).

Design decision — does quality metadata affect `observation_id`? NO.
`observation_id` is derived only from `ObservationIdentity`'s own
fields plus `ObservationValue` (the "fact": what was observed, by
whom, at what time, and what value it carried) -- never from
`ObservationQualityMetadata`. Two Observations that agree on identity
and value but differ only in how their quality was later assessed
(e.g. a completeness re-check, a freshness re-read) are the *same*
observation for identity/equality purposes; quality is a read *about*
the fact, not part of the fact itself. This mirrors MOF Deliverable 3's
own distinction between `ObservationQuality` ("MOF's own disclosed,
evidenced assessment") and the Observation it is assessing -- and keeps
`observation_id` stable across a quality re-assessment, which downstream
consumers (Derived Evidence, per Deliverable 4) depend on for dedup and
provenance tracing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple


# ---------------------------------------------------------------------------
# ObservationIdentity — "what was observed, by whom, at what time."
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ObservationIdentity:
    observation_id: str
    observation_type: str
    instrument: str
    exchange: str
    segment: str
    timestamp: str
    resolution: str
    source: str
    schema_version: str


# ---------------------------------------------------------------------------
# ObservationQualityMetadata — metadata-only. Never read by Identity/Value
# construction or equality logic; consumed only by query/reporting code
# that explicitly asks for it. `confidence` is Optional[float]: None
# means the source published no confidence signal (per MOF Deliverable 3's
# ObservationConfidence) -- it must never be fabricated as 0.0 or 1.0.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ObservationQualityMetadata:
    completeness: float                      # 0.0-1.0: fraction of the expected set captured.
    freshness: float                          # seconds elapsed between capture and consumption; must be >= 0.
    confidence: Optional[float]               # Source-published confidence, if any. None if source publishes none.
    missing_fields: Tuple[str, ...]
    validation_status: str
    source_quality: str


# ---------------------------------------------------------------------------
# ObservationProvenance — where this Observation came from and how it
# got here, mirroring MIC v2's existing `provenance` field shape
# (per MOF Deliverable 6/7) rather than inventing a new one.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ObservationProvenance:
    originating_source: str
    acquisition_timestamp: str
    normalization_timestamp: str
    origin: str
    version: str
    transformation_history: Tuple[str, ...]   # Empty tuple, never None, when no transformation occurred.


# ---------------------------------------------------------------------------
# ObservationValue — domain-neutral. A closed set of variants
# (taxonomy.ALL_VALUE_KINDS), discriminated by `value_kind`, with a
# generic `payload`. MOC v1 deliberately does NOT define
# PriceValue/OIValue/VIXValue per-domain subclasses: every one of the
# 17 ObservationTypes rides inside this one shape, so Identity/Series/
# Engine code never branches on domain.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ObservationValue:
    value_kind: str                            # One of taxonomy.ALL_VALUE_KINDS.
    payload: Any                                # float | Mapping[str, float] | str, per value_kind.


# ---------------------------------------------------------------------------
# Observation — the canonical unit. Composes Identity + QualityMetadata
# + Provenance + Value into one frozen record. This is MOF's
# `ObservationSnapshot`.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Observation:
    identity: ObservationIdentity
    quality: ObservationQualityMetadata
    provenance: ObservationProvenance
    value: ObservationValue


# ---------------------------------------------------------------------------
# SeriesGap — an explicit marker of a missing interval inside an
# ObservationSeries. Per MOF Deliverable 10 / this project's "no silent
# data gaps" discipline: a gap is always a first-class recorded object,
# never a silent absence in the underlying tuple.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SeriesGap:
    after_timestamp: str          # Timestamp of the last Observation before the gap.
    before_timestamp: str         # Timestamp of the first Observation after the gap (same as after_timestamp if gap is at series end/unknown).
    reason: str                   # One of taxonomy.ALL_GAP_REASONS.


# ---------------------------------------------------------------------------
# ObservationSeries — ordered, immutable, tuple-backed sequence of
# Observations sharing observation_type + instrument, at a defined
# resolution, spanning an explicit window. Storage only -- no
# analytics, no derived metrics (that is MSI's job, per MOF Deliverable
# 1's boundary: MOF stops at ObservationSnapshot/ObservationSeries).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ObservationSeries:
    observation_type: str
    instrument: str
    resolution: str
    window_start: str
    window_end: str
    observations: Tuple[Observation, ...]
    gaps: Tuple[SeriesGap, ...]


# ---------------------------------------------------------------------------
# ValidationResult — the outcome of engine.validate_observation() /
# engine.validate_series(). Never fabricates a PASS; `reasons` is always
# complete (every failing structural check contributes a reason, never
# short-circuited on the first failure), matching this project's
# established "explain fully, never partially" discipline.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ValidationResult:
    is_valid: bool
    status: str                     # One of taxonomy.ALL_VALIDATION_STATUSES.
    reasons: Tuple[str, ...]        # Empty tuple when is_valid is True.
