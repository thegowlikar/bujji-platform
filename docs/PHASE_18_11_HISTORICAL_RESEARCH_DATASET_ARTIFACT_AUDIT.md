# Phase 18.11 — Historical Research Dataset Artifact & Reproducibility Layer Audit

**Status: AUDIT ONLY. Zero code changes.** Every claim is backed by a
direct code read or a live query against the real VPS repository, run
fresh for this phase.

---

## 1. Current Dataset Identity

**What uniquely identifies a historical dataset today?**
`DatasetVersion.dataset_version_id` (Phase 18.7) — a SHA-256
`fingerprint_state()` hash over `{coverage_start, coverage_end,
resolution, included_components, reconstruction_version,
fingerprint_lineage}`. This is real and it works — re-confirmed this
session (Phase 18.10) that the identical call reproduces the identical
hash.

**Can two identical datasets created months apart prove they are
identical?** **Only if someone still has the code and database to
rebuild it and compare by hand.** `dataset_version_id` is **never
persisted anywhere** — confirmed by fresh grep this phase:
`DatasetVersion` is referenced only inside
`bujji/market_reality_snapshot/` itself (`builder.py`, `models.py`,
`readiness.py`, `dataset_version.py`) — no store, no file, no consumer
writes it down. `build_dataset_version()` is a pure function: call it,
get an object, and when the Python process exits, that object is gone.
Two datasets built months apart CAN be proven identical **if you
happen to have recorded the first one's `dataset_version_id` somewhere
outside this codebase** (a lab notebook, a prior phase's report) and
rebuild the second to compare — which is exactly how this audit itself
verified fingerprint stability in Phase 18.10 (by checking a value
recorded in Phase 18.5's own report). **That is not a system
capability — it is this project's own reports serving as an ad hoc,
manual ledger.** A real customer-facing system has no equivalent.

**Can two datasets built with different reconstruction logic be
distinguished?** **Yes, and this is real, working, and already
tested** — `build_dataset_version()` raises `ValueError` if a
requested range spans mixed `reconstruction_version` values (Phase
18.7, live-tested), and `fingerprint()` deliberately excludes
`reconstruction_version` from its own hash specifically so
`(fingerprint, reconstruction_version)` can be compared as a pair
(Phase 18.3's own documented design decision). This part of the
question is **solved**, not a gap.

## 2. Dataset Artifact Design (Minimum, Not Implemented)

Every field evaluated against a real, already-existing source — this
audit adds no field without saying exactly where its value would come
from:

```
DatasetArtifact
  dataset_id                     NEW -- see below; distinct from
                                        dataset_version_id (a content
                                        hash) the way an invoice NUMBER
                                        differs from a checksum of its
                                        contents
  created_at                      = DatasetVersion.created_at (18.7), real
  date_range                       = DatasetVersion.coverage_start/end (18.7), real
  resolution                       = DatasetVersion.resolution (18.7), real
  underlying universe               NOT A REAL FIELD ANYWHERE TODAY --
                                     "NIFTY" is hardcoded
                                     (`OPTIONS_UNDERLYING`, `SPOT_SYMBOL`
                                     etc. in builder.py) and never
                                     threaded through as a parameter;
                                     every dataset built by this
                                     codebase today is implicitly
                                     NIFTY-only. A real, if currently
                                     moot (single-universe), gap.
  included instruments              = DatasetVersion.included_components (18.7), real
  dataset_version_id                 = DatasetVersion.dataset_version_id (18.7), real
  snapshot fingerprints               = DatasetVersion.fingerprint_lineage (18.7), real
  reconstruction_version               = DatasetVersion.reconstruction_version (18.7), real
  certification references              PARTIALLY real -- ResearchSessionReadiness
                                         only exposes a coarse, snapshot-level
                                         boolean (`certified_lineage_available`,
                                         18.5's own disclosed limitation,
                                         unchanged through every subsequent
                                         phase); the actual `certification_ref`
                                         STRINGS exist on `MarketRealitySnapshot.
                                         certification_refs` but DatasetVersion
                                         does not currently roll them up across
                                         its date range (a real, small, additive
                                         gap -- the same class of fix Phase 18.10
                                         just made for ingestion_run_ids)
  ingestion lineage references            = DatasetVersion.ingestion_run_references (18.7), real
  schema versions                          PARTIALLY real -- MarketRealitySnapshot.
                                          schema_version (currently "1.2.0") exists
                                          per-snapshot but DatasetVersion does not
                                          currently surface it at all (a real,
                                          small omission -- every other
                                          per-snapshot field Phase 18.7 rolled up,
                                          this one was not)
  creation environment/version              NOT WIRED, though the mechanism
                                            EXISTS -- `bujji.epistemics.identity.
                                            resolve_code_identity()` (Phase 16D,
                                            re-confirmed by fresh grep this
                                            phase: zero imports anywhere in
                                            `market_reality_snapshot/`). Its
                                            own `dirty` flag would today report
                                            `True` for nearly any real artifact
                                            built on this deployment -- this
                                            session's own `git status` shows
                                            345 changed lines/files uncommitted
                                            right now. An honest artifact would
                                            need to say so, not omit the field.
```

**`dataset_id` vs. `dataset_version_id` — a real, necessary
distinction this audit is introducing, not inventing gratuitously**:
`dataset_version_id` is a pure content hash — recompute the same
inputs, get the same value, by design (that is its whole purpose,
proven in Phase 18.7/18.10). `dataset_id` would be **the identity of
one specific, published, immutable ARTIFACT** — assigned once, at
creation time, and never recomputed. Two artifacts with the same
`dataset_version_id` (same underlying data, same logic) but created at
different times for different customers would legitimately have
**different** `dataset_id`s (different creation events) while
correctly sharing one `dataset_version_id` (proving they used the same
facts). Conflating the two would either (a) prevent two customers from
ever getting their own distinct, timestamped artifact record for
identical underlying data, or (b) let `dataset_id` silently change if
the artifact were ever "recomputed" instead of stored — neither is
correct. **This distinction is the single most important design
finding of this audit** — everything else in this section already had
a home in `DatasetVersion`; this one concept did not exist before this
audit named it.

## 3. Immutability Requirement

**Can dataset artifacts be modified after creation?** Not applicable
today — none exist to modify (§1's own finding: `DatasetVersion` is
never persisted). If one existed as a stored object, nothing in this
codebase currently defines an update path for it, but that is because
nothing defines ANY path for it yet, not because immutability was
deliberately engineered.

**Can historical observations mutate?** **No — re-confirmed, not
re-cited.** Fresh grep this phase against `historical_reality/store.py`
and `market_reality/store.py`: zero `UPDATE`/`DELETE` statements exist
in either file (unchanged since Phase 18.2 §9/§10's original finding,
now verified for the third time across separate phases). Every write
is `INSERT`; a conflicting fact under an existing natural key raises
`ConflictingHistoricalObservationError` rather than overwriting.

**Can fingerprints detect changes?** **Yes, proven mechanism, never
yet exercised as a change-detector in practice** — `fingerprint()`
would produce a different hash for genuinely different underlying
data (Phase 18.3/18.10's own tests confirm this indirectly: identical
data → identical hash, and the mechanism is pure/deterministic by
construction). But detecting a change requires comparing a NEW
fingerprint against an OLD one, and **no old one is ever kept** (§1's
own finding, restated in this specific context) — the capability to
detect exists; the capability to actually catch a real drift event
does not, because there is nothing to compare against.

**Are existing storage mechanisms sufficient?** For raw Reality:
**yes, proven immutable, sufficient as-is.** For a dataset artifact
specifically: **no store exists for one at all** — `MarketRealitySnapshotStore`
(Phase 17H.5) persists individual DAILY snapshots (252 real rows,
one per date), not a multi-date `DatasetVersion`/`DatasetArtifact` as
one object; re-confirmed unchanged from Phase 18.4's own original
finding on this exact point.

## 4. Reproducibility Test Design (Future, Not Implemented)

Walking the phase's own worked example — NIFTY Iron Condor research
dataset, 2026-08-14, 09:20–15:15, 5-minute — against what would need
to be TRUE for each question, and what exists today to make it true:

- **"Which exact observations were used?"** — Answerable today, per
  snapshot: `MarketRealitySnapshot.source_observation_ids` (rolled up
  across spot/futures/vix/options, Phase 18.1/18.3). A future test
  would assert this set is non-empty and every id resolves back to a
  real stored row.
- **"Which ingestion runs created them?"** — Answerable today:
  `DatasetVersion.ingestion_run_references` (Phase 18.7, now
  correctly `as_of_time`-scoped for options per Phase 18.10's own
  fix). A future test would assert these ids are a subset of what
  `IngestionRun`/lineage records actually contain for that window.
- **"Which reconstruction logic built them?"** — Answerable today:
  `DatasetVersion.reconstruction_version`, enforced uniform across the
  range (Phase 18.7).
- **"Can the same artifact be rebuilt one year later?"** — **Partially
  answerable today, NOT provably so.** The underlying data is proven
  immutable (§3); the reconstruction is proven deterministic
  (`fingerprint()` reproducibility, Phase 18.3/18.7/18.10, tested
  repeatedly). But "rebuilt" implies comparing against something kept
  from the FIRST build — and nothing is kept (§1, §3's own findings,
  converging on the same root cause a third time in this audit). A
  future test can only assert "if I keep today's fingerprint myself
  and rebuild in a year, do they match" — it cannot assert Bujji
  itself would know, because Bujji itself keeps nothing.
- **"Does the fingerprint match?"** — Same answer: computable on
  demand, not remembered.

**The recurring theme across all five questions**: every one of them
is answerable **as a live computation right now**, and **none of them
is answerable as a historical record check**, because nothing is ever
written down. This is the same root cause named three separate times
in this audit (§1, §3, §4) — not three different problems.

## 5. Commercial Readiness Assessment

"Backtest yesterday's strategy" (once a backtester exists — not built
here):

**A) Only results** — trivially always possible, uninteresting as a
bar.

**B) Results + dataset identity** — **achievable today, with real
primitives, if a caller bothers to capture and hand back
`dataset_version_id` alongside the result.** Nothing prevents this
now; nothing does it automatically either — it would be the
backtester's own responsibility to call `build_dataset_version()` and
keep the id somewhere near its own result, since Bujji's Reality/
Snapshot layer itself does not.

**C) Results + complete reproducible research artifact** — **not
achievable today.** This requires exactly the missing piece named
across §1/§3/§4: a persisted, immutable `DatasetArtifact` a customer
could be handed a reference to, and later ask Bujji to re-verify
against, without the customer (or Bujji) needing to have manually
kept a copy of the original fingerprint somewhere. That storage layer
does not exist.

**Current state: solidly at (B), with all the raw material for (C)
already built and proven — just never assembled into a kept record.**

## 6. Gaps, Named Only (No Implementation)

1. **No `dataset_id` concept** — distinct from `dataset_version_id`,
   per §2's own finding; the single most important naming/design gap
   this audit surfaces.
2. **No persistence for any dataset-level artifact** — `DatasetVersion`
   is pure in-memory; `MarketRealitySnapshotStore` only persists
   single-date DAILY snapshots, not multi-date artifacts.
3. **`underlying universe` is implicit, not a real field** — hardcoded
   to NIFTY throughout `builder.py`; moot today (single-universe
   system) but a real gap the moment a second underlying is ever
   added.
4. **Certification references not rolled up at the `DatasetVersion`
   level** — exist per-snapshot, not aggregated across a date range,
   unlike ingestion lineage (which Phase 18.7/18.10 did aggregate).
5. **`schema_version` not surfaced on `DatasetVersion`** — exists per
   snapshot, silently dropped at the dataset-artifact level.
6. **Creation environment/code identity never wired in** —
   `resolve_code_identity()` (Phase 16D) exists, works, and is
   unconnected; this deployment's own working tree is currently
   uncommitted (345 changed lines, checked live this phase) —
   `dirty=True` would be the honest, unavoidable answer for any
   artifact built today, and no artifact design should silently omit
   that fact.

None of these six requires new invention — every one of them is either
"assemble already-existing per-snapshot data at the dataset level" (4,
5 — the exact pattern Phase 18.10 already used once for ingestion
lineage) or "wire in an already-built, already-tested module" (6,
`resolve_code_identity`), except for #1 (a genuinely new, small
concept) and #2 (a genuinely new, small store — the one piece of real
new architecture this whole chain of findings points to).

## Final Decision

**B — Needs more foundation work, but a small, well-scoped amount.**
Not (A): a persistence layer and a `dataset_id` concept do not exist
and building `DatasetArtifact` without them would just be building
another in-memory, un-kept object — repeating §1/§3/§4's own root
cause a fourth time. Not (C): there is no deep architectural gap —
every primitive `DatasetArtifact` needs already exists, is already
tested, and (per Phase 18.10) is now fast enough to compute on demand;
nothing here requires rethinking Reality, Snapshot, or reconstruction
semantics, all of which this audit re-confirmed sound for the third or
fourth consecutive phase in a row.

## Recommended Next Phase

**Phase 18.12 — Historical Research Dataset Artifact Implementation**,
scoped narrowly to exactly the six named gaps, in this order:

1. Define `dataset_id` (a new, small, additive concept — assigned once
   at creation, distinct from the content-hash `dataset_version_id`).
2. Add a persistence store for `DatasetArtifact` — new, but small and
   precedented (same WAL-SQLite pattern every other store in this
   project already uses; not a new storage PARADIGM).
3. Roll up `certification_refs` and `schema_version` onto
   `DatasetVersion`/`DatasetArtifact`, the same additive pattern
   already used for `ingestion_run_ids` in Phase 18.10.
4. Wire `resolve_code_identity()` into artifact creation, honestly
   surfacing `dirty=True` where applicable rather than omitting the
   field.
5. Implement the reproducibility test suite designed in §4 against the
   real, now-persisted artifact — not just the underlying snapshot
   fingerprints in isolation.

Explicitly NOT recommended yet: `underlying universe` generalization
beyond NIFTY (§2's gap #3) — real, but moot until a second underlying
is ever actually captured; solving it now would be speculative
architecture ahead of any real need, which this project's own standing
discipline (and this phase's own instructions) both caution against.
