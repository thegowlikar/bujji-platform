# Phase 18.1 — Extend market_reality_snapshot to Cover Options and 5-Minute Resolution

**Status: IMPLEMENTED, TESTED, LIVE-VALIDATED.** Closes the second of
Phase 18.0's three findings: `market_reality_snapshot` can now answer
that phase's own worked example ("NIFTY market state at 10:35 on
14-Aug-2026") for the instruments Reality actually has data for at
that moment — options and, where 5-min historical data exists,
spot/futures/VIX.

---

## What changed

Purely additive. Every new capability is reached through new,
optional parameters and new, `None`-defaulted model fields — no
existing caller, stored row, or test needed to change, and none did.

- **`bujji/market_reality_snapshot/models.py`**: added
  `OptionContractSnapshot` and `OptionsSnapshot`; added `options`,
  `resolution`, `as_of` to `MarketRealitySnapshot` (all defaulted:
  `None`, `RESOLUTION_DAILY`, `None` respectively). `SCHEMA_VERSION`
  bumped `1.0.0` → `1.1.0`, the exact same additive-bump pattern this
  project already used once for `market_observation.taxonomy`
  (Phase 17E).
- **`bujji/market_reality_snapshot/builder.py`**:
  `build_market_reality_snapshot()` gained two new optional kwargs,
  `resolution` (default `RESOLUTION_DAILY`) and `as_of_time` (default
  `None`). Four new private helpers:
  `_build_spot_snapshot_intraday`, `_build_futures_snapshot_intraday`,
  `_build_vix_snapshot_intraday`, `_build_options_snapshot`, plus
  `_latest_at_or_before()` — the single shared no-look-ahead primitive
  every intraday helper uses.
- **`bujji/historical_reality/store.py`**: added
  `HistoricalObservationStore.range_by_prefix()` — a read-only sibling
  of the existing `range()`, needed because a composite options
  identity (`"NIFTY|2026-08-18|21850|CE"`) has no single exact string
  to query "every contract for this underlying" by. Write path,
  schema, and every existing `range()` call are untouched.
- **`tests/test_market_reality_snapshot_options_intraday.py`**: 14 new
  tests (below).

**Deliberately not changed**: `MarketRealitySnapshotStore` (the
persistence layer). See "Why 5-minute snapshots are not persisted"
below.

## How the DAILY path stays byte-for-byte unchanged

`build_market_reality_snapshot(date, historical_store=...)` — called
with no new kwargs, exactly as every pre-18.1 caller does — takes the
`resolution == RESOLUTION_DAILY` branch, which calls the same,
unmodified `_build_spot_snapshot` / `_build_futures_snapshot` /
`_build_vix_snapshot` functions this module has always had. Verified,
not assumed: the full pre-existing test suites
(`test_market_reality_snapshot.py`, `test_market_reality_reconstruction.py`
— 27 tests) were re-run unmodified against the new code and all 27
still pass. A new test
(`test_from_dict_on_a_pre_18_1_record_defaults_new_fields_correctly`)
additionally proves a real 1.0.0-shaped dict — exactly the shape of
every one of the 252 already-persisted snapshots on disk — round-trips
through the new `from_dict` with `options=None`, `resolution=DAILY`,
`as_of=None`, matching what a pre-18.1 reader would have produced.

`options` is still *attempted* on the DAILY path (via
`_build_options_snapshot` queried at `RESOLUTION_DAILY`, not
hardcoded to `None`) — it returns `None` today only because no
DAILY-resolution option data has ever been ingested (Phase 18.0 §6's
own finding), an honest absence rather than a special case. A new test
(`test_daily_snapshot_never_surfaces_five_minute_option_data`) proves
the DAILY path cannot accidentally leak FIVE_MINUTE option rows in
through a resolution mismatch.

`_classify_completeness()` was deliberately left scoring only
spot/futures/vix, unchanged. Folding `options` into COMPLETE/PARTIAL/
EMPTY would have silently downgraded every one of the 252 already-
persisted, `is_final=True` historical snapshots from COMPLETE to
PARTIAL the moment this shipped, for a signal (options) that never
existed for those dates in the first place — a real, avoided
regression, documented inline in the function's own docstring.

## Point-in-time reconstruction — the new capability

`resolution=RESOLUTION_FIVE_MINUTE` requires `as_of_time` (raises
`ValueError` if omitted — fails closed). Every instrument is then
built from **its own most recent real row at or before `as_of_time`**
— the no-look-ahead guarantee is the store's own `timestamp <=
as_of_time` filter (`range()`/`range_by_prefix()`), not a
post-hoc check: a row timestamped after the bound is never even read
out of the database.

**Live-verified against real data on the VPS**, not just unit tests:

```
build_market_reality_snapshot(
    "2026-08-13", historical_store=store,
    resolution="FIVE_MINUTE", as_of_time="2026-08-13T10:35:00+05:30",
)
→ completeness=COMPLETE
  spot.close=24327.1  futures.close=24399.0  vix.close=11.71
  options=None  (no options data exists before 2026-08-14, correctly absent)
```

```
build_market_reality_snapshot(
    "2026-08-14", historical_store=store,
    resolution="FIVE_MINUTE", as_of_time="2026-08-14T10:35:00+05:30",
)
→ options.contracts = 2176 real contracts, sample:
    NIFTY|2026-08-18|21800|CE  ltp=2657.7  bid=0  ask=0  oi=0
  spot=None  futures=None  vix=None
```

The second result is not a bug — it is the mechanism working exactly
as designed and surfacing a real, previously undocumented fact: **the
5-minute spot/futures/VIX historical series lags by one full trading
day** (its last row on the day this test ran was 2026-08-13
15:25 IST; today's, 2026-08-14, hadn't been backfilled into
`HistoricalObservationStore` at 5-min resolution yet), while options
capture live-forward same-day (Phase 17I.10). Phase 18.0's own worked
example — "NIFTY market state at 10:35 on 14-Aug-2026" — is therefore
answerable **today, for options**, and **for spot/futures/VIX only
once that day's 5-min backfill completes** (typically end-of-day or
next-day, per the existing ingestion scripts' own cadence, unchanged
by this phase). This is now a visible, queryable fact instead of an
invisible gap — exactly what Phase 18.0 asked this extension to
surface.

**No-look-ahead directly proven, not assumed**: a test writes a real
bar at `09:15` and another at `15:25` for the same day, requests
`as_of_time=09:15`, and asserts the `15:25` value never appears in the
result — live-mirrored on the VPS by querying `range()` directly and
confirming the last row returned has `timestamp <= 09:20` when bounded
to `09:20`.

**Per-contract independence proven**: a live-observed real scenario
from Phase 18.0 (14 new option identities appeared intraday on
2026-08-14, as new strikes got listed) is directly tested — two
contracts, one with rows at both `09:15` and `09:20`, one newly
appearing only at `09:20`; requesting `as_of_time=09:20` correctly
picks each contract's own latest row independently, not one shared
cycle timestamp for all.

## Why 5-minute snapshots are not persisted through `MarketRealitySnapshotStore`

Checked directly: `market_reality_snapshots.db` already holds **252
real rows**, one per `date` (the table's primary key). Extending that
key to also vary by `resolution`/`as_of_time` would be a breaking
schema change to a store already carrying real, `is_final=True`
historical data — a real migration risk this phase's own scope did not
require taking on. Instead, a 5-minute snapshot is always freshly
computed from source on each call — the same "thin VIEW, never a new
store" precedent `market_reality_snapshot/reconstruction.py` (Phase
17H.7) already established for a different case, reused here rather
than invented. This is a disclosed, deliberate scope boundary, not an
oversight: if intraday snapshot caching is ever needed for performance,
it is a separate, explicit future decision (a new cache keyed
differently, not a mutation of the existing `market_reality_snapshots`
table).

## Tests

14 new tests in `tests/test_market_reality_snapshot_options_intraday.py`:

- 3 backward-compatibility tests (DAILY-path behavior unchanged,
  round-trip serialization with the new fields, legacy-dict
  `from_dict` defaulting).
- 2 validation tests (`ValueError` on an unknown resolution; on
  `FIVE_MINUTE` without `as_of_time`).
- 3 intraday spot/futures/VIX tests (latest-at-or-before selection,
  direct no-look-ahead proof, honest `None` before any data exists —
  no stale carry-forward).
- 5 options tests (real multi-contract reconstruction, per-contract
  independent latest-row selection, no-look-ahead, DAILY/FIVE_MINUTE
  resolution isolation, serialization round-trip).
- 1 test for the new `range_by_prefix()` store method, confirming it
  is additive and does not disturb the existing exact-match `range()`.

## Regression result

- New test file: **14 passed**.
- Full pre-existing snapshot/reconstruction suite
  (`test_market_reality_snapshot.py` + `test_market_reality_reconstruction.py`):
  **27 passed, unmodified, zero changes needed**.
- **Full suite: 5,692 passed, 0 failed** (up from 5,678 — exactly the
  14 new tests, zero regressions elsewhere).

## What this phase did not do

- Did not touch futures identity (Phase 18.0's #1, highest-severity
  finding — the synthetic `NIFTY_FUT_CONTINUOUS` series remains the
  only historical futures source; out of this phase's stated scope).
- Did not touch the orphaned `bujji.replay`/`options_observation`
  duplication (Phase 18.0's #3 finding) — still open.
- Did not backfill today's (2026-08-14) 5-minute spot/futures/VIX data
  — that gap is now visible through this extension's own honest
  `None` result rather than closed by it.
- Did not persist intraday snapshots (see above) — every
  `FIVE_MINUTE` call recomputes from source; acceptable at today's
  real data volume (a handful of stores, low-thousands of rows per
  query), a real future cost to revisit if usage scales.
