"""MIL Next data contracts -- frozen dataclasses only.

`MarketDataPoint` is this laboratory's OWN, self-contained input
representation (event_time/price/volume) -- deliberately NOT the real
`bujji.market_episode`/`bujji.market_observation` model classes. This
keeps the offline laboratory fully self-contained and testable without
depending on (or risking accidental live coupling with) the real
ingestion chain. Achieving parity with the real Episode/Observation
shapes is explicit, out-of-scope future work for Gate 2 promotion.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from . import taxonomy as tx


@dataclass(frozen=True)
class MarketDataPoint:
    event_time: datetime
    price: float
    volume: Optional[float] = None
    symbol: str = "NIFTY"
    sequence_no: Optional[int] = None   # provider sequence number, when available


@dataclass(frozen=True)
class SourceHealth:
    """Per-source connection/heartbeat state, when the feed exposes one."""
    connected: bool
    reconnected_since_last_observation: bool = False


@dataclass(frozen=True)
class DataQualityContext:
    freshness: str
    completeness: str
    completeness_detail: Optional[Dict[str, Any]]
    outlier_flag: bool
    feed_disagreement_state: str
    feed_disagreement_bps: Optional[float]          # raw, audit-only, EXCLUDED from content_hash
    event_time_synthetic: bool
    clock_skew_detected: bool
    skew_ms: Optional[float]                          # raw, audit-only, EXCLUDED from content_hash
    arrival_age_ms: Optional[float]                     # raw, audit-only, EXCLUDED from content_hash
    transport_latency_ms: Optional[float]                # raw, audit-only, EXCLUDED from content_hash
    transport_latency_status: str
    mandatory_data_failure_reason: Optional[str]
    as_of: datetime                                        # raw, audit-only, EXCLUDED from content_hash


@dataclass(frozen=True)
class RegimeAssessment:
    timeframe: str
    direction: str
    regime: str
    window_start_event_time: datetime
    window_end_event_time: datetime
    lag_ms: float
    sample_count: int
    as_of_event_time: datetime


@dataclass(frozen=True)
class ComposedRegimeView:
    structural: Dict[str, str]        # {timeframe: direction}, referenced from RegimeAssessment.direction
    volatility_instability: str        # VSB-owned regime constant (taxonomy REGIME_* excl. structural ones)


@dataclass(frozen=True)
class TradeThesis:
    thesis_id: str
    direction: str
    supporting_evidence: Tuple[str, ...]
    conviction_rank: int               # 0 (none) .. 3 (highest)
    timeframe: str


@dataclass(frozen=True)
class CompetingThesis:
    thesis: TradeThesis
    timeframe: str
    supporting_evidence: Tuple[str, ...]
    invalidation_condition: str


@dataclass(frozen=True)
class ContradictionScore:
    supporting_domain_count: int
    contradicting_domain_count: int
    evidence_freshness_penalty: float
    regime_consistency_penalty: float
    overall: str


@dataclass(frozen=True)
class OIContext:
    bias: str
    as_of: Optional[datetime]
    effective_weight: str
    downgrade_reason: Optional[str]


@dataclass(frozen=True)
class TimeframeConfig:
    config_version: str
    buckets: Tuple[str, ...] = tx.ALL_TIMEFRAMES
    bucket_definitions: Dict[str, Dict[str, Any]] = field(default_factory=lambda: {
        tx.TIMEFRAME_1M: {"window_seconds": 60},
        tx.TIMEFRAME_5M: {"window_seconds": 300},
        tx.TIMEFRAME_15M: {"window_seconds": 900},
        tx.TIMEFRAME_SESSION: {"window_seconds": None},
    })
    max_lag_ms: Dict[str, float] = field(default_factory=lambda: {
        tx.TIMEFRAME_1M: 90_000.0,
        tx.TIMEFRAME_5M: 360_000.0,
        tx.TIMEFRAME_15M: 1_080_000.0,
        tx.TIMEFRAME_SESSION: 3_600_000.0,
    })


@dataclass(frozen=True)
class EventCalendarEntry:
    label: str
    window_start: datetime
    window_end: datetime


@dataclass(frozen=True)
class EventCalendarView:
    calendar_version: str
    effective_from: Any    # date
    effective_to: Optional[Any]
    entries: Tuple[EventCalendarEntry, ...]
    coverage_state: str    # KNOWN_NO_EVENT will never appear here -- this is raw coverage metadata;
                             # KNOWN_NO_EVENT/KNOWN_EVENT/COVERAGE_UNKNOWN is derived by event_calendar.resolve()


@dataclass(frozen=True)
class MarketDataInputs:
    """All read-only inputs snapshot_builder.build_snapshot() needs.
    Callers assemble this from whatever real data source they have --
    this laboratory never fetches/ingests data itself."""
    session_id: str
    points_by_symbol: Dict[str, Tuple[MarketDataPoint, ...]]
    source_health: Dict[str, SourceHealth]
    max_silence_ms: Dict[str, float]


@dataclass(frozen=True)
class MarketIntelligenceSnapshot:
    snapshot_id: str
    cadence_id: str
    session_id: str
    schema_version: str
    decision_cutoff_event_time: datetime
    idempotency_key: str
    content_hash: str
    active_config_versions: Dict[str, str]
    timeframe_config_version: str

    data_quality: DataQualityContext
    event_time_provenance: Dict[str, Any]
    receipt_time_provenance: Dict[str, Any]

    timeframe_states: Any          # Dict[str, RegimeAssessment] | Literal[NOT_EVALUATED...]
    timeframe_agreement: str
    timeframe_agreement_excluded: Tuple[str, ...]
    composed_regime: Any            # ComposedRegimeView | NOT_EVALUATED...

    primary_thesis: Any              # TradeThesis | NOT_EVALUATED...
    competing_thesis: Any             # Optional[CompetingThesis] | NOT_EVALUATED...
    contradiction_score: Any           # ContradictionScore | NOT_EVALUATED...
    oi_context: Any                     # OIContext | NOT_EVALUATED...

    volatility_posture: Any              # RegimeAssessment | NOT_EVALUATED...
    liquidity_posture: Any                 # str | NOT_EVALUATED...

    structural_conflict: Any                # bool | NOT_EVALUATED...
    known_event_risk_state: str
    unscheduled_shock_detected: Any          # bool | NOT_EVALUATED...

    invalidation_conditions: Tuple[str, ...]

    posture: str
    posture_reasons: Tuple[str, ...]

    version: str = tx.MIL_NEXT_VERSION


@dataclass(frozen=True)
class MILSnapshotRevision:
    original_idempotency_key: str
    revision_id: str
    revision_reason: str
    revised_at: datetime
    superseding_content_hash: str
