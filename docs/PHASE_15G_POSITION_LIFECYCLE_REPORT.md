# Phase 15G -- Position Lifecycle Intelligence: Final Report

## 1. Forensic findings

**Identity audit**: `ShadowTradeCandidate.candidate_id` (Phase 14,
`"STC-" + md5(source_cycle_id|family)`) is deterministic but identifies
"what a given cycle WOULD construct" -- recomputed identically every
time that cycle/family pair is observed, regardless of whether Bujji
ever acts on it. It is NOT a suitable canonical position identity by
itself; a position identity must instead mark the deliberate MOMENT of
entry, a distinct one-time event. No other existing identifier
(broker order id, PaperBroker's internal symbol-keyed dict, journal
entries) is session-independent, restart-safe, or multi-leg-aware
enough to serve as the canonical identity either.

**Lifecycle fragment audit**: `bujji/trading_brain/{exit_engine,
portfolio_valuation, position_sizing, risk_governor, capital_brain}`
and the `bujji/journal/` package (many journals) are confirmed, AGAIN,
disconnected legacy lineage -- not imported anywhere in the live
Shadow/MSI pipeline (consistent with every prior phase's own finding,
re-verified this phase rather than assumed). None were wired in, per
the mission's explicit "do not wire legacy components merely because
they exist" instruction. `bujji/broker/paper.py` (PaperBroker) has no
lifecycle identity concept at all -- its `_positions` dict is keyed by
plain `symbol` string, with no position-level (multi-leg) grouping.

**EventStore sufficiency (Step 4)**: `bujji.state_persistence.EventStore`
(Phase 15B) was confirmed fully sufficient to represent lifecycle
events -- atomic append-only JSONL, `event_id`-based idempotent dedup,
`session_id`/`cycle_id`/`timestamp`/`schema_version` already present on
every `PersistedEvent`. **No new persistence mechanism was built.**

## 2. Canonical identity decision

`position_id = "POS-" + md5(session_id|candidate_id|entry_timestamp)[:24]`

- Deterministic: same three real inputs always produce the same id (replay-safe).
- Session-scoped: never collides across sessions.
- Independent of broker order ids, symbol alone, or JSONL line position.
- A different `entry_timestamp` for the same `candidate_id` correctly produces a DIFFERENT position -- a real re-entry is a genuinely different position, never silently merged.

`leg_id = "LEG-" + md5(position_id|role|option_type|strike|expiry)[:16]` --
scoped under its parent `position_id`, so identical leg shapes across
two different positions never collide. All legs of a multi-leg
position share one `position_id`; the lifecycle identity lives at the
POSITION level, never fragmented per-leg.

## 3. Lifecycle architecture

Deliberately minimal, per Step 2's explicit instruction not to assume
every listed state belongs in the first implementation:

```
(no event yet)  ->  OPEN  ->  CLOSED
                      |
                      +-- MONITORING (DERIVED display label: OPEN +
                          >=1 thesis evaluation recorded -- NOT a
                          stored transition, since it isn't a real
                          state change worth its own event type)
```

3 event types (`bujji/position_lifecycle/`):
- `POSITION_OPENED` -- position_id, compact `EntrySnapshot` (references the real source intelligence_cycle record by `source_cycle_id` rather than duplicating a whole MarketSnapshot, per Step 6), all legs.
- `THESIS_EVALUATED` -- position_id, cycle_id, the real `ThesisEvaluation.to_dict()` (Phase 15F) verbatim.
- `POSITION_CLOSED` -- position_id, closed_at, exit_reason.

`ADJUSTING`/`RISK_REDUCING`/`EXIT_PENDING`/`OUTCOME_RECORDED` are
explicitly deferred -- no code path references them, consistent with
the mission's own minimum-state-machine instruction.

Every event is applied through ONE pure reducer, `apply_event()` --
the SAME function a live session and a hydration/replay call both go
through, so replay can never silently diverge from live behavior (same
discipline as every Phase 15B-15F reducer).

## 4. Multi-leg correctness (Step 5)

Proven with fixtures: a 4-leg Iron Condor (`SHORT_CALL`/`LONG_CALL`/
`SHORT_PUT`/`LONG_PUT`) produces 4 distinct `leg_id`s, all linked to
ONE `position_id`; each leg preserves its own real entry premium/delta
(copied verbatim from the candidate's already-solved leg data, Phase
14 -- nothing re-derived).

## 5. Entry snapshot (Step 6)

`EntrySnapshot` is compact: `candidate_id` + `source_cycle_id`
(reference, not duplication -- the full context is always recoverable
from the already-persisted `intelligence_cycle.jsonl`), plus a small
set of real scalars (direction, regime, thesis, confidence, underlying
price) and the SAME real `entry_greeks`/`entry_premium_behaviour`
dicts Phase 15F's `PositionEntrySnapshot` already captures -- answering
"what exactly did Bujji believe when this position became OPEN?"
without duplicating an entire MarketSnapshot.

## 6. Thesis continuity (Step 7)

Every `THESIS_EVALUATED` event carries `position_id` + `cycle_id` +
the real `ThesisEvaluation`. Position Intelligence itself remains
completely stateless (Phase 15F's own finding, unchanged) -- the
lifecycle accumulates a real, ordered HISTORY of evaluations as
EVENTS, not by making Position Intelligence itself stateful. Proven:
4 sequential evaluations (INTACT, INTACT, WEAKENING, INVALIDATED)
replay in exact order; `final_thesis_status` reflects the MOST RECENT
evaluation, never a summary/vote.

## 7. Transition guards (Step 8) -- all required scenarios proven

- `candidate -> OPEN` only via a real `POSITION_OPENED` event -- PASS
- `CLOSED -> OPEN` without a new identity -- REJECTED -- PASS
- `OPEN -> OPEN` duplicate event, IDENTICAL content -- IDEMPOTENT -- PASS
- `OPEN -> OPEN` duplicate `position_id`, DIFFERENT content -- REJECTED (conflicting event) -- PASS
- unknown `position_id` (thesis eval or close before open) -- REJECTED -- PASS
- duplicate `event_id` -- IDEMPOTENT (via `EventStore`'s existing dedup, before even reaching the reducer) -- PASS
- malformed event (missing required field) -- REJECTED -- PASS
- event from another session -- REJECTED -- PASS
- thesis evaluation on an already-CLOSED position -- REJECTED -- PASS
- second `POSITION_CLOSED` for an already-closed position -- REJECTED, first close never overwritten -- PASS

**20/20 core lifecycle tests passing** (`tests/test_position_lifecycle.py`).

## 8. Event ordering (Step 9)

Every `PersistedEvent` already carries `session_id`/`event_id`/
`event_type`/`cycle_id`/`timestamp` (Phase 15B) -- no new sequence
field was needed. Ordering correctness for THIS package rests on
`EventStore`'s existing atomic-append + dedup guarantees, proven again
in this phase's own recovery tests (duplicated/malformed/reordered-
by-session events all handled correctly).

## 9. Recovery (Step 10) -- 13 tests, all real semantic-state equivalence

Reuses `EventStore` directly. Tested restart at: before entry,
immediately after entry, mid-monitoring, after thesis weakening,
immediately before close, after close, a multi-leg position, and
multiple simultaneous positions in one session -- every test compares
full `PositionLifecycle.to_dict()` equality (status, legs, thesis
history, close data), not merely "the file loads." Also: torn final
event degrades to `RECOVERY_PARTIAL` without losing prior state;
duplicate `event_id` is idempotent at the store layer; an event tagged
for another session is rejected during replay, never silently applied.

**13/13 recovery tests passing** (`tests/test_position_lifecycle_recovery.py`).

## 10. PaperBroker integration audit (Step 11)

**Deliberately NOT wired this phase.** Findings:
1. PaperBroker knows only per-symbol positions (`_positions: Dict[str, dict]`), no multi-leg grouping, no lifecycle identity.
2. It lacks `position_id` entirely -- linking it additively (a `restore_position`-shaped setter tagging entries with a `position_id`) is technically possible but was NOT attempted this phase, since no real position has ever been opened through it in any persisted session (confirmed again this phase).
3. Its state COULD be treated as an observation (a snapshot of what a hypothetical broker would show), never the source of truth -- the lifecycle's own event log is the source of truth, consistent with the whole project's advisory-only discipline.
4. Order history reconstruction remains the same disclosed Phase 15B limitation -- NOT silently resolved or hidden here.
5. **Conclusion: integration is premature.** Lifecycle identity and recovery needed to be proven first (this phase); PaperBroker linkage is real, but separate, future work.

## 11. P&L linkage (Step 12) / Outcome placeholder (Step 13)

No new P&L mathematics were built (verified by a dedicated safety
test). `PositionLifecycle.realized_pnl` exists as an honest `Optional[float]
= None` placeholder -- linkage to a real PaperBroker P&L number is
future work, not fabricated here. At closure, the lifecycle already
preserves enough to eventually answer the mission's outcome-attribution
questions: `entry` (why opened, what was expected), `thesis_evaluations`
(when it weakened/invalidated and why, via each `ThesisCheck.reason`),
`exit_reason` (why it closed) -- `realized_pnl`/outcome ATTRIBUTION
itself remains explicitly deferred to Phase 15H+, per the mission's
own instruction.

## 12. Safety (Step 14)

8/8 dedicated safety tests: no forbidden imports/calls (AST-based), no
broker/network/capital/margin references, no side-effecting-looking
calls in the reducer, `guard.py`/`hybrid.py` byte-untouched, invalid
transitions fail closed (direct test), identity cannot be silently
reused for different content (direct test), no P&L math computed here.
No new documented exceptions were needed -- this phase touched no
pre-existing protected boundary.

## 13. Real-data validation vs. semantic fixtures (Step 15) -- kept explicitly separate

**REAL MARKET VALIDATION** (against `SHADOW-OBSERVATORY-2026-08-06`):
this session never opened a real PaperBroker position (confirmed,
consistent with every prior phase) -- so, per the mission's explicit
instruction, NO fabricated OPEN/MONITOR/CLOSE lifecycle evidence was
produced. Instead, the INFRASTRUCTURE a real lifecycle would need was
validated against real data: 174/174 real cycle timestamps unique
(cycle identity usable), 153/174 real cycles had a real selected
strategy family (candidate-construction-ready moments), 0/174 had a
real `trade_intent` populated (an honest finding consistent with the
known archived-data limitation from Phase 14C -- this session predates
some Phase 14B fixes), 0 `position_id` collisions across 153 real
deterministic identity computations, and real cycle timestamps
confirmed strictly increasing (a real ordering prerequisite).

**SEMANTIC FIXTURE VALIDATION** (clearly labeled, `tests/test_position_lifecycle.py`
and `tests/test_position_lifecycle_recovery.py`): full OPEN -> MONITOR
-> CLOSE behavior, multi-leg correctness, all transition guards, and
crash/restart recovery -- 33 tests total, all deterministic fixtures,
never presented as real-market evidence.

## 14. Replay (Step 16)

Proven as a direct consequence of Step 10's recovery tests: `events ->
replay -> PositionLifecycle` via the same `apply_event` reducer used
live; `PositionLifecycle(original) == PositionLifecycle(replayed)` in
every decision-relevant field, confirmed for single- and multi-position,
single- and multi-leg cases. A dedicated formal Replay Engine (Gap D,
below) was NOT built this phase -- the audit found this lifecycle
model to be a strong natural foundation for one, but building the
formal engine itself was judged separate, future work rather than an
in-scope requirement of Phase 15G.

## 15. Regression (Step 17) -- exact counts

- Lifecycle unit/transition/multi-leg tests: 20 (`test_position_lifecycle.py`)
- Recovery/restart tests: 13 (`test_position_lifecycle_recovery.py`)
- Safety tests: 8 (`test_position_lifecycle_safety.py`)
- Real-data validation: 1 script, all assertions passing (infrastructure-level, see Step 15)
- **Phase 15G new tests: 41, all passing**
- **Full regression: 4703 passed** (1 pre-existing, unrelated deprecation warning)

## 16. Files changed

New (all additive, zero existing files modified):
- `bujji/position_lifecycle/{__init__,models,identity,engine,recovery}.py`
- `tests/test_position_lifecycle.py`
- `tests/test_position_lifecycle_recovery.py`
- `tests/test_position_lifecycle_safety.py`

## 17. Remaining gaps

- PaperBroker remains unlinked (Step 11's own conclusion -- correctly premature).
- `realized_pnl` is an honest placeholder, not yet computed or linked.
- No formal Replay Engine yet (ad hoc replay proven sufficient for this phase's own validation).
- Adaptive management (adjustment/hedge/roll/partial exit) has no code path yet -- correctly deferred, since it needs the lifecycle identity this phase just built.
- Outcome attribution itself (WHY a trade succeeded/failed, selection vs. construction vs. management fault) is explicitly Phase 15H+.
- This session's `trade_intent` field being 0/174 populated is a known archived-data limitation (Phase 14C), not a Phase 15G defect -- worth a fresh live session validation once market data is available again.

## 18. Architecture gap audit (Step 18) and recommended next phase

Re-inspecting with the mission's own priority list:

- **A. Adaptive position management**: still entirely absent as code, but NOW has a real identity to act against. The natural next check-in-progress is a `PositionAdjusted`/`PositionRiskReduced` event pair added to the SAME 3-event model -- small, additive, and would directly extend what this phase built rather than starting a new subsystem.
- **B. Outcome memory**: the lifecycle now preserves everything needed for it (entry reasoning, thesis evolution, exit reason) -- but attribution logic itself (was the FAILURE from selection, construction, timing, or management?) doesn't exist yet.
- **C. Formal Replay Engine**: the lifecycle event model is now a strong, proven foundation (Step 16) -- of the 5 audit areas, this is the most mechanically ready to build next, since Phases 15B-15G have all independently proven the SAME event-replay pattern five separate times now (regime memory, observation memory, premium behaviour, position intelligence composition, position lifecycle) -- consolidating that into one formal, reusable engine is now clearly justified rather than premature.
- **D. PaperBroker integration**: still correctly deferred per Step 11's own conclusion.
- **E. Dynamic strategy adaptation**: still correctly deferred -- no feedback loop from lifecycle/outcome back into same-session strategy selection exists, and none should be built before outcome attribution itself exists (Gap B).

**Recommended: Phase 15H -- Formal Replay Engine.** Five independent
phases (15B/15C/15D/15E/15G, and 15F's own composition test) have each
built a near-identical "replay real persisted events through the same
pure function live code uses" pattern from scratch. Consolidating this
into one formal, reusable Replay Engine is now the highest-leverage
infrastructure investment: it would let EVERY future phase (adaptive
management, outcome memory, dynamic strategy adaptation) validate
itself the same proven way without re-deriving the pattern again, and
it directly serves the mission's stated principle that Bujji's
intelligence layer must stay trustworthy and verifiable before it
gains more decision-making capability.
