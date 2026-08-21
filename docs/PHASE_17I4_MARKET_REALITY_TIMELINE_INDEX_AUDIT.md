# Phase 17I.4 — Market Reality Timeline Index: Audit & Design

**Status: AUDIT-FIRST, then narrowly-scoped implementation.** No new
facts, no derived features. This is a read-only composition layer over
already-existing Reality stores.

## Naming collision, caught before writing code

`MarketRealityTimeline` **already exists** (`bujji/market_reality_snapshot/timeline.py`,
Phase 17H.6) — a condition-filter query over `MarketRealitySnapshot`
rows ("find days where VIX was 10–13"). This phase's request asks a
structurally different question — **"what certified reality exists for
date X"**, an availability/coverage manifest, not a condition filter —
but the requested name ("Market Reality Timeline Index") would collide
with the existing class if taken literally. Per this engagement's own
recurring discipline (avoided identically for `MarketState`/`MarketRealitySnapshot`
in 17H.5, `HistoricalCandle`/`HistoricalObservation` in 17H.3),
**named `RealityCoverageIndex` instead**, in a new small package
`bujji/reality_coverage/`. `MarketRealityTimeline` is untouched and
remains the tool for condition-based queries; `RealityCoverageIndex` is
the tool for "what do we have."

## What already exists, reused unchanged

- `HistoricalObservationStore.range(instrument_identity, resolution, from_ts, to_ts)`
  (17H.4) — used for both daily presence and intraday row counts. No
  new store method.
- `reconstruct_market_reality(date, historical_store=..., live_store=...)`
  (17H.7) — already produces exactly the daily-granularity
  availability/lineage/completeness manifest this phase needs for the
  daily case. **Reused directly, not reimplemented.**
- `bujji.market_reality.replay.replay(store, as_of=...)` (17E/17I) —
  the existing no-look-ahead-safe iterator over `RawObservation`s,
  already used by `market_reality_snapshot.builder._live_observations_for_day()`
  for exactly this "which live facts fall on this date" filtering
  pattern. Reused identically to find `MARKET_DEPTH` observations for a
  date — no new store-reading mechanism.
- `CertificationGate.status_for(access_method, instrument_type)` (17A.5)
  — used to report live certification status per access_method at
  query time, not cached or assumed.

**No new store, no new observation model, no new identity mechanism.**
Everything the index reports is read from stores that already exist;
the index itself holds no state and writes nothing.

## Contract

`RealityCoverageIndex.resolve(date: str) -> dict`, composing four
already-real facts into one manifest:

```
{
  "date": date,
  "daily": <reconstruct_market_reality() output, unchanged>,
  "intraday": {
    "spot":    {"available": bool, "observation_count": int, "resolution": "FIVE_MINUTE"},
    "futures": {...},
    "vix":     {...},
  },
  "microstructure": {
    "futures_depth": {"available": bool, "observation_count": int}
  },
  "certification": {
    "<access_method>": {"instrument_type": ..., "status": ..., "ref": ...},
    ... one entry per (instrument_type, access_method) pair actually
        used anywhere in the Reality layer for this date's instruments
  },
}
```

- `intraday.*.observation_count` is a real `len(store.range(...))` over
  that calendar day's IST bounds — the same `_day_bounds()` helper
  `market_reality_snapshot.builder` already uses, imported not
  reimplemented.
- `microstructure.futures_depth` is a real count of `MARKET_DEPTH` raw
  observations on that date, via `replay()` — filtered to `kind ==
  "MARKET_DEPTH"`, mirroring the existing instrument-type filter
  pattern exactly.
- `certification` reports LIVE status per access_method (never cached
  from a prior resolve() call) — an index whose certification claims
  could go stale the moment a re-certification changes the artifact on
  disk would be lying about what it "has."
- Absence is always `available=False`/`observation_count=0`, never
  omitted or defaulted to a non-zero placeholder — same no-fake-completeness
  discipline as every Reality-tier model in this project.

## Explicitly out of scope

No similarity, no pattern matching, no trend/regime labeling, no new
computed field. `resolve()` answers "what do we have," never "what does
it mean" — the exact Reality/Memory boundary already drawn in
`PHASE_17I0_MARKET_REALITY_INVENTORY_AND_MEMORY_BOUNDARY_AUDIT.md`.
