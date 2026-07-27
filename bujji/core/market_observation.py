"""Market Observation — Production Pipeline Stage 1.

Production Engineering Sprint 3 (Stage Interface Extraction). This is an
EXTRACTION, not a rewrite: the classification rule here — a candle at or
before the last processed timestamp is a duplicate/stale candle; a gap
more than 1.5x the configured candle interval is a gap — is identical to
what previously ran inline inside Orchestrator.on_candle(). No threshold
changed, no new candle state introduced.

Pure and side-effect-free: takes only the two timestamps and the
configured candle interval, and returns a CandleAdmissionDecision. It
never mutates RuntimeStatus, never logs, never touches the event bus --
all of that stays in Orchestrator.on_candle(), which performs the exact
same side effects as before, chosen by branching on this function's
result. This is what makes candle-admission classification independently
unit-testable without constructing a live Orchestrator.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

DUPLICATE_OR_STALE = "duplicate_or_stale"
GAP_DETECTED = "gap_detected"
OK = "ok"

# Tolerate minor scheduler jitter -- identical multiplier to the prior
# inline code.
_GAP_TOLERANCE_MULTIPLIER = 1.5


@dataclass(frozen=True)
class CandleAdmissionDecision:
    action: str                        # One of DUPLICATE_OR_STALE / GAP_DETECTED / OK.
    gap_seconds: Optional[float] = None  # Populated only when action == GAP_DETECTED.


def classify_candle(
    candle_timestamp: datetime,
    last_candle_ts: Optional[datetime],
    candle_minutes: int,
) -> CandleAdmissionDecision:
    if last_candle_ts is None:
        return CandleAdmissionDecision(action=OK)

    if candle_timestamp <= last_candle_ts:
        return CandleAdmissionDecision(action=DUPLICATE_OR_STALE)

    expected = timedelta(minutes=candle_minutes)
    actual = candle_timestamp - last_candle_ts
    if actual > expected * _GAP_TOLERANCE_MULTIPLIER:
        return CandleAdmissionDecision(action=GAP_DETECTED, gap_seconds=actual.total_seconds())

    return CandleAdmissionDecision(action=OK)
