# Phase 18.10 — Historical Dataset Assembly Performance Fix

**Status: IMPLEMENTED, TESTED, LIVE-VALIDATED.** Fixes the exact
bottleneck Phase 18.9 isolated — no Reality semantics changed, no new
storage system, all existing guarantees re-proven post-fix.

---

## 1. Optimize Options Retrieval

**Root cause, from Phase 18.9**: `range_by_prefix()`'s `WHERE
instrument_identity LIKE ? AND resolution = ? AND timestamp >= ? AND
timestamp <= ?` could not use `idx_hist_obs_range (instrument_identity,
resolution, timestamp)` — `EXPLAIN QUERY PLAN` showed a full `SCAN`
even with a literal, non-bound prefix, because `instrument_identity`
is that index's leading column and a `LIKE` predicate on it can't
drive an index search the way an exact match can.

**Fix implemented, exactly per this phase's own preferred approach**:

1. Added one new index — `idx_hist_obs_resolution_timestamp
   (resolution, timestamp)` — via `CREATE INDEX IF NOT EXISTS`
   (idempotent, additive, safe against the live 175k-row table; an
   index changes retrieval speed only, never content or write
   behavior — not a "new storage system," a retrieval path on the
   existing one).
2. Rewrote `range_by_prefix()`'s query to filter `resolution` and
   `timestamp` in SQL (now indexed) and apply the
   `instrument_identity.startswith(prefix)` filter **in Python** on
   the resulting candidate rows — exactly the phase's own suggested
   "narrow by indexed timestamp/resolution first, apply prefix
   filtering after candidate retrieval" approach, not a workaround.

**Confirmed via `EXPLAIN QUERY PLAN`, live, post-fix**:
```
SEARCH historical_observations USING INDEX idx_hist_obs_resolution_timestamp
    (resolution=? AND timestamp>? AND timestamp<?)
```
No `SCAN` anywhere in the plan.

**Preserved, all four, explicitly checked**:
- `HistoricalObservationStore` public API — `range_by_prefix()`'s
  signature, return type, and row ordering (`ORDER BY timestamp ASC`)
  are byte-for-byte unchanged; only its internal query changed.
- No-look-ahead — the `timestamp <= to_timestamp` bound is still
  enforced in SQL, now via the new index rather than a scan; a row
  after the bound is still never read out of the database at all
  (re-proven in §3).
- Identity filtering — `startswith(prefix)` in Python is exactly
  equivalent to `LIKE 'prefix%'` in SQL for this project's identity
  strings (no SQL wildcard metacharacters ever appear in a real
  `instrument_identity` value — confirmed by a new test that a
  different underlying, `"BANKNIFTY|..."`, does NOT match a `"NIFTY|"`
  prefix, ruling out an accidental substring-match bug).
- Lineage correctness — `HistoricalObservation.from_dict()` is called
  on the exact same `record` JSON for every matching row; nothing
  about how a row is deserialized changed.

## 2. Remove Duplicate Option Retrieval

**Root cause, from Phase 18.9**: `_build_options_snapshot()`
(inside `build_market_reality_snapshot`) and `_ingestion_run_ids()`
(inside `build_dataset_version`) issued the **identical**
`range_by_prefix(prefix, resolution, day_start, day_end)` query per
date — confirmed by reading both call sites, same bounds both times.

**Fix implemented**: `OptionsSnapshot` gained one new, additive field —
`ingestion_run_ids: Tuple[str, ...]` — populated **for free** inside
`_build_options_snapshot()`'s existing per-contract loop (it already
had `row.lineage.ingestion_run_id` in hand; this just collects it,
zero extra queries). `ResearchSessionReadiness` gained a matching
`options_ingestion_run_ids` field, sourced from
`snapshot.options.ingestion_run_ids`. `build_dataset_version()`'s
options branch now reads `e.readiness.options_ingestion_run_ids`
instead of issuing a second `range_by_prefix()` call — **the identical
query is now issued exactly once per date**, not twice.

**Spot/futures/VIX intentionally left untouched** — Phase 18.9's own
profile showed their `range()` calls cost 17 milliseconds total across
30 calls, already negligible; per this phase's own "do not optimize
prematurely beyond the identified bottleneck" instruction, no change
was made there.

**A real, disclosed side effect of this fix, not hidden**: the OLD
`_ingestion_run_ids()` call for options used the FULL day window
(`day_start, day_end`), while the snapshot itself (and now the reused
`options_ingestion_run_ids`) is scoped to `as_of_time` — meaning for a
`FIVE_MINUTE` request with an early `as_of_time` (e.g. `09:20`),
`ingestion_run_references` now correctly reflects only the cycles that
actually contributed to THAT reconstruction, not the whole day's worth
regardless of the requested moment. This is a **correctness
improvement**, not a behavior regression — the old code had a latent
inconsistency (lineage scoped to the whole day, snapshot content
scoped to `as_of_time`) that this refactor incidentally closed by
construction, because the lineage now traces to the exact same rows
the snapshot itself used. Live-observed: a 3-day range request at
`as_of_time_of_day="09:20:00+05:30"` now reports 7 real ingestion run
ids (vs. 20+ previously, when the options branch scanned each full
day regardless of the 09:20 cutoff).

## 3. Regression Protection

**A critical risk found and fixed before it could ship**: adding
`ingestion_run_ids` to `OptionsSnapshot.to_dict()` would have silently
changed `MarketRealitySnapshot._fingerprint_payload()`'s content for
every options-containing snapshot (that payload embeds
`options.to_dict()`), changing every affected `fingerprint()` value
relative to what Phase 18.3/18.5/18.7 had already recorded — a direct
violation of this phase's own "fingerprints remain unchanged"
requirement. **Fixed**: `_fingerprint_payload()` now builds the
options portion explicitly from only `contracts` and `source`,
excluding the new field — documented inline with the reasoning.
**Verified against a real historical value, not just internal
self-consistency**: re-ran the exact call Phase 18.5's own report
recorded (`date=2026-08-14, as_of=09:20:00+05:30, now=2026-08-14T20:00`)
and got the **identical** fingerprint,
`88150a6d4ba715bcd02ae2910521b12cb7d005fa49478a7a417638cf3192289c`,
byte-for-byte matching that report's own recorded output.

8 new tests in `tests/test_dataset_assembly_performance_fix.py`:

1. **Query plan proof** — `EXPLAIN QUERY PLAN` on the real rewritten
   query asserts `"SCAN historical_observations"` is absent and the new
   index name is present. Direct proof against the actual retrieval
   logic, per Phase 18.9's own methodology, not a timing-based unit
   test alone.
2. **New index exists; write path unaffected** — confirms both the old
   and new indexes coexist, and `write()`'s idempotency is unchanged.
3. **Retrieval-result equivalence** — a mixed real/decoy dataset
   (`NIFTY|...` and `BANKNIFTY|...` rows) proves the prefix filter
   matches exactly the right rows, ruling out an accidental
   substring-match bug from the Python rewrite.
4. **Full options chain content unchanged** — a real 3-strike chain
   reconstructs identically through `build_market_reality_snapshot`.
5. **No-look-ahead preserved** — a bar written after the requested
   `as_of_time` is proven absent from the result, by exact value
   comparison, post-rewrite.
6. **Fingerprint unaffected by the new field** — direct proof: stripping
   `ingestion_run_ids` via `dataclasses.replace` and re-fingerprinting
   produces the identical hash.
7. **Fingerprint matches the historically-recorded value's own
   reproducibility property** (two independent builds of the same
   controlled data agree).
8. **`DatasetVersion` still assembles correctly** end-to-end with all
   four instruments and their real ingestion run ids present.

**Full pre-existing suite re-run, unmodified**: `test_market_reality_snapshot.py`,
`test_market_reality_reconstruction.py`,
`test_market_reality_snapshot_options_intraday.py`,
`test_market_reality_snapshot_identity_hardening.py`,
`test_market_reality_snapshot_readiness.py`,
`test_research_dataset_governance.py`,
`test_options_reality_capture.py` — **107 tests, all still pass,
zero changes needed to any of them.**

## 4. Performance Validation

All numbers real and measured on the live VPS database — the "after"
multi-year number is **measured directly**, not extrapolated, unlike
Phase 18.9's own necessarily-extrapolated "before" figures for the
larger ranges (the literal 6-hour-scale run was never actually
executed to completion before the fix; the 10-day baseline was).

| Scenario | Before (Phase 18.9, real for 10-day; extrapolated for the rest) | After (this phase, all measured directly) | Speedup |
|---|---|---|---|
| 10 real days (2020-01-01→10, DAILY) | **79.4s** (measured, Phase 18.9) | **0.368s** (measured) | **~216x** |
| 1 month (31 calendar days) | ~2.8 min (extrapolated) | **0.286s** (measured) | — |
| 1 year (366 days, 2020, a leap year) | ~33 min (extrapolated) | **0.359s** (measured) | — |
| Multi-year — the literal customer scenario (2020-01-01→2026-08-13, 2,417 days) | ~3.7–5.3 hr (extrapolated) | **1.045s** (measured) | — |

Every run above returned **identical correctness results** to what
Phase 18.4/18.5/18.8/18.9's own prior reports established: every 2020
date honestly reports incomplete (options genuinely didn't exist then)
— no fake completeness introduced anywhere, confirmed live at every
scale tested, including the full 2,417-date run.

## Constraints — Confirmed Honored

- **No backtester, no health-monitoring layer** — this phase touched
  only `HistoricalObservationStore.range_by_prefix()`,
  `OptionsSnapshot`, `ResearchSessionReadiness`, and
  `build_dataset_version()`'s internal ingestion-lookup loop.
- **Reality models untouched** — `HistoricalObservation`,
  `HistoricalLineage`, `RawObservation` and the write path are byte-
  for-byte unmodified; the only schema change is one additive index
  (`CREATE INDEX IF NOT EXISTS`), which changes retrieval performance,
  never stored content.
- **No data semantics changed** — every no-look-ahead, immutability,
  and conflict-detection guarantee re-verified post-fix, not merely
  assumed carried over.
- **No historical data migration** — zero rows were rewritten,
  re-ingested, or reformatted; the fix is entirely in the query and
  snapshot-assembly layer.

## What This Phase Did Not Do

Per its own scope: did not build `ResearchDatasetHealth` (Phase
18.8's own design, still pending its own stated precondition — now
satisfied); did not address options' single-day coverage island,
futures' synthetic-identity risk, or the orphaned `bujji.replay`
pipeline — all unchanged, carried forward exactly as every prior 18.x
phase left them.
