"""Futures Statistics -- Phase 17F.1.2. Pure model, no IO.

Per `docs/PHASE_17F1_2_FUTURES_STATS_MATERIALIZER_DESIGN.md` Part 3/4,
now decided in full via `docs/PHASE_17F1_2_Q1_Q2_Q3_Q5_DECISIONS.md`.

ONE RECORD PER (instrument, interval, window_start, calc_version) -- same
key shape as `Candle`, deliberately, so the two series compose on the
same window grid.

PRICE IS REFERENCED, NEVER DUPLICATED (Q1, decided: store `price_change`
as a materialized scalar). `referenced_candle_keys` names exactly which
`Candle` row(s) (futures, and spot when basis was computed) this
record's price-derived fields came from -- `price_change`/`basis` are
never re-observed from Layer 0 a second time; they are read from an
already-materialized Candle and pointed at, not copied wholesale.

OI IS ABSENCE, NOT ZERO. `oi_open`/`oi_close`/`oi_change` are `None`
whenever no `MARKET_DEPTH` observation in the window carried a non-null
`oi` -- never coerced to 0.0. Same discipline for `basis`/`basis_percent`
(None if either side's Candle is missing) and `price_volatility` (None
if fewer than the required trailing bars exist).

BOOK STATE IS THREE-VALUED, DELIBERATELY. `OBSERVED_EMPTY` (a real,
structural fact -- bids/asks keys present, both empty lists) and
`NOT_OBSERVED` (zero MARKET_DEPTH observations landed in this window at
all) are never collapsed into one "no liquidity data" bucket -- doing so
would be exactly the "empty book vs. no observation" conflation Phase
17F.0.4 warned against.

FORBIDDEN, PERMANENTLY: no field here names a trend, a signal, a score,
a sentiment, a bias, or any fused OI+price interpretation. The schema
stops at the two raw deltas (`oi_change`, `price_change`), reported side
by side, never combined into a causal-sounding conclusion.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from bujji.epistemics.uncertainty import Uncertainty

SCHEMA_VERSION = "1.0.0"
RECOGNIZED_SCHEMA_VERSIONS = ("1.0.0",)

# Three states, never two -- see module docstring.
BOOK_STATE_OBSERVED_NONEMPTY = "OBSERVED_NONEMPTY"
BOOK_STATE_OBSERVED_EMPTY = "OBSERVED_EMPTY"
BOOK_STATE_NOT_OBSERVED = "NOT_OBSERVED"
ALL_BOOK_STATES = (
    BOOK_STATE_OBSERVED_NONEMPTY,
    BOOK_STATE_OBSERVED_EMPTY,
    BOOK_STATE_NOT_OBSERVED,
)

# A pointer into CandleStore -- (instrument, interval, window_start,
# calc_version), the exact 4-part primary key `CandleStore.get_candle`
# takes. A tuple, never a copy of the Candle's own price fields.
CandleKey = Tuple[str, str, str, str]


@dataclass(frozen=True)
class FuturesStatistics:
    """One materialized record for one futures instrument's window.

    `quality` is an `epistemics.uncertainty.Uncertainty` value, composed
    (never hand-set) by the materializer from the window's actual
    inputs -- see `futures_stats_materializer.py`'s `_compose_quality`.
    """

    instrument: str
    interval: str
    window_start: str
    window_end: str

    # -- Lineage (per brief §4 / design doc §3.1) ------------------------
    source_observation_ids: Tuple[str, ...] = ()   # Layer 0 MARKET_DEPTH ids folded in.
    referenced_candle_keys: Tuple[CandleKey, ...] = ()
    materializer_id: str = ""
    calc_version: str = ""
    transformation_history: Tuple[str, ...] = ()
    first_event_time: Optional[str] = None
    last_event_time: Optional[str] = None
    knowledge_boundary: Optional[str] = None
    capture_event_overlap: Tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION

    # -- Open interest (MARKET_DEPTH only; None means absent, not zero) --
    oi_open: Optional[float] = None
    oi_close: Optional[float] = None
    oi_change: Optional[float] = None
    oi_observation_count: int = 0
    depth_observation_count: int = 0

    # -- Price (referenced from Candle, never re-derived) ----------------
    volume: Optional[float] = None
    price_change: Optional[float] = None

    # -- Depth / liquidity -------------------------------------------------
    book_state: str = BOOK_STATE_NOT_OBSERVED
    top_bid_size_last: Optional[float] = None
    top_ask_size_last: Optional[float] = None

    # -- Basis (requires both futures and spot Candle for the same key) --
    basis: Optional[float] = None
    basis_percent: Optional[float] = None

    # -- Volatility (delegated to indicators.realised_volatility) --------
    price_volatility: Optional[float] = None

    quality: Uncertainty = Uncertainty()

    def to_dict(self) -> dict:
        return {
            "instrument": self.instrument, "interval": self.interval,
            "window_start": self.window_start, "window_end": self.window_end,
            "source_observation_ids": list(self.source_observation_ids),
            "referenced_candle_keys": [list(k) for k in self.referenced_candle_keys],
            "materializer_id": self.materializer_id, "calc_version": self.calc_version,
            "transformation_history": list(self.transformation_history),
            "first_event_time": self.first_event_time, "last_event_time": self.last_event_time,
            "knowledge_boundary": self.knowledge_boundary,
            "capture_event_overlap": list(self.capture_event_overlap),
            "schema_version": self.schema_version,
            "oi_open": self.oi_open, "oi_close": self.oi_close, "oi_change": self.oi_change,
            "oi_observation_count": self.oi_observation_count,
            "depth_observation_count": self.depth_observation_count,
            "volume": self.volume, "price_change": self.price_change,
            "book_state": self.book_state,
            "top_bid_size_last": self.top_bid_size_last, "top_ask_size_last": self.top_ask_size_last,
            "basis": self.basis, "basis_percent": self.basis_percent,
            "price_volatility": self.price_volatility,
            "quality": self.quality.to_dict(),
        }

    @staticmethod
    def from_dict(d: dict) -> "FuturesStatistics":
        return FuturesStatistics(
            instrument=d["instrument"], interval=d["interval"],
            window_start=d["window_start"], window_end=d["window_end"],
            source_observation_ids=tuple(d.get("source_observation_ids") or ()),
            referenced_candle_keys=tuple(
                tuple(k) for k in (d.get("referenced_candle_keys") or ())
            ),
            materializer_id=d.get("materializer_id", ""),
            calc_version=d.get("calc_version", ""),
            transformation_history=tuple(d.get("transformation_history") or ()),
            first_event_time=d.get("first_event_time"),
            last_event_time=d.get("last_event_time"),
            knowledge_boundary=d.get("knowledge_boundary"),
            capture_event_overlap=tuple(d.get("capture_event_overlap") or ()),
            schema_version=d.get("schema_version", SCHEMA_VERSION),
            oi_open=d.get("oi_open"), oi_close=d.get("oi_close"), oi_change=d.get("oi_change"),
            oi_observation_count=d.get("oi_observation_count", 0),
            depth_observation_count=d.get("depth_observation_count", 0),
            volume=d.get("volume"), price_change=d.get("price_change"),
            book_state=d.get("book_state", BOOK_STATE_NOT_OBSERVED),
            top_bid_size_last=d.get("top_bid_size_last"),
            top_ask_size_last=d.get("top_ask_size_last"),
            basis=d.get("basis"), basis_percent=d.get("basis_percent"),
            price_volatility=d.get("price_volatility"),
            quality=Uncertainty.from_dict(d["quality"]) if d.get("quality") else Uncertainty(),
        )
