"""Market Reality Snapshot — Phase 17H.5. Models only, no IO.

NAMED DELIBERATELY NOT "MarketState": `bujji.market_state` and
`bujji.market_state_builder.market_state.MarketState` already exist and
are Understanding/Intelligence-tier concepts (episode/event detection,
confidence, direction summaries -- Shadow Campaign v2 Phase 3B/3C).
Reusing that name here, one layer below, would create exactly the kind
of confusion this audit exists to prevent. `MarketRealitySnapshot` is a
Reality-tier concept: a derived VIEW over already-immutable Layer 0 and
Historical Reality facts, never a step toward regime/episode/confidence
scoring.

DELIBERATELY NOT A CANDLE: `bujji.market_timeseries.Candle` (17F/15Q)
assumes ticks Bujji itself folded (tick_count, calc_version,
capture_event_overlap). A snapshot combining a historical daily bar
(zero live ticks behind it) with same-day live futures/VIX ticks cannot
honestly claim either shape uniformly -- same reasoning already applied
once in Phase 17H.2/17H.3 for HistoricalObservation.

NO FAKE COMPLETENESS: every field is `Optional`. `None` means the fact
genuinely does not exist yet (no live capture that day, no historical
backfill, a market holiday) -- never zero, never estimated, never
carried forward from a prior day.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

# Pure-function reuse only (Phase 18.3) -- `replay_engine.engine` has zero
# project imports of its own (stdlib `hashlib`/`json` only), so importing
# its one hashing function here introduces no cycle and no IO; this
# module's "no IO" docstring rule is about filesystem/network/broker
# access, which `fingerprint_state()` never performs.
from bujji.replay_engine.engine import fingerprint_state

# Bumped 1.0.0 -> 1.1.0 in Phase 18.1 (`options`/`resolution`/`as_of`),
# then 1.1.0 -> 1.2.0 in Phase 18.3 (`observed_at` on every component,
# `reconstruction_version` on the snapshot). Every field added at every
# step defaults such that `from_dict` on one of the 252 real,
# already-persisted 1.0.0 snapshots (none of which have any of these
# keys) reproduces the exact same object a 1.0.0 reader would have
# built -- no migration, no reinterpretation of existing rows.
SCHEMA_VERSION = "1.2.0"

# Phase 18.3: bumped by hand whenever RECONSTRUCTION LOGIC changes
# materially (a different rule for picking which row represents a given
# as_of_time, a different aggregation method, etc.) -- deliberately NOT
# bumped for a pure data change (new dates captured) or a pure model
# additive change that doesn't alter what gets selected. This is the
# single field that lets two snapshots be compared as "same data,
# different logic" vs "different data, same logic" (see `fingerprint()`
# below, which deliberately excludes this field for exactly that reason).
RECONSTRUCTION_VERSION = "18.3.0"

SOURCE_HISTORICAL = "historical"
SOURCE_LIVE = "live"

# Phase 18.1: reused verbatim from `market_observation.taxonomy`'s own
# string values (not imported -- this module stays IO/dependency-free,
# per its own docstring) so a snapshot's `resolution` field is
# byte-identical to what `HistoricalObservationStore` already stores.
RESOLUTION_DAILY = "DAILY"
RESOLUTION_FIVE_MINUTE = "FIVE_MINUTE"

COMPLETENESS_COMPLETE = "COMPLETE"    # all three instruments available
COMPLETENESS_PARTIAL = "PARTIAL"      # one or two available
COMPLETENESS_EMPTY = "EMPTY"          # none available
ALL_COMPLETENESS_STATES = (COMPLETENESS_COMPLETE, COMPLETENESS_PARTIAL, COMPLETENESS_EMPTY)


@dataclass(frozen=True)
class SpotSnapshot:
    """Full OHLC -- either read directly from a real historical daily
    bar, or honestly aggregated (min/max/first/last, not estimated)
    from the same day's real live ticks when no historical bar exists
    yet. `volume` stays None for a live-aggregated day: the live
    collector's payload never carries one (verified against
    `capture_market_reality_session.py`'s own `build_spot_observation()`
    -- payload is `{"ltp": ...}` only)."""

    open: float
    high: float
    low: float
    close: float
    volume: Optional[float]
    source: str  # SOURCE_HISTORICAL | SOURCE_LIVE
    source_observation_ids: Tuple[str, ...]
    # Phase 18.3, additive: the REAL timestamp this component's own data
    # reflects -- for a historical bar, that bar's own timestamp; for a
    # live-aggregated day, the LAST real tick folded into this OHLC (the
    # moment the `close` value became true), never the snapshot's own
    # requested/reconstruction time. `None` only when `source_observation_ids`
    # is also empty (nothing to date), never a guess.
    observed_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "open": self.open, "high": self.high, "low": self.low, "close": self.close,
            "volume": self.volume, "source": self.source,
            "source_observation_ids": list(self.source_observation_ids),
            "observed_at": self.observed_at,
        }

    @staticmethod
    def from_dict(d: Mapping) -> "SpotSnapshot":
        return SpotSnapshot(
            open=d["open"], high=d["high"], low=d["low"], close=d["close"],
            volume=d.get("volume"), source=d["source"],
            source_observation_ids=tuple(d.get("source_observation_ids") or ()),
            observed_at=d.get("observed_at"),
        )


@dataclass(frozen=True)
class FuturesSnapshot:
    """`open`/`high`/`low` (Phase 17H.7, additive): a real historical
    daily bar (Phase 17H.6's continuous futures ingestion) carries full
    OHLC -- discarding it down to `close` alone, as this model did
    before 17H.6 existed, was a real information loss, not a
    deliberate design choice. `None` for a live-aggregated day where a
    field genuinely wasn't captured (never fabricated). `close` for a
    live day is the last real tick, honest, not a certified settlement
    price. `basis`/`expiry_days` are derived on-the-fly at build time
    (Section: MarketRealitySnapshot), never stored redundantly here."""

    instrument: str  # canonical identity: "NIFTY_FUT_CONTINUOUS" (historical) or the real
                      # resolved live contract symbol, e.g. "NSE:NIFTY26AUGFUT" (live)
    close: float
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    expiry_date: Optional[str]  # ISO date, from the observation's own identity_fields -- live only;
                                 # historical continuous rows carry no expiry (Phase 17H.3 Part 2.4).
    source: str  # SOURCE_HISTORICAL | SOURCE_LIVE
    source_observation_ids: Tuple[str, ...]
    observed_at: Optional[str] = None  # Phase 18.3 -- same meaning as SpotSnapshot's own field.

    def to_dict(self) -> dict:
        return {
            "instrument": self.instrument, "close": self.close,
            "open": self.open, "high": self.high, "low": self.low,
            "expiry_date": self.expiry_date, "source": self.source,
            "source_observation_ids": list(self.source_observation_ids),
            "observed_at": self.observed_at,
        }

    @staticmethod
    def from_dict(d: Mapping) -> "FuturesSnapshot":
        return FuturesSnapshot(
            instrument=d["instrument"], close=d["close"],
            open=d.get("open"), high=d.get("high"), low=d.get("low"),
            expiry_date=d.get("expiry_date"),
            source=d["source"], source_observation_ids=tuple(d.get("source_observation_ids") or ()),
            observed_at=d.get("observed_at"),
        )


@dataclass(frozen=True)
class VixSnapshot:
    """`open`/`high`/`low` (Phase 17H.7, additive): same reasoning as
    `FuturesSnapshot` -- Phase 17H.6's historical VIX ingestion carries
    full OHLC, no longer discarded down to `close` alone. `close` for a
    live day is the last real tick. `change_percent` is deliberately
    left unset by the builder (Phase 17H.5 foundation scope) --
    computing it needs the PRIOR day's snapshot, a real, legitimate
    future extension, not implemented here."""

    close: float
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    change_percent: Optional[float]
    source: str
    source_observation_ids: Tuple[str, ...]
    observed_at: Optional[str] = None  # Phase 18.3 -- same meaning as SpotSnapshot's own field.

    def to_dict(self) -> dict:
        return {
            "close": self.close, "open": self.open, "high": self.high, "low": self.low,
            "change_percent": self.change_percent,
            "source": self.source, "source_observation_ids": list(self.source_observation_ids),
            "observed_at": self.observed_at,
        }

    @staticmethod
    def from_dict(d: Mapping) -> "VixSnapshot":
        return VixSnapshot(
            close=d["close"], open=d.get("open"), high=d.get("high"), low=d.get("low"),
            change_percent=d.get("change_percent"), source=d["source"],
            source_observation_ids=tuple(d.get("source_observation_ids") or ()),
            observed_at=d.get("observed_at"),
        )


@dataclass(frozen=True)
class OptionContractSnapshot:
    """One option contract's state at the snapshot's point in time --
    always Reality-tier facts only (ltp/bid/ask/oi), never a Greek, an
    IV, or any derived metric (PHASE_17I11's own value_kind=MAPPING
    classification is exactly what this model surfaces, unchanged).

    `identity` is the same composite string
    `HistoricalObservationStore` already keys on
    (`underlying|expiry|strike|option_type`, PHASE_17I10) -- the three
    identity fields below are parsed out of it once here, purely for
    caller convenience; `identity` remains the authoritative value."""

    identity: str
    expiry: str
    strike: float
    option_type: str
    ltp: Optional[float]
    bid: Optional[float]
    ask: Optional[float]
    open_interest: Optional[float]
    volume: Optional[float]
    source: str  # SOURCE_HISTORICAL | SOURCE_LIVE -- always SOURCE_HISTORICAL today
                  # (options have no live-only capture path distinct from
                  # historical_reality, PHASE_17I10) -- kept as a real field,
                  # not a hardcoded constant, so this model does not silently
                  # become wrong if a live options path is ever added.
    source_observation_ids: Tuple[str, ...]
    # Phase 18.3: THIS is the field that makes Gap 1's own worked example
    # (spot/futures/vix all at 10:35:00 while one option contract's own
    # last real row was 10:34:58) directly observable -- each contract is
    # picked independently (`_latest_at_or_before`, per-identity, Phase
    # 18.1), so each contract's `observed_at` can legitimately differ
    # from every other contract's, and from the other three instruments'.
    observed_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "identity": self.identity, "expiry": self.expiry, "strike": self.strike,
            "option_type": self.option_type, "ltp": self.ltp, "bid": self.bid, "ask": self.ask,
            "open_interest": self.open_interest, "volume": self.volume,
            "source": self.source, "source_observation_ids": list(self.source_observation_ids),
            "observed_at": self.observed_at,
        }

    @staticmethod
    def from_dict(d: Mapping) -> "OptionContractSnapshot":
        return OptionContractSnapshot(
            identity=d["identity"], expiry=d["expiry"], strike=d["strike"],
            option_type=d["option_type"], ltp=d.get("ltp"), bid=d.get("bid"), ask=d.get("ask"),
            open_interest=d.get("open_interest"), volume=d.get("volume"),
            source=d["source"], source_observation_ids=tuple(d.get("source_observation_ids") or ()),
            observed_at=d.get("observed_at"),
        )


@dataclass(frozen=True)
class OptionsSnapshot:
    """The full observed option chain state at the snapshot's point in
    time -- every contract that had a real, captured row at or before
    that moment, never a synthesized/interpolated chain. `None`
    contracts never appear; a contract simply absent from this tuple
    means Bujji has no fact for it at that moment (PHASE_18_0 §1's own
    "no fake completeness" rule, reused verbatim)."""

    contracts: Tuple[OptionContractSnapshot, ...]
    source: str
    # PHASE_18_10: real `HistoricalLineage.ingestion_run_id` values,
    # de-duplicated and sorted, collected for FREE while the builder
    # already iterates each contract's own row to build `contracts`
    # above -- exists specifically so a caller (e.g. DatasetVersion,
    # Phase 18.7) never needs a SECOND `range_by_prefix()` query just to
    # learn which ingestion runs produced this chain (PHASE_18_9's own
    # confirmed duplicate-query finding). Additive, defaults to `()` so
    # every pre-18.10 caller/serialized record is unaffected.
    ingestion_run_ids: Tuple[str, ...] = ()

    @property
    def source_observation_ids(self) -> Tuple[str, ...]:
        ids: List[str] = []
        for contract in self.contracts:
            ids.extend(contract.source_observation_ids)
        return tuple(ids)

    def to_dict(self) -> dict:
        return {
            "contracts": [c.to_dict() for c in self.contracts],
            "source": self.source,
            "ingestion_run_ids": list(self.ingestion_run_ids),
        }

    @staticmethod
    def from_dict(d: Mapping) -> "OptionsSnapshot":
        return OptionsSnapshot(
            contracts=tuple(OptionContractSnapshot.from_dict(c) for c in d.get("contracts", ())),
            source=d["source"],
            ingestion_run_ids=tuple(d.get("ingestion_run_ids") or ()),
        )


@dataclass(frozen=True)
class MarketRealitySnapshot:
    """One date's unified Reality view -- the bridge between Historical
    Reality (Phase 17H.4) and Live Reality (Phase 17I), nothing more.

    NOT necessarily immutable the way Layer 0/HistoricalObservation
    are: a live trading day's snapshot legitimately changes as more
    ticks arrive during the session (more ticks -> a real, different
    high/low/close). `is_final` records whether this snapshot was built
    from a settled day (date strictly before the day it was built on)
    or an in-progress one -- the store (MarketRealitySnapshotStore)
    uses this to decide whether re-computation is expected to differ
    (never a conflict) or should be stable (a conflict is a real
    finding)."""

    date: str  # "YYYY-MM-DD"
    spot: Optional[SpotSnapshot]
    futures: Optional[FuturesSnapshot]
    vix: Optional[VixSnapshot]
    completeness: str  # ALL_COMPLETENESS_STATES
    is_final: bool
    certification_refs: Tuple[str, ...]
    built_at: str  # ISO8601+IST -- when this view was computed, distinct from `date`
    schema_version: str = SCHEMA_VERSION
    # Phase 18.1, additive (see SCHEMA_VERSION comment above):
    options: Optional[OptionsSnapshot] = None
    resolution: str = RESOLUTION_DAILY  # RESOLUTION_DAILY (the only mode that existed
                                          # before this phase) | RESOLUTION_FIVE_MINUTE
    as_of: Optional[str] = None          # the point-in-time ISO8601+IST timestamp this
                                          # snapshot reflects when resolution=FIVE_MINUTE;
                                          # None for a full-day DAILY snapshot (unchanged
                                          # meaning from before this phase).
    # Phase 18.3, additive: which version of the RECONSTRUCTION LOGIC
    # (this module's own selection/aggregation rules) produced this
    # snapshot -- distinct from `schema_version` (data SHAPE) and never
    # itself part of `fingerprint()`'s input (see that method's own
    # docstring for why).
    reconstruction_version: str = RECONSTRUCTION_VERSION

    @property
    def source_observation_ids(self) -> Tuple[str, ...]:
        """Every real observation this view was derived from, across
        every instrument -- full lineage back to Reality, exactly as
        required."""
        ids: List[str] = []
        if self.spot is not None:
            ids.extend(self.spot.source_observation_ids)
        if self.futures is not None:
            ids.extend(self.futures.source_observation_ids)
        if self.vix is not None:
            ids.extend(self.vix.source_observation_ids)
        if self.options is not None:
            ids.extend(self.options.source_observation_ids)
        return tuple(ids)

    def _fingerprint_payload(self) -> dict:
        """PHASE 18.3 -- the exact, documented set of fields that
        constitute a state's DETERMINISTIC IDENTITY, per this phase's
        own requirement to define this decision explicitly rather than
        leave it implicit.

        INCLUDED: every real Reality-derived fact -- `date`,
        `resolution`, `as_of` (the request itself, since two different
        requests are two different states by definition), `completeness`,
        every component's full `to_dict()` (values, `observed_at`,
        `source_observation_ids` -- real lineage, not runtime noise),
        and `certification_refs` (a real provenance fact: WHICH
        certification backed this data -- SORTED, because the refs'
        collection order is an artifact of this module's own iteration
        order over spot/futures/vix/options, never a market fact itself).

        EXCLUDED, deliberately, each for a stated reason:
          * `built_at` -- wall-clock time this specific Python process
            happened to run; two builds of identical data one second
            apart must fingerprint identically.
          * `is_final` -- depends on the caller's `now` relative to
            `date`, not on the underlying market facts; the same real
            data, reconstructed today vs. a year from now, would
            otherwise appear to have a "different" fingerprint purely
            because of when it was asked for, not what it contains.
          * `schema_version` / `reconstruction_version` -- version
            METADATA about the record, not market content; this is the
            exact design choice PHASE_18_3's own Gap 3 worked example
            requires -- if the version fields were hashed IN, a pure
            logic change and a pure data change would be
            indistinguishable from the fingerprint alone. Comparing
            `(fingerprint, reconstruction_version)` as a PAIR across two
            builds is how the two cases are told apart; folding version
            into the hash itself would destroy that."""
        return {
            "date": self.date,
            "resolution": self.resolution,
            "as_of": self.as_of,
            "completeness": self.completeness,
            "spot": self.spot.to_dict() if self.spot else None,
            "futures": self.futures.to_dict() if self.futures else None,
            "vix": self.vix.to_dict() if self.vix else None,
            # PHASE_18_10: deliberately NOT `self.options.to_dict()` --
            # that now also carries `ingestion_run_ids` (added this
            # phase, purely a query-performance artifact, see
            # OptionsSnapshot's own docstring). Including it here would
            # silently change `fingerprint()`'s output for every
            # options-containing snapshot relative to every fingerprint
            # computed before this phase (PHASE_18_5/18.7's own reports
            # recorded real values, e.g. `88150a6d4ba715bc...` for
            # 2026-08-14 09:20 -- re-verified byte-identical after this
            # fix, live, in the PHASE_18_10 report). `contracts` and
            # `source` are the only fields that were ever part of the
            # fingerprint's own documented content; `ingestion_run_ids`
            # is redundant with `source_observation_ids` (already
            # included per-contract) and was never meant to change what
            # "the same data" means.
            "options": (
                {"contracts": [c.to_dict() for c in self.options.contracts], "source": self.options.source}
                if self.options else None
            ),
            "certification_refs": sorted(self.certification_refs),
        }

    def fingerprint(self) -> str:
        """Deterministic content fingerprint of this state's own
        Reality-derived facts (see `_fingerprint_payload()`'s own
        docstring for exactly what is and is not included).

        REUSES `replay_engine.fingerprint_state()` verbatim -- no new
        hashing mechanism (per this phase's own explicit instruction).
        That function already normalizes tuples/lists and serializes
        with `sort_keys=True`, so this method needs no additional
        normalization of its own beyond the field SELECTION performed
        in `_fingerprint_payload()`.

        Computed fresh on every call, NEVER cached as a stored field --
        a stored fingerprint could silently go stale if a
        `MarketRealitySnapshot` were ever constructed by hand (e.g. via
        `from_dict` on hand-edited data) with mismatched content; a
        computed method can never disagree with the object's own actual
        state."""
        return fingerprint_state(self._fingerprint_payload())

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "spot": self.spot.to_dict() if self.spot else None,
            "futures": self.futures.to_dict() if self.futures else None,
            "vix": self.vix.to_dict() if self.vix else None,
            "completeness": self.completeness,
            "is_final": self.is_final,
            "certification_refs": list(self.certification_refs),
            "built_at": self.built_at,
            "schema_version": self.schema_version,
            "options": self.options.to_dict() if self.options else None,
            "resolution": self.resolution,
            "as_of": self.as_of,
            "reconstruction_version": self.reconstruction_version,
        }

    @staticmethod
    def from_dict(d: Mapping) -> "MarketRealitySnapshot":
        return MarketRealitySnapshot(
            date=d["date"],
            spot=SpotSnapshot.from_dict(d["spot"]) if d.get("spot") else None,
            futures=FuturesSnapshot.from_dict(d["futures"]) if d.get("futures") else None,
            vix=VixSnapshot.from_dict(d["vix"]) if d.get("vix") else None,
            completeness=d["completeness"],
            is_final=d["is_final"],
            certification_refs=tuple(d.get("certification_refs") or ()),
            built_at=d["built_at"],
            schema_version=d.get("schema_version", SCHEMA_VERSION),
            # `.get(...)` with the pre-18.1/pre-18.3 default on every new
            # field -- a 1.0.0 or 1.1.0 record (none of these keys
            # present) round-trips to exactly the object a reader from
            # that era would have built.
            options=OptionsSnapshot.from_dict(d["options"]) if d.get("options") else None,
            resolution=d.get("resolution", RESOLUTION_DAILY),
            as_of=d.get("as_of"),
            reconstruction_version=d.get("reconstruction_version", RECONSTRUCTION_VERSION),
        )
