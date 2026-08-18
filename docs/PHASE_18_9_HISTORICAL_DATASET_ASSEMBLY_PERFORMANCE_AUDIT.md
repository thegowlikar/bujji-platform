# Phase 18.9 — Historical Research Dataset Assembly Performance Audit

**Status: AUDIT ONLY. Zero code changes.** Every number below is from
a real `cProfile` run and a real `EXPLAIN QUERY PLAN` against the live
VPS database — not estimated.

---

## 1. Profiling the Current Assembly Path

Profiled `build_dataset_version('2020-01-01', '2020-01-05',
resolution=RESOLUTION_DAILY)` — 5 real dates, the cheapest realistic
case — via `cProfile`, sorted by cumulative time:

```
1366 function calls (1214 primitive) in 39.110 seconds

ncalls  cumtime  function
   40    39.104  sqlite3.Connection.execute        <- 99.98% of ALL time
   10    39.091  HistoricalObservationStore.range_by_prefix
    1    20.053  research_calendar.build_research_calendar
    5    20.053  build_market_reality_snapshot
    5    20.043  builder._build_options_snapshot
   20    19.057  dataset_version._ingestion_run_ids
   30     0.017  HistoricalObservationStore.range   <- exact-match reads: negligible
```

**The bottleneck is total, singular, and precisely located**: 100% of
wall-clock time is SQL execution; of that, essentially all of it is
inside `range_by_prefix()` — called 10 times in this 5-date run (twice
per date: once from `_build_options_snapshot`, once from
`_ingestion_run_ids`'s options branch). The 30 exact-match `range()`
calls (spot/futures/vix snapshot reads + their own 3 non-options
ingestion-lineage lookups, 6 per date × 5 dates) together cost **17
milliseconds total** — utterly negligible by comparison.

**Root cause, confirmed via `EXPLAIN QUERY PLAN`, not inferred**:

```sql
-- range_by_prefix()'s actual query (bound parameter, as shipped):
SELECT record FROM historical_observations
WHERE instrument_identity LIKE ? AND resolution = ?
  AND timestamp >= ? AND timestamp <= ? ORDER BY timestamp ASC
--> (3, 0, 0, 'SCAN historical_observations')          -- FULL TABLE SCAN

-- Re-tested with a LITERAL prefix baked into the SQL text (not bound)
-- to rule out "SQLite can't optimize bound LIKE params" as the cause:
--> (3, 0, 0, 'SCAN historical_observations')          -- STILL a full scan

-- The exact-match range() query, for comparison:
SELECT record FROM historical_observations
WHERE instrument_identity = ? AND resolution = ?
  AND timestamp >= ? AND timestamp <= ? ORDER BY timestamp ASC
--> (4, 0, 0, 'SEARCH historical_observations USING INDEX
                idx_hist_obs_range (instrument_identity=? AND
                resolution=? AND timestamp>? AND timestamp<?)')
```

`idx_hist_obs_range (instrument_identity, resolution, timestamp)`
supports exact-match `instrument_identity` lookups perfectly (a real
indexed `SEARCH`, confirmed) but **cannot be used at all for a
`LIKE 'prefix%'` predicate on that same leading column** — proven by
the literal-vs-bound test producing the identical `SCAN` plan either
way, ruling out the common "bound parameters disable LIKE
optimization" explanation as the (sole) cause here. **Every
`range_by_prefix()` call scans the entire `historical_observations`
table** (175,235 real rows at time of this audit) regardless of how
narrow the requested date window is — which is why the cost is
roughly constant per call (~2–4 seconds) **independent of how much
data actually exists for the requested date**, exactly matching the
observed symptom (January 2020, zero option rows, still cost the same
per-date as a real 2026-08-14 query with 2,190 contracts).

**A second, distinct, real finding — genuine repeated work**:
`_build_options_snapshot` and `_ingestion_run_ids` issue **the
identical query** (same prefix, same resolution, same day bounds) for
the same date — confirmed by reading both call sites: DAILY-mode
`build_market_reality_snapshot` calls
`_build_options_snapshot(date, day_end, RESOLUTION_DAILY, ...)`, and
`dataset_version.py`'s own loop separately calls `_ingestion_run_ids(...,
f"{OPTIONS_UNDERLYING}|", resolution, day_start, day_end, True)` — same
`day_start`/`day_end` both times. This alone roughly doubles the
options-prefix cost unnecessarily, on top of the missing-index problem.

## 2. Can Historical Reconstruction Support Batch/Range Processing?

**Not today, structurally** — every layer in the chain
(`HistoricalObservationStore.range()`/`range_by_prefix()`,
`build_market_reality_snapshot()`, `check_research_session_readiness()`,
`build_research_calendar_entry()`) is a **single-date, single-query**
API. `build_research_calendar()`/`build_dataset_version()` (Phase 18.7)
already loop over a date range, but the loop body re-issues fresh,
independent queries per date — confirmed by the profile above (40
separate `execute()` calls for a 5-date request, not one batched
query).

**What WOULD support batching, real and unused today**:
- `HistoricalObservationStore.range()`'s own query shape already
  supports an arbitrarily wide `[from_timestamp, to_timestamp]` window
  for a SINGLE `instrument_identity` — nothing stops calling it once
  with a multi-year window for `NSE:NIFTY50-INDEX` and then
  partitioning the returned rows by date in Python, instead of issuing
  one query per date. This is a real, available capability, just not
  used by any date-range caller today.
- `market_reality.replay.replay()` (Phase 17F.1.2's bitemporal reader,
  re-audited in Phase 18.2 §11) already reads an entire event stream in
  one pass with bound filtering — a working precedent for "one query,
  many dates," though it targets Layer 0, not `HistoricalObservationStore`.
- No existing primitive batches ACROSS instruments (spot+futures+vix+options
  in one query) — each remains its own query regardless; this was true
  before this phase and remains true, not a new finding.

## 3. Fingerprint Handling — Recompute, Persist, or Cache?

Profiled cost of `fingerprint()` itself: **0.001 seconds cumulative
across 5 real calls** — genuinely negligible; it is not, and was never
suspected to be, part of the bottleneck (confirmed, not assumed).

Evaluated against the three options this phase names:

- **Recompute on demand (current behavior)**: correct, proven
  deterministic (Phase 18.3/18.7's own live tests), and — now
  confirmed — **cheap**. There is no performance argument for changing
  this.
- **Persist during dataset creation**: would enable Phase 18.8 §4's own
  `fingerprint_consistency` field (currently un-sourceable because
  nothing persists a prior value to compare against) — a real,
  worthwhile capability, but an *added guarantee*, not a *performance
  fix*. Recomputing costs nothing measurable; persisting exists to
  detect *future* silent drift, a different problem than assembly
  speed.
- **Cache with version invalidation**: **not justified** by this
  phase's own evidence — caching solves a cost problem, and
  `fingerprint()` has no measurable cost. Introducing a cache here
  would add real invalidation complexity (exactly the kind of
  "optimize for convenience" this project's own standing discipline
  warns against) to solve a problem that does not exist.

**Recommendation, directly from the evidence**: keep `fingerprint()`
recomputed on demand — it is not the bottleneck and cannot become one
at any realistic scale (it operates on one already-in-memory snapshot,
not the database). If `fingerprint_consistency` (Phase 18.8 §4) is
ever pursued, that is a **persistence** decision for drift-detection,
unrelated to this phase's performance question.

## 4. Ingestion Lineage Audit

Re-confirmed, not re-derived: **spot/futures/VIX** have real
`IngestionRun` ledger entries (`record_ingestion_run()`, called by all
6 daily/intraday backfill scripts) — `status`/`rows_accepted`/`rows_rejected`
available, though `DatasetVersion` (Phase 18.7) does not currently read
`ingestion_runs_for()` at all (it reads `lineage.ingestion_run_id` off
raw rows instead, per its own documented design choice). **Options has
no `IngestionRun` ledger entry at all** — `capture_options_reality_session.py`
never calls `record_ingestion_run()` (re-confirmed by fresh grep this
phase, unchanged since Phase 18.7/18.8's own identical finding).

**Impact on `DatasetVersion` reproducibility, specifically**: none
directly — `DatasetVersion.ingestion_run_references` already works
uniformly for every instrument including options (by reading
`lineage.ingestion_run_id` off rows, not the `IngestionRun` table),
so reproducibility of the assembled artifact itself is unaffected by
this gap. **The impact is entirely on observability**: there is no way
to ask "did options capture complete cleanly on date X" the way
`IngestionRun.status` answers that question for the other three
instruments — a real, carried-forward gap, not a performance concern,
and not newly discovered this phase (re-confirmed, not new).

## 5. Can `completeness.py` Become the Foundation for `ResearchDatasetHealth`?

Re-read `market_reality/completeness.py` (Phase 17E) against this
question specifically. **Its logic is directly reusable in concept**:
`expected_interval_count()`, `measure()` (expected vs. received vs.
missing intervals), and `classify_source_health()` are exactly the
shape Phase 18.8 §4's `data_quality_status` field needs and currently
has zero source for. **Its implementation is not directly reusable
as-is** — it operates on `RawObservation`/`CompletenessReport` (Layer 0
models, with `event_timestamp`/`capture_timestamp` fields specific to
that shape), not on `HistoricalObservation`/`HistoricalLineage` (the
model `HistoricalObservationStore` actually holds). Porting it would
mean either (a) writing a second, `HistoricalObservation`-shaped
implementation of the same expected/received/missing logic, or (b)
generalizing `completeness.py` to accept either model shape — a real
design decision, correctly out of this audit's own "no implementation"
scope, but the audit's own answer is: **yes, as a logical foundation;
no, not as a direct import** — the concept transfers, the code does
not.

## 6. Performance Targets

Extrapolated directly from this phase's own measured rate
(`range_by_prefix`'s ~2–4s/call dominating a roughly constant
~7.9s/date total observed in Phase 18.8's 10-day DAILY run, reconfirmed
this phase's 5-day profile at a consistent per-date rate), **under
today's unfixed, full-table-scan behavior**:

| Range | Real dates (approx.) | Extrapolated time today | Target (post-fix, aspirational, not designed here) |
|---|---|---|---|
| 1 month | ~21 trading days | ~2.8 minutes | seconds |
| 1 year | ~250 trading days | ~33 minutes | low single-digit seconds to ~1 minute |
| Multi-year (2020→today) | ~1,700 trading days | ~3.7 hours | a few minutes at most |

These targets are **not designed or committed to in this phase**
(explicitly out of scope: "do not implement") — they are stated only
to give the next phase a concrete bar, derived directly from the real
measured baseline above, not an arbitrary number.

## Current Bottleneck Map

```
build_dataset_version()
  └─ build_research_calendar()                     [loop, per date]
       └─ build_research_calendar_entry()
            └─ check_research_session_readiness()
                 └─ build_market_reality_snapshot()
                      ├─ _build_spot_snapshot()      range()            ~fast (indexed SEARCH)
                      ├─ _build_futures_snapshot()    range()            ~fast (indexed SEARCH)
                      ├─ _build_vix_snapshot()        range()            ~fast (indexed SEARCH)
                      └─ _build_options_snapshot()    range_by_prefix()  <-- SLOW: full table SCAN
  └─ _ingestion_run_ids() x4 per date                 3x range() [fast] + 1x range_by_prefix() [SLOW, DUPLICATE of the above]
  └─ fingerprint_state() [dataset_version_id]          negligible
```

**Everything not touching `range_by_prefix()` is already fast.** This
is not a diffuse performance problem needing broad optimization — it
is one missing capability (an efficient prefix-range query path) used
in two places per date, one of which is outright redundant.

## Reusable Primitives (Confirmed Real, Not Proposed)

- `HistoricalObservationStore.range()` — already indexed, already fast,
  proven at 0.017s/30 calls.
- `idx_hist_obs_range (instrument_identity, resolution, timestamp)` —
  works correctly for its designed case (exact-match); the schema
  itself is sound for every non-options instrument.
- `fingerprint_state()` / `fingerprint()` — proven cheap, no change
  needed.
- `market_reality.completeness.measure()` — reusable logic (§5) for a
  future health layer, once ported to the right model shape.
- `IngestionRun` ledger — real, complete for 6 of 7 writers; the
  concept (not the implementation) is what options is missing.

## Architectural Options (Not Chosen, Not Implemented)

Presented as options with their real, evidence-based trade-offs — no
recommendation to pick one implemented here, per this phase's own
constraint:

1. **Add a query path that avoids `LIKE` on `instrument_identity`
   entirely for the options case** — e.g., a covering index on
   `(resolution, timestamp)` alone (already-indexed columns, just not
   leading), letting SQLite narrow by the (highly selective) date
   window FIRST via that index, then filter the small resulting row
   set by prefix in Python rather than in SQL. Directly addresses the
   confirmed `EXPLAIN QUERY PLAN` finding — the date-window filter
   alone is highly selective (a handful of rows per 5-minute date
   window out of 175k+), so doing that part with an index and the
   prefix filter as a cheap post-filter would very likely eliminate
   the full scan entirely.
2. **Deduplicate the two identical `range_by_prefix()` calls per
   date** (§1's own second finding) — independent of the index fix,
   removes literally half the redundant work for options specifically,
   with no schema change at all.
3. **Batch across dates within one query**, per §2 — call `range()`/a
   fixed version of the prefix-query once with a wide multi-date
   window, partition results by date in Python, instead of one query
   per date. Reduces query COUNT (fewer round trips), independent of
   fixing the per-query cost.
4. **Do nothing to the query layer; instead cap what a single
   `DatasetVersion` request can span** (e.g., reject ranges over N
   days) — a real, honest option that avoids implementation risk
   entirely, at the cost of the customer scenario (Phase 18.8 §6)
   remaining unanswerable for genuinely long ranges. Listed for
   completeness, not favored by the evidence above (options 1–3 all
   have direct, well-understood fixes; this one just defers the
   problem).

Options 1 and 2 are not mutually exclusive with 3, and address root
cause rather than symptom — directly implied by the evidence rather
than a preference stated independent of it.

## Recommended Implementation Sequence

Not implemented this phase; ordered by evidence-backed leverage:

1. **Fix `range_by_prefix()`'s query plan** (architectural option 1) —
   the single highest-leverage change, directly targeting the
   confirmed `SCAN` vs. `SEARCH` finding; likely eliminates the vast
   majority of the measured cost on its own.
2. **Deduplicate the double options-prefix query per date**
   (architectural option 2) — small, mechanical, compounds with #1.
3. **Re-profile** (repeat this phase's own `cProfile` methodology)
   against a real multi-month range after #1–#2, to confirm the fix
   before assuming it and to produce real, not extrapolated, numbers
   for the performance targets in §6.
4. **Only then**, revisit Phase 18.8's health-monitoring design — its
   own stated precondition ("assembly performance solved first") is
   satisfied at that point, not before.
