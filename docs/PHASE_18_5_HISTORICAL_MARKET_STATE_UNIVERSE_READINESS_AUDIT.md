# Phase 18.5 — Historical Market State Universe Readiness Audit and First Complete Research Session

**Status: IMPLEMENTED (backfill), AUDITED, TESTED, LIVE-VALIDATED.**
Closes Phase 18.4's central P0 finding — options and spot/futures/VIX
5-minute data now overlap on at least one real date.

---

## Task 1 — Backfill 2026-08-14 5-Minute Spot/Futures/VIX

No new code — reused the three already-existing, already-proven
ingestion scripts (`ingest_nifty_spot_intraday_historical.py`,
`ingest_nifty_futures_intraday_historical.py`,
`ingest_india_vix_intraday_historical.py`), run with
`--start 2026-08-14 --end 2026-08-14` against the live FYERS token.

**Real results**:
```
spot:    chunk 2026-08-14..2026-08-14  status=OK  accepted=75  rejected=0
futures: chunk 2026-08-14..2026-08-14  status=OK  accepted=77  rejected=0
vix:     chunk 2026-08-14..2026-08-14  status=OK  accepted=75  rejected=0
```

**Directly re-confirmed after the backfill** (re-querying
`HistoricalObservationStore.range()` for all three symbols on
2026-08-14): all three now have real rows, spanning
`09:15:00 → 15:25:00/15:35:00 IST`, matching the trading session.

**The first complete multi-instrument research session, live-proven**
— re-running Phase 18.4's exact NIFTY Iron Condor scenario
(entry 09:20, exit 15:15) against the same real store:

```
ENTRY 09:20  spot=24324.85  futures=24396.0   vix=11.41  options=2162 contracts
EXIT  15:15  spot=24354.85  futures=24452.8   vix=11.27  options=2190 contracts
entry: completeness=COMPLETE  fingerprint=88150a6d4ba715bc...
exit:  completeness=COMPLETE  fingerprint=2d5728c383631be6...
```

Both snapshots report `COMPLETE` (all of spot/futures/vix/options
present) — this is a **first**: no prior phase's live query ever
produced `COMPLETE` for a snapshot that also had options data,
because no such date existed until this backfill ran.

## Task 2 — Coverage Audit

Directly queried, aggregated by instrument and resolution
(`instrument_identity`, `resolution`, `MIN/MAX(timestamp)`,
`COUNT(*)`, `COUNT(DISTINCT date)`):

| Instrument | Resolution | Earliest | Latest | Rows | Distinct dates |
|---|---|---|---|---|---|
| Spot (`NSE:NIFTY50-INDEX`) | DAILY | 1998-05-04 | 2026-08-13 | 7,041 | 7,041 |
| Spot | FIVE_MINUTE | 2017-07-17 | **2026-08-14** | 168,194 | 2,250 |
| VIX (`NSE:INDIAVIX-INDEX`) | DAILY | 2008-04-17 | 2026-08-13 | 4,524 | 4,524 |
| VIX | FIVE_MINUTE | 2017-07-17 | **2026-08-14** | 168,157 | 2,249 |
| Futures (`NIFTY_FUT_CONTINUOUS`) | DAILY | 2018-01-02 | 2026-08-13 | 2,131 | 2,131 |
| Futures | FIVE_MINUTE | 2018-02-01 | **2026-08-14** | 157,966 | 2,114 |
| Options (composite identity) | FIVE_MINUTE | 2026-08-14 | 2026-08-14 | 162,150 | **1** |

**Missing dates / synchronization gaps, directly observed, not
estimated**:

- **Options remain a single-day island** — 1 distinct date out of
  2,250+ available for every other instrument. This backfill closed
  the overlap for *that one day*; it did not, and was not scoped to,
  extend options coverage backward. Every future trading day options
  capture runs on will widen this by one more date; every day it
  does NOT run on (a manual-execution-only script, per Phase 17I.10's
  own design) stays a gap.
- **Futures FIVE_MINUTE has 136 fewer distinct dates than spot/VIX**
  (2,114 vs. 2,250/2,249) — a real, pre-existing gap this phase did
  not investigate further (out of this phase's stated scope: the
  backfill task was specifically "include 2026-08-14," not "audit
  every historical futures gap"). Flagged here as a genuine finding
  for a future phase, not silently absorbed into the "coverage looks
  fine" narrative.
- **DAILY resolution does not yet include 2026-08-14** for any
  instrument (`MAX(timestamp)` still `2026-08-13` for all three DAILY
  series) — expected and correct: a daily bar for a session is
  normally ingested only after that session has settled, and this
  phase deliberately only backfilled FIVE_MINUTE (the resolution
  needed for the research-session goal), not DAILY.
- **2026-08-01, used as a customer-scenario example in Phase 18.4, is
  a real market holiday (Saturday)** — re-confirmed here as a
  synchronization non-issue, not a gap: every instrument correctly
  has zero rows for that date.

## Task 3 — Research Session Readiness Contract

Implemented as `bujji/market_reality_snapshot/readiness.py` — a
`ResearchSessionReadiness` dataclass and
`check_research_session_readiness(date, *, historical_store,
resolution, as_of_time)` function. **Not a new store, not a new
reconstruction path** — it calls `build_market_reality_snapshot()`
verbatim (the same reconstruction every prior phase already trusts)
and restates that snapshot's own presence facts as explicit answers to
the six questions this phase's brief poses:

| Question | Field | How answered |
|---|---|---|
| Spot available? | `spot_available: bool` | `snapshot.spot is not None` |
| Futures available? | `futures_available: bool` | `snapshot.futures is not None` |
| VIX available? | `vix_available: bool` | `snapshot.vix is not None` |
| Options available? | `options_available: bool` | `snapshot.options is not None and len(contracts) > 0` |
| Depth available? | `depth_available: Optional[bool]` | **`None`, deliberately** — depth lives only in Layer 0 (`RawObservationStore`), never integrated into `MarketRealitySnapshot` (Phase 18.0's own unchanged finding); claiming a real True/False here would overstate what this contract actually checks. Documented in the field's own docstring, not silently defaulted to `False`. |
| Certified lineage available? | `certified_lineage_available: bool` | `len(snapshot.certification_refs) > 0` — a coarse, **snapshot-level** signal (the model only exposes one de-duplicated tuple, not a per-instrument breakdown); documented as a real limitation, not a hidden one |

Also exposes `is_complete` (all four of spot/futures/vix/options
present — the exact bar the Iron Condor scenario needed),
`missing` (tuple of exactly which components are absent),
`completeness`, `fingerprint` (reused from
`MarketRealitySnapshot.fingerprint()`, Phase 18.3), and
`reconstruction_version`.

**Live-tested against three real cases**:
```
2026-08-14, FIVE_MINUTE, as_of 09:20 -> is_complete=True,  missing=[]
2026-08-01, FIVE_MINUTE, as_of 09:20 -> is_complete=False, missing=[spot,futures,vix,options]  (real holiday)
2018-01-05, DAILY                     -> is_complete=False, missing=[options]                   (pre-options era)
```

No backtesting logic, no strategy code, no derived indicator was
written — every field is a direct presence check or a value already
computed by `MarketRealitySnapshot` itself.

## Tests and Regression

7 new tests in `tests/test_market_reality_snapshot_readiness.py`: full
absence, full presence, partial presence with exact missing-component
reporting, DAILY-resolution honest options absence, fingerprint
consistency with the underlying snapshot, never-raises/never-fabricates
on a totally empty store, and JSON-serializability of `to_dict()`.

- New test file: **7 passed**.
- **Full suite: 5,716 passed, 0 failed** (up from 5,709 — exactly the
  7 new tests, zero regressions elsewhere, confirming the backfill and
  new module touched nothing existing).

## Decision Required: Can Bujji Reliably Create a Research Dataset Version for Customer-Facing Backtesting?

**Not yet reliably — one real day exists, not a dataset.** This phase
proved the *mechanism* is sound end-to-end (backfill → complete
snapshot → readiness contract → fingerprint), on real data, for the
first time. But a "Research Dataset Version" implies a *range* of
dates a customer could select, and today that range is exactly one
calendar day (2026-08-14) where every required instrument coexists.
Phase 18.4's own P0 finding is **closed for that one day**, not
resolved architecturally — the underlying cause (options capture only
runs manually, forward, from 2026-08-14; 5-min spot/futures/VIX
backfill requires an explicit script run per date, as this phase's own
Task 1 demonstrated) still means every future date needs the same
two-step treatment (options capture, if the operator runs it that day)
+ (an explicit 5-min backfill run) before it can support a complete
research session.

**What would change the answer to "yes, reliably"**: either (a) options
capture becomes a standing daily habit (already technically running
correctly per Phase 17I.10, contingent only on the operator running it)
until enough calendar days accumulate to call it a real range, or (b)
the 5-min spot/futures/VIX backfill is run routinely enough that it
never again lags behind options' own capture date — this phase closed
one specific one-day gap by hand; it did not install a mechanism that
prevents the gap from recurring tomorrow. The readiness contract built
this phase (`check_research_session_readiness`) is exactly the tool a
future operator or scheduled job would use to detect that recurrence
early, but running it is not yet automated.
