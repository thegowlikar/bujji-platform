"""Market Timeseries -- Phase 15Q. Pure models, no IO, no broker, no
execution.

Purpose: give Bujji a queryable, durable OHLC candle history (spot,
VIX, and per-strike option premium) so technical analysis can run over
a real series rather than a single point-in-time snapshot.

Forensic findings driving this design (Step 1):
- `bujji.live_observation` ALREADY implements correct tick aggregation
  (`AggregationWindow`, `Tick`, `LateTick`, `new_window`, `add_tick`,
  `close_window`), including `INTERVAL_FIVE_MINUTE` and -- critically --
  `close_window` returning `None` for a zero-tick window rather than
  fabricating a flat candle. All of that is REUSED here, never
  reimplemented.
- What did NOT exist: durable storage (its journal is an append-only
  JSONL audit trail, not a queryable timeseries), window->candle
  persistence, a currently-forming ("live") candle accessor, and any
  technical-analysis surface.
- SQLite is already an established storage technology in this codebase
  (`bujji/journal/journal.py`, `bujji/mil_next/snapshot_journal.py`,
  both WAL-mode) -- so a `.db` file introduces no new dependency and
  follows existing precedent rather than inventing a pattern.

EPISTEMIC DISCIPLINE (the project's strongest principle, applied here):
- A window with zero ticks produces NO candle. There is no row, and no
  synthetic flat bar. A gap in the series is real information -- the
  market genuinely produced no trade/quote for that instrument in that
  window -- and must remain visible to any technical analysis built on
  top. Nothing here forward-fills.
- `tick_count` is stored on every candle so a downstream consumer can
  tell a 2-tick candle from a 400-tick candle and weight its own
  confidence accordingly. A thin candle is not the same evidence as a
  liquid one, and this store refuses to hide the difference.

PROVENANCE (Phase 17F.0/17F.1 -- schema 1.1.0): a candle is a DERIVED
record, not a raw one. Every candle now carries the chain back to the
Layer 0 (`bujji.market_reality`) observations it was folded from, so
"which raw observations created this candle?" is always answerable, and
`REPLAY_VERIFIED` (Phase 17D Part 5) is provable rather than assumed.

`calc_version` (via `epistemics.lineage.calc_version_for`, a CONTENT HASH
of the aggregation definition, never hand-maintained) is part of this
store's primary key (operator decision, Phase 17F.0 Part 2.4): a
re-materialization under a changed aggregation rule produces a
DIFFERENT, comparable row rather than colliding with the old one under
`ConflictingCandleError`. Two calculation versions disagreeing is
expected; the SAME calculation disagreeing with itself is the only thing
that error now means.

`first_event_time`/`last_event_time` are the ACTUAL observed span of the
raw ticks folded in -- distinct from the nominal `window_start`/
`window_end` boundary. A bar built from ticks spanning the full window
and one built from a nine-second sliver are different evidence; only the
real span tells them apart.

`capture_event_overlap` holds the ids of any Layer 0 `CaptureEvent`
(disconnects, overflow, etc.) whose window intersects this candle's own.
A NON-EMPTY tuple here means the candle was built while Bujji was
partially blind for some of its span -- this must never be silently
dropped, because a materializer or a human reading this candle later has
no other way to know the bar's own claim ("this is what happened in this
window") is weaker than it looks.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

SCHEMA_VERSION = "1.2.0"
RECOGNIZED_SCHEMA_VERSIONS = ("1.0.0", "1.1.0", "1.2.0")

# --- Intervals -- reuse live_observation's own vocabulary verbatim. --------
INTERVAL_FIVE_MINUTE = "FIVE_MINUTE"
INTERVAL_ONE_MINUTE = "ONE_MINUTE"

# --- Instrument kinds (what a series represents) --------------------------
KIND_SPOT = "SPOT"          # e.g. NIFTY index level.
KIND_VIX = "VIX"            # India VIX.
KIND_OPTION = "OPTION"      # one option series (a specific strike + CE/PE + expiry).
KIND_FUTURES = "FUTURES"    # Added 17F.0/17F.1 -- a specific futures contract series.
ALL_KINDS = (KIND_SPOT, KIND_VIX, KIND_OPTION, KIND_FUTURES)


@dataclass(frozen=True)
class Candle:
    """One closed OHLC bar for ONE instrument over ONE window.

    Immutable historical fact -- once written it is never updated in
    place (same discipline as `OutcomeMemoryRecord`, Phase 15N). A
    re-observation of the same (instrument, interval, window_start)
    with identical content is idempotent; with different content it is
    REJECTED by the store, never silently overwritten.

    `volume` is Optional and stays `None` when the feed published no
    volume -- never coerced to 0.0, because "no volume reported" and
    "zero volume traded" are different facts.

    PROVENANCE FIELDS (schema 1.1.0, additive) -- see the module
    docstring for the full rationale. Defaulted so every 1.0.0-era
    construction site keeps compiling; `calc_version` defaults to the
    empty string only for that backward-compatibility reason -- a real
    materializer MUST supply a real one (enforced by
    `MaterializerStore.write_candle`, not by this dataclass, since a
    plain model has no business rejecting its own construction).
    """

    instrument: str          # canonical symbol, e.g. "NSE:NIFTY50-INDEX" or "NSE:NIFTY2580724500CE".
    kind: str                # one of ALL_KINDS.
    interval: str
    window_start: str        # ISO8601, inclusive.
    window_end: str          # ISO8601, exclusive.
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float]
    tick_count: int          # how many real ticks formed this candle -- evidence density, never hidden.
    open_interest: Optional[float] = None   # options only; None when the feed published none.
    schema_version: str = SCHEMA_VERSION
    # --- Provenance (17F.0/17F.1) --------------------------------------
    source_observation_ids: Tuple[str, ...] = ()     # Layer 0 observation ids folded into this bar.
    materializer_id: str = ""                        # which materializer produced it, e.g. "tick_to_candle".
    calc_version: str = ""                            # content hash of the aggregation definition; part of the PK.
    first_event_time: Optional[str] = None            # actual observed span start (distinct from window_start).
    last_event_time: Optional[str] = None             # actual observed span end (distinct from window_end).
    knowledge_boundary: Optional[str] = None          # the as_of knowledge-time this candle was computed under.
    capture_event_overlap: Tuple[str, ...] = ()       # CaptureEvent ids overlapping this window, if any.
    # Schema 1.2.0 (17F.1.2 Q2 retrofit): additive, defaulted -- brings Candle
    # in line with ObservationProvenance (Layer 0, since 17E) and
    # FuturesStatistics (17F.1.2), both of which already carry this field.
    # A single-hop derivation (Layer 0 -> Candle) previously left "what
    # produced this, in what steps" implicit in materializer_id/calc_version
    # alone; this makes it an explicit, self-contained tuple instead.
    transformation_history: Tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "instrument": self.instrument, "kind": self.kind, "interval": self.interval,
            "window_start": self.window_start, "window_end": self.window_end,
            "open": self.open, "high": self.high, "low": self.low, "close": self.close,
            "volume": self.volume, "tick_count": self.tick_count,
            "open_interest": self.open_interest, "schema_version": self.schema_version,
            "source_observation_ids": list(self.source_observation_ids),
            "materializer_id": self.materializer_id, "calc_version": self.calc_version,
            "first_event_time": self.first_event_time, "last_event_time": self.last_event_time,
            "knowledge_boundary": self.knowledge_boundary,
            "capture_event_overlap": list(self.capture_event_overlap),
            "transformation_history": list(self.transformation_history),
        }

    @staticmethod
    def from_dict(d: dict) -> "Candle":
        return Candle(
            instrument=d["instrument"], kind=d.get("kind", KIND_SPOT), interval=d["interval"],
            window_start=d["window_start"], window_end=d["window_end"],
            open=d["open"], high=d["high"], low=d["low"], close=d["close"],
            volume=d.get("volume"), tick_count=d.get("tick_count", 0),
            open_interest=d.get("open_interest"),
            schema_version=d.get("schema_version", SCHEMA_VERSION),
            source_observation_ids=tuple(d.get("source_observation_ids") or ()),
            materializer_id=d.get("materializer_id", ""),
            calc_version=d.get("calc_version", ""),
            first_event_time=d.get("first_event_time"),
            last_event_time=d.get("last_event_time"),
            knowledge_boundary=d.get("knowledge_boundary"),
            capture_event_overlap=tuple(d.get("capture_event_overlap") or ()),
            transformation_history=tuple(d.get("transformation_history") or ()),
        )


@dataclass(frozen=True)
class FormingCandle:
    """The currently-open (not yet closed) bar -- the "live ticking
    candle". Deliberately a SEPARATE type from `Candle` so no consumer
    can mistake an in-progress bar for settled history: it is never
    persisted, and `is_closed` is structurally always False.

    Technical analysis that requires closed bars must ignore this;
    analysis that wants the live value can read it explicitly. Making
    that a type-level distinction rather than a boolean flag is
    deliberate -- it prevents the single most common backtest-vs-live
    divergence bug, where an indicator is computed on a partial bar in
    live trading but on a completed bar in replay.
    """

    instrument: str
    kind: str
    interval: str
    window_start: str
    window_end: str
    open: float
    high: float
    low: float
    last: float              # named `last`, NOT `close` -- it is not a close until the window shuts.
    volume: Optional[float]
    tick_count: int
    is_closed: bool = False

    def to_dict(self) -> dict:
        return {
            "instrument": self.instrument, "kind": self.kind, "interval": self.interval,
            "window_start": self.window_start, "window_end": self.window_end,
            "open": self.open, "high": self.high, "low": self.low, "last": self.last,
            "volume": self.volume, "tick_count": self.tick_count, "is_closed": self.is_closed,
        }
