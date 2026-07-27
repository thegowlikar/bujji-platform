"""Deterministic JSON round-trip for FuturesObservation /
FuturesObservationSeries — Engineering Series 73B.

Reuses `bujji.market_observation.serialization`'s Observation /
ObservationSeries primitives for the wrapped MOC objects, adding only
the futures-domain fields (`expiry`, `instrument_symbol`) on top. No
uuid4(), no datetime.now(), no unseeded randomness.
"""
from __future__ import annotations

import json
from typing import Any, Dict

from bujji.market_observation.serialization import (
    observation_from_dict,
    observation_to_dict,
    series_from_dict,
    series_to_dict,
)

from .models import FuturesObservation, FuturesObservationSeries


def futures_observation_to_dict(fo: FuturesObservation) -> Dict[str, Any]:
    return {
        "expiry": fo.expiry,
        "underlying": fo.underlying,
        "observation": observation_to_dict(fo.observation),
    }


def futures_observation_from_dict(d: Dict[str, Any]) -> FuturesObservation:
    return FuturesObservation(
        observation=observation_from_dict(d["observation"]),
        expiry=d["expiry"],
        underlying=d["underlying"],
    )


def futures_observation_to_json(fo: FuturesObservation) -> str:
    return json.dumps(futures_observation_to_dict(fo), sort_keys=True)


def futures_observation_from_json(text: str) -> FuturesObservation:
    return futures_observation_from_dict(json.loads(text))


def futures_series_to_dict(fs: FuturesObservationSeries) -> Dict[str, Any]:
    return {
        "expiry": fs.expiry,
        "underlying": fs.underlying,
        "series": series_to_dict(fs.series),
    }


def futures_series_from_dict(d: Dict[str, Any]) -> FuturesObservationSeries:
    return FuturesObservationSeries(
        series=series_from_dict(d["series"]),
        expiry=d["expiry"],
        underlying=d["underlying"],
    )


def futures_series_to_json(fs: FuturesObservationSeries) -> str:
    return json.dumps(futures_series_to_dict(fs), sort_keys=True)


def futures_series_from_json(text: str) -> FuturesObservationSeries:
    return futures_series_from_dict(json.loads(text))
