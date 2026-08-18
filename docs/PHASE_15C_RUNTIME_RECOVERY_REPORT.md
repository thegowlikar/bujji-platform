# Phase 15C -- Runtime Recovery Integration: Final Report

## 1. Objective

Wire Phase 15B's proven State Persistence + Hydration layer into the
actual Shadow Runtime startup path, so a running Bujji process can be
killed and restarted without silently forgetting its intelligence
state -- while adding zero new broker capability and changing zero
existing behavior for any caller that doesn't opt in.

## 2. Startup architecture -- before

```
ShadowSessionRunner.__init__()
    -> IntelligenceCycleRecorder()          # always fresh
           -> MarketStateBuilder()          # fresh ObservationMemory
           -> RegimeMemoryState()           # fresh, empty
```

Every process restart discarded `RegimeMemoryState` unconditionally.
Cycle identity did not exist as an explicit concept -- only a
`for _ in range(max_cycles)` loop index, local to one process's
`start()` call, and a JSONL line position in `intelligence_cycle_path`.
`PaperBroker` and Position Intelligence are **not constructed or
referenced anywhere in the runtime** -- confirmed by source
inspection (`bujji/shadow_runtime/shadow_session_runner.py` never
imports either), not assumed. This matches Phase 15B's own audit:
Position Intelligence has no state to lose, and PaperBroker simply
isn't part of this runtime yet.

## 3. Startup architecture -- after

```
ShadowSessionRunner.__init__(..., regime_memory_event_store_path=None)
    -> recover_shadow_session(intelligence_cycle_path,
                               regime_memory_event_store_path, session_id)
           -> next_cycle_index = count of real lines already in
                                   intelligence_cycle_path (0 if none)
           -> if regime_memory_event_store_path given:
                  hydrate_regime_memory(store, session_id)
              else:
                  (RegimeMemoryState(), None)   # recovery not requested
    -> IntelligenceCycleRecorder(initial_regime_state=<hydrated or fresh>)
```

`regime_memory_event_store_path` defaults to `None`. With it unset,
`recover_shadow_session` always returns a fresh `RegimeMemoryState()`,
`next_cycle_index=0`, and `recovery_report=None` -- byte-identical to
pre-Phase-15C behavior, verified by the existing (now passing without
modification) `tests/test_shadow_runtime.py`,
`test_shadow_runtime_phase5.py`, `test_intelligence_cycle_recorder.py`.

## 4. Recovery flow

1. `ShadowSessionRunner.__init__` calls `recover_shadow_session()` once, synchronously, before any broker call.
2. If no event store path is given: clean fresh session, exactly as before.
3. If given but the store file doesn't exist yet: `RECOVERY_COMPLETE`, `events_discovered=0` -- a real, honest "nothing to recover" result, not an error.
4. If given and events exist: replay through `RegimeMemoryState.advance()` (Phase 15B's event-derived reconstruction, unchanged) and seed the new `IntelligenceCycleRecorder` with the resulting state.
5. Every cycle thereafter, if the event store is configured, the runner persists that cycle's **raw** regime signal (before `advance()`, including `None`) via `record_regime_cycle()` -- additive, wrapped in the same `try/except` that already protects the Continuous Intelligence Observatory step, so a persistence failure degrades to a logged error, never crashes the loop.
6. The final `ShadowSessionArtifact` carries the recovery result as a new, defaulted field: `recovery_report: Optional[Dict] = None`.

## 5. State ownership

| State | Owned by | Persisted/hydrated in Phase 15C? |
|---|---|---|
| `RegimeMemoryState` | `IntelligenceCycleRecorder` | Yes |
| `MarketStateBuilder`'s `ObservationMemory` (episodes/events) | `IntelligenceCycleRecorder` | **No -- new finding, see Remaining Limitations** |
| `PaperBroker` positions/P&L | N/A -- not constructed by the runtime | N/A |
| Position Intelligence thesis state | N/A -- no state exists | N/A |

## 6. Session / cycle identity

- `session_id` is supplied by the caller and is already deterministic per real calendar day in the one production launcher (`scripts/run_shadow_live_observatory.py`: `f"SHADOW-OBSERVATORY-{today}"`) -- a same-day restart naturally reuses the same session.
- `cycle_id` did not exist before Phase 15C. The smallest safe deterministic mechanism was added: `cycle_id = f"c{n}"` where `n` starts at `count_persisted_cycles(intelligence_cycle_path)` (a real count of lines already written this session) and increments once per cycle. This is derived from real, already-observed data -- never fabricated.
- Duplicate/collision protection is NOT primarily this counter -- it's `EventStore`'s existing deterministic `event_id = hash(session_id, cycle_id)` first-occurrence-wins dedup (Phase 15B). Even if the counter were ever slightly off, the store's own idempotency is the real safety net, proven directly by `test_restart_with_duplicate_event_is_not_double_applied`.

## 7. Recovery statuses (unchanged from Phase 15B, now observed at runtime)

`RECOVERY_COMPLETE` / `RECOVERY_PARTIAL` / `RECOVERY_FAILED`, now surfaced on `ShadowSessionArtifact.recovery_report`. A `None` `recovery_report` (distinct from a `RECOVERY_COMPLETE` dict) means recovery was never requested for that run.

## 8. Failure behavior -- all 12 required scenarios, `tests/test_shadow_runtime_recovery.py`

1. Clean startup, no persisted state -- `RECOVERY_COMPLETE`, `events_discovered=0` -- PASS
2. Recovery not requested at all -- `recovery_report is None` -- PASS
3. Normal mid-session restart (2 cycles, kill, restart, 3 more) -- state, cycle count, and per-cycle `regime_memory` snapshots match an uninterrupted 5-cycle reference run exactly -- PASS
4. Restart with a torn final event -- `RECOVERY_PARTIAL`, 1 skipped, session still continues -- PASS
5. Restart with a duplicate final event -- not double-applied, `events_skipped_duplicate=1` -- PASS
6. Malformed persistence record -- skipped, `RECOVERY_PARTIAL` -- PASS
7. Schema version mismatch -- skipped, prior state preserved -- PASS
8. Missing component state (regime store never created, even though prior real cycles exist) -- cycle numbering still resumes correctly from real history -- PASS
9. Partially recoverable session (valid + malformed mixed) -- valid events still replayed -- PASS
10. Restart immediately after a real regime transition -- `previous_regime` preserved exactly, not lost or blended -- PASS
11/12. PaperBroker / Position Intelligence restart -- **honestly N/A at the runtime layer** (neither is wired into `ShadowSessionRunner` -- confirmed by source inspection, asserted by `test_paper_broker_is_not_wired_into_shadow_runtime_today` / `test_position_intelligence_is_not_wired_into_shadow_runtime_today`); PaperBroker restart itself remains fully covered at the state_persistence layer per Phase 15B.
- Multiple historical sessions in the same store -- isolated correctly -- PASS

16/16 tests passing, plus 6/6 dedicated safety tests.

## 9. Safety boundary

- No `place_order`/`modify_order`/`cancel_order` calls anywhere in the new/changed code (AST-verified, not text search).
- No forbidden imports (`execution_engine`, `risk_governor`, `trading_brain`, `capital_brain`, `fyers_apiv3`) in any Phase 15C file.
- `bujji/shadow_runtime/recovery.py` never references a broker object at all -- verified by direct string-absence check.
- `bujji/broker/guard.py` and `bujji/broker/hybrid.py` confirmed byte-untouched via `git diff --stat`.
- `shadow_session_runner.py`'s diff confirmed to add no new broker method call (diff-based, not just current-content).
- `intelligence_cycle_recorder.py`'s diff confirmed to contain zero removed lines -- purely additive.
- A real, useful safety test caught a real issue during this phase: an earlier draft read `record.get("market_state")` inside `shadow_session_runner.py` to extract the raw regime signal, which tripped the pre-existing `test_shadow_session_runner_not_coupled_to_market_state` grep-based safety test. Rather than weakening that test, the fix was architectural: `IntelligenceCycleRecorder` now exposes the raw per-cycle regime signal directly via a `last_raw_regime` property, so the runner never parses the recorder's internal record shape at all -- a cleaner boundary, not a workaround.

## 10. Real-data validation

Two distinct validations were run against the real 174-cycle
`SHADOW-OBSERVATORY-2026-08-06` session (kept clearly separate from the
deterministic synthetic tests above):

1. **Phase 15B (persistence layer)**: replaying the real regime
   sequence cycle-by-cycle through `hydrate_regime_memory` matched the
   session's own live-persisted `regime_memory` snapshots at all 174
   cycles, 0 mismatches.
2. **Phase 15C (runtime entry point, this phase)**: calling the actual
   `recover_shadow_session()` function -- the real function
   `ShadowSessionRunner.__init__` calls -- against the same real
   session reproduced the exact final state
   (`current_regime=COMPRESSED, duration_cycles=17`, matching the real
   session's own persisted final record) and correctly resumed cycle
   numbering at `174`, `RECOVERY_COMPLETE`, `events_replayed=174`.

No live session was fabricated; both validations read only the real,
already-recorded `intelligence_cycle.jsonl`.

## 11. Full regression

`4554 passed` (1 pre-existing, unrelated deprecation warning). No
safety-test exceptions were needed this phase beyond the one already
documented in Phase 15B (`paper.py` was not touched again this phase).

## 12. Files changed

New:
- `bujji/shadow_runtime/recovery.py`
- `tests/test_shadow_runtime_recovery.py`
- `tests/test_shadow_runtime_recovery_safety.py`

Modified (additive only, verified by diff-based safety tests):
- `bujji/shadow_runtime/shadow_session_runner.py` -- recovery wiring, new optional constructor param, per-cycle regime-event persistence.
- `bujji/shadow_runtime/shadow_session_artifact.py` -- new defaulted `recovery_report` field.
- `bujji/market_state/intelligence_cycle_recorder.py` -- new optional `initial_regime_state` constructor param, new `last_raw_regime` property.
- `tests/test_shadow_runtime_phase5.py` -- updated one exact-field-set assertion to include the new, non-decision `recovery_report` field.

## 13. Remaining limitations

- **New finding this phase**: `MarketStateBuilder`'s `ObservationMemory`
  (episode/event continuity) is ALSO genuinely stateful and is
  currently reset on every restart, same as `RegimeMemoryState` was
  before Phase 15B. It was outside this phase's scope (which targeted
  the components Phase 15B already built persistence for) but is now
  an explicitly disclosed gap for a future phase, not silently ignored.
- PaperBroker and Position Intelligence recovery remain unintegrated at
  the runtime layer simply because neither is constructed by
  `ShadowSessionRunner` today -- this is accurate, not a shortfall to
  paper over; when either is wired into the runtime (a future,
  explicitly separate phase), their already-built Phase 15B persistence
  functions are ready to be connected the same way regime memory was
  here.
- Cycle numbering's correctness depends on `intelligence_cycle_path`
  being supplied whenever `regime_memory_event_store_path` is -- both
  are always set together in the one real launcher today; this
  coupling is implicit rather than enforced, and is worth hardening
  (e.g. a startup validation error) before broader adoption.

## 14. Conclusion

A running Bujji shadow session can now be killed and restarted without
losing its regime-memory intelligence: recovery is automatic, additive
(opt-in via one new constructor parameter, default `None` = unchanged
behavior), deterministic, validated against 12 synthetic failure
scenarios plus a real 174-cycle session, and provably makes no new
broker-write capability reachable. Per the mission's explicit
ordering, PaperBroker lifecycle integration, live paper execution, and
Phase 15D (Trade Construction Hardening) remain deliberately
deferred to their own phases.
