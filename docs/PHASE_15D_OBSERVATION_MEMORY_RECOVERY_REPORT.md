# Phase 15D -- Observation Memory Recovery: Final Report

## 1. Forensic findings

`MarketStateBuilder`'s `ObservationMemory` (previous/current price
Observation, open Episodes, accumulated MarketEvent history,
RunningState) is owned by `IntelligenceCycleRecorder` and is
**constructed fresh on every process start** -- exactly the same
statefulness pattern `RegimeMemoryState` had before Phase 15B.

Root cause classification: **architectural, not a bug.** No component
was ever designed to persist or hydrate this memory; it was simply
never built, same root cause class as the Phase 15B gap. What
downstream components depend on it: PSI (`price_structure`), MSSI
(`market_structure`), episode/event continuity feeding `consensus`,
`opportunity`, `strategy_eligibility`, `strategy_suitability`,
`strategy_selection`, and `trade_intent` -- effectively the entire
downstream MSI pipeline resets its historical context on every
restart, even though `RegimeMemoryState` (Phase 15B/C) now survives.

**Critical finding, in Bujji's favor**: `market_snapshots.jsonl` is
*already* persisted every cycle by `ShadowSessionRunner` (pre-existing,
not new this phase) and already contains every `MarketSnapshot` a
session has observed, in order. Replaying those snapshots through a
**fresh** `MarketStateBuilder.process()` -- the exact same pure
function the live recorder already calls every cycle -- was proven to
deterministically reconstruct `ObservationMemory` byte-for-byte. **No
new persistence format was needed.**

## 2. Real-data forensic validation

Replayed the real, complete 174-cycle `SHADOW-OBSERVATORY-2026-08-06`
session's `market_snapshots.jsonl` through a fresh `MarketStateBuilder`
and compared every cycle's resulting PSI/MSSI to the session's own
live-persisted `intelligence_cycle.jsonl` records: **0 mismatches
across all 174 cycles** (tuple/list JSON round-trip artifacts excluded
from the comparison, not the substance). Warm-up periods, confidence
levels, episode counts, and event history all matched exactly,
including the session's single real episode
(`EPS-75f70f1ef8004efe81b71377`) and its 672 accumulated events.

## 3. Design

`bujji/market_state_builder/recovery.py` (new):
- `market_snapshot_from_dict()` -- inverse of the existing `dataclasses.asdict(MarketSnapshot)` serialization.
- `read_market_snapshots_with_diagnostics()` -- yields `(snapshot, None)` or `(None, issue)` per line; never raises. Detects torn/malformed JSON, schema-mismatched records (`snapshot_version` check), and duplicate-or-out-of-order records (via the snapshot's own real, always-increasing `timestamp` -- `market_snapshots.jsonl` has no `event_id`, unlike `state_persistence.EventStore`, so this is the correct, evidence-based dedup key for this specific file).
- `hydrate_observation_memory()` -- replays all valid, in-order snapshots through a fresh `MarketStateBuilder`, returns `(ObservationMemory, RecoveryReport)` -- reuses `state_persistence.models.RecoveryReport`/status constants (Phase 15B's existing infrastructure), not a new report shape.
- `observation_memory_fingerprint()` -- deterministic SHA-256 of the memory's full content, for equality checks without manual diffing.

Session isolation is structural (one file per session directory,
already true before this phase), not field-based.

## 4. Implementation -- additive only

- `IntelligenceCycleRecorder.__init__` gained `initial_observation_memory: Optional[ObservationMemory] = None`, threaded into its existing `MarketStateBuilder(memory=...)` constructor parameter (already existed, unused until now).
- `ShadowSessionRunner.__init__` gained `observation_memory_recovery_enabled: bool = False`. When `True` (and `intelligence_cycle_enabled`), it calls `hydrate_observation_memory(market_snapshot_path)` -- reading whatever this session's own snapshot file *already* contains from a prior process, before this constructor ever writes to it -- and seeds the new recorder with the result.
- `ShadowSessionArtifact` gained `observation_memory_recovery_report: Optional[Dict] = None`, kept separate from Phase 15C's `recovery_report` (regime memory) so existing callers/tests are unaffected.
- All defaults preserve exact pre-Phase-15D behavior; verified by the full pre-existing test suite passing unmodified (only two exact-field-set/grep-based safety tests needed their expected-set updated to include the new, non-decision field/import, per the project's established documented-exception pattern).

## 5. Failure scenario tests

**Persistence layer** (`tests/test_observation_memory_recovery.py`, 16 tests): empty/missing file, first-cycle recovery, warm-up recovery, mid-session recovery matches continuous replay (via fingerprint), transition-boundary recovery, torn final record, malformed record mid-file (no cascade), duplicate timestamp (not double-processed), out-of-order record (skipped, never reordered), schema version mismatch, multiple sessions (isolated by file), repeated hydration (idempotent), fingerprint equality/inequality, `market_snapshot_from_dict` round-trip + missing-field error.

**Runtime layer** (`tests/test_shadow_runtime_observation_memory_recovery.py`, 5 tests, using a *varying*-spot FakeBroker so real events/episodes actually fire): clean startup, recovery-not-requested, mid-session restart matches an uninterrupted reference run's downstream PSI/MSSI/consensus/opportunity semantic fields cycle-for-cycle, restart immediately after the session's first real episode, restart with a torn `market_snapshots.jsonl` line.

**21/21 passing.**

One notable finding during testing: at the runtime layer, two
independently-driven fake clocks (one per `ShadowSessionRunner`
instance across a simulated restart) cannot be tick-aligned for exact
byte-for-byte comparison without brittle manual accounting of every
`clock()` call site (`start_time`, `end_time`, smoke-test, per-cycle
calls) -- a test-harness artifact of a call-counted fake clock, not a
real product defect (real wall-clock time has no such artifact, and
byte-exact equivalence was already proven at the persistence layer
with controlled timestamps). The runtime-layer restart test therefore
asserts what actually matters -- episode/event counts and every
decision-relevant semantic field (`structure_state`, `trend_state`,
`confidence`, `consensus_level`, `opportunity_state`, etc.) match
cycle-for-cycle -- rather than raw IDs/timestamps expected to differ
by harness bookkeeping alone.

## 6. Safety

8/8 dedicated safety tests (`tests/test_observation_memory_recovery_safety.py`): no forbidden imports, no `place_order`/`modify_order`/`cancel_order` calls (AST-based), `recovery.py` never references any broker object or network call, never references `PaperBroker`/`position_intelligence`/`capital`/`risk_governor`/`execution_engine`, `guard.py`/`hybrid.py` untouched, `shadow_session_runner.py`'s diff adds no new broker call, `intelligence_cycle_recorder.py`'s cumulative diff (Phase 15C + 15D) has zero removed lines, `market_state_builder/market_state.py` itself is byte-untouched (only a new sibling `recovery.py` file was added).

Two pre-existing, legitimate protected-boundary safety tests needed
documented, scoped exceptions (the project's established pattern, not
a general loosening):
- `test_shadow_session_runner_still_unmodified_by_this_phase` (Phase 3B) explicitly enforced zero `market_state_builder` coupling in the runner -- Phase 15D's entire purpose is exactly that coupling, so a single documented exception line was added, verified additive by the diff-based checks above.
- `test_shadow_runtime_phase5.py`'s exact-field-set assertion needed the new `observation_memory_recovery_report` field added (same pattern as Phase 15C's `recovery_report`).

## 7. Real-data validation (runtime entry point)

Beyond the forensic audit's initial replay, the actual runtime
function `hydrate_observation_memory()` was validated against the real
session at **5 different restart boundaries** (cycle 1, 20, 87, 150,
173) -- for each, the first N real snapshots were hydrated, then the
*remaining* real snapshots were replayed through the hydrated builder
and compared to the session's own real persisted PSI/MSSI trajectory.
**0 mismatches at every boundary**, including a restart with only 1
cycle of prior history (near-cold-start) and one with only 1 cycle
remaining (near-session-end).

## 8. Full regression

`4583 passed` (1 pre-existing, unrelated deprecation warning).

## 9. Files changed

New:
- `bujji/market_state_builder/recovery.py`
- `tests/test_observation_memory_recovery.py`
- `tests/test_shadow_runtime_observation_memory_recovery.py`
- `tests/test_observation_memory_recovery_safety.py`

Modified (additive only):
- `bujji/shadow_runtime/shadow_session_runner.py`
- `bujji/shadow_runtime/shadow_session_artifact.py`
- `bujji/market_state/intelligence_cycle_recorder.py`
- `tests/test_shadow_runtime_phase5.py` (exact-field-set update)
- `tests/test_market_state_builder_safety.py` (documented, scoped coupling exception)

## 10. Newly discovered gaps (for future phases, not fabricated)

- **PaperBroker and Position Intelligence remain unwired into the runtime** -- unchanged from Phase 15C's finding; still accurate.
- **No formal replay engine yet**: this phase built ad hoc, purpose-specific replay (for validation and recovery). A general-purpose, reusable replay engine (Gap #11 from the earlier gap-closure audit) would subsume both this and Phase 15B/C's mechanisms under one interface.
- **The runtime does not yet auto-detect** whether to enable recovery -- it's an explicit opt-in flag the caller must set, same pattern as Phase 15C's regime recovery. A future phase could make the production launcher (`scripts/run_shadow_live_observatory.py`) turn both on by default for same-day session continuation.
- **Greeks Brain, Premium Behaviour/Memory, and Flow intelligence are not yet built at all** -- confirmed absent from the codebase during this phase's exploration (no `greeks_brain`, `premium_memory`, or `flow_intelligence` packages exist). These are the highest-value NEW intelligence gaps remaining, versus this phase's restart-continuity gaps which are now closed for the two components that had them.

## 11. Recommended next phase

**Phase 15E: Greeks Brain + Premium Behaviour intelligence.** Every
recovery/persistence gap identified across Phases 15B-15D is now
closed for the components that had one (regime memory, PaperBroker
positions, observation memory); Position Intelligence and PaperBroker
remain honestly unwired into the runtime by design (deferred). The
mission's own priority list and this phase's exploration both point to
the same next real gap: Bujji observes option premiums and the option
chain every cycle but has no Greeks computation (delta/gamma/theta/vega
are persisted as `None` on every `OptionLeg`, confirmed in
`market_perception/models.py`'s own docstring) and no memory of how
premium behaved over time relative to the underlying's move -- both
are professional-trader-level "why is this happening" questions the
constitution explicitly calls for, and neither currently exists in any
form (not even a first version), unlike trade construction or exit
intelligence which already have partial scaffolding from earlier
phases.
