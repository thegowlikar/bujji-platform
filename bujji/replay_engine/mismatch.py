"""Formal Replay Engine -- mismatch cross-check, Phase 15H Step 7.

Compares a RECONSTRUCTED field against its own real, already-persisted
counterpart on the intelligence_cycle record for the SAME cycle
(cross-check, never a second opinion invented from nothing). Every
result is classified, never silently discarded.
"""
from __future__ import annotations

from typing import Any, Optional

from .models import (
    CLASS_MATCH, CLASS_MISMATCH, CLASS_NOT_APPLICABLE, CLASS_UNAVAILABLE,
    REASON_REPLAY_DEFECT, REASON_SOURCE_DATA_DEFICIENCY, MismatchRecord,
)


def _normalize(x):
    if isinstance(x, dict):
        return {k: _normalize(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_normalize(v) for v in x]
    return x


def cross_check_field(field: str, reconstructed_value: Any, referenced_value: Optional[dict]) -> MismatchRecord:
    """`referenced_value`: the SAME field read directly from the real
    persisted intelligence_cycle record for this cycle (the ground
    truth a live session actually produced). `None` for either side
    means genuinely unavailable -- never treated as a mismatch."""
    if reconstructed_value is None and referenced_value is None:
        return MismatchRecord(field, CLASS_UNAVAILABLE, None, "both reconstructed and referenced values are absent")
    if reconstructed_value is None:
        return MismatchRecord(field, CLASS_UNAVAILABLE, None, "reconstructed value unavailable (source data deficiency)")
    if referenced_value is None:
        return MismatchRecord(field, CLASS_NOT_APPLICABLE, None, "no persisted reference value exists for this cycle")

    if _normalize(reconstructed_value) == _normalize(referenced_value):
        return MismatchRecord(field, CLASS_MATCH, None, "reconstructed value matches the real persisted reference exactly")

    return MismatchRecord(field, CLASS_MISMATCH, REASON_REPLAY_DEFECT,
                           f"reconstructed value diverges from the real persisted reference for {field!r}")
