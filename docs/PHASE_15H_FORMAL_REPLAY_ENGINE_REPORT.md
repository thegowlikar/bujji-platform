# Phase 15H -- Formal Replay Engine: Final Report

## 1. Forensic findings

Five prior phases (15B, 15C, 15D, 15E, 15G) each independently built
the SAME pattern from scratch: read a real persisted JSONL artifact,
replay it through the exact production reducer, compare to what live
code produced. Confirmed by direct inspection of each phase's own
hydration function (`hydrate_regime_memory`, `hydrate_observation_memory`,
`hydrate_premium_behaviour`, `hydrate_position_lifecycles`) and every
`real_data_validation_15*.py` script -- all share the identical shape:
diagnostics-based line reading, dedup, a pure per-item reducer call,
and a `RecoveryReport`.

**Authoritative vs. derived artifacts**: `market_snapshots.jsonl` is
the authoritative raw-observation log (confirmed in Phase 15D) --
`ObservationMemory`/PSI/MSSI/MDI, Greeks, and Premium Behaviour are all
fully derivable from it alone. `intelligence_cycle.jsonl` is a DERIVED
artifact (the live recorder's own conclusions) -- but for one specific,
real reason it is NOT fully redundant: **`MarketSnapshot` never
persists the spot candles a cycle used** (confirmed by direct source
inspection -- `fetch_spot_candles(broker, ...)` is called live and its
result is discarded after the cycle). Volatility Structure (VSB), and
everything downstream of it (consensus, opportunity, eligibility,
selection, TradeIntent), therefore CANNOT be independently
reconstructed from today's persisted raw artifacts. This is the
central architectural finding of this phase, and it is not
worked around: those fields are treated as **REFERENCED** data (read
verbatim from `intelligence_cycle.jsonl`), explicitly and permanently
distinguished from **RECONSTRUCTED** data (independently rebuilt).

No new persistence format was created -- `EventStore` (Phase 15B) and
the flat JSONL artifacts (Phase 8+) were both confirmed sufficient.

## 2. Replay architecture

`bujji/replay_engine/`:
- `models.py` -- `ReplayCheckpoint` (just a cycle index + session_id -- resuming means re-hydrating from the SAME real file, exactly how every prior phase's hydration functions already work; no separate serialized state blob), `ReplayCycleResult` (`reconstructed` dict + `referenced` dict, explicitly disjoint namespaces), `ReplaySession` (full result + `final_fingerprint` + per-domain `RecoveryReport`s), `MismatchRecord` (Step 7's 4-way classification).
- `engine.py` -- `ReplayEngine`, orchestrating ALREADY-EXISTING production functions directly: `MarketStateBuilder.process`, `build_market_direction`, `build_greeks_assessment`, `premium_behaviour.engine.evaluate`, `market_regime_memory.engine.evaluate` -- none forked, none reimplemented.
- `mismatch.py` -- `cross_check_field()`, the Step 7 comparator.

Naming was adapted from this codebase's own conventions (`RecoveryReport`
reused directly rather than inventing a competing report shape), per
the mission's own instruction to inspect existing conventions first.

## 3. Deterministic contract (Step 3)

`fingerprint_state()` normalizes tuples/lists (a JSON round-trip
artifact, never semantic) before hashing -- proven by a dedicated test
that a tuple-valued and list-valued payload with identical content
fingerprint identically. Fake-clock call counts and generated runtime
timestamps are never part of any RECONSTRUCTED field's identity (every
timestamp used is the REAL snapshot's own `timestamp` field, never a
wall-clock read). Semantic comparisons were never weakened to pass a
test -- confirmed by the real-data cross-check (Section 6) finding 0
mismatches using the FULL, unnormalized-for-anything-but-representation
comparison.

## 4. Checkpoint / resume (Step 5)

Tested at cycle 1, early warm-up (cycle 2), ~25%/50%/75%, and the
penultimate cycle of a 20-cycle fixture session -- in every case, the
resumed replay's cycle results and final fingerprint are BYTE-IDENTICAL
to the tail of an uninterrupted full replay. An ADDITIONAL, stronger
proof was added beyond what "resume" strictly requires: a genuine
file-truncation test (write only the first 10 cycles to a REAL file,
replay it, then append the remaining 10 and replay again) confirms the
exact same final fingerprint as an uninterrupted 20-cycle replay --
proving the invariant holds even when the "checkpoint" is a real,
physically smaller file, not just a skip-recording implementation
detail.

**7/7 checkpoint/resume tests passing.**

## 5. Real-data validation (Step 6) -- kept explicitly separate from fixtures

**REAL MARKET EVIDENCE**, against the complete
`SHADOW-OBSERVATORY-2026-08-06` session (174 real cycles):

- Total cycles replayed: **174/174**, `RECOVERY_COMPLETE`.
- Cross-check of every RECONSTRUCTED field with a real reference
  (`price_structure`, `market_structure`, `market_direction`,
  `regime_memory`) against the session's own persisted values: **696/696
  MATCH** (174 cycles x 4 fields), **0 mismatches**.
- Final `regime_memory` state: `current_regime=COMPRESSED,
  duration_cycles=17` -- matches Phase 15D/15C's own independently
  proven value exactly, now reproduced through the unified engine.
- **Greeks/Premium Behaviour cross-check: correctly `NOT_APPLICABLE`
  for all 174 cycles** -- this archived session predates Phase 15E
  (confirmed: 0 real `"greeks"`/`"premium_behaviour"` keys exist in its
  persisted `intelligence_cycle.jsonl`), so there is genuinely no
  reference to compare against. This was NOT silently skipped or
  fabricated as a match.
- Reconstructed Greeks/Premium Behaviour availability (3/174, 0/174)
  reconfirms Phase 15E's own real-data finding about this session's
  sparse ATM liquidity -- not a new or different result.
- Trajectory summary: consensus `UNANIMOUS_CONSENSUS` in 172/174
  cycles, `NO_CONSENSUS` in 2/174; opportunity state `MONITOR` in
  174/174; 8 distinct strategy families selected across the session
  (led by `VOLATILITY_COMPRESSION`, 48 cycles); **0/174 cycles had a
  real `trade_intent`** -- an honest, already-known limitation of this
  specific archived session (Phase 14C), not a Phase 15H defect.

**SEMANTIC FIXTURE EVIDENCE** (clearly labeled,
`tests/test_replay_engine.py`): empty session, one-cycle session,
malformed/duplicate/out-of-order/corrupted/truncated/schema-mismatched
records, all 7 checkpoint/resume boundaries, deterministic double
replay, session isolation, `UNKNOWN`/missing-dependency propagation
(referenced fields correctly `None` when no `intelligence_cycle_path`
is supplied), and all 4 mismatch classifications -- **28 tests, all
passing.**

## 6. Mismatch classification (Step 7)

All 4 required classes implemented and tested: `MATCH`, `MISMATCH`
(tagged `REAL_REPLAY_DEFECT` when found -- 0 occurred against real
data), `UNAVAILABLE` (either side genuinely missing), `NOT_APPLICABLE`
(no persisted reference exists for this field/cycle at all, as with
Greeks/Premium Behaviour on this pre-15E archived session). A
representation-only difference (tuple vs. list) is explicitly proven
to classify as `MATCH`, never a false `MISMATCH`.

## 7. Safety (Step 8)

6/6 dedicated safety tests: no forbidden imports/calls (AST-based), no
broker/PaperBroker/network/capital/margin references, no file opened
in write/append mode anywhere in the package (AST-verified), a real
regression proof that a full replay leaves its source
`market_snapshots.jsonl` byte-for-byte unchanged, and confirmation that
`guard.py`/`hybrid.py`/`trading_brain/`/`journal/` remain untouched
(with `paper.py`'s already-documented Phase 15B exception excluded from
this specific check, verified elsewhere). No new documented exceptions
were needed -- this phase touched no pre-existing protected boundary.

## 8. Regression (Step 10) -- exact counts

- Replay engine core/determinism/checkpoint tests: 28 (`test_replay_engine.py`)
- Safety tests: 6 (`test_replay_engine_safety.py`)
- **Phase 15H new tests: 34, all passing**
- **Full regression: 4737 passed** (1 pre-existing, unrelated deprecation warning)

## 9. Files changed

New (all additive, zero existing files modified):
- `bujji/replay_engine/{__init__,models,engine,mismatch}.py`
- `tests/test_replay_engine.py`
- `tests/test_replay_engine_safety.py`

## 10. Limitations (disclosed, not hidden)

- **VSB and everything downstream of it (consensus, opportunity,
  eligibility, selection, TradeIntent) cannot be independently
  reconstructed** -- the central, real architecture gap this phase
  discovered and documented rather than working around. Closing it
  would require persisting the spot candles a cycle actually used
  (a genuinely new, disclosed future capability, not attempted here).
- Position Lifecycle / Position Intelligence replay was NOT
  additionally wired into `ReplayEngine` this phase -- Phase 15G's own
  `hydrate_position_lifecycles` already IS a complete, independently
  proven replay mechanism for that domain; duplicating it into this
  engine's `run()` loop was judged unnecessary composition rather than
  a missing capability (both already share the identical
  `EventStore`-based pattern).
- `resume_from` in `ReplayEngine.run()` still iterates every prior
  cycle internally (to correctly rebuild state) rather than skipping
  computation for them -- a genuine "resume without recomputation"
  optimization was judged out of scope; determinism, not performance,
  was this phase's requirement, and the genuine-file-truncation test
  (Section 4) proves the semantic invariant independent of this
  internal detail.

## 11. Architecture gap audit (Step 12) and recommended next phase

Re-inspecting the whole system with the mission's priority list:

- **Adaptive position management**: still zero code (confirmed again). Phase 15G's own 3-event lifecycle model is the natural place to add `PositionAdjusted`/`PositionRiskReduced` -- small, additive, directly buildable now that both lifecycle identity (15G) AND a formal replay/validation substrate (15H) exist to prove it correct.
- **Outcome attribution**: still absent. The lifecycle now preserves everything an attribution engine would need (entry reasoning, thesis evolution, exit reason) -- the attribution LOGIC itself doesn't exist yet.
- **Market-flow intelligence / liquidity intelligence**: `LiquidityBrain`/`liquidity` field already exists (Phase 9) and is persisted every cycle, but genuine order-flow/tape-reading intelligence does not exist -- confirmed absent from the source tree.
- **Unified position + market state reasoning**: Position Intelligence (15F) already consumes market state as read-only evidence; Position Lifecycle (15G) now threads that over time. The remaining gap is a single reasoning surface that looks at BOTH a position's own state AND current market intelligence simultaneously to recommend action -- which is exactly adaptive management's own prerequisite.

**Recommended: Phase 15I -- Adaptive Position Management (Adjustment /
Risk Reduction), built as an additive extension of Phase 15G's existing
3-event lifecycle model.** This is the highest-leverage next step
because: (a) it has a real, proven identity to act against (15G), (b)
it has a real, proven way to validate itself deterministically (15H),
(c) it directly extends rather than duplicates existing infrastructure,
and (d) it is the mission's own stated next foundation once lifecycle
and replay both exist. Per the project's standing discipline, this
would remain strictly advisory (never triggering a real order) until a
separate, explicit PaperBroker-activation decision is made.
