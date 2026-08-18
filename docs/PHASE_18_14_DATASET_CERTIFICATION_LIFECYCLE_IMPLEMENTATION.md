# Phase 18.14 — Dataset Certification, Lifecycle Governance & Backtest Eligibility Implementation

**Status: IMPLEMENTED, TESTED, LIVE-VALIDATED.** Closes all seven
proven gaps from Phase 18.13's audit. No changes to Reality models,
`HistoricalObservationStore`, `MarketRealitySnapshot`, reconstruction
logic, the fingerprint algorithm, or any ingestion path — confirmed by
diff and by regression.

**A real incident occurred and was corrected during this phase's own
live demonstration — disclosed in full in §6, not hidden.**

---

## Files Created / Changed

- **`bujji/market_reality_snapshot/dataset_lifecycle_store.py`** (new) —
  `DatasetLifecycleStore`: WAL SQLite, append-only event log.
- **`bujji/market_reality_snapshot/dataset_artifact.py`** (extended) —
  added lifecycle states/transitions, `run_certification_checks()`,
  `DatasetManifest`/`build_manifest()`,
  `BacktestDatasetEligibility`/`check_backtest_eligibility()`, and a
  `parent_artifact_id` field on `DatasetArtifact`.
- **`tests/test_dataset_lifecycle_governance.py`** (new) — 24 tests.

`DatasetArtifact`'s own core fields, `DatasetIdentity`,
`DatasetVersion`, `MarketRealitySnapshot`, and `HistoricalObservationStore`
are byte-for-byte unchanged — every addition here is either a new
field with a safe default or a new function/model in the same module.

## 1. Lifecycle State (Gap #4)

Six states, exactly as specified: `CREATED`, `VALIDATING`,
`CERTIFIED`, `PUBLISHED`, `DEPRECATED`, `INVALID`.

**Design decision, and why**: state is **not** a mutable column on
`DatasetArtifact` (which stays `frozen=True`, INSERT-only, exactly as
Phase 18.12 built it — "do not mutate historical research content").
Instead, `DatasetLifecycleStore` is a **separate, append-only event
log** keyed by `artifact_id`; the current state is always the latest
event, never a stored, overwritable field. This mirrors
`IngestionRun`'s own append-only ledger pattern (Phase 17H.2), reused
as a pattern, not duplicated as code.

`ALLOWED_LIFECYCLE_TRANSITIONS` is an explicit, closed table:
```
CREATED    -> VALIDATING, INVALID
VALIDATING -> CERTIFIED, INVALID
CERTIFIED  -> PUBLISHED, DEPRECATED, INVALID
PUBLISHED  -> DEPRECATED
DEPRECATED -> (terminal)
INVALID    -> (terminal)
```
`transition_lifecycle_state()` re-reads the CURRENT state fresh (never
cached) before validating — any transition not in this table, or any
attempt to transition an artifact with no recorded history, raises
`InvalidLifecycleTransitionError`.

**Live-proven, not just unit-tested**: attempting `CREATED → PUBLISHED`
directly on the real VPS was rejected with the exact expected message;
`INVALID`/`DEPRECATED` correctly refuse any further transition. 6 of the
24 new tests exercise this state machine directly, including a
parametrized test proving every non-adjacent skip from `CREATED` is
rejected.

**Can an invalid dataset accidentally become usable?** Closed: an
artifact created via `create_artifact(..., lifecycle_store=...)`
starts in `CREATED`, and `LIFECYCLE_STATES_ALLOWING_RESEARCH_USE =
(CERTIFIED, PUBLISHED)` — `check_backtest_eligibility()` (§5) refuses
any artifact not in one of those two states, regardless of how
complete or correct its underlying data actually is.

## 2. Certification Gate Integration (Gaps #2, #3)

`run_certification_checks()` — genuinely NEW research-suitability
checks, distinct from `verify_artifact()`'s own consistency checks
(Phase 18.13 §1's own named distinction). Reuses, invents nothing:

- **`DatasetVersion.ready_dates`/`incomplete_dates`** (Phase 18.7) —
  already computed, never previously consulted by artifact creation
  (Phase 18.13's own finding) — now drives `all_dates_ready` and
  `has_required_observations`.
- **The real `CertificationGate`** (Phase 17E/17I, unmodified) — called
  directly, per required instrument, using each instrument's REAL
  `access_method` constant read straight from the actual ingestion
  scripts (`direct_sdk_fyers_historical_rest` /
  `direct_sdk_fyers_historical_intraday_rest` /
  `direct_sdk_fyers_optionchain_reality`), not guessed. **A real,
  live-confirmed finding this phase**: `CertificationGate.status_for()`
  requires an actual, dated certification artifact FILE on disk — a
  row's own `certification_status` field is not sufficient. A test
  against an isolated temp store with no cert artifacts correctly
  reports `CERTIFICATION_MISSING` for every instrument even though
  every row claims `CERTIFIED_AVAILABLE` lineage — proving this check
  cannot be satisfied by fabricated row-level claims alone, only by a
  real certification run.
- **`artifact.certification_references`** (Phase 18.12) — reused as a
  simple non-empty presence check.

`CertificationCheckResult.passed` requires ALL of: required
observations present, required instruments present, resolution has
real data, every date in range `READY`, certification references
non-empty, AND the live `CertificationGate` reporting
`CERTIFIED_AVAILABLE` for every required instrument.

## 3. Parent Artifact Relationship (Gap #5)

`DatasetArtifact.parent_artifact_id: Optional[str] = None` — additive,
caller-supplied, never auto-matched by content (this function does not
guess which prior artifact a new one "extends"). Confirmed:

- **Never mutates the parent** — a dedicated test creates a parent,
  then a child pointing at it, then reloads the parent and asserts it
  is byte-for-byte unchanged.
- **Two children can share one parent** without conflict — `artifact_id`
  is independently unique per creation event regardless of
  `parent_artifact_id` (re-confirmed: sibling artifacts get distinct
  `artifact_id`s, same `parent_artifact_id`).
- Genuinely different coverage between parent and child correctly
  produces a genuinely different `dataset_id` — the content-identity
  guarantee from Phase 18.11/18.12 is undisturbed by this addition.

"What changed between these dataset versions" is answerable by a
caller today by comparing two artifacts' `fingerprint`/`ingestion_run_references`
via their now-linked `parent_artifact_id` — no new diffing mechanism
was built (out of this phase's own minimal scope), but the pointer
that makes such a comparison discoverable now exists.

## 4. Dataset Manifest (Gap #6)

`DatasetManifest`/`build_manifest()` — generated **entirely** from
already-real `DatasetArtifact` fields, confirmed by reading the
function: no new store, no new reconstruction. `instruments` (Phase
18.13's own confirmed gap — never copied onto the artifact) must be
supplied by the caller rather than re-derived by a costly rebuild,
since a manifest is a presentation view, not a second reconstruction.
`certification_status` reflects the artifact's real, current lifecycle
state (never a fabricated `"CERTIFIED"` default) — `"UNKNOWN"` when no
`lifecycle_store` is supplied, proven by a dedicated test.

**Live-generated for the real 2026-08-14 dataset**:
```json
{
  "dataset_name": "NIFTY_OPTIONS_5MIN_2026_08_14",
  "dataset_id": "38e3bb1b5bb9133df997430a87fea6b79a828d8d5c3b92d3adc8d325b54d652a",
  "artifact_id": "ART-ab034f9b31034dc1bc93524b611bb019",
  "coverage": {"from": "2026-08-14", "to": "2026-08-14"},
  "resolution": "FIVE_MINUTE",
  "instruments": ["spot", "futures", "vix", "options"],
  "reconstruction_version": "18.3.0", "schema_version": "1.2.0",
  "certification_status": "PUBLISHED",
  "code_identity": "59895b87439a+dirty",
  "ingestion_lineage_summary": {"ingestion_run_count": 6, "certification_reference_count": 4}
}
```
Note `dataset_id` matches the value independently computed in Phase
18.12's own demonstration for the identical content — reconfirming
determinism across sessions, not just within one.

## 5. Backtest Dataset Eligibility (Gap #7)

`check_backtest_eligibility()` — the single read-only "is this dataset
safe to use" call a future backtester should make first. Combines
existing checks, invents no new verification mechanism:
`verify_artifact()` (fingerprint/schema/lineage), lifecycle state
(§1), `run_certification_checks()` (§2), and `code_identity.resolved`.

**Live-proven true**, for the real, fully-published 2026-08-14
artifact: `eligible: true`, all eight sub-checks `true`, zero reasons.

**Live-proven false, five distinct ways**, each a real rejection with
real reasons, not a hypothetical:
1. Empty dataset (a real market holiday, 2026-08-01) — certification
   fails on `no_required_observations`/`no_certification_references`;
   eligibility correctly `False`.
2. A real pre-options-era date (2020-01-06) — certification fails on
   `required_instruments_missing`; eligibility `False`.
3. Good, complete data, but still `CREATED` (never advanced) —
   eligibility `False` on `lifecycle_state_CREATED_does_not_allow_research_use`,
   regardless of data quality.
4. An invalid lifecycle transition (`CREATED → PUBLISHED`) — correctly
   raises before any eligibility question is even reached.
5. A genuinely different value under an existing natural key —
   correctly **refused at the Reality-tier write itself**
   (`ConflictingHistoricalObservationError`), the strongest possible
   proof that a published artifact's underlying facts cannot silently
   drift: the immutability guarantee (Phase 18.2 §9/§10) makes this
   class of "changed fingerprint" scenario structurally unreachable,
   not merely detected after the fact. (Fingerprint-change detection
   itself, for the reachable case — new data arriving at a
   previously-unoccupied timestamp inside an already-covered window —
   remains proven via Phase 18.12's own
   `test_verify_artifact_detects_data_that_changed_since_creation`,
   re-run and still passing this phase.)

## 6. Real Demonstration — and a Real Incident, Disclosed

Ran the full 8-step demonstration against real 2026-08-14 NIFTY
options data on the live VPS: create → validate → certify → publish →
manifest → eligibility → reload after a genuinely separate `python3`
process → re-verify. All eight steps succeeded exactly as designed
(full output in the working session; `dataset_id`, `fingerprint`, and
`eligible: true` all reproduced identically after the real restart).

**Incident**: while constructing the "changed fingerprint" failure
demonstration, an early attempt mistakenly used the **live production**
`historical_observations.db` (instead of an isolated temp store) to
inject a synthetic bar (`NSE:NIFTY50-INDEX`, `2026-08-14T09:16:00+05:30`,
fabricated OHLC `1/1/1/1`, `ingestion_run_id=RUN-drift`). Because the
timestamp did not collide with any real natural key, the write
succeeded and persisted into production data — a real violation of
this project's own Reality-tier integrity, caused by test-scripting
carelessness, not by any flaw in the immutability guarantee itself
(the guarantee worked exactly as designed against a *second* attempt
at the same timestamp, correctly raising `ConflictingHistoricalObservationError`).

**Caught immediately** (before this report was written), disclosed to
the user directly, and corrected only after explicit user approval:
the single fabricated row was removed via a scoped `DELETE ... WHERE
observation_id = 'OBS-f6f82f19d382777906d4cd4f'` — verified by exact
row-count delta (670,164 → 670,163, exactly −1) and by re-reading the
real `09:15`/`09:20`/`09:25` spot bars for 2026-08-14 to confirm they
were untouched (75 total 5-minute spot rows for that date, unchanged
from before this phase). No other row was touched. All subsequent
demonstration work in this phase used exclusively isolated
`tempfile.TemporaryDirectory()` stores, and the new test file
(`test_dataset_lifecycle_governance.py`) does the same throughout —
its own module docstring states this explicitly as a lesson from this
incident, not left implicit.

## 7. Tests and Regression

24 new tests in `tests/test_dataset_lifecycle_governance.py`, covering
every category this phase required: lifecycle transition rules (6),
certification blocking (3), eligibility decisions (4), manifest
correctness (3), parent artifact relationship (2), and existing-artifact/
regression compatibility (3), plus a structural no-mutation check and
an "unknown transition target" check.

- New test file: **24 passed**.
- Full pre-existing suite (every file from Phases 18.1–18.13,
  including `test_dataset_artifact_registry.py`,
  `test_research_dataset_governance.py`,
  `test_market_reality_snapshot_readiness.py`): **re-run unmodified,
  all still pass.**
- **Full regression: 5,751 → 5,775 passed, 0 failed** (exactly the 24
  new tests, zero regressions elsewhere).

## Final Outcome

Bujji can now answer, deterministically:

> "Can this historical dataset legally and technically support a
> customer backtest?"

with `check_backtest_eligibility(artifact_id, ...) →
BacktestDatasetEligibility(eligible: bool, reasons: [...])` — a
single, read-only, real function call combining lifecycle state,
certification (backed by the real `CertificationGate`, not a
self-reported claim), completeness, reproducibility, code identity,
and lineage validity. Live-proven both to say **yes** (the real,
published 2026-08-14 dataset) and to say **no**, correctly and
specifically, across five distinct real failure scenarios.
