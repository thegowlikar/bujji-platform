"""Feature generation. Every value carries where it came from and when.

A feature without provenance is an assertion. Once a number loses the record
of which source produced it and at what instant, nothing downstream can tell a
tick fact from a chain fact, a measurement from an interpolation, or a value
observed at 09:20 from one observed at 15:20 -- and every one of those
confusions produces a backtest that cannot be reproduced or refuted.

THE SYNCHRONICITY RULE. Tick features and REST-chain OI features live in
separate containers that cannot be merged into one row without an explicit,
recorded staleness. Placing them in one row silently asserts they were
observed together. They were not: a chain snapshot may be minutes old when a
tick arrives, and on the measured lite-mode corpus the p90 worst staleness for
an option was over an hour.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .dataset import (LookAheadError, PointInTime, SOURCE_REST_CHAIN,
                      SOURCE_TICK, TickDataset)

FEATURE_SCHEMA_VERSION = "qr-features-1"


class StalenessUnknown(RuntimeError):
    """Raised when two observations are combined without a computable age."""


@dataclass(frozen=True)
class FeatureValue:
    """One feature, inseparable from its provenance."""
    name: str
    value: Optional[float]
    source: str
    observed_ts: Optional[float]
    computed_at_ts: float
    inputs: int = 0
    note: str = ""

    def age_at(self, ts: float) -> Optional[float]:
        if self.observed_ts is None:
            return None
        return ts - self.observed_ts


@dataclass
class FeatureRow:
    """Tick-sourced features as of one instant. Chain facts are NOT in here."""
    symbol: str
    as_of_ts: float
    values: Dict[str, FeatureValue] = field(default_factory=dict)
    schema_version: str = FEATURE_SCHEMA_VERSION

    def add(self, fv: FeatureValue) -> None:
        if fv.observed_ts is not None and fv.observed_ts > self.as_of_ts:
            raise LookAheadError(
                f"feature {fv.name!r} was observed at {fv.observed_ts} which is "
                f"after this row's instant {self.as_of_ts}")
        self.values[fv.name] = fv

    def as_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol, "as_of_ts": self.as_of_ts,
            "schema_version": self.schema_version,
            "features": {k: {"value": v.value, "source": v.source,
                             "observed_ts": v.observed_ts,
                             "age_s": v.age_at(self.as_of_ts),
                             "inputs": v.inputs, "note": v.note}
                         for k, v in self.values.items()},
        }


@dataclass
class ChainFeatureRow:
    """REST-chain features. Deliberately a DIFFERENT type from FeatureRow.

    Being a separate type is the enforcement: code cannot accidentally hand a
    chain row where a tick row is expected, so the two can only be combined by
    calling `join_with_staleness`, which records the age it is admitting.
    """
    symbol: str
    snapshot_ts: Optional[float]
    values: Dict[str, FeatureValue] = field(default_factory=dict)
    schema_version: str = FEATURE_SCHEMA_VERSION


def join_with_staleness(tick_row: FeatureRow, chain_row: ChainFeatureRow,
                        *, max_staleness_s: float) -> Dict[str, Any]:
    """Combine tick and chain features, recording the age of the chain fact.

    Refuses when the chain snapshot has no timestamp: an unknown age is not a
    small age, and treating it as one is how a stale positioning figure ends up
    presented as a current market state.
    """
    if chain_row.snapshot_ts is None:
        raise StalenessUnknown(
            f"chain row for {chain_row.symbol} has no snapshot timestamp, so "
            f"its age at {tick_row.as_of_ts} cannot be computed. It may not be "
            f"joined to tick features.")
    age = tick_row.as_of_ts - chain_row.snapshot_ts
    if age < 0:
        raise LookAheadError(
            f"chain snapshot at {chain_row.snapshot_ts} is AFTER the decision "
            f"instant {tick_row.as_of_ts}")
    admitted = age <= max_staleness_s
    return {
        "symbol": tick_row.symbol,
        "as_of_ts": tick_row.as_of_ts,
        "tick_features": tick_row.as_dict()["features"],
        "chain_features": ({k: v.value for k, v in chain_row.values.items()}
                           if admitted else None),
        "chain_snapshot_ts": chain_row.snapshot_ts,
        "chain_age_s": age,
        "chain_admitted": admitted,
        "max_staleness_s": max_staleness_s,
        "note": ("tick and chain observations are NOT synchronous; the chain "
                 "age above is the interval between them and is part of the "
                 "feature, not a footnote"),
    }


def tick_features_at(dataset: TickDataset, symbol: str, as_of_ts: float,
                     *, lookback_s: float = 300.0) -> FeatureRow:
    """Build a tick feature row from history only.

    Reads through a PointInTime view, so the no-look-ahead property is
    structural rather than a convention this function promises to honour.
    """
    pit: PointInTime = dataset.at(as_of_ts)
    row = FeatureRow(symbol=symbol, as_of_ts=as_of_ts)

    last_ltp = last_ts = None
    window: List[float] = []
    spreads: List[float] = []
    n = 0
    for rec in pit.history():
        if rec.symbol != symbol or rec.recv_ts is None:
            continue
        n += 1
        ltp = rec.field("ltp")
        if isinstance(ltp, (int, float)) and ltp > 0:
            last_ltp, last_ts = float(ltp), rec.recv_ts
            if rec.recv_ts >= as_of_ts - lookback_s:
                window.append(float(ltp))
        bid, ask = rec.field("bid_price"), rec.field("ask_price")
        if (isinstance(bid, (int, float)) and isinstance(ask, (int, float))
                and bid > 0 and ask >= bid):
            mid = (bid + ask) / 2.0
            if mid > 0 and rec.recv_ts >= as_of_ts - lookback_s:
                spreads.append((ask - bid) / mid)

    row.add(FeatureValue("last_price", last_ltp, SOURCE_TICK, last_ts,
                         as_of_ts, inputs=n,
                         note="last traded price; not an executable price"))
    row.add(FeatureValue(
        "price_range_in_window",
        (max(window) - min(window)) if len(window) >= 2 else None,
        SOURCE_TICK, last_ts, as_of_ts, inputs=len(window),
        note=f"over the trailing {lookback_s:.0f}s; None when under 2 prints"))
    row.add(FeatureValue(
        "mean_relative_spread",
        (sum(spreads) / len(spreads)) if spreads else None,
        SOURCE_TICK, last_ts, as_of_ts, inputs=len(spreads),
        note="None means no two-sided quote was seen, NOT a spread of zero"))
    row.add(FeatureValue("staleness_s",
                         (as_of_ts - last_ts) if last_ts is not None else None,
                         SOURCE_TICK, last_ts, as_of_ts, inputs=n,
                         note="age of the newest observation at this instant"))
    return row
