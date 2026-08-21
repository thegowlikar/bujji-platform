# Phase 18.7 — Research Dataset Governance Layer Implementation

**Status: IMPLEMENTED, TESTED, LIVE-VALIDATED.** Converts Phase
18.6's audited design (`ResearchCalendar`, `DatasetVersion`) into real,
working code — pure composition of already-proven primitives from
Phases 18.1–18.5. No strategy logic, no backtesting, no PnL — verified
structurally, not just by intent (see the new structural-guard test).

---

## Files Created

- **`bujji/market_reality_snapshot/research_calendar.py`** —
  `ResearchCalendarEntry`, `build_research_calendar_entry()`,
  `build_research_calendar()`.
- **`bujji/market_reality_snapshot/dataset_version.py`** —
  `DatasetVersion`, `build_dataset_version()`.
- **`tests/test_research_dataset_governance.py`** — 13 tests.

**Zero changes** to `HistoricalObservationStore`,
`MarketRealitySnapshotStore`, `RawObservationStore`, or any Reality-tier
ingestion script — every requirement of "remain read-only over Reality
data" / "do not modify raw Reality storage" is satisfied by
construction: neither new module contains a single `write()` or
`INSERT` call, confirmed by re-reading both files after writing them.

## 1. `ResearchCalendar`

`build_research_calendar_entry(date, *, historical_store, resolution,
as_of_time, calendar)` wires together, for the first time, two
primitives that Phase 18.6's own audit found already existed but had
never been connected:

- **`bujji.market_calendar.MarketCalendar.is_trading_day()`** (Sprint
  112) — real, offline, honest: weekends via real `date` arithmetic;
  holidays via a deliberately empty, unverified template
  (`holiday_calendar_verified=False`).
- **`readiness.check_research_session_readiness()`** (Phase 18.5) —
  the per-instrument presence facts.

**Status classification** (`_classify_status`), exactly as specified,
plus one disclosed, justified addition:

```
NOT a trading day (real weekend, or a manually-marked closure)
    -> NON_TRADING_DAY
is_complete (spot+futures+vix+options all present)
    -> READY
some but not all present
    -> PARTIAL
none present, and IS a trading day per what Bujji can verify
    -> FAILED
```

`NON_TRADING_DAY` is not one of this phase's three literally-named
states. It was added because requirement 1 ("identify trading
sessions") is not honestly implementable without it: without a
distinct state, a real weekend with zero data would have to report
`FAILED`, falsely implying a capture gap — exactly the "no fake
completeness" violation this whole Phase 18 series has repeatedly
guarded against. It is deliberately **narrow**: only real, provable
weekends (or an explicit `add_manual_closure()`) earn
`NON_TRADING_DAY`; an *unlisted* real NSE holiday on a weekday still
reports `FAILED` — honestly reflecting "no data, and this module
cannot rule out a real gap," preserving Phase 18.6's own disclosed
`holiday_calendar_verified=False` limitation rather than silently
resolving it. **Directly tested**: a real Tuesday (2026-08-18,
confirmed via `date.strftime('%A')`) with an unpopulated holiday
calendar and zero data correctly reports `FAILED`, not
`NON_TRADING_DAY`.

`build_research_calendar(start_date, end_date, ...)` iterates every
calendar date in the range — **never skips a date**, confirmed by a
test asserting the returned date list exactly equals the full
requested range regardless of status. Takes `as_of_time_of_day`
(a time-only suffix, e.g. `"09:20:00+05:30"`), not a full timestamp —
**a real bug caught and fixed before deployment**: an earlier draft
accepted one full `as_of_time` string applied unchanged to every date
in the range, which is only meaningful for the single date it literally
names; every other date in a multi-day range would have silently
queried the wrong day. Fixed by combining the time-of-day with each
date individually inside the loop, documented inline in the function's
own docstring as a caught defect, not a hidden one.

## 2. `DatasetVersion`

`build_dataset_version(start_date, end_date, *, historical_store,
resolution, as_of_time_of_day, calendar, now)` assembles a
`ResearchCalendar` range into one identified artifact:

| Field | Source |
|---|---|
| `dataset_version_id` | `replay_engine.fingerprint_state()` (Phase 18.3's own precedent, reused verbatim — no second hashing mechanism) over `{coverage_start, coverage_end, resolution, included_components, reconstruction_version, fingerprint_lineage}` |
| `coverage_start`/`coverage_end` | the requested range, echoed back |
| `resolution` | the requested resolution, echoed back |
| `included_components` | union, across every date in range, of which of spot/futures/vix/options had ANY presence |
| `reconstruction_version` | `MarketRealitySnapshot.reconstruction_version` (Phase 18.3) — **enforced uniform across the whole range**, see below |
| `ingestion_run_references` | real `HistoricalLineage.ingestion_run_id` values, read directly off stored rows for every date/instrument in range |
| `fingerprint_lineage` | `MarketRealitySnapshot.fingerprint()` (Phase 18.3), one per date, **for every date, not just READY ones** |
| `ready_dates` / `incomplete_dates` | the full requested range partitioned by `ResearchCalendar` status — together, always equal to the complete range |

**A real, disclosed asymmetry found and worked around, not hidden**:
`HistoricalObservationStore.ingestion_runs_for()` (Phase 17H.2) is
keyed by an "instrument" label the 6 daily/intraday backfill scripts
populate via `record_ingestion_run()` — but
`capture_options_reality_session.py` **never calls
`record_ingestion_run()` at all** (confirmed by grep this phase).
Using `ingestion_runs_for()` would have silently produced an empty
`ingestion_run_references` list for options specifically. Instead,
`ingestion_run_references` is built by reading `lineage.ingestion_run_id`
directly off the real stored `HistoricalObservation` rows for every
instrument — this works identically and correctly for options
(confirmed live: real values like
`"OPTCHAIN-2026-08-14T09:15:13.274043+05:30"` appear in the assembled
list) without needing any new store method.

**`reconstruction_version` mismatch guard**: if the covered dates were
built under different `reconstruction_version` values, `build_dataset_version`
**raises `ValueError`** rather than silently picking one or blending
them — directly satisfying Phase 18.6's own design note that mixing
reconstruction logic inside one dataset would break the "same logic
version" guarantee. Proven with a real test that forces a mismatch (by
patching `build_research_calendar`'s return value, since no two real
reconstruction versions coexist in this codebase today) and asserts
the function refuses.

## 3. Connected Primitives — Confirmed, Not Assumed

- `MarketRealitySnapshot.fingerprint()` — called once per date inside
  `ResearchSessionReadiness` (Phase 18.5 already computes it), reused
  by `DatasetVersion.fingerprint_lineage`, and reused AGAIN as the
  hashing mechanism for `dataset_version_id` itself. **One fingerprint
  function, three consumers, zero duplication.**
- `reconstruction_version` — read from every date's readiness result,
  enforced uniform (above).
- `certification_refs` — already folded into
  `ResearchSessionReadiness.certified_lineage_available`, reused as-is.
- `ingestion_run_id` — read directly from stored lineage, as described
  above.

## 4. Live Validation — Real Data, Real Range

Built a real `ResearchCalendar` spanning 2026-08-13 → 2026-08-15
(the exact three-day window that includes the day Phase 18.5's backfill
completed) against the live VPS database:

```
2026-08-13  PARTIAL          trading_day=True   ("is a trading day")   -- spot/futures/vix present, no options.
2026-08-14  READY            trading_day=True   ("is a trading day")   -- all four present, COMPLETE.
2026-08-15  NON_TRADING_DAY  trading_day=False  ("is a weekend (Saturday)")
```

Built a real `DatasetVersion` over the same range:
```
dataset_version_id: 52837523ed119cb7...
included_components: [futures, options, spot, vix]
reconstruction_version: 18.3.0
ingestion_run_references: 20+ real OPTCHAIN-<timestamp> run ids, plus
                           the real spot/futures/vix RUN-* ids
```
No fabrication anywhere in this output — every value traces to a real
stored row, confirmed by construction (the module contains no literal
fallback/default value for any of these fields beyond the documented
`None` cases already covered in Phase 18.5).

## 5. Tests and Regression

13 new tests in `tests/test_research_dataset_governance.py`, directly
matching this phase's own required scenarios:

1. **Complete session** — `READY`, `is_complete=True`.
2. **Missing options** — `PARTIAL`, `missing == ("options",)`.
3. **Missing futures** — `PARTIAL`, `missing == ("futures",)`.
4. **Holiday (real weekend)** — `NON_TRADING_DAY`, never `FAILED`.
   Plus a second holiday-adjacent test: an *unlisted* weekday holiday
   honestly reports `FAILED`, not a fabricated `NON_TRADING_DAY`.
5. **Incomplete capture** (spot/futures/vix landed, options never did)
   — `PARTIAL`, never rounds up to `READY`.

Plus: full-range date coverage (never skips a date), `end < start`
rejection, `DatasetVersion` field assembly against real writes,
`DatasetVersion` never silently dropping incomplete dates,
`dataset_version_id` reproducibility across two independent builds, the
`reconstruction_version` mismatch guard, and a structural guard
confirming neither new module's source contains any PnL/strategy/
backtest-engine/order-placement vocabulary.

- New test file: **13 passed**.
- **Full suite: 5,729 passed, 0 failed** (up from 5,716 — exactly the
  13 new tests, zero regressions elsewhere).

## What This Phase Did Not Do

Per its own constraints, confirmed by the structural guard test:

- No backtester, no strategy simulator, no PnL calculation anywhere in
  either new module.
- No automated publishing — `build_dataset_version()` is a pure,
  on-demand function; nothing schedules or persists its output anywhere
  (Phase 18.6 §4's own automation-boundary recommendation: keep
  artifact assembly manual).
- No modification to raw Reality storage — both new modules are
  exclusively read paths over already-existing, already-immutable
  stores.
- Options' single-day coverage island and futures' synthetic-identity
  risk remain exactly as Phase 18.0–18.6 left them — this phase adds a
  governance layer ON TOP of existing data, it does not create new
  data (beyond what Phase 18.5's own backfill already did).
