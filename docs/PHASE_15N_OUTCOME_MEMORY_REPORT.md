# Phase 15N -- Outcome Memory Layer: Final Report

## 1. Forensic audit (verified from source, Step 1)

| Question | Finding |
|---|---|
| What outcome information exists today | `PositionOutcomeAttribution` (Phase 15J) is the canonical, already-complete causal record -- `readiness`, `outcome_direction`, `realized_pnl`, `primary_cause`, `contributing_factors`, `protective_factors`, `evidence` (per-dimension, itself carrying `STRENGTH_UNKNOWN`/`IMPACT_UNKNOWN` as first-class values), `narrative`. |
| Canonical fields | `PositionLifecycle.to_dict()` (entry snapshot, legs, thesis/management history, `structured_exit`) and `PositionOutcomeAttribution.to_dict()` -- both already the single source of truth for their domains, never re-derived by any downstream phase. |
| Derivable fields | `underlying_symbol`, `entry_regime`, `entry_direction`, greeks/premium-behaviour presence, management-assessment presence -- all real, already-captured fields, extracted for query convenience only. |
| Missing fields | Account equity/margin (broker-only, Phase 15L's own finding, still true); portfolio-level context at the moment of any given position's outcome (no session in this codebase has ever built a `PortfolioSnapshot` at the exact moment a position closed -- see Step 12 below). |
| What should become durable memory | The mission's own four categories (identity, market context, decision context, outcome, attribution) -- resolved by embedding the REAL `PositionLifecycle`/`PositionOutcomeAttribution` `to_dict()` output VERBATIM rather than re-flattening dozens of new Optional fields that could drift from the canonical source over time. |
| Can `EventStore` be reused directly | YES -- `PersistedEvent` (Phase 15B) is fully generic, no position-lifecycle-specific shape. No new persistence architecture needed. |
| Is a new event type sufficient | YES -- one new event, `OUTCOME_MEMORY_RECORDED`, carrying the full record payload. No second persistence format. |
| Can any legacy memory implementation be reused | NO -- confirmed by direct inspection: `bujji/trading_brain/` has no outcome-memory or learning package at all, only the already-disconnected `portfolio_risk_aggregator`/`portfolio_valuation` (Phase 15M's own finding, reconfirmed here). |

**Critical architectural finding, not assumed from any prior report**: every previous `EventStore` consumer (`PositionLifecycle`, `RegimeMemoryState`, `ObservationMemory`) is deliberately PER-SESSION -- `position_lifecycle.engine.apply_event` explicitly REJECTS any event whose `event_session_id` doesn't match the target session. Outcome Memory is architecturally different BY DESIGN: the entire point of durable memory is to span many sessions. `hydrate_outcome_memory` therefore takes NO target `session_id` at all and accepts every well-formed event regardless of origin session -- `session_id` survives as a per-record IDENTITY/QUERY field, never a hydration-time isolation gate. This is documented explicitly in `models.py`'s own module docstring so no future phase mistakes it for an oversight.

## 2. Outcome Memory model (Step 2)

New package: `bujji/outcome_memory/` (`models.py`, `engine.py`, `recovery.py`, `query.py`). `OutcomeMemoryRecord` stores:
- **Identity**: `memory_id` (deterministic, see Step 5), `session_id`, `position_id`, `candidate_id`, `strategy_family`, `entry_timestamp`, `exit_timestamp`, `recorded_at`.
- **Market/decision/outcome/attribution context**: NOT re-flattened into dozens of new fields -- embedded VERBATIM as `lifecycle_snapshot` (the real `PositionLifecycle.to_dict()`, carrying entry regime/direction/Greeks/premium-behaviour/thesis-evaluations/management-assessments/structured-exit) and `attribution_snapshot` (the real `PositionOutcomeAttribution.to_dict()`, carrying primary cause/contributing/protective factors/evidence/narrative). This is deliberately the MOST faithful way to preserve historical reasoning context -- a lossy re-summarization risks silently drifting from the canonical source as those upstream models evolve; the verbatim snapshot cannot.
- **Query-convenience extracts**: a small number of top-level fields (`entry_regime`, `entry_direction`, `underlying_symbol`, `outcome_direction`, `realized_pnl`, `pnl_status`, `final_thesis_status`, `primary_cause`) copied verbatim for fast filtering without re-parsing nested dicts on every query -- never a second, competing copy of the truth.

"Do NOT store fields merely because they exist" (Step 2's own instruction) was applied directly: fields with no proven future analytical value (e.g. raw Greeks numeric values duplicated at the top level, individual evidence-item text duplicated outside the snapshot) were deliberately NOT extracted -- they remain available inside the verbatim snapshots for any caller who needs them, without bloating the query-convenience surface.

## 3. Epistemic honesty (Step 3)

Four first-class statuses (`STATUS_KNOWN`/`STATUS_UNKNOWN`/`STATUS_NOT_APPLICABLE`/`STATUS_NOT_AVAILABLE`) applied to every field the mission specifically called out:
- `pnl_status`: `KNOWN` only when `structured_exit.pnl_status == COMPLETE`; `UNKNOWN` for both a genuinely missing P&L AND a `PARTIAL` resolution (never presented as more resolved than it is).
- `greeks_status` / `premium_behaviour_status`: `KNOWN` only when the real `entry_greeks`/`entry_premium_behaviour` dict is present; `UNKNOWN` otherwise.
- `management_status`: `NOT_APPLICABLE` when zero management events were ever recorded for the position (a legitimate case, not missing evidence) vs. `KNOWN` when at least one exists.
- `portfolio_context_status`: `NOT_AVAILABLE` unless a real `PortfolioSnapshot` was explicitly supplied at recording time (never fabricated).
- A record hydrated from an old, pre-Phase-15N-shaped dict (missing these fields entirely) defaults every one of them to `NOT_AVAILABLE` via `OutcomeMemoryRecord.from_dict`'s own `.get(..., STATUS_NOT_AVAILABLE)` defaults -- proven directly (`test_old_legacy_record_hydrates_with_not_available`).

## 4. Event-sourced persistence (Step 4)

Reuses `EventStore` directly -- zero new persistence architecture. One new event type, `OUTCOME_MEMORY_RECORDED`. Append-only (inherited from `EventStore.append`'s own atomic single-write guarantee); deterministic (pure `apply_event` reducer, same live/replay-identical discipline as every prior phase); idempotent (exact-duplicate `event_id` caught by `EventStore`'s own dedup, exact-duplicate CONTENT for the same `memory_id` caught by the reducer as `TRANSITION_IDEMPOTENT`); conflicting-content rejection (different content for the same `memory_id` is `TRANSITION_REJECTED`, original preserved -- memory is immutable); session isolation of IDENTITY (proven -- see Step 1's finding); schema/version awareness (reuses `RECOGNIZED_SCHEMA_VERSIONS`); recovery after restart and torn-record tolerance (Section 6 below).

## 5. Memory identity (Step 5)

`memory_id_for(session_id, position_id, attribution_version)` -- deterministic MD5 hash, same style as `position_id_for`/`leg_id_for` (Phase 15G) and `client_order_id_for` (Phase 15L). `attribution_version` is the attribution's own `evaluation_timestamp`, included deliberately: if a position is ever re-attributed (e.g. a corrected replay), the resulting memory identity is genuinely DIFFERENT rather than silently overwriting the original historical fact -- memory records are immutable, never updated in place. Collision-resistant (24-hex-char MD5 slice, same proven scheme as every prior identity function); session-safe and cross-session-collision-proof (`session_id` is a direct input, proven -- `test_memory_id_deterministic_and_session_scoped`); replay-safe (pure function of already-real inputs); independent of any broker order ID (no broker value anywhere in the hash).

## 6. Query interface (Step 6)

`bujji/outcome_memory/query.py` -- `filter_records` (explicit, real-field-only filtering: session/strategy family/regime/direction/underlying) plus six composable read-only functions: `query_outcome_distribution`, `query_thesis_invalidation_rate`, `query_management_recommendation_outcomes`, `query_attribution_cause_distribution`, `query_pnl_summary`, `query_regime_performance`. Every result is an `OutcomeSample` carrying its own `sample_size` and `status` (`SUFFICIENT`/`INSUFFICIENT_HISTORY`, gated on `MIN_SAMPLE_SIZE = 3`) -- a caller can never mistake a tiny sample for a confident conclusion (proven directly, Section 8).

## 7. No feedback path (Step 7) -- the phase's most important boundary

Proven by a dedicated safety test suite (`test_outcome_memory_safety.py`, 8 tests):
- No import anywhere in the package of `msi_strategy_selection_foundation`, `msi_strategy_selector`, `msi_strategy_eligibility`, `msi_decision_synthesis`, `msi_trade_intent`, execution modules, `bujji.broker` (any PaperBroker access at all), FYERS, or `risk_governor` -- checked by AST, the strongest available guarantee (`test_no_forbidden_imports`).
- `query.py` specifically double-checked via raw source-text scan for even an indirect reference to decision/strategy/execution vocabulary (`test_query_module_never_imports_decision_or_execution_paths`) -- belt-and-suspenders on the module most likely to be read by a future phase.
- No function anywhere in the package is named like a decision/recommendation/action producer (`test_no_function_in_this_package_returns_a_decision_or_action`) -- one false positive was caught and fixed during this test's own construction: `query_management_recommendation_outcomes` legitimately QUERIES about a historical recommendation and was initially flagged by a naive substring check; narrowed to a prefix check so the test verifies the real property (no function DECIDES/RECOMMENDS/ADVISES) without banning a legitimate query name.
- Records proven immutable (`OutcomeMemoryRecord` is a frozen dataclass; conflicting content is rejected at the reducer layer, proven both functionally and as a dedicated safety test).
- The architecture is `Decision -> Outcome -> Memory` throughout -- nothing in this package is called by, or feeds a return value into, any live decision path. `bujji.position_management` itself is on the forbidden-import list (memory reads its OUTPUT, verbatim, already-recorded inside `lifecycle_snapshot` -- it never calls the live engine).

## 8. Historical reconstruction (Step 8) -- 18/18 scenarios, all passing

`tests/test_outcome_memory.py`: profitable trade, losing trade, unknown outcome, unknown P&L (legacy close with no `structured_exit`), invalidated thesis, intact thesis, management assessment faithfully carried through (whatever the real engine actually recommends, never hand-faked), no management (`NOT_APPLICABLE`), multi-leg trade, partial exit (one leg's exit evidence genuinely missing -> `PNL_PARTIAL` at the lifecycle level, honestly `UNKNOWN` at the memory-query level -- never presented as more resolved), execution slippage/fees preserved verbatim, missing Greeks (`UNKNOWN`), present Greeks (`KNOWN`), missing premium behaviour (`UNKNOWN`), old legacy record (`NOT_AVAILABLE` across every Phase-15N-only field), duplicate attribution (`IDEMPOTENT`), conflicting attribution (`REJECTED`, original preserved), a `NOT_READY` attribution correctly produces NO record at all, and deterministic session-scoped memory identity.

## 9. Query correctness (Step 9) -- 10/10 tests, all passing

`tests/test_outcome_memory_query.py`: a 1-record sample correctly reports `INSUFFICIENT_HISTORY`; a 3-record sample correctly reports `SUFFICIENT`; filtering by strategy family/regime/underlying/session never mixes incompatible cohorts (each proven with a real mixed-cohort fixture); P&L summaries never treat an `UNKNOWN` record's contribution as zero (proven with a mixed known+unknown cohort); management-recommendation queries correctly exclude records with no management event at all (never counted as a silent "didn't recommend X").

## 10. Real-data validation (Step 10)

**Result: `NO_REAL_OUTCOME_MEMORY_AVAILABLE`.** No persisted session contains real new-Core `position_lifecycle` (`position_id`-keyed) events, so no real `PositionOutcomeAttribution` could ever have been produced, so no real `OutcomeMemoryRecord` could ever exist yet -- consistent with every prior phase's finding (15G-15M).

- **REAL**: `EventStore` ordering/replay determinism; every reducer/replay code path this phase's own test suite exercises directly (real `apply_event`, real `attribute_position_outcome`, real `build_outcome_memory_record`, never mocked).
- **LEGACY**: `shadow_sessions/DRY_RUN_2026-05-25/` contains a real, genuinely-executed paper trade -- but under the disconnected `trading_brain` journal's `position_group_id` scheme (the same finding Phase 15L's and 15M's own audits made). No compatibility bridge exists; per this phase's explicit instruction, **not** converted into new-Core evidence.
- **FIXTURE**: all 46 new tests this phase -- reconstruction, replay/recovery, query correctness, safety -- built via the real reducers with constructed positions, never presented as real-market evidence.
- **NOT_AVAILABLE**: any real historical population for the query interface to report statistics over. Per the mission's own explicit acceptance criterion, the infrastructure itself is fully trusted even though its real population is currently empty.

## 11. Replay and recovery (Step 11) -- 10/10 tests, all passing

`tests/test_outcome_memory_replay.py`, reusing `EventStore` directly: uninterrupted recording+hydration; restart mid-recording (fresh `EventStore` against the same file, then more events appended, then re-hydrated); duplicate event deduped at the `EventStore` layer; conflicting event rejected at the reducer layer (original preserved); malformed event and torn JSONL both degrade to `RECOVERY_PARTIAL` without losing the earlier valid record; deterministic replay (byte-identical `to_dict()` across two independent hydrations); checkpoint/resume (a consistent superset, never a divergent state); multi-session coexistence with correct per-record identity/session isolation; historical ordering preserved via each record's own honest `recorded_at` timestamp (never fabricated or re-sequenced).

## 12. Portfolio memory (Step 12) -- investigated, NOT introduced

Per the mission's own explicit "do not automatically add one" instruction: investigated whether a separate `PORTFOLIO_OUTCOME` memory record is needed alongside `POSITION_OUTCOME`. Finding: **not yet -- no evidence of need.** No session in this codebase has ever built a `PortfolioSnapshot` (Phase 15M) at the exact moment any position closed (confirmed by Phase 15M's own `NO_REAL_PORTFOLIO_AVAILABLE` finding, reconfirmed by this phase's own real-data validation). Introducing a second event type and record shape now would be speculative complexity with zero real evidence driving its design (the exact anti-pattern Phase 15K deliberately avoided with `PARTIAL_EXIT = DEFERRED`). Instead, `OutcomeMemoryRecord` already carries an OPTIONAL `portfolio_context_snapshot` field (`NOT_AVAILABLE` unless genuinely supplied) -- a future phase with real evidence of need can either populate this field from a real, contemporaneous `PortfolioSnapshot`, or introduce a genuinely separate `PORTFOLIO_OUTCOME` record type once it is clear the two must be distinguished (the mission's own stated concern: "a profitable individual position can still belong to a poor portfolio decision" is real and worth preserving room for -- just not yet, without evidence).

## 13. Safety (Step 13) -- 8/8 tests, all passing (Section 7 above has full detail)

## 14. Full regression (Step 14)

**4999 passed, 0 failed** (up from the 4953 baseline; 46 new tests this phase: 18 reconstruction + 10 replay + 10 query + 8 safety; 1 pre-existing, unrelated deprecation warning).

### Bugs discovered
One test-authoring bug, not a production bug: `test_no_function_in_this_package_returns_a_decision_or_action`'s initial substring check flagged the legitimately-named `query_management_recommendation_outcomes` as if it recommended something. Fixed by narrowing to a prefix check (documented in Section 7). No defect existed in `models.py`/`engine.py`/`recovery.py`/`query.py` themselves -- every functional test passed on its first attempt.

### Architectural findings
The per-session vs. cross-session hydration distinction (Section 1) is the most significant architectural finding of this phase -- it is the first EventStore-backed component in the codebase that deliberately does NOT filter by a single target session, and this is now documented explicitly so no future phase mistakes the pattern for an oversight or copies the per-session filtering pattern where it doesn't belong.

### Files changed
New: `bujji/outcome_memory/__init__.py`, `bujji/outcome_memory/models.py`, `bujji/outcome_memory/engine.py`, `bujji/outcome_memory/recovery.py`, `bujji/outcome_memory/query.py`, `tests/test_outcome_memory.py`, `tests/test_outcome_memory_replay.py`, `tests/test_outcome_memory_query.py`, `tests/test_outcome_memory_safety.py`. Zero changes to any existing file.

## 15. Verdict

**`OUTCOME_MEMORY_TRUST: TRUSTED`** -- for the scope actually built.

- Identity proven deterministic, collision-resistant, session-scoped, broker-independent, replay-safe: YES.
- Persistence proven append-only, idempotent, conflict-rejecting, torn-record-tolerant, cross-session by design: YES.
- Epistemic honesty proven for every field the mission named: YES (`KNOWN`/`UNKNOWN`/`NOT_APPLICABLE`/`NOT_AVAILABLE`, each with a dedicated passing test).
- Query correctness proven: never mixes incompatible cohorts, never treats a small sample as confident, never treats unknown P&L as zero: YES.
- The no-feedback-path boundary proven with the strongest available guarantee (AST import checks plus source-text belt-and-suspenders on the query module): YES.
- No fabricated real-market or real-outcome evidence: YES -- `NO_REAL_OUTCOME_MEMORY_AVAILABLE` reported honestly, including the DRY_RUN_2026-05-25 legacy-system disclosure.
- No portfolio-memory speculation without evidence: YES (Step 12 investigated and deliberately deferred).

**Scope boundary, explicit**: this verdict covers the MEMORY INFRASTRUCTURE -- proven correct for durably recording and querying any real, already-attributed position outcome, with zero live-decision influence. It does NOT mean Bujji has learned anything yet, nor that any real outcome has ever been recorded (none has). The mission's own progression stage `REMEMBER` is now built; `Learn`/`Improve` remain entirely unbuilt and are explicitly NOT this phase's concern.

## 16. Fresh architecture gap audit (Step 15)

Re-inspecting from scratch, per the mission's own central question: **"What does Bujji still need before historical memory can safely become useful intelligence?"** Not mechanically choosing 15O.

- **Flow / liquidity / premium-volatility intelligence**: mature, multi-phase-deep (Phases 9-12, 15E), unchanged.
- **Portfolio risk**: exists now (15M), read-only, unchanged this phase.
- **Execution quality**: raw material exists (Phase 15L's `ExecutionReport`/fees/slippage/latency) but still nothing SYNTHESIZES it into a judgment ("was this fill good or bad") -- unchanged finding from Phase 15M, still the single largest gap in the "Measure" stage.
- **Statistical sample sufficiency**: this phase's OWN query layer (`MIN_SAMPLE_SIZE`, `INSUFFICIENT_HISTORY`) is a real, working primitive -- but it is currently the ONLY statistical-rigor primitive Bujji has anywhere. There is no broader "is this pattern statistically meaningful, or curve-fit noise" capability yet.
- **Historical similarity/search**: **confirmed absent.** Outcome Memory can filter by EXACT field match (strategy family, regime, direction, underlying) but cannot ask "what's the most SIMILAR historical setup to what I'm looking at right now" -- no embedding, no distance metric, no nearest-neighbor concept exists anywhere in the codebase.
- **Outcome distributions**: exist now (`query_outcome_distribution`), correctly gated on sample sufficiency -- a real, working primitive, currently unpopulated (no real data).
- **Causal attribution**: exists (15J), now queryable (15N) -- mature for a single position; NOT yet aggregated into "which causal dimension fails most often across many positions of a given shape" (that IS buildable today with `query_attribution_cause_distribution` + `filter_records`, but no phase has yet exercised it against any real population, because none exists).
- **Learning architecture / confidence calibration / strategy feedback / adaptive strategy selection**: **confirmed still entirely absent**, and per the mission's own explicit "do NOT jump ahead" instruction, correctly NOT this phase's concern.

**The single highest-value remaining gap, before memory can safely become useful intelligence, is NOT learning itself** -- it is that **Bujji has no real population to learn from at all**, and the reason is now traceable to one specific, concrete root cause rather than a vague "no live trading yet": **the shadow runtime has never actually driven a `position_lifecycle`-shaped position from `POSITION_OPENED` through `POSITION_CLOSED` in any unattended, persisted session.** Phases 15G through 15N have built an exceptionally deep, well-tested, replay-proven CAPABILITY to open, monitor, manage, close, attribute, aggregate, and now remember a position -- and every single phase's own real-data validation has independently confirmed the exact same gap: no session has ever actually exercised that capability end-to-end on its own, live, without a test harness constructing the position by hand.

**Recommended Phase 15O: Shadow Runtime Position Lifecycle Wiring.** Not a new intelligence capability -- an INTEGRATION phase, wiring the shadow runtime's own live intelligence-cycle loop (already producing real `ShadowTradeCandidate`s, per Phase 14) through to actually calling `build_position_opened_payload`/persisting it/monitoring it/eventually closing it, for the FIRST time, inside a real, unattended, persisted session. This is a genuine PREREQUISITE for every "MEMORY -> HISTORICAL ANALYSIS -> PATTERN DISCOVERY" step the mission's own stated future progression requires -- none of them can produce anything but `INSUFFICIENT_HISTORY`/`NO_REAL_...AVAILABLE` until a real position actually exists. Per the mission's own hard safety boundaries maintained throughout Phases 15B-15N, this would still be PaperBroker-only (no live capital), and would need its own careful audit of exactly where in the shadow runtime's existing loop the wiring belongs, without introducing any new execution capability beyond what Phase 15L's bridge already made observationally possible.

Per your instruction, Phase 15O has not been started.
