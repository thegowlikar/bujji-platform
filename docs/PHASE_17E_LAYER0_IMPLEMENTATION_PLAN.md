# Phase 17E — Layer 0 Raw Observation Store: Implementation Plan

**Status: PLAN ONLY. No code written. Awaiting architecture review before execution.**

Phase 17D (Market Reality Data Contract) is accepted. This document is the
pre-execution plan for implementing Layer 0 strictly under that contract:
what gets built, what gets reused, what changes, what it costs, and what
tests must pass.

---

## Part 1 — Pre-Implementation Audit (completed; this is what changed the plan)

Per instruction, existing persistence and observation infrastructure was
inspected before designing anything. The audit found **substantially more
existing, directly-reusable infrastructure than the 17C/17D documents
assumed** — enough that the shape of 17E changes materially.

### 1.1 What already exists and is directly reusable

| Asset | Location | Verified capability |
|---|---|---|
| `EventStore` | `bujji/state_persistence/store.py` | Append-only JSONL; single atomic `write()` + `flush()` + `fsync()` per record; tolerates a torn trailing line on crash; `read_events_with_diagnostics()` separates malformed lines from valid ones. Proven across 5 phases. |
| `deduplicated_events()` | `bujji/state_persistence/store.py` | First-occurrence-wins dedup by `event_id`, order-preserving. |
| `PersistedEvent` | `bujji/state_persistence/models.py` | `event_id` (idempotency key), `event_type`, `session_id`, `cycle_id`, `timestamp`, `schema_version`, `provenance`, `payload`. |
| `RecoveryReport` | `bujji/state_persistence/models.py` | Three-state (`COMPLETE`/`PARTIAL`/`FAILED`) + counts for discovered/replayed/duplicate/malformed/schema-mismatch. |
| `Observation` + parts | `bujji/market_observation/models.py` | `ObservationIdentity` (observation_id, observation_type, instrument, exchange, segment, timestamp, resolution, source, schema_version), `ObservationQualityMetadata` (completeness, freshness, confidence, missing_fields, validation_status, source_quality), **`ObservationProvenance` (originating_source, acquisition_timestamp, normalization_timestamp, origin, version, `transformation_history`)**, `ObservationValue` (closed variant: value_kind + payload). |
| `_observation_id()` | `bujji/market_observation/engine.py` | Deterministic content hash over identity + value (explicitly **excludes** quality, so a quality re-assessment doesn't change identity). |
| `build_observation()` / `validate_observation()` / `detect_gaps()` / `validate_series_ordering()` | `bujji/market_observation/engine.py` | Construction + validation + explicit gap objects (`SeriesGap`), no silent absences. |
| `Lineage` | `bujji/epistemics/lineage.py` | Phase 16C canonical contract: `data_class` (AUTHORITATIVE/DERIVED/EPHEMERAL), `as_of` (**event** time, not arrival), `source_event_ids`, `descends_from()` (accumulates parent sources, never summarises away), `look_ahead_violation()`, and **`from_observation_provenance()`** — an adapter to the shape above already written. |
| SQLite store patterns | `bujji/market_timeseries/store.py` | WAL + `synchronous=FULL`, `_run()` retry-on-locked, idempotent-write / `ConflictingCandleError` on differing content at same key. |

**The single most important audit finding**: `ObservationProvenance`
already carries `transformation_history`, and `epistemics.Lineage`
already implements the full lineage contract *including an adapter to it*.
17D's lineage model is therefore ~80% pre-existing. Building a new lineage
type would have created exactly the parallel system this phase was told to
avoid.

### 1.2 What is genuinely missing (the real 17E scope)

| Gap | Evidence |
|---|---|
| **Market depth observation type** | `grep -rn 'DEPTH' bujji/market_observation/` returns **zero matches**. The 17-type closed taxonomy has no depth domain. |
| **Certification status anywhere in the observation/epistemics layers** | `grep -rn 'certification' bujji/market_observation/ bujji/epistemics/` returns **zero matches**. The 17A.5 write gate has no representation in the data model. |
| **Rejected-observation storage** | Nothing in the codebase stores a rejected/failed observation. `MarketObservationJournal` records only successful `Observation`/`ObservationSeries`. |
| **A Layer 0 store binding these together** | `MarketObservationJournal` exists but is **unwired** (only self-import + one reference from `futures_observation/engine.py`), has **no fsync** (unlike `EventStore`), no duplicate detection, and no certification gate. |
| **Ordered, diagnosable replay of raw observations** | `journal.read_all()` returns a list in file order with no dedup, no malformed-line accounting, and no `RecoveryReport`. |

### 1.3 Architectural consequence

Layer 0 is **not a new persistence system**. It is a thin composition
layer over three existing, proven ones:

```
market_reality (NEW — thin)
   ├── record shape ........ market_observation.Observation      (reused as-is)
   ├── identity hash ....... market_observation._observation_id  (reused as-is)
   ├── lineage ............. epistemics.Lineage                  (reused as-is)
   └── durability .......... state_persistence.EventStore        (reused as-is)
```

No new JSONL writer. No new hashing. No new lineage type. No new dedup
algorithm. 17E writes the *gate*, the *depth schema*, the *rejection
path*, and the *replay contract* — nothing else.

---

## Part 2 — Design

### 2.1 Mapping a raw observation onto `PersistedEvent` (zero new persistence machinery)

| `PersistedEvent` field | Layer 0 meaning |
|---|---|
| `event_id` | `observation_id` — the deterministic content hash. **Duplicate detection comes free**: two identical observations produce the same id, and `deduplicated_events()` already handles replay-side dedup. |
| `event_type` | `observation_kind` — `TICK` / `QUOTE_POLL` / `DEPTH_SNAPSHOT` / `OPTION_CHAIN_SNAPSHOT` / `HISTORICAL_CANDLE_ECHO` / `VIX_POLL` (17D Part 1.6) |
| `session_id` | Capture session id |
| `cycle_id` | `None` — Layer 0 is deliberately **not** cycle-bound; it records market reality, not intelligence cycles |
| `timestamp` | `observed_at` when the source provides one, else `received_at` (which of the two was used is recorded explicitly in the payload's lineage block, never left ambiguous) |
| `provenance` | `access_method` (e.g. `direct_sdk_fyers_broker_py`) |
| `payload` | `{identity, value, quality, lineage}` — the serialized `Observation` plus the Layer 0 lineage block |

This is the whole storage design. `EventStore` is used **unmodified**.

### 2.2 New package layout

```
bujji/market_reality/
  __init__.py
  taxonomy.py        Layer 0 vocabulary: observation kinds, rejection reasons,
                     certification states, and the required-field contract per
                     observation kind (17D Part 1.1–1.6)
  models.py          RawObservation (Observation + Layer 0 lineage block),
                     RejectedObservation, ValidationOutcome, ReplayReport
  certification.py   Reads data_certification/*.json; answers
                     "is (access_method, instrument_type) CERTIFIED_AVAILABLE
                     right now?" — pure file read, no network, no broker
  validator.py       The five checks (2.3 below). Pure function:
                     candidate -> ValidationOutcome. No IO.
  store.py           RawObservationStore: validate -> accept to EventStore
                     OR reject to the rejection EventStore. Never both.
  replay.py          Ordered replay + RecoveryReport-style diagnostics
```

### 2.3 Validator — the five required checks

Pure, IO-free (certification status is *passed in*, so the validator stays
deterministic and unit-testable without touching the filesystem):

1. **Symbol identity** — instrument non-empty; `instrument_type` in the
   closed set; derivative kinds carry `expiry`, options additionally carry
   `strike` + `option_type`; identity fields internally consistent with the
   declared `observation_kind`.
2. **Timestamp validity** — well-formed ISO-8601; not in the future
   relative to the supplied capture clock; `observed_at <= received_at`
   when both are present. (Reuses `engine._is_well_formed_timestamp`'s
   existing rule rather than a second parser.)
3. **Required fields** — per-kind required keys present in the value
   payload, per 17D Part 1. **Absent ≠ zero**: a missing field is a
   rejection, a legitimately-zero field (e.g. `bid=0` on a dead far-OTM
   option — confirmed real market behaviour on 2026-08-12) is **accepted
   as a true fact**, not treated as missing.
4. **Source certification** — the `(access_method, instrument_type)` pair
   must be `CERTIFIED_AVAILABLE` at write time. Anything else rejects.
5. **Duplicate detection** — an `observation_id` already present in the
   store is not re-appended. Identical content → idempotent no-op (not an
   error). Differing content at the same identity key → **hard rejection**
   with reason `CONFLICTING_CONTENT`, mirroring `ConflictingCandleError`'s
   existing "history is never silently rewritten" discipline.

### 2.4 Rejection path

Rejected observations are written to a **separate, equally permanent**
`EventStore` (`rejected_observations.jsonl`) carrying the full candidate
payload, the full lineage block, and a `rejection_reason` — so a gap in
Layer 0 is never ambiguous between "nothing happened" and "something was
discarded". Per 17D Part 4, a rejected record **never** enters the trusted
store, not even flagged.

### 2.5 Lineage block (17D Part 2, assembled from existing parts)

`ObservationProvenance` supplies `originating_source`,
`acquisition_timestamp`, `normalization_timestamp`, `origin`, `version`,
`transformation_history`. Layer 0 adds only the fields the audit proved
missing: `access_method`, `certification_status`, `certification_ref`,
`observed_at` / `received_at` (kept distinct), and `confidence` derived
**mechanically** (`HIGH` iff certified *and* integrity-clean; else `LOW`)
— never hand-set.

`transformation_history` at Layer 0 contains **exactly one** entry:
`RAW_CAPTURE`. Per 17D Part 2.1, any component appending a second entry to
a Layer 0 record is by definition not Layer 0. A test enforces this.

### 2.6 Replay

`replay.py` returns observations in **exact original append order**
(EventStore file order — the recorded order is itself part of the audit
trail), with an optional `as_of` cut-off, plus a `ReplayReport` reusing
`RecoveryReport`'s existing three-state shape and counters
(discovered/replayed/duplicate/malformed/schema-mismatch).

### 2.7 Two deliberate deferrals (with rationale)

**(a) No SQLite mirror in 17E.** 17D Part 3.1 specifies a SQLite read
index alongside the JSONL log. It is deliberately *not* built in this
phase: nothing queries it yet (materializers arrive in 17F), JSONL already
satisfies 17E's actual requirement (ordered replay), and an index with no
reader is speculative work that would need rewriting once real query
patterns exist. The JSONL log is the source of truth either way — adding
the index later is purely additive and changes no record. **Flagged for
review**: if you want it built now anyway, say so.

**(b) No collector/ingestion wiring in 17E.** Scope is the store, the
validator, and replay. Nothing yet subscribes to a live feed or polls
FYERS — that is 17F, and it keeps this phase fully testable without a
broker connection or a live token.

---

## Part 3 — File Changes

### 3.1 New files (7 source + 4 test)

| File | Purpose | Est. LOC |
|---|---|---|
| `bujji/market_reality/__init__.py` | Package marker (empty, per codebase convention) | 0 |
| `bujji/market_reality/taxonomy.py` | Observation kinds, rejection reasons, certification states, per-kind required-field contract | ~120 |
| `bujji/market_reality/models.py` | `RawObservation`, `RejectedObservation`, `ValidationOutcome`, `ReplayReport` (frozen dataclasses, no logic) | ~140 |
| `bujji/market_reality/certification.py` | Certification-status lookup from `data_certification/*.json` | ~80 |
| `bujji/market_reality/validator.py` | The five checks; pure function | ~160 |
| `bujji/market_reality/store.py` | `RawObservationStore` — accept/reject routing over two `EventStore`s | ~150 |
| `bujji/market_reality/replay.py` | Ordered replay + diagnostics | ~110 |
| `tests/test_market_reality_store.py` | Storage, immutability, idempotency, conflict | — |
| `tests/test_market_reality_validator.py` | All five checks, pass and fail paths | — |
| `tests/test_market_reality_replay.py` | Ordering, `as_of`, crash/truncation, diagnostics | — |
| `tests/test_market_reality_safety.py` | Anti-drift enforcement (see 4.3) | — |

### 3.2 Modified files (exactly one, additive)

| File | Change | Risk |
|---|---|---|
| `bujji/market_observation/taxonomy.py` | Add `TYPE_MARKET_DEPTH = "MARKET_DEPTH"` to the closed type set + `ALL_OBSERVATION_TYPES` | **Low, but needs a review decision — see below** |

The taxonomy's own docstring states a new domain "requires a deliberate
addition here, never an inferred string" — so this addition is sanctioned
by design, not a workaround.

**Open review question**: does adding a value to a closed vocabulary
require bumping `RECOGNIZED_SCHEMA_VERSIONS` from `1.0.0`? Argument for
no: no existing field changes shape, no previously-valid record becomes
invalid, and every existing reader keeps working. Argument for yes: a
`1.0.0` reader encountering a `MARKET_DEPTH` record will reject it as an
unknown type, which is a real compatibility edge. **My recommendation:
keep `1.0.0` and rely on the closed-set validation to reject unknown types
loudly — but this is your call, and I will not decide it silently.**

### 3.3 Files explicitly NOT modified

`bujji/state_persistence/*` (reused unmodified), `bujji/epistemics/*`
(reused unmodified), `bujji/market_observation/{models,engine,journal,
serialization,query,runner,config}.py`, `bujji/market_timeseries/*`,
`bujji/broker/*`, every `msi_*` package, `execution_engine`,
`production_runtime`, `trading_brain`, `mic_replay`.

---

## Part 4 — Migration Impact

### 4.1 Data migration: none

Layer 0 writes to a **new**, previously-unused path (`data/market_reality/`).
No existing file is read, rewritten, moved, or reinterpreted. `bujji.db`,
`decision_journal.jsonl`, `incident_log.jsonl`, the 23 domain journals,
`market_snapshots.jsonl`, and every existing `EventStore` file are
untouched.

### 4.2 Behavioural impact on the running system: none

Nothing imports `bujji/market_reality/` after this phase — it is inert
storage infrastructure with no caller until 17F wires a collector. The
shadow runtime, intelligence cycle recorder, and every `msi_*` package
behave identically before and after.

### 4.3 Backward compatibility

The single modified file adds one constant to a tuple. Existing readers
that validate against `ALL_OBSERVATION_TYPES` gain a valid value; none
lose one. No serialized record changes shape.

### 4.4 Dependency note requiring attention

The certification gate reads `data_certification/*.json`. Those artifacts
are currently **untracked** on the VPS (confirmed via `git status`). If
they are ever absent at runtime, the gate must **fail closed** — treat a
missing certification artifact as `NOT_CERTIFIED` and reject the write,
never as an implicit pass. This is specified behaviour, and it gets its
own test.

### 4.5 Known repository-state risk (pre-existing, not introduced here)

The repo has no commit since `b148e39` (2026-08-03). All of Phases 9→17
exist only as uncommitted working-tree state on this one VPS. 17E adds
~760 LOC to that pile. **This is not a blocker for 17E, but it is a real
and growing risk that remains unaddressed** — flagged again here because
the exposure grows with every phase.

---

## Part 5 — Tests Required

### 5.1 Validator (`test_market_reality_validator.py`)

- Symbol identity: valid spot/future/option/depth accepted; missing
  instrument, unknown `instrument_type`, option missing `strike`, future
  missing `expiry` → each rejected with its own specific reason.
- Timestamp: malformed string rejected; future timestamp rejected;
  `observed_at > received_at` rejected; `observed_at=None` accepted (a
  source genuinely providing no event time is legal per 17D Part 2).
- Required fields: per-kind missing key rejected; **legitimately-zero
  `bid`/`ask` on an illiquid option accepted as a real fact** (direct
  regression guard for the 2026-08-12 stale-strike finding).
- Certification: `CERTIFIED_AVAILABLE` accepted; `PARTIAL_CERTIFICATION`,
  `NOT_CERTIFIED`, and **missing artifact** each rejected (fail-closed).
- Duplicates: identical content → idempotent no-op; differing content at
  same identity → `CONFLICTING_CONTENT` rejection.

### 5.2 Store (`test_market_reality_store.py`)

- Accepted observation is retrievable byte-identical to what was written.
- Rejected observation lands in the rejection store **and is absent from
  the accepted store** (both assertions, not just the first).
- Re-appending an identical observation does not duplicate it.
- Restart safety: a new store instance over the same path sees all prior
  records and never truncates.
- Lineage completeness: every stored record carries all six required
  lineage fields; a record missing any is unwritable.
- `transformation_history` on every Layer 0 record has **exactly one**
  entry, `RAW_CAPTURE`.

### 5.3 Replay (`test_market_reality_replay.py`)

- Observations replay in exact original append order, including across a
  simulated restart.
- `as_of` cut-off excludes later records and includes the boundary
  correctly.
- A torn/truncated trailing line (simulated crash) is counted as malformed
  and skipped — never raises, never silently included.
- `ReplayReport` counts are accurate for a mixed file (valid + duplicate +
  malformed).
- **Determinism**: replaying the same store twice yields identical output.

### 5.4 Safety / anti-drift (`test_market_reality_safety.py`)

Following the codebase's established `test_*_safety.py` AST-scan pattern,
mechanically enforcing the user's "do not add" list rather than trusting
convention — an import/symbol scan over `bujji/market_reality/` asserting
**absence** of: `iv`/`implied_vol`, `delta`/`gamma`/`theta`/`vega`,
`vwap`, `regime`, `signal`, `score`, `indicator`, `strategy`,
`place_order`, and any `bujji.msi_*` / `bujji.trading_brain` /
`bujji.execution_engine` / broker import. Plus: no `datetime.now()` in the
package (capture clock is always injected — replay-safety depends on it,
same rule `market_observation/journal.py` already follows).

### 5.5 Regression

Full `tests/` suite must stay green (current baseline: **5,172 passed**).

---

## Part 6 — What 17E Proves, and What It Cannot Yet Prove

The standing requirement is: *"If all derived databases are deleted, can
Bujji rebuild them from Layer 0?"*

**17E proves the precondition, not the full claim** — and the distinction
is worth stating plainly rather than overclaiming:

- ✅ Layer 0 survives deletion of every other store (it depends on none).
- ✅ Layer 0 replays in exact original order, deterministically, with
  honest diagnostics for anything malformed.
- ✅ Every stored record carries the lineage needed for a future
  materializer to be traced back to it.
- ❌ **Not yet provable**: that derived output is byte-identically
  rebuildable — that requires materializers to exist (17F) and is the
  explicit subject of 17D Part 5's `REPLAY_VERIFIED` proof.

Claiming the full rebuild guarantee at the end of 17E would be false. The
honest statement is: *Layer 0 is the durable, ordered, lineage-complete
foundation that makes that proof possible in 17F.*

---

## Part 7 — Execution Sequence (once approved)

| Step | Scope | Gate |
|---|---|---|
| 17E.1 | `taxonomy.py` + `models.py` + the one additive `market_observation/taxonomy.py` change | Compiles; existing suite still green |
| 17E.2 | `certification.py` + `validator.py` + validator tests | All 5.1 tests pass |
| 17E.3 | `store.py` + store tests | All 5.2 tests pass |
| 17E.4 | `replay.py` + replay tests | All 5.3 tests pass |
| 17E.5 | Safety tests + full regression | 5.4 passes; full suite ≥ 5,172 passed |

---

## Part 8 — Review Questions (I will not decide these silently)

1. **Schema version**: bump `RECOGNIZED_SCHEMA_VERSIONS` for the added
   `MARKET_DEPTH` type, or keep `1.0.0`? (My recommendation: keep `1.0.0`
   — rationale in 3.2.)
2. **SQLite mirror**: accept the deferral to 17F (my recommendation), or
   build it now?
3. **Storage path**: `data/market_reality/` — correct, or does this
   deployment want it elsewhere?
4. **Session scoping**: one Layer 0 file per trading day, or one
   continuous append-only log with dated archive segments per 17D Part
   3.2? (My recommendation: per-day files — they make the 3.2 archive
   roll-off trivial and bound single-file size.)

---

## Part 9 — Gate Status

| Gate | Status |
|---|---|
| Pre-implementation infrastructure audit | **Complete** — Part 1 |
| Design (reuse map, package layout, validator, lineage, replay) | **Complete** — Part 2 |
| File changes enumerated | **Complete** — Part 3 |
| Migration impact assessed | **Complete** — Part 4 |
| Tests specified | **Complete** — Part 5 |
| Architecture review / approval | **Pending — awaiting operator review** |
| Any code written | **None. Not authorized.** |
