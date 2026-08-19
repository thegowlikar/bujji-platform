"""Market Reality Snapshot builder — Phase 17H.5.

Pure combination logic: reads from the two ALREADY-EXISTING, immutable
stores (`HistoricalObservationStore`, Phase 17H.4; `RawObservationStore`
via `market_reality.replay.replay()`, Phase 17E/17I) and produces one
`MarketRealitySnapshot`. Writes nothing itself -- creates no new live
database, per the explicit instruction to consume existing observations
rather than duplicate storage.

SOURCE PRECEDENCE, per instrument (Phase 17H.7 update: futures/VIX now
follow the SAME historical-first precedence spot always had, now that
Phase 17H.6 actually ingested historical data for them -- this builder
was stale relative to that phase until this fix; verified live, not
assumed, that all three instruments' historical stores cover this):
  * Spot:    historical daily bar if it exists for that date (identity
             "NSE:NIFTY50-INDEX"), else aggregated from that day's real
             live ticks, else None.
  * Futures: historical daily bar if it exists (identity
             "NIFTY_FUT_CONTINUOUS", Phase 17H.6), else aggregated from
             live ticks, else None.
  * VIX:     historical daily bar if it exists (identity
             "NSE:INDIAVIX-INDEX", Phase 17H.6), else aggregated from
             live ticks, else None.

`live_store` is now Optional -- pure historical reconstruction (Phase
17H.7's primary use case) needs no live store at all when the
requested date is already covered by historical ingestion.

AGGREGATION, NOT ESTIMATION: when a day's spot reality comes from live
ticks (no historical bar yet), OHLC is real min/max/first/last over
that day's actual captured prices -- the same category of operation
`market_timeseries.CandleAggregator` already performs elsewhere in this
project, distinct from an indicator (RSI/EMA compute a number that
never literally happened; open/high/low/close of real ticks IS exactly
what happened).
"""
from __future__ import annotations

import datetime
from typing import List, Optional, Tuple

from bujji.historical_reality.store import HistoricalObservationStore
from bujji.market_observation import taxonomy as moc_taxonomy
from bujji.market_reality import replay as reality_replay
from bujji.market_reality import taxonomy as reality_taxonomy
from bujji.market_reality.store import RawObservationStore

from .models import (
    COMPLETENESS_COMPLETE,
    COMPLETENESS_EMPTY,
    COMPLETENESS_PARTIAL,
    FuturesSnapshot,
    MarketRealitySnapshot,
    OptionContractSnapshot,
    OptionsSnapshot,
    RECONSTRUCTION_VERSION,
    RESOLUTION_DAILY,
    RESOLUTION_FIVE_MINUTE,
    SOURCE_HISTORICAL,
    SOURCE_LIVE,
    SpotSnapshot,
    VixSnapshot,
)

SPOT_SYMBOL = "NSE:NIFTY50-INDEX"
VIX_SYMBOL = "NSE:INDIAVIX-INDEX"
FUTURES_CONTINUOUS_IDENTITY = "NIFTY_FUT_CONTINUOUS"  # Phase 17H.6's stored identity -- never a request symbol.
OPTIONS_UNDERLYING = "NIFTY"  # PHASE_17I10's identity prefix: "NIFTY|<expiry>|<strike>|<type>".
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
ALL_RESOLUTIONS = (RESOLUTION_DAILY, RESOLUTION_FIVE_MINUTE)


def _day_bounds(date: str) -> Tuple[str, str]:
    return f"{date}T00:00:00+05:30", f"{date}T23:59:59+05:30"


def _is_final(date: str, now: Optional[datetime.datetime] = None) -> bool:
    """A day is settled (its snapshot should not legitimately change on
    recomputation) once it is strictly in the past relative to now."""
    today = (now or datetime.datetime.now(IST)).date()
    return datetime.date.fromisoformat(date) < today


def _live_observations_for_day(
    store: Optional[RawObservationStore], date: str, instrument_type: str,
) -> List:
    """Every live RawObservation for `instrument_type` whose own
    timestamp falls on `date` (IST calendar date) -- filtered from
    `replay()`, the same existing reader every other consumer of Layer 0
    already uses. No new live database, no new scanning mechanism.

    `store=None` (Phase 17H.7: pure historical reconstruction needs no
    live store at all) returns no matches -- never an error, since
    "there is no live store to check" and "the live store has nothing
    for this day" are the same fact from this function's point of view."""
    if store is None:
        return []
    day_start, day_end = _day_bounds(date)
    matches = []
    for obs in reality_replay.replay(store, as_of=day_end):
        if obs.instrument_type != instrument_type:
            continue
        ts = obs.observation.identity.timestamp
        if day_start <= ts <= day_end:
            matches.append(obs)
    matches.sort(key=lambda o: o.observation.identity.timestamp)
    return matches


def _build_spot_snapshot(
    date: str, historical_store: HistoricalObservationStore, live_store: Optional[RawObservationStore],
    cert_refs: List[str],
) -> Optional[SpotSnapshot]:
    day_start, day_end = _day_bounds(date)
    historical_rows = historical_store.range(SPOT_SYMBOL, moc_taxonomy.RESOLUTION_DAILY,
                                              day_start, day_end)
    if historical_rows:
        row = historical_rows[0]
        payload = row.payload
        if row.lineage.certification_ref:
            cert_refs.append(row.lineage.certification_ref)
        return SpotSnapshot(
            open=payload["open"], high=payload["high"], low=payload["low"],
            close=payload["close"], volume=payload.get("volume"),
            source=SOURCE_HISTORICAL, source_observation_ids=(row.observation_id,),
            observed_at=row.observation.identity.timestamp,
        )

    live_obs = _live_observations_for_day(live_store, date, reality_taxonomy.INSTRUMENT_SPOT)
    if not live_obs:
        return None
    prices = [o.payload["ltp"] for o in live_obs if "ltp" in o.payload]
    if not prices:
        return None
    cert_refs.extend(o.lineage.certification_ref for o in live_obs if o.lineage.certification_ref)
    return SpotSnapshot(
        open=prices[0], high=max(prices), low=min(prices), close=prices[-1],
        volume=None,  # Live spot payload never carries volume -- verified, never fabricated.
        source=SOURCE_LIVE, source_observation_ids=tuple(o.observation_id for o in live_obs),
        # PHASE_18_3: the LAST real tick folded into this aggregate -- the
        # moment `close` became true, not the day's start or the request time.
        observed_at=live_obs[-1].observation.identity.timestamp,
    )


def _build_futures_snapshot(
    date: str, historical_store: HistoricalObservationStore,
    live_store: Optional[RawObservationStore], cert_refs: List[str],
) -> Optional[FuturesSnapshot]:
    day_start, day_end = _day_bounds(date)
    historical_rows = historical_store.range(FUTURES_CONTINUOUS_IDENTITY, moc_taxonomy.RESOLUTION_DAILY,
                                              day_start, day_end)
    if historical_rows:
        row = historical_rows[0]
        payload = row.payload
        if row.lineage.certification_ref:
            cert_refs.append(row.lineage.certification_ref)
        return FuturesSnapshot(
            instrument=FUTURES_CONTINUOUS_IDENTITY,
            close=payload["close"], open=payload.get("open"), high=payload.get("high"),
            low=payload.get("low"),
            expiry_date=None,  # Historical continuous rows carry no expiry (PHASE_17H3 Part 2.4).
            source=SOURCE_HISTORICAL, source_observation_ids=(row.observation_id,),
            observed_at=row.observation.identity.timestamp,
        )

    live_obs = _live_observations_for_day(live_store, date, reality_taxonomy.INSTRUMENT_FUTURE)
    if not live_obs:
        return None
    prices = [o.payload["ltp"] for o in live_obs if "ltp" in o.payload]
    if not prices:
        return None
    cert_refs.extend(o.lineage.certification_ref for o in live_obs if o.lineage.certification_ref)
    last = live_obs[-1]
    return FuturesSnapshot(
        instrument=last.instrument,  # Live: the real resolved contract symbol IS the identity.
        close=prices[-1], open=prices[0], high=max(prices), low=min(prices),
        expiry_date=last.identity_fields.get("expiry"),
        source=SOURCE_LIVE, source_observation_ids=tuple(o.observation_id for o in live_obs),
        observed_at=last.observation.identity.timestamp,
    )


def _build_vix_snapshot(
    date: str, historical_store: HistoricalObservationStore,
    live_store: Optional[RawObservationStore], cert_refs: List[str],
) -> Optional[VixSnapshot]:
    day_start, day_end = _day_bounds(date)
    historical_rows = historical_store.range(VIX_SYMBOL, moc_taxonomy.RESOLUTION_DAILY,
                                              day_start, day_end)
    if historical_rows:
        row = historical_rows[0]
        payload = row.payload
        if row.lineage.certification_ref:
            cert_refs.append(row.lineage.certification_ref)
        return VixSnapshot(
            close=payload["close"], open=payload.get("open"), high=payload.get("high"),
            low=payload.get("low"),
            change_percent=None,  # See models.py docstring -- deliberately not computed this phase.
            source=SOURCE_HISTORICAL, source_observation_ids=(row.observation_id,),
            observed_at=row.observation.identity.timestamp,
        )

    live_obs = _live_observations_for_day(live_store, date, reality_taxonomy.INSTRUMENT_INDEX)
    if not live_obs:
        return None
    prices = [o.payload["ltp"] for o in live_obs if "ltp" in o.payload]
    if not prices:
        return None
    cert_refs.extend(o.lineage.certification_ref for o in live_obs if o.lineage.certification_ref)
    return VixSnapshot(
        close=prices[-1], open=prices[0], high=max(prices), low=min(prices),
        change_percent=None,
        source=SOURCE_LIVE, source_observation_ids=tuple(o.observation_id for o in live_obs),
        observed_at=live_obs[-1].observation.identity.timestamp,
    )


def _classify_completeness(spot, futures, vix) -> str:
    """Unchanged from before Phase 18.1 -- deliberately still scores
    completeness against the original three instruments only. `options`
    is a real, disclosed fourth signal (see `build_market_reality_snapshot`'s
    own docstring) but is never a historically-available concept at
    DAILY resolution (PHASE_18_0 §6's own finding: no daily options
    data has ever been ingested), so folding it into COMPLETE/PARTIAL/
    EMPTY would make every historical DAILY snapshot ever built
    silently downgrade from COMPLETE to PARTIAL the instant this phase
    shipped -- a real regression against the 252 already-persisted,
    `is_final=True` rows this exact function's output already backs."""
    available = sum(1 for x in (spot, futures, vix) if x is not None)
    if available == 3:
        return COMPLETENESS_COMPLETE
    if available == 0:
        return COMPLETENESS_EMPTY
    return COMPLETENESS_PARTIAL


def _latest_at_or_before(rows: List, as_of_time: str):
    """The single most recent row whose own timestamp is <= as_of_time.
    `rows` must already be ordered ascending by timestamp (both
    `range()` and `range_by_prefix()` guarantee this) -- taking the
    last element is then exactly "most recent <= as_of_time" because
    the store's own query already filtered to `timestamp <= as_of_time`
    (PHASE_18_0 §2's own no-look-ahead requirement: a row fetched AFTER
    as_of_time is never even read out of the store, not merely
    filtered out here)."""
    return rows[-1] if rows else None


def _build_spot_snapshot_intraday(
    date: str, as_of_time: str, historical_store: HistoricalObservationStore, cert_refs: List[str],
) -> Optional[SpotSnapshot]:
    day_start, _ = _day_bounds(date)
    rows = historical_store.range(SPOT_SYMBOL, RESOLUTION_FIVE_MINUTE, day_start, as_of_time)
    row = _latest_at_or_before(rows, as_of_time)
    if row is None:
        return None
    payload = row.payload
    if row.lineage.certification_ref:
        cert_refs.append(row.lineage.certification_ref)
    if "open" in payload:
        return SpotSnapshot(
            open=payload["open"], high=payload["high"], low=payload["low"], close=payload["close"],
            volume=payload.get("volume"), source=SOURCE_HISTORICAL, source_observation_ids=(row.observation_id,),
            observed_at=row.observation.identity.timestamp,
        )
    # POINT-SAMPLE row (2026-08-19): live spot captured from the option-chain
    # sentinel is a single certified LTP ({"ltp": ...}, value_kind=MAPPING),
    # not an OHLC bar. The first day such rows existed, this function raised
    # KeyError: 'open' and the first-ever otherwise-successful daily run was
    # marked FAILED. Represent the sample as a degenerate bar -- o=h=l=c=ltp
    # -- which asserts exactly one price at exactly one instant; range
    # information does not exist for a point sample and is NOT fabricated
    # beyond that equality.
    if "ltp" in payload:
        ltp = payload["ltp"]
        return SpotSnapshot(
            open=ltp, high=ltp, low=ltp, close=ltp,
            volume=payload.get("volume"), source=SOURCE_HISTORICAL,
            source_observation_ids=(row.observation_id,),
            observed_at=row.observation.identity.timestamp,
        )
    return None  # unknown payload shape: absent beats invented


def _build_futures_snapshot_intraday(
    date: str, as_of_time: str, historical_store: HistoricalObservationStore, cert_refs: List[str],
) -> Optional[FuturesSnapshot]:
    day_start, _ = _day_bounds(date)
    rows = historical_store.range(FUTURES_CONTINUOUS_IDENTITY, RESOLUTION_FIVE_MINUTE, day_start, as_of_time)
    row = _latest_at_or_before(rows, as_of_time)
    if row is None:
        return None
    payload = row.payload
    if row.lineage.certification_ref:
        cert_refs.append(row.lineage.certification_ref)
    return FuturesSnapshot(
        instrument=FUTURES_CONTINUOUS_IDENTITY,
        close=payload["close"], open=payload.get("open"), high=payload.get("high"), low=payload.get("low"),
        expiry_date=None,  # Same continuity caveat as the daily path (PHASE_18_0 §5) -- unresolved by this phase.
        source=SOURCE_HISTORICAL, source_observation_ids=(row.observation_id,),
        observed_at=row.observation.identity.timestamp,
    )


def _build_vix_snapshot_intraday(
    date: str, as_of_time: str, historical_store: HistoricalObservationStore, cert_refs: List[str],
) -> Optional[VixSnapshot]:
    day_start, _ = _day_bounds(date)
    rows = historical_store.range(VIX_SYMBOL, RESOLUTION_FIVE_MINUTE, day_start, as_of_time)
    row = _latest_at_or_before(rows, as_of_time)
    if row is None:
        return None
    payload = row.payload
    if row.lineage.certification_ref:
        cert_refs.append(row.lineage.certification_ref)
    return VixSnapshot(
        close=payload["close"], open=payload.get("open"), high=payload.get("high"), low=payload.get("low"),
        change_percent=None, source=SOURCE_HISTORICAL, source_observation_ids=(row.observation_id,),
        observed_at=row.observation.identity.timestamp,
    )


def _build_options_snapshot(
    date: str, as_of_time: str, resolution: str,
    historical_store: HistoricalObservationStore, cert_refs: List[str],
) -> Optional[OptionsSnapshot]:
    """Every contract's own most recent row at or before `as_of_time`
    -- NOT the whole day's rows, and NOT one row per cycle: different
    contracts can have different "most recent" rows if, e.g., a
    contract was newly listed partway through the day (PHASE_18_0 §5's
    own live-observed fact: 14 new option identities appeared intraday
    on 2026-08-14). `resolution` is always RESOLUTION_FIVE_MINUTE in
    practice (no daily options data has ever been ingested,
    PHASE_18_0 §6) -- passed through rather than hardcoded so this
    function does not silently need a second edit if that ever
    changes."""
    day_start, _ = _day_bounds(date)
    prefix = f"{OPTIONS_UNDERLYING}|"
    rows = historical_store.range_by_prefix(prefix, resolution, day_start, as_of_time)
    if not rows:
        return None
    latest_per_contract = {}
    for row in rows:  # ascending order (range_by_prefix's own contract) -> last write per key wins.
        latest_per_contract[row.instrument] = row
    contracts: List[OptionContractSnapshot] = []
    for identity in sorted(latest_per_contract):
        row = latest_per_contract[identity]
        payload = row.payload
        parts = identity.split("|")
        if len(parts) != 4:
            continue  # Not a real option identity under this prefix -- never guessed at.
        _underlying, expiry, strike_str, option_type = parts
        if row.lineage.certification_ref:
            cert_refs.append(row.lineage.certification_ref)
        contracts.append(OptionContractSnapshot(
            identity=identity, expiry=expiry, strike=float(strike_str), option_type=option_type,
            ltp=payload.get("ltp"), bid=payload.get("bid"), ask=payload.get("ask"),
            open_interest=payload.get("open_interest"), volume=payload.get("volume"),
            source=SOURCE_HISTORICAL, source_observation_ids=(row.observation_id,),
            # PHASE_18_3, Gap 1's own worked example: each contract's OWN
            # row timestamp, picked independently per-identity (Phase
            # 18.1) -- this is what lets one contract legitimately show
            # 10:34:58 while spot/futures/vix show 10:35:00 for the same
            # requested as_of_time.
            observed_at=row.observation.identity.timestamp,
        ))
    if not contracts:
        return None
    # PHASE_18_10: collected from the SAME rows already fetched above --
    # zero extra queries. Exists so a caller needing "which ingestion
    # runs produced this chain" (DatasetVersion, Phase 18.7) never has
    # to re-run `range_by_prefix()` a second time for the identical
    # prefix/resolution/day-window (PHASE_18_9's own confirmed
    # duplicate-query finding).
    ingestion_run_ids = tuple(sorted({row.lineage.ingestion_run_id for row in latest_per_contract.values()}))
    return OptionsSnapshot(contracts=tuple(contracts), source=SOURCE_HISTORICAL,
                            ingestion_run_ids=ingestion_run_ids)


def build_market_reality_snapshot(
    date: str,
    *,
    historical_store: HistoricalObservationStore,
    live_store: Optional[RawObservationStore] = None,
    now: Optional[datetime.datetime] = None,
    resolution: str = RESOLUTION_DAILY,
    as_of_time: Optional[str] = None,
) -> MarketRealitySnapshot:
    """Build one date's unified Reality view. Never raises for missing
    data -- a date with nothing available yields an EMPTY snapshot, not
    an exception. `now` is injectable for deterministic testing/replay
    (no wall-clock read when supplied), matching this project's
    established discipline (RawObservationStore, CandleStore, etc.).

    `live_store` is optional (Phase 17H.7): a pure historical
    reconstruction for a date already covered by Phase 17H.4/17H.6's
    ingestion needs no live store at all.

    PHASE 18.1 -- `resolution`/`as_of_time` (both new, both optional,
    both default to the exact behavior that existed before this phase):

    * `resolution=RESOLUTION_DAILY` (the default): spot/futures/vix are
      built by the SAME, UNCHANGED daily helper functions this module
      has always used -- this call path is byte-for-byte identical to
      pre-18.1 behavior, verified by this phase's own regression suite
      re-running the full pre-existing test file unmodified. `options`
      is still attempted (a real signal, not a hardcoded None) via
      `_build_options_snapshot`, but returns None today because no
      daily options data exists to find (PHASE_18_0 §6) -- an honest
      absence, not a special case.
    * `resolution=RESOLUTION_FIVE_MINUTE`: `as_of_time` becomes
      REQUIRED (a point-in-time view is meaningless without a point in
      time) -- raises `ValueError` if omitted. Every instrument is then
      built from its most recent real row at or before `as_of_time`
      (`_latest_at_or_before`), the store's own `timestamp <=
      as_of_time` filter being the actual no-look-ahead guarantee
      (PHASE_18_0 §2). This is the mechanism that answers PHASE_18_0
      §3's own worked example ("NIFTY market state at 10:35 on
      14-Aug-2026") for the first time -- previously no code path could
      answer it at all.
    * Any other `resolution` value raises `ValueError` -- fails closed,
      never silently misinterpreted as DAILY.

    Point-in-time snapshots built this way are NOT persisted through
    `MarketRealitySnapshotStore` (PHASE_18_1 doc's own disclosed scope
    boundary) -- that store's schema keys one row per `date` and
    already holds 252 real, `is_final=True` daily snapshots; giving it
    a second, incompatible shape per date was judged a real schema-
    migration risk out of this phase's scope, not attempted here. A
    5-minute snapshot is always freshly computed, the same "thin VIEW,
    never a new store" precedent `reconstruction.py` (Phase 17H.7)
    already established for a different case."""
    if resolution not in ALL_RESOLUTIONS:
        raise ValueError(f"unknown resolution {resolution!r}, expected one of {ALL_RESOLUTIONS}")
    if resolution == RESOLUTION_FIVE_MINUTE and not as_of_time:
        raise ValueError("as_of_time is required when resolution=RESOLUTION_FIVE_MINUTE")

    cert_refs: List[str] = []
    if resolution == RESOLUTION_DAILY:
        spot = _build_spot_snapshot(date, historical_store, live_store, cert_refs)
        futures = _build_futures_snapshot(date, historical_store, live_store, cert_refs)
        vix = _build_vix_snapshot(date, historical_store, live_store, cert_refs)
        day_start, day_end = _day_bounds(date)
        options = _build_options_snapshot(date, day_end, RESOLUTION_DAILY, historical_store, cert_refs)
        effective_as_of = None
    else:
        spot = _build_spot_snapshot_intraday(date, as_of_time, historical_store, cert_refs)
        futures = _build_futures_snapshot_intraday(date, as_of_time, historical_store, cert_refs)
        vix = _build_vix_snapshot_intraday(date, as_of_time, historical_store, cert_refs)
        options = _build_options_snapshot(date, as_of_time, RESOLUTION_FIVE_MINUTE, historical_store, cert_refs)
        effective_as_of = as_of_time

    built_at = (now or datetime.datetime.now(IST)).isoformat()

    return MarketRealitySnapshot(
        date=date,
        spot=spot, futures=futures, vix=vix,
        completeness=_classify_completeness(spot, futures, vix),
        is_final=_is_final(date, now),
        certification_refs=tuple(dict.fromkeys(cert_refs)),  # De-duplicated, order-preserved.
        built_at=built_at,
        options=options,
        resolution=resolution,
        as_of=effective_as_of,
        reconstruction_version=RECONSTRUCTION_VERSION,
    )
