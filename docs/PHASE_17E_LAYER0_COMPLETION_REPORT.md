# Phase 17E — Layer 0 Raw Observation Store: Completion Report

**Status: IMPLEMENTED.** Layer 0 exists, is tested, and is inert (nothing
imports it yet — a collector arrives in 17F).

---

## 1. What was built

```
              Certified Source
                     |
                     v
             Observation Validator
                     |
          +----------+----------+
          |                     |
      Accepted              Rejected
      EventStore          Rejection Store
```

| File | Role |
|---|---|
| `bujji/market_reality/taxonomy.py` | Layer 0 vocabulary: 5 observation kinds, instrument types, required/forbidden payload fields, certification states, rejection reasons, append outcomes, source-health states |
| `bujji/market_reality/models.py` | `RawObservation`, `Layer0Lineage`, `ValidationOutcome`, `RejectedObservation`, `AppendResult`, `ReplayReport`, `CompletenessReport` — frozen, no logic beyond `derive_confidence()` |
| `bujji/market_reality/capture.py` | `build_raw_observation()` — the collector-facing constructor; delegates id minting to MOC |
| `bujji/market_reality/certification.py` | `CertificationGate` — reads `data_certification/*.json`, fails closed |
| `bujji/market_reality/validator.py` | The five checks; pure, IO-free, no wall-clock |
| `bujji/market_reality/store.py` | `RawObservationStore` — accept/duplicate/reject routing over two `EventStore`s |
| `bujji/market_reality/replay.py` | Deterministic ordered replay + `as_of` + honest diagnostics |
| `bujji/market_reality/completeness.py` | Observation Completeness Monitor |

---

## 2. Reuse: what was NOT built

The point of the pre-implementation audit was to avoid a parallel
persistence system. What Layer 0 delegates rather than reimplements:

| Concern | Delegated to | Modified? |
|---|---|---|
| Durable append-only write (flush + fsync, atomic line, torn-line tolerance) | `state_persistence.EventStore` | **No — used as-is** |
| Idempotency key / dedup | `PersistedEvent.event_id` + deterministic `observation_id` | **No** |
| Observation record shape (identity/quality/provenance/value) | `market_observation.Observation` | **No** |
| `observation_id` minting | `market_observation.engine.build_observation()` | **No** |
| `transformation_history` | `market_observation.ObservationProvenance` | **No** |
| Recovery-report vocabulary | mirrors `state_persistence.RecoveryReport` | **No** |

The storage design reduces to one mapping:

| `PersistedEvent` field | Layer 0 meaning |
|---|---|
| `event_id` | `observation_id` (deterministic content hash) |
| `event_type` | observation kind |
| `cycle_id` | `None` — Layer 0 is market reality, not an intelligence cycle |
| `timestamp` | event time when published, else capture time |
| `provenance` | `access_method` |
| `payload` | serialized `Observation` + Layer 0 lineage block |

`test_market_reality_safety.py` enforces this mechanically: `store.py`
must import `EventStore`, and must contain no `json.dump` and no
`os.fsync` of its own.

---

## 3. Files changed

**New:** `bujji/market_reality/` (9 files incl. `__init__.py`), plus 5
test modules (`tests/test_market_reality_{validator,store,replay,
completeness,safety}.py`).

**Modified — two files, both additive:**

1. `bujji/market_observation/taxonomy.py`
   - `+ TYPE_MARKET_DEPTH` (added to `ALL_OBSERVATION_TYPES`)
   - `MARKET_OBSERVATION_VERSION`: `1.0.0` → `1.1.0`
   - `RECOGNIZED_SCHEMA_VERSIONS`: `("1.0.0",)` → `("1.0.0", "1.1.0")`
2. `tests/test_market_state_builder_safety.py`
   - A pre-existing guard asserted `bujji/market_observation/` was
     entirely unmodified. Rather than weaken it, a named documented
     exception (`_phase17e_layer0_exception`) was added for exactly
     `taxonomy.py` — the same pattern this file already uses for the
     Phase 9 Liquidity Bridge. Every other file in every protected
     package remains fully guarded; the check changed from "nothing
     changed" to "nothing changed except this one reviewed file."

**Untouched, as instructed:** MSI (all 35+ packages), MIC, strategy
engines, trade execution, intelligence layers, `execution_engine`,
`production_runtime`, `trading_brain`, `mic_replay`, every broker module,
`market_timeseries`, `state_persistence`, `epistemics`, and every file in
`market_observation/` other than `taxonomy.py`.

---

## 4. Migration impact

**Data migration: none.** Layer 0 writes to a new, previously unused
path. No existing file is read, rewritten, or reinterpreted.

**Schema bump blast radius: verified, not assumed.** `futures_observation`
and `options_observation` use their *own* domain versions
(`FUTURES_OBSERVATION_VERSION` / `OPTIONS_OBSERVATION_VERSION`), so the
MOC bump cannot reach them — confirmed by reading their `config.py`
before making the change. `live_observation/engine.py` is the only module
stamping MOC's constant; it now emits `1.1.0`, which remains recognized.
Because `1.0.0` stays in the recognized tuple, **no previously-written
record is invalidated**. The bump gates new consumers, exactly as
intended, without rewriting history.

**Behavioural impact: none.** Nothing imports `bujji.market_reality`
after this phase. Every runtime path behaves identically.

---

## 5. Test results

| Suite | Result |
|---|---|
| `tests/test_market_reality_*.py` (new) | **78 passed** |
| Full regression `tests/` | **5,250 passed, 0 failed** |

Baseline before this phase was 5,172 passed. The delta is the 78 new
tests, with no pre-existing test lost or weakened.

### Notable coverage

- **Zero is a fact, absent is a failure** — a far-OTM option quoting
  `bid=0/ask=0` is stored as true market information (direct regression
  guard for the 2026-08-12 stale-strike finding). Only absence rejects.
- **Forbidden derived fields rejected at runtime** — the safety test
  iterates the *entire* `FORBIDDEN_PAYLOAD_FIELDS` list and asserts each
  is refused. A declared list nobody checks is documentation, not a
  guarantee.
- **Certification fails closed** — missing directory, malformed artifact,
  unmapped instrument type, and a certification belonging to a *different
  access method* all deny the write. The last one matters: a certification
  of the MCP connector says nothing about the direct SDK path, and
  conflating them is precisely what the connector incident proved
  dangerous.
- **Torn trailing line** (simulated crash mid-write) is counted as
  malformed and skipped — never raised, never silently included.
- **Restart safety** — duplicate detection and replay order both survive
  reopening the store.
- **No wall-clock anywhere in the package** — enforced by AST scan;
  replay determinism depends on it.

---

## 6. Design decisions worth recording

**Duplicate is not a rejection.** Because `observation_id` is a
deterministic content hash over identity + value, an identical id means
an identical fact. Re-capturing the same fact (a retried poll, a
reconnecting feed) is normal operation; writing a rejection record for
each retry would fill the rejection log with non-events. The outcome is
`DUPLICATE`: an idempotent no-op. Nothing written, nothing lost, nothing
polluted.

**Confidence is derived, never asserted.** There is deliberately no
argument anywhere in the package letting a caller declare its own data
trustworthy. `derive_confidence()` is a pure function of certification
status and integrity.

**`CERTIFICATION_MISSING` is the default.** `build_raw_observation()`
defaults to the fail-closed value, so a caller that forgets to resolve a
real certification status produces a record the validator rejects.

**VIX/INDEX is honestly uncertified.** Phase 17A.5 never produced a VIX
certification artifact, so `INSTRUMENT_INDEX` is absent from the
certification key map and resolves to `CERTIFICATION_MISSING`. Borrowing
NIFTY_SPOT's certification for it would have been convenient and wrong.

---

## 7. What this phase proves — and what it does not

The standing requirement: *"If all derived databases are deleted, can
Bujji rebuild them from Layer 0?"*

**Proven now:**
- Layer 0 survives deletion of every other store (it depends on none).
- Replay is deterministic and order-exact, across repeated reads and
  across process restarts.
- Every stored record carries complete lineage: source, access method,
  event time, capture time (distinct), certification status +
  auditable ref, derived confidence, and a single-entry
  `RAW_CAPTURE` transformation history.
- Rejections are permanent and attributable, so a gap in Layer 0 is never
  ambiguous between "nothing happened" and "something was discarded."

**Not proven, and not claimed:** that derived output is byte-identically
rebuildable. That requires materializers to exist (17F) and is the
explicit subject of Phase 17D Part 5's `REPLAY_VERIFIED` proof. Layer 0
is the foundation that makes that proof possible; it is not the proof.

---

## 8. Known state risk (pre-existing, unresolved)

The repository still has no commit since `b148e39` (2026-08-03). All of
Phases 9→17, including this one, exist only as uncommitted working-tree
state on a single VPS. This phase adds ~1,900 lines to that exposure.
Flagged again; still unaddressed; still growing.
