"""Reality Coverage Index — Phase 17I.4.

Answers exactly one question: "What certified market reality does
Bujji possess for a given date?" A read-only composition over
already-existing Reality stores -- no new store, no new observation
model, no new identity mechanism, no derived/computed field.

NAMED DELIBERATELY NOT `MarketRealityTimeline`: that class already
exists (`bujji.market_reality_snapshot.timeline`, Phase 17H.6) and
answers a different question -- a condition FILTER over stored
snapshots ("find days where VIX was 10-13"). This module answers an
AVAILABILITY question ("what do we have for this day") -- structurally
distinct, so it gets a distinct name, per this project's own recurring
discipline against reusing a name for a different concept
(MarketState/MarketRealitySnapshot, 17H.5; HistoricalCandle/
HistoricalObservation, 17H.3).

Reuses, unmodified:
  * `reconstruct_market_reality()` (17H.7) for the daily-granularity view.
  * `HistoricalObservationStore.range()` (17H.4) for intraday row counts.
  * `bujji.market_reality.replay.replay()` (17E/17I) for live
    MARKET_DEPTH observation counts -- the same no-look-ahead-safe
    iteration pattern `market_reality_snapshot.builder` already uses
    for live ticks.
  * `CertificationGate.status_for()` (17A.5) for a live (never cached)
    certification snapshot.
"""
from __future__ import annotations

import datetime
from typing import Dict, Optional

from bujji.historical_reality.store import HistoricalObservationStore
from bujji.market_observation import taxonomy as moc_taxonomy
from bujji.market_reality import replay as reality_replay
from bujji.market_reality import taxonomy as reality_taxonomy
from bujji.market_reality.certification import CertificationGate
from bujji.market_reality.store import RawObservationStore
from bujji.market_reality_snapshot.builder import (
    FUTURES_CONTINUOUS_IDENTITY,
    SPOT_SYMBOL,
    VIX_SYMBOL,
)
from bujji.market_reality_snapshot.reconstruction import reconstruct_market_reality

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

KIND_MARKET_DEPTH = "MARKET_DEPTH"

# Every (instrument_type, access_method) pair actually used anywhere in
# the Reality layer today -- not an exhaustive cross-product, only the
# combinations this codebase's collectors/ingestion scripts really use.
# Adding a new access_method elsewhere in the project means adding one
# entry here, not changing the resolver's logic.
_TRACKED_ACCESS_METHODS = (
    (reality_taxonomy.INSTRUMENT_SPOT, "direct_sdk_fyers_historical_rest"),
    (reality_taxonomy.INSTRUMENT_SPOT, "direct_sdk_fyers_historical_intraday_rest"),
    (reality_taxonomy.INSTRUMENT_INDEX, "direct_sdk_fyers_historical_rest"),
    (reality_taxonomy.INSTRUMENT_INDEX, "direct_sdk_fyers_historical_intraday_rest"),
    (reality_taxonomy.INSTRUMENT_FUTURE, "direct_sdk_fyers_historical_rest"),
    (reality_taxonomy.INSTRUMENT_FUTURE, "direct_sdk_fyers_historical_intraday_rest"),
    (reality_taxonomy.INSTRUMENT_FUTURE, "direct_sdk_fyers_broker_py"),
    (reality_taxonomy.INSTRUMENT_FUTURE, "direct_sdk_fyers_broker_py_depth"),
)


def _day_bounds(date: str) -> tuple:
    return f"{date}T00:00:00+05:30", f"{date}T23:59:59+05:30"


def _intraday_coverage(historical_store: HistoricalObservationStore,
                        instrument_identity: str, date: str) -> dict:
    day_start, day_end = _day_bounds(date)
    rows = historical_store.range(
        instrument_identity, moc_taxonomy.RESOLUTION_FIVE_MINUTE, day_start, day_end,
    )
    return {
        "available": bool(rows),
        "observation_count": len(rows),
        "resolution": moc_taxonomy.RESOLUTION_FIVE_MINUTE,
    }


def _microstructure_coverage(live_store: Optional[RawObservationStore], date: str) -> dict:
    if live_store is None:
        return {"available": False, "observation_count": 0}
    day_start, day_end = _day_bounds(date)
    count = 0
    for obs in reality_replay.replay(live_store, as_of=day_end):
        if obs.kind != KIND_MARKET_DEPTH:
            continue
        ts = obs.observation.identity.timestamp
        if day_start <= ts <= day_end:
            count += 1
    return {"available": count > 0, "observation_count": count}


def _certification_snapshot(gate: CertificationGate) -> Dict[str, dict]:
    snapshot: Dict[str, dict] = {}
    for instrument_type, access_method in _TRACKED_ACCESS_METHODS:
        status, ref = gate.status_for(access_method, instrument_type)
        snapshot[access_method] = {
            "instrument_type": instrument_type, "status": status, "ref": ref,
        }
    return snapshot


class RealityCoverageIndex:
    """Read-only. Holds references to already-existing stores; writes
    nothing, computes nothing about the market itself -- every value in
    `resolve()`'s output is a presence check or a count over real,
    already-stored records."""

    def __init__(self, *, historical_store: HistoricalObservationStore,
                 certification_gate: CertificationGate,
                 live_store: Optional[RawObservationStore] = None) -> None:
        self._historical_store = historical_store
        self._live_store = live_store
        self._gate = certification_gate

    def resolve(self, date: str, *, now: Optional[datetime.datetime] = None) -> dict:
        daily = reconstruct_market_reality(
            date, historical_store=self._historical_store,
            live_store=self._live_store, now=now,
        )
        intraday = {
            "spot": _intraday_coverage(self._historical_store, SPOT_SYMBOL, date),
            "futures": _intraday_coverage(self._historical_store, FUTURES_CONTINUOUS_IDENTITY, date),
            "vix": _intraday_coverage(self._historical_store, VIX_SYMBOL, date),
        }
        microstructure = {
            "futures_depth": _microstructure_coverage(self._live_store, date),
        }
        certification = _certification_snapshot(self._gate)

        return {
            "date": date,
            "daily": daily,
            "intraday": intraday,
            "microstructure": microstructure,
            "certification": certification,
        }
