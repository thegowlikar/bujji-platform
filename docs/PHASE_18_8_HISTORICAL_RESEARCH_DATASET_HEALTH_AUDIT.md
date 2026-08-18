# Phase 18.8 — Historical Research Dataset Health Monitoring Audit

**Status: AUDIT ONLY. Zero code changes.** Every claim is backed by a
direct code read or a live, timed query against the real VPS database,
run fresh for this phase.

---

## Current Capability Map

| Layer | What exists | Phase |
|---|---|---|
| Raw capture | `HistoricalObservationStore`, immutable, INSERT-only, conflict-guarded | 17H.3/17H.4 |
| Per-instrument presence | `ResearchSessionReadiness` (spot/futures/vix/options booleans, cert signal, fingerprint) | 18.5 |
| Per-date classification | `ResearchCalendar` (READY/PARTIAL/FAILED/NON_TRADING_DAY, wired to `MarketCalendar`) | 18.7 |
| Multi-date artifact | `DatasetVersion` (coverage, fingerprint lineage, ingestion-run references, reconstruction_version guard) | 18.7 |
| **Continuous health monitoring** | **does not exist** | — |

## 1. Coverage Monitoring

**Can Bujji currently answer "which dates have complete coverage /
which are missing components / which are partial"?** Yes, **per-date,
on demand** — `ResearchCalendar` (Phase 18.7) already answers exactly
this, reusing `ResearchSessionReadiness` (18.5) which reuses
`HistoricalObservationStore.range()`/`range_by_prefix()` (18.1) —
confirmed live, re-running the same real 2026-08-13→15 query from Phase
18.7's own report and getting the identical, correct result again
(`PARTIAL`/`READY`/`NON_TRADING_DAY`).

**"Which dates have corrupted/inconsistent observations"** — **no
mechanism exists for this today**, and this is a genuinely different
question from the three above it. `ResearchCalendar`/`ResearchSessionReadiness`
answer *presence* ("is there a row"), never *correctness* ("is the row
internally consistent" — e.g. `high < low`, a negative OI, a strike
that doesn't match its own encoded identity). No existing primitive,
old or new, checks this. **A real, newly-identified gap for this
phase** — not carried forward from any prior 18.x report, because
nothing in 18.0–18.7 examined row-level internal consistency, only
presence/lineage/timestamp questions.

**No duplicate logic was written or needs to be** — reuse map:
`ResearchCalendar` → `ResearchSessionReadiness` → `MarketRealitySnapshot`
→ `HistoricalObservationStore`. One chain, no parallel implementation.

## 2. Data Freshness / Capture Verification

**"How does Bujji know a capture process completed successfully?"**
Re-confirmed by re-reading `capture_options_reality_session.py`'s own
stop condition (Phase 18.6 §1's own finding, re-verified unchanged this
phase): **`within_market_hours()` is a clock check, not a completeness
check.** The script logs `"Market hours ended mid-run -- stopping
cleanly"` and exits once `now.time() > 15:30` — it never re-queries the
store afterward to confirm the expected number of cycles/rows actually
landed. **"Market closed" and "all expected data captured" are
currently the same event in Bujji's own code, and they are not
logically the same fact** — a script that silently died at 11:00 IST
would leave no trace distinguishing it from a normal, complete session,
except that a human happens to notice fewer rows than expected.

**Reusable primitives for freshness verification, confirmed real**:
- `ingestion_run_id` — real, present on every row's lineage
  (`HistoricalLineage.ingestion_run_id`), and now (Phase 18.7)
  aggregated per date-range by `DatasetVersion.ingestion_run_references`.
  Tells you WHICH runs contributed, not whether a run was COMPLETE.
- **`IngestionRun` records** (Phase 17H.2, `record_ingestion_run()`) —
  carry `status` (`OK`/`NO_DATA`/`ERROR`/`PARTIAL`), `rows_returned`,
  `rows_accepted`, `rows_rejected` — a genuinely richer, already-real
  signal than presence alone. **Confirmed by fresh grep this phase**:
  only the 6 daily/intraday backfill scripts call this;
  `capture_options_reality_session.py` never does (re-confirming Phase
  18.7's own finding) — meaning options capture has **no queryable
  "did this run complete cleanly" record at all**, only the raw rows
  themselves and the script's own console log output (not persisted
  anywhere structured).
- **Row counts / observation ranges** — real and queryable
  (`store.count()`, `store.range()`), but nothing today compares an
  actual count against an EXPECTED count for a session (e.g., "a full
  options session should produce ~2,190 contracts × ~75 cycles" is
  knowledge that exists only in this project's own prior phase reports,
  never encoded as a checkable expectation in code).
- **`market_reality.completeness.measure()`** (Phase 17E) — **a real,
  substantive, and completely orphaned capability found this phase**:
  computes `expected_interval_count`, `received`, `missing_intervals`,
  and `classify_source_health()` — exactly "missing 5-minute intervals"
  detection. **Confirmed by grep: zero call sites anywhere outside its
  own module and its own tests.** Critically, it is scoped to Layer 0's
  `RawObservation` stream (event/capture timestamp fields specific to
  that model), **not** `HistoricalObservationStore` — the store where
  spot/futures/VIX/options 5-min data actually lives. It cannot be
  pointed at `HistoricalObservationStore` without adaptation (different
  underlying model shape), but its *logic* (expected-vs-received
  interval counting, source-health classification) is directly
  reusable in concept for a future health layer — a second, real
  "capability exists but isn't wired" finding, alongside `MarketCalendar`'s
  own (Phase 18.6, closed in 18.7).

## 3. Integrity Checks

Evaluated one at a time, against real code, not assumption:

| Check | Can Bujji detect it today? | Evidence |
|---|---|---|
| Duplicate observations | **Prevented at write time**, not "detected" after the fact | `ConflictingHistoricalObservationError`/idempotent-no-op (Phase 18.2 §9, re-confirmed unchanged) — structurally impossible for two *conflicting* facts to coexist under one natural key; a *retroactive scan* for anomalies is a different, unimplemented capability |
| Missing 5-minute intervals | **No** (for `HistoricalObservationStore`) | `completeness.measure()` exists but targets Layer 0 only (above) |
| Impossible timestamps | **Partially, at write time only, for some writers** | Options' `_validate_row` rejects missing/negative prices (Phase 17I.10) but not timestamp plausibility; no retroactive scan exists for any instrument |
| Instrument identity collisions | **Structurally prevented**, not actively monitored | The natural key (`instrument_identity, resolution, timestamp, source`) makes a true collision a write-time conflict, per above — same caveat: prevention ≠ monitoring |
| Option contract identity changes | **Not directly observed yet** (re-confirmed, unchanged from Phase 18.2/18.4 §2 and §14) — the composite identity is structurally stable by design (content-derived, not broker-symbol-derived), but no real expiry rollover has been captured yet to exercise this in practice |
| Fingerprint changes caused by data mutation | **Detectable only via manual re-run and comparison** — `fingerprint()` (Phase 18.3) would produce a different value if underlying data genuinely changed, but nothing automatically re-computes and compares a fingerprint against a prior stored one; there is nowhere a "prior" fingerprint is even persisted yet (Phase 18.4 §1's own finding, unchanged) |
| `reconstruction_version` mismatch | **Yes, real, enforced** — `DatasetVersion` (Phase 18.7) raises `ValueError` if a requested range spans mixed reconstruction versions, live-tested in that phase's own report |

**REALITY corruption vs. RECONSTRUCTION logic changes — the
separation this phase asks for, restated precisely**: Reality
corruption would be a genuine anomaly *inside* `HistoricalObservationStore`
itself (a bad row, a wrong value, a broken identity) — nothing in this
codebase currently scans for that class of problem at all, only
prevents *conflicting* writes at insert time. Reconstruction-logic
changes are *already* separated and guarded (`reconstruction_version`,
Phase 18.3/18.7) — a real, working distinction for the one class of
change this project has so far modified (Phase 18.3's own snapshot
identity hardening). **The asymmetry is real**: reconstruction-version
drift is guarded; raw-data corruption is not actively monitored at all,
only prevented for the one specific failure mode (conflicting writes)
SQL uniqueness can catch.

## 4. `ResearchDatasetHealth` — Design Concept (not implemented)

Every field justified against something that either already exists or
was found missing in §1–§3 above — no field invented without a stated
source:

```
ResearchDatasetHealth
  date_range:               {start, end}
  available_components:      Tuple[str,...]     # = DatasetVersion.included_components (18.7), reused
  missing_components:         per-date, from ResearchCalendarEntry.readiness.missing (18.5), reused
  completeness_status:         per-date READY/PARTIAL/FAILED/NON_TRADING_DAY (= ResearchCalendar status, 18.7), reused
  data_quality_status:          NEW CONCEPT, NO SOURCE TODAY -- §1's own finding
                                  (row-level internal consistency; nothing computes this)
  fingerprint_consistency:       PARTIALLY sourced -- fingerprint() (18.3) exists and is callable,
                                  but nothing PERSISTS a prior fingerprint to compare against (18.4 §1);
                                  today this field could only ever report "computed," never "unchanged
                                  since last check," without a persistence decision this phase does not make
  ingestion_lineage_status:       PARTIALLY sourced -- real for the 6 backfill scripts (`IngestionRun.status`),
                                  ABSENT for options (never calls `record_ingestion_run`, §2's own finding)
  reconstruction_version:         = DatasetVersion.reconstruction_version (18.7), reused, fully real
  certification_status:           = ResearchSessionReadiness.certified_lineage_available (18.5), reused,
                                  with the same coarse "snapshot-level, not per-instrument" caveat
                                  already disclosed in that phase's own report
```

**Two fields (`data_quality_status`) have no real source anywhere in
this codebase today** — correctly reflecting §1/§3's own finding, not
smoothed over. **Two more (`fingerprint_consistency`,
`ingestion_lineage_status`) are partially, not fully, sourced** — the
mechanism exists but a persistence/coverage gap (respectively) limits
what they could honestly report today. This is the intended output of
an audit: a design that is honest about which of its own fields are
real today and which are aspirational.

## 5. Automation Boundary

Consistent with Phase 18.6 §4's own boundary (re-applied here, not
re-derived):

**Allowed, safely automatable** (read-only, cannot corrupt anything by
construction):
- Running `ResearchCalendar`/`ResearchSessionReadiness` on a schedule
  and persisting the *report* (not the underlying data) somewhere
  queryable.
- Alerting on `FAILED` status for a real trading day (a genuine
  capture-gap signal).
- Computing and logging `fingerprint()` values for later comparison
  (still requires a persistence decision, per §4, but the *computation*
  itself is a pure read).

**Not allowed without explicit approval** (re-stated per this phase's
own instruction, each grounded in a specific real risk already found
in this project's history):
- Modifying historical Reality, deleting observations, silently
  "repairing" a detected anomaly — no repair mechanism exists today
  regardless (§3's own finding: no anomaly detection exists to repair
  FROM), but the boundary is stated explicitly for when one eventually
  does.
- Rewriting a fingerprint to match new data — this would defeat the
  entire purpose `fingerprint_consistency` (§4) exists to serve; a
  changed fingerprint is itself the finding, never something to
  silently correct.
- Any backfill run (Phase 18.5's own precedent — the 2026-08-14 gap
  was closed by a deliberate, reviewed human action, not a scheduled
  job) — a health monitor should **detect and report** a gap, and stop
  there.

## 6. Customer/Product Perspective — "Backtest Jan 2020 to today"

Directly tested, not estimated: built a real `DatasetVersion` for
`2020-01-01` → `2020-01-10` (10 real days, `DAILY` resolution — the
cheapest possible real case) against the live database.

```
ready_dates:     ()
incomplete_dates: (all 10 dates)
included_components: (futures, spot, vix)   -- options correctly absent (pre-2026-08-14, honest)
wall-clock time: 1m19s (real, measured)
```

**Two findings, both real**:

1. **Correctness held at scale**: every one of the 10 real 2020 dates
   correctly reported `incomplete` (missing options, which genuinely
   never existed then) — no fake completeness leaked in even across a
   full range, consistent with every prior 18.x phase's own finding.
2. **A genuine, newly-discovered scaling blocker**: **10 days took 79
   real seconds.** The customer scenario asks about `2020-01-01` →
   `2026-08-14` — roughly **2,400 calendar days**. Extrapolating
   linearly from the measured rate (`~7.9s/date`), a literal
   `build_dataset_version()` call across that full range would take
   **on the order of 5+ hours of wall-clock time** — not viable as a
   live, synchronous answer to a customer's question, regardless of
   whether the underlying data were otherwise ready. The likely cause,
   read directly from `dataset_version.py`'s own implementation: 4
   separate `_ingestion_run_ids()` store queries per date, on top of a
   full `build_market_reality_snapshot()` call per date, each issuing
   its own SQLite queries with no batching across the date range —
   **not a data problem, an unbatched-query problem**, real and
   measured, not a corruption or completeness issue at all, but
   equally load-bearing for whether this architecture is
   "commercial-platform-ready" today.

**Direct answer to this phase's own six questions**:

| Question | Answer today |
|---|---|
| Do we have the required data? | Answerable per-date (`ResearchCalendar`), but not efficiently at "6 years" scale (above) |
| Which dates are complete? | Correctly computable, same scale caveat |
| Which market instruments exist? | Yes, real, `included_components` |
| Is the dataset reproducible? | Yes, for unchanged data (`fingerprint()`, Phase 18.3, proven) |
| What exact data version will be used? | Yes, `dataset_version_id` (Phase 18.7), but see §4's caveat on `fingerprint_consistency` — reproducible RIGHT NOW, not provably stable against future silent data drift, since no prior fingerprint is persisted anywhere to check against |

## Missing Capability List, Severity-Classified

**Blocking** (a commercial backtest cannot safely launch without
these):
- Query performance at multi-year date-range scale (§6) — the single
  most concrete, measured finding this phase produced. A customer
  cannot be told "checking..." for 5 hours.
- Row-level data-quality/corruption detection (§1, §3) — currently
  zero coverage; a customer-facing platform promising "trustworthy
  data" cannot rest on presence-checking alone.
- Options' single-day coverage island (carried forward, unchanged,
  from Phase 18.4/18.5/18.6 — still exactly one real calendar date).

**Important** (materially weakens the trust story, not an immediate
launch-blocker):
- Options has no `IngestionRun` ledger at all (§2) — no queryable
  "did today's capture complete cleanly" signal beyond raw row
  presence.
- No persisted fingerprint history (§4/§6) — reproducibility is
  provable today but not provably STABLE over time without a
  persistence decision.
- `market_reality.completeness.measure()` remains unwired to the store
  that actually matters (§2) — real logic, wrong target.

**Future** (real, correctly out of scope for a health-monitoring
layer specifically):
- Futures synthetic-continuous-contract identity (carried forward,
  unchanged since Phase 18.0 — a data-architecture decision, not a
  monitoring gap).
- The orphaned `bujji.replay` pipeline (carried forward, unchanged).
- Per-instrument (rather than snapshot-level) certification-status
  granularity (Phase 18.5's own disclosed limitation, unchanged).

## Recommended Next Phase

**Not a health-monitoring implementation phase yet** — a narrower,
prerequisite one: **address the query-performance finding first**
(§6), since every other health-monitoring capability this audit
designs (§4's `ResearchDatasetHealth`) would inherit the same
per-date, unbatched query cost and be equally unusable at real
customer-facing scale. A `data_quality_status` design pass (§1, the
one field this audit found with zero existing source) is the second
priority, once health-checking itself is fast enough to run.

## Final Verdict

**Not yet — the historical research universe is honest, but not yet
trustworthy at commercial scale.** Every mechanism built through Phase
18.7 tells the truth about what it checks (proven repeatedly, live,
across six audit phases) — the "no fake completeness" discipline has
held under adversarial testing every single time it was tried. What
this phase found, for the first time, is that **the mechanisms
querying that truth do not yet scale to the date ranges a real
commercial customer would actually request**, and that **one entire
category of failure (data corruption within an already-accepted row)
has no detection coverage at all**, distinct from and additional to
every completeness/reproducibility gap already known from Phases
18.0–18.7.
