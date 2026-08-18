"""bujji.market_microstructure.models — Phase 19.20.2.

`MinuteObservation`: one closed 1-minute window's OHLC plus the
microstructure texture derived from its (temporary, discarded-after-use)
raw ticks. Frozen, no logic -- construction lives in
`microstructure_aggregator.py`, matching this codebase's established
"models carry no logic" discipline (`bujji.live_observation.models`,
`bujji.market_timeseries.models`).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Reused verbatim from market_timeseries -- never redefined here.
from bujji.market_timeseries.models import KIND_FUTURES, KIND_OPTION, KIND_SPOT, KIND_VIX  # noqa: F401

OPTION_TYPE_CE = "CE"
OPTION_TYPE_PE = "PE"
ALL_OPTION_TYPES = (OPTION_TYPE_CE, OPTION_TYPE_PE)

SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True)
class MinuteObservation:
    """One closed 1-minute window for ONE instrument. Immutable —
    once constructed, never mutated. A window that received zero valid
    ticks never produces one of these (see `microstructure_aggregator.py`'s
    own "never fabricate" discipline, inherited directly from
    `bujji.live_observation.engine.close_window`)."""

    instrument: str
    kind: str                                  # one of market_timeseries.models.ALL_KINDS
    session_date: str                          # YYYY-MM-DD, derived from window_start
    window_start: str                          # ISO 8601, inclusive
    window_end: str                            # ISO 8601, exclusive

    # OHLC -- transport, not derived evidence (same reasoning as
    # live_observation.engine.close_window's own docstring: open/high/
    # low/close have exactly one possible value given a set of ticks,
    # no formula choice involved).
    open: float
    high: float
    low: float
    close: float

    # Microstructure texture.
    tick_count: int
    max_tick_silence_seconds: Optional[float]      # None when tick_count < 2 (no gap is observable)
    avg_tick_interval_seconds: Optional[float]     # None when tick_count < 2
    max_price_move: float                          # largest single tick-to-tick |delta|; 0.0 when tick_count < 2

    # Options-only metadata. None for SPOT/FUTURES/VIX.
    max_premium_move: Optional[float] = None
    open_interest: Optional[float] = None
    strike: Optional[float] = None
    option_type: Optional[str] = None

    # Integrity metadata.
    first_tick_timestamp: str = ""
    last_tick_timestamp: str = ""
    rejected_tick_count: int = 0                   # ticks seen this window but excluded (invalid price/timestamp)
    observation_quality_score: float = 0.0         # 0-100; see microstructure_aggregator.py's scoring rationale

    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return {
            "instrument": self.instrument, "kind": self.kind, "session_date": self.session_date,
            "window_start": self.window_start, "window_end": self.window_end,
            "open": self.open, "high": self.high, "low": self.low, "close": self.close,
            "tick_count": self.tick_count,
            "max_tick_silence_seconds": self.max_tick_silence_seconds,
            "avg_tick_interval_seconds": self.avg_tick_interval_seconds,
            "max_price_move": self.max_price_move,
            "max_premium_move": self.max_premium_move,
            "open_interest": self.open_interest,
            "strike": self.strike,
            "option_type": self.option_type,
            "first_tick_timestamp": self.first_tick_timestamp,
            "last_tick_timestamp": self.last_tick_timestamp,
            "rejected_tick_count": self.rejected_tick_count,
            "observation_quality_score": self.observation_quality_score,
            "schema_version": self.schema_version,
        }

    @staticmethod
    def from_dict(d: dict) -> "MinuteObservation":
        return MinuteObservation(
            instrument=d["instrument"], kind=d["kind"], session_date=d["session_date"],
            window_start=d["window_start"], window_end=d["window_end"],
            open=d["open"], high=d["high"], low=d["low"], close=d["close"],
            tick_count=d["tick_count"],
            max_tick_silence_seconds=d.get("max_tick_silence_seconds"),
            avg_tick_interval_seconds=d.get("avg_tick_interval_seconds"),
            max_price_move=d.get("max_price_move", 0.0),
            max_premium_move=d.get("max_premium_move"),
            open_interest=d.get("open_interest"),
            strike=d.get("strike"),
            option_type=d.get("option_type"),
            first_tick_timestamp=d.get("first_tick_timestamp", ""),
            last_tick_timestamp=d.get("last_tick_timestamp", ""),
            rejected_tick_count=d.get("rejected_tick_count", 0),
            observation_quality_score=d.get("observation_quality_score", 0.0),
            schema_version=d.get("schema_version", SCHEMA_VERSION),
        )
