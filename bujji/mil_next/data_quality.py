"""Data-quality classification -- BUJJI MIL Next.

Completeness hierarchy (evaluated top-down, first applicable signal
wins, never blended):
  1. exchange/provider sequence-gap detection, when available
  2. provider heartbeat/connection state
  3. instrument-specific maximum-silence window
  4. otherwise UNKNOWN -- the honest default for a quiet-but-healthy
     market; never itself a posture-degrading signal.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

from . import taxonomy as tx
from .models import DataQualityContext, MarketDataPoint, SourceHealth

DEFAULT_SKEW_TOLERANCE_MS = 50.0
DEFAULT_ARRIVAL_AGE_STALE_MS = 30_000.0
DEFAULT_TRANSPORT_LATENCY_STALE_MS = 15_000.0
DEFAULT_OUTLIER_STD_MULTIPLE = 4.0
DEFAULT_FEED_DISAGREEMENT_TOLERANCE_BPS = 25.0


def classify_completeness(
    points: Sequence[MarketDataPoint],
    source_health: Optional[SourceHealth],
    max_silence_ms: float,
    now: datetime,
) -> tuple:
    if len(points) >= 2:
        seq = [p.sequence_no for p in points if p.sequence_no is not None]
        if len(seq) >= 2:
            for prev, cur in zip(seq, seq[1:]):
                if cur - prev > 1:
                    return tx.COMPLETENESS_GAP_DETECTED, {"gap_size": cur - prev - 1}

    if source_health is not None:
        if source_health.connected and not source_health.reconnected_since_last_observation:
            # heartbeat confirms liveness, but only decisive when tier 1 gave no signal at all
            if points:
                last_event_time = max(p.event_time for p in points)
                silent_for_ms = (now - last_event_time).total_seconds() * 1000.0
                if silent_for_ms > max_silence_ms:
                    return tx.COMPLETENESS_MAX_SILENCE_EXCEEDED, {"silent_for_ms": silent_for_ms}
            return tx.COMPLETENESS_CONNECTED_NO_GAP_SIGNAL, None

    if points:
        last_event_time = max(p.event_time for p in points)
        silent_for_ms = (now - last_event_time).total_seconds() * 1000.0
        if silent_for_ms > max_silence_ms:
            return tx.COMPLETENESS_MAX_SILENCE_EXCEEDED, {"silent_for_ms": silent_for_ms}

    return tx.COMPLETENESS_UNKNOWN, None


def classify_event_time_trust(
    event_time: datetime,
    receipt_time: datetime,
    event_time_synthetic: bool,
    skew_tolerance_ms: float = DEFAULT_SKEW_TOLERANCE_MS,
) -> dict:
    if event_time_synthetic:
        return {
            "event_time_synthetic": True,
            "clock_skew_detected": False,
            "skew_ms": None,
            "transport_latency_ms": None,
            "transport_latency_status": tx.TRANSPORT_LATENCY_NOT_MEASURABLE_SYNTHETIC_EVENT_TIME,
        }
    skew_ms = (receipt_time - event_time).total_seconds() * 1000.0
    clock_skew = skew_ms < -skew_tolerance_ms
    return {
        "event_time_synthetic": False,
        "clock_skew_detected": clock_skew,
        "skew_ms": skew_ms,
        "transport_latency_ms": skew_ms,
        "transport_latency_status": (
            tx.TRANSPORT_LATENCY_MEASURED_BUT_UNTRUSTED_CLOCK_SKEW if clock_skew
            else tx.TRANSPORT_LATENCY_MEASURED
        ),
    }


def classify_freshness(
    arrival_age_ms: Optional[float],
    transport_latency_ms: Optional[float],
    transport_latency_status: str,
    arrival_age_stale_ms: float = DEFAULT_ARRIVAL_AGE_STALE_MS,
    transport_latency_stale_ms: float = DEFAULT_TRANSPORT_LATENCY_STALE_MS,
) -> str:
    if arrival_age_ms is None:
        return tx.FRESHNESS_UNKNOWN
    if arrival_age_ms > arrival_age_stale_ms:
        return tx.FRESHNESS_STALE
    if transport_latency_status == tx.TRANSPORT_LATENCY_MEASURED and transport_latency_ms is not None:
        if transport_latency_ms > transport_latency_stale_ms:
            return tx.FRESHNESS_STALE
    return tx.FRESHNESS_FRESH


def detect_outlier(
    points: Sequence[MarketDataPoint],
    std_multiple: float = DEFAULT_OUTLIER_STD_MULTIPLE,
) -> bool:
    if len(points) < 5:
        return False
    prices = [p.price for p in points]
    latest = prices[-1]
    history = prices[:-1]
    mean = sum(history) / len(history)
    variance = sum((p - mean) ** 2 for p in history) / len(history)
    std = variance ** 0.5
    if std == 0:
        return False
    return abs(latest - mean) > std_multiple * std


def classify_feed_disagreement(
    disagreement_bps: Optional[float],
    tolerance_bps: float = DEFAULT_FEED_DISAGREEMENT_TOLERANCE_BPS,
) -> str:
    if disagreement_bps is None:
        return tx.FEED_DISAGREEMENT_NONE
    if abs(disagreement_bps) > tolerance_bps:
        return tx.FEED_DISAGREEMENT_BREACHED
    return tx.FEED_DISAGREEMENT_WITHIN_TOLERANCE


def build_data_quality_context(
    points: Sequence[MarketDataPoint],
    source_health: Optional[SourceHealth],
    max_silence_ms: float,
    receipt_time: datetime,
    event_time_synthetic: bool = False,
    disagreement_bps: Optional[float] = None,
    mandatory_data_failure_reason: Optional[str] = None,
) -> DataQualityContext:
    now = receipt_time
    completeness, completeness_detail = classify_completeness(points, source_health, max_silence_ms, now)
    outlier_flag = detect_outlier(points)
    feed_disagreement_state = classify_feed_disagreement(disagreement_bps)

    if points:
        last_event_time = max(p.event_time for p in points)
        arrival_age_ms = (now - last_event_time).total_seconds() * 1000.0
        trust = classify_event_time_trust(last_event_time, receipt_time, event_time_synthetic)
    else:
        arrival_age_ms = None
        trust = {
            "event_time_synthetic": event_time_synthetic,
            "clock_skew_detected": False,
            "skew_ms": None,
            "transport_latency_ms": None,
            "transport_latency_status": tx.TRANSPORT_LATENCY_NOT_MEASURABLE_SYNTHETIC_EVENT_TIME,
        }

    freshness = classify_freshness(arrival_age_ms, trust["transport_latency_ms"], trust["transport_latency_status"])

    return DataQualityContext(
        freshness=freshness,
        completeness=completeness,
        completeness_detail=completeness_detail,
        outlier_flag=outlier_flag,
        feed_disagreement_state=feed_disagreement_state,
        feed_disagreement_bps=disagreement_bps,
        event_time_synthetic=trust["event_time_synthetic"],
        clock_skew_detected=trust["clock_skew_detected"],
        skew_ms=trust["skew_ms"],
        arrival_age_ms=arrival_age_ms,
        transport_latency_ms=trust["transport_latency_ms"],
        transport_latency_status=trust["transport_latency_status"],
        mandatory_data_failure_reason=mandatory_data_failure_reason,
        as_of=receipt_time,
    )


def is_mandatory_failure(dq: DataQualityContext) -> Optional[str]:
    if dq.freshness == tx.FRESHNESS_STALE:
        return "MANDATORY_SOURCE_STALE"
    if dq.completeness == tx.COMPLETENESS_MAX_SILENCE_EXCEEDED:
        return "COMPLETENESS_MAX_SILENCE_EXCEEDED"
    if dq.completeness == tx.COMPLETENESS_GAP_DETECTED:
        return "COMPLETENESS_GAP_DETECTED"
    if dq.feed_disagreement_state == tx.FEED_DISAGREEMENT_BREACHED:
        return "FEED_DISAGREEMENT_BREACHED"
    return None
