"""Deterministic JSON round-trip for OptionObservation /
OptionObservationSeries — Engineering Series 73C.

Reuses `bujji.market_observation.serialization`'s Observation /
ObservationSeries primitives for the wrapped MOC objects, adding only
the options-domain fields (`strike`, `expiry`, `option_type`,
`underlying`) on top. No uuid4(), no datetime.now(), no unseeded
randomness. Mirrors `bujji/futures_observation/serialization.py`
exactly.
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

from .models import OptionObservation, OptionObservationSeries


def option_observation_to_dict(oo: OptionObservation) -> Dict[str, Any]:
    return {
        "strike": oo.strike,
        "expiry": oo.expiry,
        "option_type": oo.option_type,
        "underlying": oo.underlying,
        "symbol_provenance": oo.symbol_provenance,
        "observation": observation_to_dict(oo.observation),
    }


def option_observation_from_dict(d: Dict[str, Any]) -> OptionObservation:
    return OptionObservation(
        observation=observation_from_dict(d["observation"]),
        strike=d["strike"],
        expiry=d["expiry"],
        option_type=d["option_type"],
        underlying=d["underlying"],
        # REQUIRED, never defaulted. An artifact written before provenance
        # existed genuinely does not record where its symbol came from, and
        # picking any of the four values for it would be a fabricated fact
        # about a real recording. A KeyError naming the field is the honest
        # outcome: re-ingest the source. Verified 2026-08-21 that no such
        # artifact exists on the VPS (no data/ or shadow_sessions/ file
        # contains an "option_observation"/"option_series" payload), so this
        # requirement breaks nothing today.
        symbol_provenance=d["symbol_provenance"],
    )


def option_observation_to_json(oo: OptionObservation) -> str:
    return json.dumps(option_observation_to_dict(oo), sort_keys=True)


def option_observation_from_json(text: str) -> OptionObservation:
    return option_observation_from_dict(json.loads(text))


def option_series_to_dict(os_: OptionObservationSeries) -> Dict[str, Any]:
    return {
        "strike": os_.strike,
        "expiry": os_.expiry,
        "option_type": os_.option_type,
        "underlying": os_.underlying,
        "symbol_provenance": os_.symbol_provenance,
        "series": series_to_dict(os_.series),
    }


def option_series_from_dict(d: Dict[str, Any]) -> OptionObservationSeries:
    return OptionObservationSeries(
        series=series_from_dict(d["series"]),
        strike=d["strike"],
        expiry=d["expiry"],
        option_type=d["option_type"],
        underlying=d["underlying"],
        symbol_provenance=d["symbol_provenance"],  # required -- see above
    )


def option_series_to_json(os_: OptionObservationSeries) -> str:
    return json.dumps(option_series_to_dict(os_), sort_keys=True)


def option_series_from_json(text: str) -> OptionObservationSeries:
    return option_series_from_dict(json.loads(text))
