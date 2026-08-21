"""Market Context Window engine -- pure functions, no state, no IO.
Phase 11 Upgrade 3.

Consumes a plain list of already-persisted per-cycle record dicts (the
SAME shape `IntelligenceCycleRecorder` already produces) -- caller
supplies whatever window it has (e.g. the session's own accumulated
records so far). No broker call, no clock read; cycle counts stand in
for elapsed time (this codebase's cycles run on an approximately fixed
interval -- callers needing wall-clock-accurate windows should slice
`records` by timestamp before calling, this module does not assume a
cadence).
"""
from __future__ import annotations

from collections import Counter
from typing import List, Optional, Sequence

from .models import ContextWindowReport, HistoricalContext, HorizonSummary, SessionContext

# Cycle counts for the "short-term"/"medium-term" windows are expressed
# as a COUNT of trailing records, not minutes -- the caller decides how
# many records correspond to 5-15min / 1-3hr for its own cycle cadence.
DEFAULT_SHORT_TERM_CYCLES = 20
DEFAULT_MEDIUM_TERM_CYCLES = 240


def _safe_get(d, key):
    return d.get(key) if isinstance(d, dict) else None


def _latest_direction(records: Sequence[dict]) -> Optional[str]:
    for r in reversed(records):
        direction = _safe_get(r.get("market_direction"), "overall_direction")
        if direction and direction != "UNKNOWN":
            return direction
    return None


def _volatility_trend(records: Sequence[dict]) -> str:
    """Compares the FIRST and LAST resolved volatility_regime in the
    window -- a real observed change, never a forecast. UNKNOWN when
    fewer than two real readings exist in the window."""
    readings = [
        _safe_get(r.get("volatility_structure"), "volatility_regime")
        for r in records
    ]
    readings = [r for r in readings if r and r != "UNKNOWN"]
    if len(readings) < 2:
        return "UNKNOWN"
    first, last = readings[0], readings[-1]
    if first == last:
        return "STABLE"
    # bujji.msi_volatility_structure.taxonomy's own vocabulary.
    rank = {"COMPRESSED": 0, "STABLE": 1, "HIGH_VOLATILITY": 2}
    if first in rank and last in rank:
        return "RISING" if rank[last] > rank[first] else "FALLING"
    return "CHANGING"


def _dominant_regime(records: Sequence[dict]) -> Optional[str]:
    regimes = [_safe_get(r.get("market_state"), "regime") for r in records]
    regimes = [r for r in regimes if r and r != "UNKNOWN"]
    if not regimes:
        return None
    return Counter(regimes).most_common(1)[0][0]


def _summarize_horizon(records: Sequence[dict]) -> HorizonSummary:
    return HorizonSummary(
        cycles_observed=len(records),
        direction=_latest_direction(records),
        volatility_trend=_volatility_trend(records),
        dominant_regime=_dominant_regime(records),
    )


def _range_status(records: Sequence[dict]) -> str:
    locations = [_safe_get(r.get("market_structure"), "structure_location") for r in records]
    resolved = [l for l in locations if l and l != "UNKNOWN"]
    if not resolved:
        return "UNKNOWN"
    inside_range_fraction = resolved.count("INSIDE_RANGE") / len(resolved)
    return "ESTABLISHED" if inside_range_fraction >= 0.5 else "NOT_ESTABLISHED"


def build_context_window(
    records: List[dict],
    short_term_cycles: int = DEFAULT_SHORT_TERM_CYCLES,
    medium_term_cycles: int = DEFAULT_MEDIUM_TERM_CYCLES,
) -> ContextWindowReport:
    """Pure function: the full list of this session's records so far
    (oldest first) -> one ContextWindowReport. Never raises on an empty
    or short list -- every horizon honestly degrades to UNKNOWN/None
    rather than crashing or extrapolating."""
    short_term = _summarize_horizon(records[-short_term_cycles:])
    medium_term = _summarize_horizon(records[-medium_term_cycles:])
    session_context = SessionContext(
        cycles_observed=len(records),
        dominant_regime=_dominant_regime(records),
        range_status=_range_status(records),
    )
    # No cross-session index exists in this codebase today (see Phase 11
    # investigation notes) -- honestly unavailable, never fabricated.
    historical_context = HistoricalContext(
        available=False, similar_sessions_found=0, outcomes=None,
        reason="no prior-session index wired yet -- this session's own record only",
    )

    return ContextWindowReport(
        short_term=short_term, medium_term=medium_term,
        session_context=session_context, historical_context=historical_context,
    )
