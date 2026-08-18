"""Historical Reality Reconstruction Engine — Phase 17H.7.

A thin VIEW over the already-existing `MarketRealitySnapshot` (Phase
17H.5) -- NOT a new model, NOT a new store. `build_market_reality_snapshot()`
(Phase 17H.5, extended by 17H.7 to check historical stores for
futures/VIX too -- see builder.py) already does the real work: read
Historical Reality (17H.4/17H.6) and Live Reality (17E/17I), combine
honestly, never fabricate. This module only reshapes that result into
the flat, API-friendly dict this phase's spec asks for.

"What did the market actually look like on this date?" -- reconstruction,
alignment, lineage, querying, persistence. Nothing here computes a
return, a moving average, a trend label, a regime, a similarity score,
or a prediction. Every value in the output is either a real stored
number or `None` -- never estimated, interpolated, or carried forward.
"""
from __future__ import annotations

import datetime
from typing import Optional

from bujji.historical_reality.store import HistoricalObservationStore
from bujji.market_reality.store import RawObservationStore

from .builder import build_market_reality_snapshot
from .models import MarketRealitySnapshot, SOURCE_HISTORICAL, SOURCE_LIVE

ORIGIN_HISTORICAL = "HISTORICAL"
ORIGIN_LIVE = "LIVE"

_SOURCE_TO_ORIGIN = {SOURCE_HISTORICAL: ORIGIN_HISTORICAL, SOURCE_LIVE: ORIGIN_LIVE}


def _instrument_view(snap) -> dict:
    """One instrument's slice of the reconstruction output. `None` means
    genuinely absent -- never fabricated, never interpolated (rule 1)."""
    if snap is None:
        return {"available": False, "instrument_identity": None,
                "observation_id": None, "origin": None, "ohlc": None}

    ids = snap.source_observation_ids
    ohlc = {"open": getattr(snap, "open", None), "high": getattr(snap, "high", None),
            "low": getattr(snap, "low", None), "close": snap.close}
    return {
        "available": True,
        "instrument_identity": snap.instrument if hasattr(snap, "instrument") else None,
        "observation_id": ids[0] if ids else None,
        "origin": _SOURCE_TO_ORIGIN[snap.source],  # Rule 3: explicit, never silently mixed.
        "ohlc": ohlc,
    }


def _spot_view(spot) -> dict:
    view = _instrument_view(spot)
    view["instrument_identity"] = "NSE:NIFTY50-INDEX" if spot is not None else None
    return view


def reconstruct_market_reality(
    date: str,
    *,
    historical_store: HistoricalObservationStore,
    live_store: Optional[RawObservationStore] = None,
    now: Optional[datetime.datetime] = None,
) -> dict:
    """Reconstruct one date's complete market reality, as a flat dict.

    Never raises for a date with no data -- returns an all-absent,
    EMPTY-completeness dict instead (rule 1). Identity boundaries are
    preserved exactly (rule 2): spot is always "NSE:NIFTY50-INDEX",
    futures is "NIFTY_FUT_CONTINUOUS" when historically sourced or the
    real live contract symbol when live-sourced, VIX is always
    "NSE:INDIAVIX-INDEX" -- never an expiry-specific request symbol
    presented as identity.
    """
    snap: MarketRealitySnapshot = build_market_reality_snapshot(
        date, historical_store=historical_store, live_store=live_store, now=now,
    )

    spot_view = _spot_view(snap.spot)
    futures_view = _instrument_view(snap.futures)
    vix_view = _instrument_view(snap.vix)
    if vix_view["available"]:
        vix_view["instrument_identity"] = "NSE:INDIAVIX-INDEX"

    return {
        "date": snap.date,
        "spot": spot_view,
        "futures": futures_view,
        "vix": vix_view,
        "availability": {
            "spot": spot_view["available"],
            "futures": futures_view["available"],
            "vix": vix_view["available"],
        },
        "completeness": snap.completeness,
        "lineage": list(snap.source_observation_ids),
        "certification_refs": list(snap.certification_refs),
    }
