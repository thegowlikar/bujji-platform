# Phase 18.13 — Historical Dataset Lifecycle & Publication Readiness Audit

**Status: AUDIT ONLY. Zero code changes.** Every claim is backed by a
direct code read against the real, currently-deployed
`dataset_artifact.py`/`dataset_artifact_store.py` (Phase 18.12), not
inferred from that phase's own report.

---

## 1. Dataset Lifecycle

**Does Bujji currently have dataset states?** **No.** Direct grep of
`dataset_artifact.py` for `status`, `CERTIFIED`, `PUBLISHED`,
`DEPRECATED`: **zero matches.** `DatasetArtifact` (Phase 18.12) has no
state field of any kind — an artifact exists, fully formed, the
instant `create_artifact()` returns. There is no `CREATED` state
distinct from any other state, because there is only ever one state.

**Can datasets move creation → validation → certification →
publication?** **No such pipeline exists.** `create_artifact()` calls
`build_dataset_version()`, computes an identity, resolves code/environment
identity, and writes one immutable record — in one function call, with
no intermediate stage a caller could observe or gate on.

**Can an invalid dataset accidentally become usable? Confirmed yes,
directly from the code, not inferred**: `create_artifact()`'s own
docstring states plainly — *"Never raises for missing/incomplete
data... an artifact can honestly represent a PARTIAL or even EMPTY
dataset."* This was the correct design choice for Phase 18.12's own
scope (matching every other builder in this project's "no fake
completeness, never raise on absence" discipline) — but it has a real
consequence this phase must name explicitly: **nothing today prevents
`create_artifact()` from producing a fully valid, fully persisted,
fully verifiable `DatasetArtifact` for a date range with zero real
data in it.** `verify_artifact()` would report `is_valid: True` for
such an artifact — it checks fingerprint/version/schema/lineage
*consistency*, never *sufficiency*. An empty-but-internally-consistent
artifact is, by every check this system currently runs, indistinguishable
from a complete one. This is real and provable, not speculative:
`DatasetVersion.ready_dates`/`incomplete_dates` (Phase 18.7) already
carries the information needed to tell the two apart — but nothing in
`create_artifact()` reads it, and nothing refuses creation based on it.

**Is certification mandatory before customer use? Confirmed no.**
Direct grep: `dataset_artifact.py` never imports
`market_reality.certification.CertificationGate`, and never checks
`ResearchSessionReadiness.certified_lineage_available` (Phase 18.5) or
any per-observation `certification_status` before creating an
artifact. `certification_references` (Phase 18.12) is a **descriptive
rollup** — "here is what certification refs the underlying data
happens to carry" — not a **gate**. An artifact can be created and
handed an `artifact_id` regardless of whether any of its underlying
data was ever certified at all.

**Can published datasets change? — there is no "published" concept to
test**, but the adjacent, real question — can an artifact's own record
change after creation — is answered: **no**, enforced at two layers
(the `frozen=True` dataclass, and `DatasetArtifactStore.write()`'s
`ConflictingDatasetArtifactError` guard, both re-verified this phase
by re-reading the live code, unchanged since Phase 18.12). This part
is genuinely solid.

## 2. Dataset Artifact Governance

**Does `DatasetArtifact` contain enough metadata for external users?**
Largely yes for *identity/provenance* (artifact_id, dataset_id,
fingerprint, reconstruction_version, schema_version, code_identity,
environment_identity, certification_references,
ingestion_run_references) — this is real and substantial. It contains
**nothing about *fitness for use*** — no field says "was this range
complete," "were all expected sessions present," or "is this safe to
build a strategy on." That information exists one layer down
(`DatasetVersion.ready_dates`/`incomplete_dates`) but is not surfaced
on the artifact itself.

**Are dataset identity and artifact identity correctly separated?**
**Yes — this is real, tested, and re-confirmed this phase.**
`dataset_id` (content-derived, `DatasetIdentity`) and `artifact_id`
(event-derived, `uuid4()`) are genuinely two different concepts with
two different determinism properties, exactly as Phase 18.11 designed
and Phase 18.12 implemented and tested
(`test_artifact_id_never_regenerated_on_identical_recreation`, still
passing, re-run this phase). **Solved, not a gap.**

**Can two identical datasets created at different times coexist
safely?** **Yes, proven** — `DatasetArtifactStore` has no uniqueness
constraint on `dataset_id` (only `artifact_id` is the primary key), so
two artifacts sharing one `dataset_id` coexist as two separate rows,
`list_for_dataset()` returns both. Live-relevant to §4/§6 below.

**Can artifacts be traced to their exact source observations?**
**Yes, transitively, through a real chain, not a shortcut**:
`artifact.ingestion_run_references` → real `lineage.ingestion_run_id`
values on real stored rows (Phase 18.10's own mechanism) →
`artifact.dataset_version_reference` → `DatasetVersion.fingerprint_lineage`
→ per-date `MarketRealitySnapshot.source_observation_ids` → real
`HistoricalObservation.observation_id` primary keys. Every link in
this chain is a real, dereferenceable value confirmed working in prior
phases. **No single field jumps straight from artifact to raw
observation IDs** (that final hop requires re-running
`build_market_reality_snapshot()`, not a stored pointer) — a real,
minor gap, not a broken chain.

## 3. Dataset Manifest

**Does a single customer-facing manifest, shaped like the phase's own
example, exist today?** **No.** Direct grep for `dataset_name` across
the whole repository: **zero matches** — the concept of a human-
readable dataset NAME (as opposed to a `dataset_id` hash) does not
exist anywhere in this codebase.

Mapping the phase's own example manifest field-by-field against what
`DatasetArtifact` (Phase 18.12) actually has:

| Manifest field | Exists on `DatasetArtifact` today? |
|---|---|
| `dataset_name` | **Missing entirely** — no naming concept exists |
| `dataset_id` | Present |
| `artifact_id` | Present |
| `coverage.from`/`coverage.to` | Present (`date_range`) |
| `resolution` | Present |
| `instruments` | **Missing from the artifact itself** — `DatasetVersion.included_components` exists but is not copied onto `DatasetArtifact` (a real, small omission Phase 18.12 did not close) |
| `certification_status` | **Missing** — only a raw `certification_references` list exists; no summarized status (`"CERTIFIED"` / `"UNCERTIFIED"` / `"PARTIAL"`) is ever computed, consistent with §1's own finding that certification is never gated or classified, only listed |
| `reconstruction_version` | Present |
| `code_identity` | Present (a full dict, not a single string as the example shows — a real, minor shape difference, not a gap) |
| `created_at` | Present |

**A single, assembled, customer-facing manifest object does not exist
— every individual fact it would need exists on `DatasetArtifact`
except `dataset_name`, `instruments`, and a summarized
`certification_status`.** This is a real but narrow gap: assembling one
is a presentation-layer exercise over already-real data for 8 of 10
fields, genuinely new work for 2 of 10 (naming, cert-status
summarization).

## 4. Dataset Evolution

**Does the current architecture support "new data arrives tomorrow —
mutate, or create v2"?** **The correct answer (create a new,
immutable version) is already the ONLY thing the architecture allows
— but the "v2" relationship itself is not modeled.**

Confirmed directly: `create_artifact()` called again tomorrow, after
more data has landed, would compute a **different** `dataset_id`
(more dates now `READY`, a different `fingerprint_lineage`) and a
**fresh** `artifact_id` — this is Option B (a new artifact), and
happens automatically, by construction, because `DatasetArtifact` is
immutable and `dataset_id` is a pure function of content (§1/§2's own
findings). **Mutating an existing artifact is not merely discouraged —
it is structurally impossible** (`ConflictingDatasetArtifactError`
would fire if anything ever tried to reuse an `artifact_id` with
different content, and nothing in the codebase ever attempts to reuse
one).

**What is genuinely missing**: the phase's own suggested
`parent_artifact_id` field. Direct grep: **zero matches anywhere.**
Today, two artifacts covering overlapping-but-different ranges of the
same underlying NIFTY options data are two **unrelated** rows in
`DatasetArtifactStore` — nothing records that artifact #2 is "the same
research line, extended" rather than "an unrelated dataset that
happens to also be about NIFTY." `list_for_dataset(dataset_id)` cannot
find artifact #2 from artifact #1's `dataset_id`, because a content
change (more data) necessarily changes `dataset_id` too. **This is a
real, named gap — not a safety problem (nothing can corrupt or
silently replace v1), but a discoverability/lineage-between-artifacts
problem.**

## 5. Backtest Eligibility Boundary

**Can Bujji answer "is this dataset safe for backtesting" without
running a strategy?** **Partially — the individual signals mostly
exist; nothing combines them into one boundary check.**

Per the phase's own six named checks, evaluated against real code:

| Check | Exists today? |
|---|---|
| Completeness | `DatasetVersion.ready_dates`/`incomplete_dates` — real, but not read by `create_artifact()`/`verify_artifact()` at all (§1's own finding) |
| Certification | `certification_references` exists (a list); no boolean/summary judgment is ever computed (§1, §3) |
| Reproducibility | **Yes, real and proven** — `verify_artifact()`'s `fingerprint_unchanged` check, live-demonstrated in Phase 18.12 |
| Identity verification | **Yes, real** — `verify_artifact()`'s `found`/`schema_compatible` checks |
| No-look-ahead guarantees | **Yes, real, structurally enforced at the query layer** (Phase 18.1/18.9/18.10's own proven mechanism) — but this is a property of the RECONSTRUCTION path, never something `verify_artifact()` itself re-checks or reports per-artifact |
| Code version availability | **Yes, real** — `code_identity.resolved`/`dirty`, live-captured in Phase 18.12's own demonstration |

**No single function combines these into the
`BacktestDatasetEligibility` object the phase's own example shows.**
`verify_artifact()` (Phase 18.12) is the closest existing primitive —
it already computes 3 of these 6 signals directly
(`fingerprint_unchanged`, `schema_compatible`≈identity verification,
and implicitly code version via the stored `code_identity`) — but its
`ArtifactVerificationResult` is shaped around "did anything change
since creation," not "is this fit to backtest against." A future
`BacktestDatasetEligibility` would extend, not replace, that object.

## 6. Commercial Readiness

**"Same dataset tomorrow"**: Yes, provably, **for the identical
`artifact_id`** — Phase 18.12's own live demonstration proved this
across a genuine process restart. **Not guaranteed for "the dataset a
customer picked by name/description"**, because no name exists (§3) —
a customer today can only be handed a raw `artifact_id`/`dataset_id`
hash, never "NIFTY Options 5-minute Jan 2020–Aug 2026" as a stable,
referenceable handle that could later resolve to a NEWER, extended
artifact.

**"Same backtest result"**: Achievable in principle (deterministic
reconstruction is proven, repeatedly, across Phases 18.3/18.7/18.10/18.12)
— but only if the backtester itself records which `artifact_id` (not
just which date range) it used. Nothing in this system enforces that
a backtester does so; that discipline would need to be designed into
the backtester itself, a future phase's responsibility, correctly
out of scope here.

**"Explainable differences after upgrades"**: **Not achievable
today**, precisely because of §4's own finding — without
`parent_artifact_id`, two artifacts for "the same" dataset before and
after a data/logic upgrade have no recorded relationship. A customer
told "your Aug 2026 result differs from your Sept 2026 rerun" would
get two unrelated `artifact_id`s and no system-provided explanation of
what changed between them — only what this audit itself did by hand
in Phase 18.10 (comparing two independently-known fingerprint values).

## Final Classification

**B — Needs lifecycle governance.** Not (A): §1 proves an invalid or
empty dataset can silently become a fully "verified" artifact today —
that alone rules out production-readiness for a customer-facing
product. Not (C): every foundational primitive this phase's six
questions probe already exists and is independently proven correct
(immutability, deterministic identity, code identity, reproducibility
verification, no-look-ahead) — re-confirmed by direct code re-read in
every section above, not assumed carried over from Phase 18.12's own
report. What is missing is **governance sitting on top of already-
sound foundations**: lifecycle states and a certification gate (§1), a
customer-facing manifest assembling already-real fields (§3), a
parent/version relationship between artifacts (§4), and one combining
eligibility-check function (§5) — none of these require touching
Reality, Snapshot, or reconstruction semantics, all four of which this
audit re-verified sound for the fifth or sixth consecutive phase in a
row.

## Recommended Next Phase

**Phase 18.14 — Dataset Lifecycle & Eligibility Gate Implementation**,
scoped to exactly the gaps this audit proved, in dependency order:

1. Add a `DatasetLifecycleState` field to `DatasetArtifact`
   (`CREATED` → `CERTIFIED` → `PUBLISHED` → `DEPRECATED`, per this
   phase's own example) and a certification gate `create_artifact()`
   must pass before advancing past `CREATED` — reusing
   `DatasetVersion.ready_dates`/`incomplete_dates` (already computed,
   never consulted) as the completeness input, and
   `CertificationGate`/`certified_lineage_available` (already
   computed, never gated on) as the certification input. Small,
   additive, no Reality/Snapshot change required.
2. Add `parent_artifact_id: Optional[str]` to `DatasetArtifact` —
   optional, caller-supplied at creation, enabling a real version
   chain without inventing new identity machinery.
3. Assemble the customer-facing manifest (§3) — a read-only view over
   already-real fields, plus the two genuinely new ones
   (`dataset_name`, summarized `certification_status`).
4. Build `BacktestDatasetEligibility` as a thin combination of
   `verify_artifact()`'s existing checks plus the new lifecycle-state/
   completeness checks from #1 — not a new verification mechanism, an
   aggregation of ones that already exist.

This is explicitly a governance/metadata phase, not a Reality-tier or
reconstruction-logic phase — every one of the four items above is
additive to `DatasetArtifact` alone.
