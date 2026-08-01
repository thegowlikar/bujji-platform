"""Canonical content hash -- BUJJI MIL Next.

`compute_content_hash(snapshot)` hashes the DECISION-relevant content
of a MarketIntelligenceSnapshot only. Excluded, explicitly:

  - content_hash, snapshot_id, idempotency_key (identifiers, not content)
  - arrival_age_ms, transport_latency_ms, transport_latency_status,
    skew_ms, feed_disagreement_bps (raw), receipt_time_provenance,
    the receipt-time components of event_time_provenance, as_of
    (all wall-clock/replay-dependent: two runs over byte-identical
    market data at different real times must still hash identically,
    provided their DATA-QUALITY CONCLUSIONS agree)

Included: every data-quality CONCLUSION (freshness, completeness,
outlier_flag, feed_disagreement_state, event_time_synthetic,
clock_skew_detected, mandatory_data_failure_reason) plus every
decision-content field (timeframe states, regime, thesis,
contradiction, OI context, posture, config versions).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from typing import Any


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _to_jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    return value


_DATA_QUALITY_EXCLUDED_FIELDS = frozenset({
    "arrival_age_ms", "transport_latency_ms", "transport_latency_status",
    "skew_ms", "feed_disagreement_bps", "as_of",
})

_SNAPSHOT_EXCLUDED_FIELDS = frozenset({
    "content_hash", "snapshot_id", "idempotency_key",
    "event_time_provenance", "receipt_time_provenance",
})


def _canonical_data_quality(dq_dict: dict) -> dict:
    return {k: v for k, v in dq_dict.items() if k not in _DATA_QUALITY_EXCLUDED_FIELDS}


def compute_content_hash(snapshot) -> str:
    payload = _to_jsonable(snapshot)
    for field_name in _SNAPSHOT_EXCLUDED_FIELDS:
        payload.pop(field_name, None)
    if isinstance(payload.get("data_quality"), dict):
        payload["data_quality"] = _canonical_data_quality(payload["data_quality"])
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
