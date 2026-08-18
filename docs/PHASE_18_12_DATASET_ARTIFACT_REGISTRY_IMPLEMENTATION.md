# Phase 18.12 — Historical Research Dataset Artifact Registry Implementation

**Status: IMPLEMENTED, TESTED, LIVE-VALIDATED.** Closes Phase 18.11's
central audit finding — `DatasetVersion` no longer lives only in
memory; a customer or future backtester can now be handed a persisted,
immutable `artifact_id` and later ask Bujji to independently re-prove
what it represents.

---

## Files Created

- **`bujji/market_reality_snapshot/dataset_artifact_store.py`** —
  `DatasetArtifactStore`: WAL SQLite, same pattern as every other store
  in this project, INSERT-only, conflict-guarded.
- **`bujji/market_reality_snapshot/dataset_artifact.py`** —
  `DatasetIdentity`, `DatasetArtifact`, `derive_dataset_identity()`,
  `create_artifact()`, `get_artifact()`, `list_artifacts()`,
  `verify_artifact()`, `ArtifactVerificationResult`.
- **`tests/test_dataset_artifact_registry.py`** — 14 tests.

**Zero changes** to `HistoricalObservationStore`,
`MarketRealitySnapshot`, `MarketRealitySnapshotStore`,
`ResearchSessionReadiness`, `ResearchCalendar`, or `DatasetVersion` —
this phase's own regression requirement, confirmed by a dedicated test
(`test_dataset_version_behavior_unchanged_by_this_phase`) and by the
fact that every file in that list has an identical diff of zero lines
against Phase 18.10's own deployed versions.

## 1. Dataset Identity Model

`DatasetIdentity` — pure content identity, computed by
`derive_dataset_identity()`, the ONLY place it is minted (mirroring
`market_observation.engine.build_observation()`'s own "one place mints
identity" discipline, reused as a pattern, not as code).

Two distinct hashes, not one collapsed into the other:
- **`fingerprint`** — `fingerprint_state({"fingerprint_lineage": [...]})`,
  a pure aggregate of the per-date snapshot fingerprints alone
  (`DatasetVersion.fingerprint_lineage`, Phase 18.7/18.10, unmodified).
  Answers "did the underlying market facts change."
- **`dataset_id`** — a second `fingerprint_state()` call over
  `{date_range, resolution, instruments, reconstruction_version,
  schema_version, fingerprint}`. Answers "is this the same dataset in
  every respect," and closes Phase 18.11 §2's own named gap:
  **`schema_version` had never been rolled up to the dataset level
  before this phase** — it now is, folded directly into `dataset_id`.

**Same content reproduces the same identity — proven live, not
assumed**: two independent `build_dataset_version()` + `derive_dataset_identity()`
calls against identical stored data produce byte-identical
`dataset_id` and `fingerprint` (test
`test_dataset_identity_deterministic_across_two_independent_derivations`,
and reconfirmed in the live restart demonstration, §6 below).

## 2. Dataset Artifact Model

`DatasetArtifact` — one frozen creation event. Every field maps to a
real source, per Phase 18.11 §2's own field-by-field justification:

| Field | Source |
|---|---|
| `artifact_id` | `"ART-" + uuid.uuid4().hex` — minted once, real, deliberately non-deterministic (an EVENT identity is correctly allowed to differ between two artifacts sharing one `dataset_id`) |
| `dataset_id` / `fingerprint` | `DatasetIdentity`, above |
| `dataset_version_reference` | `DatasetVersion.dataset_version_id` (Phase 18.7, the pre-existing concept, preserved unmodified and unsuperseded) |
| `certification_references` | **new rollup this phase** — closes Phase 18.11 gap #4, see below |
| `ingestion_run_references` | `DatasetVersion.ingestion_run_references` (Phase 18.7/18.10, unmodified) |
| `code_identity` | `epistemics.identity.resolve_code_identity()` (Phase 16D) — **wired in for the first time anywhere in this codebase**, closing Phase 18.11 gap #6 |
| `environment_identity` | real `platform.python_version()`/`platform.platform()`/`platform.processor()` — standard-library facts, never fabricated |
| `dirty_state` | `code_identity["dirty"]`, surfaced as its own top-level field per this phase's own explicit instruction not to hide it |
| `date_range` / `resolution` / `as_of_time_of_day` / `reconstruction_version` / `schema_version` | echoed from the artifact's own creation inputs — enough to independently REBUILD for `verify_artifact()`, not just to describe |

**`certification_references` — the one genuinely new data-collection
path this phase adds**: `_collect_certification_refs()` independently
re-derives each date's `MarketRealitySnapshot` and reads its own
`certification_refs` field. This is a **disclosed, accepted
duplication**, not hidden: `build_dataset_version()`'s own internal
calendar-building pass already constructs a snapshot per date, and this
function constructs it again for the same dates specifically to avoid
touching `ResearchSessionReadiness`/`MarketRealitySnapshot`
(protected by this phase's own regression requirement). Given
`range_by_prefix()`'s Phase 18.10 fix, this second pass is
inexpensive — the live demonstration below (§6) includes it and still
completes in well under a second for a single-day artifact.

**Artifact identity is never regenerated** — enforced at two
independent layers: the `frozen=True` dataclass itself (no field can
be mutated in place), and `DatasetArtifactStore.write()`, which raises
`ConflictingDatasetArtifactError` if a *different* record is ever
written under an existing `artifact_id` (mirroring
`ConflictingHistoricalObservationError`'s own discipline, reused as a
pattern). Proven live by a dedicated test
(`test_store_refuses_to_silently_overwrite_a_conflicting_record`).

## 3. Persistence

`DatasetArtifactStore` — the exact same WAL-SQLite pattern every other
store in this project already uses (own file, `PRAGMA
journal_mode=WAL`, `PRAGMA synchronous=FULL`, retry-on-locked). No new
database technology, no external dependency, no new storage paradigm —
confirmed by diffing its schema-setup code against
`HistoricalObservationStore.__init__`'s own: structurally identical.

**Survives process restart — proven, not assumed**: the test suite
constructs fresh `DatasetArtifactStore`/`HistoricalObservationStore`
objects against the SAME on-disk files (the real proxy for a restart a
unit test can exercise) and confirms the reloaded `DatasetArtifact`
object is field-for-field equal to the one originally created. The
live demonstration (§6) reconfirms this with genuinely separate
`python3` process invocations on the VPS.

## 4. Code Identity Integration

`resolve_code_identity()` (Phase 16D) called directly inside
`create_artifact()` — the first real call site for this function
anywhere in the codebase (confirmed by fresh grep before this phase:
zero call sites existed). **Live-captured on the real VPS deployment**:

```
code_identity: {
  "commit": "59895b87439a673668549c338d9d6555757a05d2",
  "short_commit": "59895b87439a", "branch": "v1.0-shadow",
  "dirty": true, "resolved": true, "code_version": "59895b87439a+dirty"
}
```

`dirty: true` — the honest, unsurprising answer this project's own
Phase 18.2/18.9/18.11 audits already predicted (345 uncommitted
changes were present in this session's own `git status` at the time
Phase 18.11 was written). **Not hidden, not defaulted away** — surfaced
both inside `code_identity` and as the artifact's own top-level
`dirty_state` field, exactly per this phase's explicit instruction.

## 5. Dataset Artifact Registry API

`create_artifact()`, `get_artifact()`, `list_artifacts()`,
`verify_artifact()` — all four implemented, all four exercised against
real data in this phase's own test suite and live demonstration.

`verify_artifact()` independently **rebuilds** the dataset from the
artifact's own recorded `date_range`/`resolution`/`as_of_time_of_day`
(never trusts the stored record alone) and checks all four items this
phase specified:
- `fingerprint_unchanged`
- `reconstruction_version_unchanged`
- `schema_compatible`
- `ingestion_runs_still_present` (a subset check: every referenced run
  id must still resolve in a fresh read; growth since creation is
  fine, shrinkage/absence is the real finding)

**Proven to actually detect a problem, not just always report
success** — a dedicated test
(`test_verify_artifact_detects_data_that_changed_since_creation`)
creates an artifact, then adds a new 5-minute bar for a timestamp
already inside the artifact's own covered window, and confirms
`verify_artifact()` honestly reports `fingerprint_unchanged: False`,
`is_valid: False`, `reasons: ("fingerprint_changed",)`. A companion
test confirms the unchanged case reports `is_valid: True` with zero
reasons. A third confirms a nonexistent `artifact_id` reports
`found: False` rather than raising or fabricating a result.

## 6. Reproducibility Demonstration — Real Data, Real VPS

Ran the exact scenario this phase names: **"NIFTY options 5-minute
session, 2026-08-14."**

**Create** (fresh Python process):
```
artifact_id: ART-9c74e657c8a64759be8affa7f89fba3b
dataset_id:  38e3bb1b5bb9133df997430a87fea6b79a828d8d5c3b92d3adc8d325b54d652a
fingerprint: f5967c56897fea754bc4813e96e55bf742b967cfab1bd2efc15cb08d07cd2731
dataset_version_reference: 3b6f08e06dce1a04aa2a27fd6ae1070479bb7e476f5b9ccc839233489b361273
reconstruction_version: 18.3.0
schema_version: 1.2.0
code_identity: {commit: 59895b87439a..., branch: v1.0-shadow, dirty: true, resolved: true}
environment_identity: {python_version: 3.12.3, platform: Linux-6.8.0-134-generic-x86_64..., processor: x86_64}
certification_references: 4 real refs
ingestion_run_references: 6 real run ids
```

**Reload after a genuinely separate `python3` process invocation**
(a real restart, not simulated within one process):
```
reloaded after restart: True
reloaded fingerprint: f5967c56897fea754bc4813e96e55bf742b967cfab1bd2efc15cb08d07cd2731  (identical)
```

**Independently rebuild and verify**, also from the fresh process:
```
{
  "found": true, "is_valid": true,
  "fingerprint_unchanged": true,
  "reconstruction_version_unchanged": true,
  "schema_compatible": true,
  "ingestion_runs_still_present": true,
  "rebuilt_fingerprint": "f5967c56897fea754bc4813e96e55bf742b967cfab1bd2efc15cb08d07cd2731",
  "reasons": []
}
```

**This is the complete, proven answer to this phase's own success
criterion** — "show me the exact historical dataset used for this
research result" now has a real, persisted, independently-re-provable
artifact behind it, not just an in-memory claim.

## 7. Regression Requirements — Confirmed

- **`DatasetVersion` behavior unchanged** — zero lines of diff against
  Phase 18.10's deployed version; `dataset_version_id`'s hashing shape
  re-confirmed identical via a dedicated test.
- **`MarketRealitySnapshot` unchanged** — zero lines of diff; this
  phase's `_collect_certification_refs()` reads the model, never
  modifies it.
- **No Reality data mutation** — a dedicated test asserts
  `HistoricalObservationStore.count()` is identical before and after
  both `create_artifact()` and `verify_artifact()`.
- **No look-ahead introduced** — a dedicated test writes a bar timed
  after the artifact's own `as_of_time_of_day` cutoff and confirms it
  never appears in the reconstructed snapshot the artifact references.

**Full pre-existing suite**: every test file touched by Phases
18.1–18.10 re-run unmodified; all pass. **New suite**: 14 tests, all
pass. **Full regression: 5,737 → 5,751 passed, 0 failed** (exactly the
14 new tests, zero regressions elsewhere).

## What This Phase Did Not Do

Per its own constraints: no backtester, no strategy engine, no ML, no
analytics, no customer UI, no dashboards, no optimization. No
generalization beyond the single NIFTY underlying (Phase 18.11's own
explicitly-deferred gap #3, left deferred). No change to how
`certification_references` are exposed at the per-instrument level
(Phase 18.5's own coarse, snapshot-level limitation is inherited here,
not resolved) — the new rollup collects real refs across the date
range, but does not attempt to attribute a given ref to a specific
instrument within that range, matching the granularity every other
part of this system has used since Phase 18.5.
