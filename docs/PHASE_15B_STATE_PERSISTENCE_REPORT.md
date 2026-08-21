# Phase 15B -- Recovery / State Hydration: Final Report

## 1. Objective

Build a reusable, deterministic State Persistence + Hydration layer for
Bujji Options OS covering every genuinely stateful component, so a
crashed or restarted shadow session can recover cleanly -- before any
PaperBroker lifecycle integration or live paper execution work begins.

## 2. Audit: what is actually stateful

| Component | Stateful? | Persisted this phase? |
|---|---|---|
| `RegimeMemoryState` (market_regime_memory) | Yes -- cross-cycle, mutates every cycle | Yes |
| `PaperBroker` positions + realized P&L | Yes -- mutates on every fill | Yes (partial, disclosed) |
| Position Intelligence (thesis monitoring) | **No** -- pure functions only, confirmed by direct audit | N/A -- nothing to persist |
| Shadow Runtime / session cycle count | Trivially recoverable already (`len(intelligence_cycle.jsonl)`) | No new work needed |

No state was invented for Position Intelligence; the audit finding is
documented, not worked around.

## 3. Architecture

`bujji/state_persistence/`:
- `models.py` -- `PersistedEvent` (frozen: `event_id, event_type, session_id, cycle_id, timestamp, schema_version, provenance, payload`), `RecoveryReport`, `RECOVERY_COMPLETE`/`RECOVERY_PARTIAL`/`RECOVERY_FAILED`, `SCHEMA_VERSION = "1.0.0"`.
- `store.py` -- `EventStore`: atomic append-only JSONL (single `write()` + `flush()` + `os.fsync()`, so a crash can only ever truncate the *last* line); `read_events_with_diagnostics()` never raises on a missing file or a malformed/truncated line; `deduplicated_events()` is first-occurrence-wins by deterministic `event_id`.
- `regime_memory.py` -- `record_regime_cycle()` / `hydrate_regime_memory()`. Event-derived: hydration replays real regime readings through `RegimeMemoryState.advance()`, the *exact same function* the live `IntelligenceCycleRecorder` calls every cycle -- hydration cannot silently diverge from live behavior.
- `paper_broker.py` -- `record_paper_state()` / `hydrate_paper_broker()`. Snapshot-based, not replayed: captures the broker's own current state via its existing public read methods (`get_open_positions`, `get_realized_pnl`) after every mutating call. Deliberately does **not** reimplement `_apply_fill`'s netting logic externally, to avoid any risk of silently diverging from the real, protected logic in `paper.py`.

Two purely additive methods were added to `bujji/broker/paper.py`
(mirroring the existing `seed_position` precedent exactly):
`restore_position(symbol, side, qty, avg_price, entry_timestamp)` and
`restore_realized_pnl(symbol, amount)`. Nothing existing in `paper.py`
was modified or removed -- verified by an automated zero-removed-lines
diff check (`test_paper_broker_change_is_scoped_to_two_additive_methods`).

## 4. Recovery guarantees

- **Deterministic reconstruction**: same event log -> same state, always.
- **Idempotent replay / duplicate protection**: `event_id` is a deterministic hash of `(session_id, cycle_id[, sequence])`; duplicates are dropped by first-occurrence-wins before replay.
- **Schema/version metadata**: every event carries `schema_version`; a future/unknown version is skipped, never applied, and counted separately (`events_skipped_schema_mismatch`).
- **Corruption/truncation handling**: a torn final line (the only thing a crash mid-write can produce, given atomic single-writes) is skipped, counted, and does not stop replay of the valid events before it.
- **Unknown-state preservation**: a `None` regime reading is recorded and replayed *as* `None` -- `RegimeMemoryState.advance(None)` is a documented no-op, so a data gap is never fabricated into a transition.
- **No market-data dependency during hydration**: both hydration functions read only from the event file; zero live/network calls (enforced by `test_hydration_never_calls_connect`).
- **Explicit recovery status**: `RECOVERY_COMPLETE` (no events, or all events clean), `RECOVERY_PARTIAL` (some issues but something replayed -- also always PARTIAL for PaperBroker, since order history is a disclosed, deliberate non-goal), `RECOVERY_FAILED` (events existed but nothing usable could be reconstructed).

## 5. Crash/restart test results -- `tests/test_state_persistence.py`

All 13 required scenarios covered and passing:

1. Clean restart (regime memory state equivalence) -- PASS
2. Repeated hydration is idempotent -- PASS
3. Duplicate event never double-applied -- PASS
4. Interrupted/partial final write skipped, not raised -- PASS
5. Malformed record (wrong shape) skipped -- PASS
6. Unknown/`None` regime reading preserved, not fabricated -- PASS
7. Schema version mismatch skipped -- PASS
8. Missing state file -> `RECOVERY_COMPLETE`, not `FAILED` -- PASS
9. Multiple sessions isolated in the same store -- PASS
10. Continuation after hydration matches an uninterrupted run -- PASS
11. PaperBroker order/position preservation across restart -- PASS
12. Position Intelligence has no persistent state to hydrate (documents the audit finding) -- PASS
13. Regime memory preservation -- covered by 1/2/9/10 -- PASS

Plus 4 store-level unit tests (dedup, ordering, parent-dir creation,
serialization round-trip). **23/23 passing.**

A real bug was caught and fixed during this testing: `record_paper_state`
originally captured per-symbol realized P&L only for currently *open*
positions, so P&L from a fully closed position silently vanished on
hydration (`pnl_after == 0` instead of `2250.0` in test 11). Fixed by
reconciling the leftover (`realized_pnl_total` minus the sum of
per-open-symbol amounts) into a disclosed `__CLOSED_POSITIONS__` bucket,
which restores the exact total without inventing which symbol it came
from -- consistent with this module's existing choice not to
reconstruct order history.

## 6. Safety test results -- `tests/test_state_persistence_safety.py`

7/7 passing: no forbidden imports (`execution_engine`, `risk_governor`,
`trading_brain`, `capital_brain`, `fyers_apiv3`), no `place_order`/
`modify_order`/`cancel_order` calls anywhere in the package (AST-based
check, not text search, so docstrings discussing these methods don't
false-positive), no reference to `FyersBroker`/`HybridPaperBroker`, no
`.connect(` call, `guard.py`/`hybrid.py` confirmed byte-untouched via
git diff, `paper.py`'s diff confirmed to contain zero removed lines,
and the two new restore methods' actual source confirmed to contain no
`place_order`/`_call(`/`await` calls.

A pre-existing Phase 14B safety test
(`test_no_capital_or_risk_files_touched`) needed a documented, scoped
exception for `bujji/broker/paper.py` -- the same established pattern
used in every prior phase (`_phase15b_paper_exception`), cross-referenced
to the additive-only diff check above rather than loosened generally.

## 7. Real-data validation

Replayed the actual 174-cycle real session
`shadow_sessions/SHADOW-OBSERVATORY-2026-08-06/intelligence_cycle.jsonl`
cycle by cycle: at every single cycle, (a) a live-recomputed
`RegimeMemoryState` matched that cycle's own persisted `regime_memory`
snapshot, and (b) the state hydrated from the event store after that
cycle matched the live-recomputed state exactly (`current_regime`,
`previous_regime`, `duration_cycles`, `total_transitions`,
`transition_counts`). **0 mismatches across all 174 real cycles.**
Final hydrated state, live-recomputed state, and the real session's own
persisted final snapshot are identical:
`current_regime=COMPRESSED, duration_cycles=17, total_transitions=9`.
Final recovery report: `RECOVERY_COMPLETE`, `events_discovered=174`,
`events_replayed=174`, 0 skipped of any kind.

## 7a. Runtime integration (Phase 15C)

This layer is now wired into actual `ShadowSessionRunner` startup --
see `docs/PHASE_15C_RUNTIME_RECOVERY_REPORT.md` for the full runtime
recovery flow, cycle-identity mechanism, and 12 failure-scenario
results. In short: `ShadowSessionRunner`'s new (default-`None`,
opt-in) `regime_memory_event_store_path` constructor parameter calls
`hydrate_regime_memory` at startup and seeds `IntelligenceCycleRecorder`
with the result, and persists each cycle's raw regime reading via
`record_regime_cycle` as it happens -- a live process can now actually
be killed and restarted without losing this state, not just prove it
could in isolation.

## 8. Full regression suite

`4532 passed` (1 pre-existing deprecation warning, unrelated). No other
failures beyond the one documented, scoped exception in section 6.

## 9. Files changed

New:
- `bujji/state_persistence/models.py`
- `bujji/state_persistence/store.py`
- `bujji/state_persistence/regime_memory.py`
- `bujji/state_persistence/paper_broker.py`
- `tests/test_state_persistence.py`
- `tests/test_state_persistence_safety.py`

Modified (additive only):
- `bujji/broker/paper.py` -- `restore_position`, `restore_realized_pnl` added; nothing existing changed.
- `tests/test_phase14b_safety.py` -- scoped exception for the above, documented inline.

## 10. Remaining gaps

- PaperBroker recovery is deliberately **partial**: order *history* is
  never reconstructed, only current positions and realized P&L. This is
  disclosed via `RECOVERY_PARTIAL` and `unresolved_notes`, never hidden.
- Position Intelligence has no persistence because it has no state
  today; if it gains stateful thesis tracking in the future, this layer
  should be extended to cover it then, not before.
- No wiring yet into the live shadow runtime's startup path (i.e.
  nothing currently *calls* `hydrate_regime_memory`/`hydrate_paper_broker`
  automatically on process start) -- this phase built and proved the
  recovery mechanism itself; integrating it into the runtime's actual
  startup sequence is separate, deferred work.

## 11. Conclusion

Recovery is now genuinely trustworthy: deterministic, event-derived,
crash-safe, and validated against both synthetic edge cases and a real
174-cycle session with zero divergence. Per the mission's explicit
ordering, PaperBroker lifecycle integration, live paper execution, and
further strategy construction work remain deferred until this
foundation existed and was proven -- which it now is.
