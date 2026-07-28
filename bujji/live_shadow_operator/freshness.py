"""bujji.live_shadow_operator.freshness — Sprint 112 Deliverable 3.

Generic data-freshness monitor. Independently classifies FIVE evidence
sources -- underlying ticks, option quotes, option chain, volatility
inputs, market observations -- each into FRESH / WARNING / STALE /
UNKNOWN. Never fabricates a "fresh" reading: a source with no real
timestamp evidence is UNKNOWN, never assumed fresh.

Thresholds are structural, disclosed, never tuned -- deliberately
modelled on the LEGACY, real `bujji.ops.health_monitor
.STALE_CANDLE_WARNING_SECONDS`/`STALE_CANDLE_CRITICAL_SECONDS`
(reused BY IDENTITY, not re-derived) for the underlying-tick check;
the other four sources are new evidence types this monitor adds,
using disclosed, comparable defaults.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional

from bujji.ops.health_monitor import STALE_CANDLE_WARNING_SECONDS, STALE_CANDLE_CRITICAL_SECONDS

FRESH = "FRESH"
WARNING = "WARNING"
STALE = "STALE"
UNKNOWN = "UNKNOWN"
ALL_FRESHNESS_STATES = (FRESH, WARNING, STALE, UNKNOWN)

# Reused by identity from the legacy, real HealthMonitor (Sprint 4) for
# the underlying-tick source -- the only source that already has an
# established, disclosed threshold anywhere in this codebase.
TICK_WARNING_SECONDS = STALE_CANDLE_WARNING_SECONDS
TICK_STALE_SECONDS = STALE_CANDLE_CRITICAL_SECONDS

# New, disclosed defaults for the four sources without a pre-existing
# threshold. Quotes/chain move on a slower real cadence (per-session
# Bhavcopy/quote refresh, not per-tick) than the underlying, hence the
# wider windows -- structural, never tuned against any outcome.
QUOTE_WARNING_SECONDS = 60.0
QUOTE_STALE_SECONDS = 180.0
CHAIN_WARNING_SECONDS = 28800.0  # 8h -- Day 1 fix: chain is loaded ONCE per session from EOD Bhavcopy, valid all day by design (docs/DAY1_LIVE_SESSION_FINDINGS.md)
CHAIN_STALE_SECONDS = 72000.0  # 20h -- flags a genuinely cross-day-stale chain, not a normal single session
VOLATILITY_WARNING_SECONDS = 300.0
VOLATILITY_STALE_SECONDS = 900.0
OBSERVATION_WARNING_SECONDS = TICK_WARNING_SECONDS
OBSERVATION_STALE_SECONDS = TICK_STALE_SECONDS

MANDATORY_SOURCES = ("underlying_tick", "option_chain")


@dataclass(frozen=True)
class FreshnessReading:
    source: str
    state: str
    age_seconds: Optional[float]
    reason: str


@dataclass(frozen=True)
class FreshnessReport:
    readings: Dict[str, FreshnessReading]

    def worst_state(self) -> str:
        rank = {FRESH: 0, UNKNOWN: 1, WARNING: 2, STALE: 3}
        return max(self.readings.values(), key=lambda r: rank[r.state]).state if self.readings else UNKNOWN

    def mandatory_stale(self) -> bool:
        """Deliverable 3's own requirement: decision generation must
        pause when a MANDATORY input is STALE (not merely WARNING)."""
        return any(self.readings[s].state == STALE for s in MANDATORY_SOURCES if s in self.readings)


def _classify(age_seconds: Optional[float], warning_at: float, stale_at: float, source: str) -> FreshnessReading:
    if age_seconds is None:
        return FreshnessReading(source=source, state=UNKNOWN, age_seconds=None,
                                reason=f"no real timestamp evidence available for {source} -- never assumed fresh")
    if age_seconds >= stale_at:
        return FreshnessReading(source=source, state=STALE, age_seconds=age_seconds,
                                reason=f"age={age_seconds:.1f}s >= STALE threshold {stale_at:.1f}s")
    if age_seconds >= warning_at:
        return FreshnessReading(source=source, state=WARNING, age_seconds=age_seconds,
                                reason=f"age={age_seconds:.1f}s >= WARNING threshold {warning_at:.1f}s")
    return FreshnessReading(source=source, state=FRESH, age_seconds=age_seconds,
                            reason=f"age={age_seconds:.1f}s within tolerance")


def assess_freshness(
    *, now: datetime, last_tick_timestamp: Optional[datetime] = None,
    last_quote_timestamp: Optional[datetime] = None, last_chain_timestamp: Optional[datetime] = None,
    last_volatility_timestamp: Optional[datetime] = None, last_observation_timestamp: Optional[datetime] = None,
) -> FreshnessReport:
    """`now` is caller-supplied (this module never reads the wall clock
    itself, mirroring this whole arc's own determinism discipline) --
    every age is `now - <source timestamp>`, real arithmetic on real
    inputs, never estimated."""
    def _age(ts: Optional[datetime]) -> Optional[float]:
        return (now - ts).total_seconds() if ts is not None else None

    readings = {
        "underlying_tick": _classify(_age(last_tick_timestamp), TICK_WARNING_SECONDS, TICK_STALE_SECONDS, "underlying_tick"),
        "option_quote": _classify(_age(last_quote_timestamp), QUOTE_WARNING_SECONDS, QUOTE_STALE_SECONDS, "option_quote"),
        "option_chain": _classify(_age(last_chain_timestamp), CHAIN_WARNING_SECONDS, CHAIN_STALE_SECONDS, "option_chain"),
        "volatility_input": _classify(_age(last_volatility_timestamp), VOLATILITY_WARNING_SECONDS, VOLATILITY_STALE_SECONDS, "volatility_input"),
        "market_observation": _classify(_age(last_observation_timestamp), OBSERVATION_WARNING_SECONDS, OBSERVATION_STALE_SECONDS, "market_observation"),
    }
    return FreshnessReport(readings=readings)
