# Phase 15M -- Portfolio Intelligence Layer: Final Report

## 1. Forensic audit (verified from source, Step 1)

| Question | Finding |
|---|---|
| 1. Multi-position coexistence | `ALREADY_EXISTS`. `apply_event` (Phase 15G) already keys state as `Dict[position_id, PositionLifecycle]`; nothing prevents multiple entries. No new discovery mechanism needed. |
| 2. Open/closed discovery from `EventStore` | `ALREADY_EXISTS`. `hydrate_position_lifecycles(store, session_id)` (Phase 15G) already replays every event for a session and returns the full dict. Portfolio Intelligence reuses this directly. |
| 3. Canonical economic fields after 15K | `AVAILABLE`. `structured_exit` (gross/net/fees/slippage, per-leg breakdown) and `realized_pnl` (best-known) on `PositionLifecycle`, computed once by `pnl.py`, never re-derived. |
| 4. Execution/fill info via 15L | `AVAILABLE` only DURING a live session (not persisted); irrelevant to a pure snapshot, which reads `structured_exit` (already the OUTPUT of a 15L reconciliation, if one happened) rather than the broker directly. |
| 5. Account equity / margin / charges / slippage | Charges/slippage: `AVAILABLE` (already inside `structured_exit`, reused). Account equity / available margin: `MISSING` from any persisted artifact -- only exists on a LIVE `PaperBroker.get_funds()` call, never written to an event. Represented as an explicit, always-`None`-by-default `CapitalOverlay` the caller may optionally populate with a real read -- never fabricated. |
| 6. Existing portfolio aggregation in the new Core | `MISSING`. Confirmed by direct inspection -- no file under `bujji/position_lifecycle/`, `bujji/position_intelligence/`, `bujji/position_management/`, or `bujji/outcome_attribution/` aggregates across positions. |
| 7. Correlation/overlap from existing artifacts | `DERIVABLE`. `entry_direction` (BULLISH/BEARISH), `entry.underlying_symbol` (Phase 15M additive -- see below), and per-leg `expiry`/`side` are all real, already-captured fields sufficient for directional/concentration/correlation detection without inventing anything. |
| 8. Field classification | See table above and `models.py`'s own module docstring -- every field is explicitly `AVAILABLE`/`DERIVABLE`/`MISSING`/`AMBIGUOUS`, never assumed. |
| 9. Legacy portfolio/risk code | `bujji/trading_brain/risk_governor/portfolio_risk_aggregator.py` and `bujji/trading_brain/portfolio_valuation/` genuinely exist and implement a portfolio concept -- but on the disconnected, `position_group_id`-keyed legacy identity scheme (same finding repeated since Phase 13). **Not connected**, per the mission's explicit instruction. |

**One genuinely new finding this phase**: `EntrySnapshot` had `underlying_price` but never the underlying TICKER (`underlying_symbol`) -- real data one layer up on `ShadowTradeCandidate.underlying_symbol` (Phase 14), never carried into the lifecycle. Captured additively this phase (mirrors Phase 15K's own `entry_bid`/`entry_ask`/`lot_size` precedent exactly) -- needed for concentration-by-underlying and directional-conflict detection.

**Greeks limitation, disclosed rather than glossed over**: `LegRecord.entry_delta` is real and leg-accurate (from `ShadowTradeLeg.delta`, Phase 14, strike-correct). Gamma/theta/vega exist ONLY as a single ATM CE/PE snapshot on `EntrySnapshot.entry_greeks`, captured once at entry -- NOT resolved per-leg-strike. A leg whose own strike differs from that snapshot's `strike` field gets an honestly `UNKNOWN` gamma/theta/vega contribution (a strike mismatch, not a missing value) -- proven by a dedicated test (`test_greek_aggregation_unknown_when_leg_strike_differs_from_snapshot`).

## 2. Canonical model (Step 2)

New package: `bujji/portfolio_intelligence/` (`models.py`, `engine.py`). `PortfolioSnapshot` represents every field the mission listed: position identity/session, open/closed position lists and count, per-greek net exposure (each with its own `KNOWN`/`PARTIAL`/`UNKNOWN` status), premium bought/sold, capital deployed (derivable from persisted premium notional), an explicit optional `CapitalOverlay` for equity/margin, gross/net realized P&L + fees + slippage + always-`UNKNOWN` unrealized P&L + per-position attribution, three concentration dimensions (underlying/strategy family/expiry), a tuple of conflict findings, portfolio thesis health with a counts breakdown, and a management-recommendation summary. UNKNOWN is first-class throughout -- every aggregate carries its own resolution status, never a silently-partial number presented as complete.

## 3. Portfolio reducer (Step 3)

`build_portfolio_snapshot(states, session_id, as_of=None, capital=None)` in `engine.py` -- a single PURE function consuming the SAME `Dict[position_id, PositionLifecycle]` that `hydrate_position_lifecycles` already produces. **No second state machine was created** -- `PositionLifecycle` remains the sole source of truth; this function never mutates it, never re-derives its P&L, and is proven (by a dedicated safety test) to contain no `EventStore`/`PersistedEvent` reference of its own. Supports multiple simultaneous positions, multiple legs per position, open+closed mixes, positions on the same underlying, different strategy families, conflicting positions, and is automatically as replay-safe as `PositionLifecycle` itself (proven directly, Section 5 below) since it has no state of its own to diverge.

## 4. Exposure intelligence (Step 4)

- **Net delta**: `sum(sign(side) * entry_delta * quantity * lot_size)` across every OPEN leg -- `KNOWN` only when every leg resolved, `PARTIAL` if some did, `UNKNOWN` if none did or there are no open legs. Sign convention verified directly (BUY = +1, SELL = -1), proven correct against a real reducer-built position.
- **Gamma/theta/vega**: same sign/quantity/lot_size convention, but gated on an EXACT strike match against the entry-time ATM greeks snapshot (see Section 1's disclosed limitation) -- proven both for the matching-strike case (`KNOWN`) and the mismatched-strike case (`UNKNOWN`).
- **Premium exposure**: bought vs. sold kept explicitly separate (never netted), same discipline as Phase 15K's gross/net P&L separation.
- **Concentration**: by underlying, strategy family, and expiry -- each a tuple of real position-id groupings, never a synthetic score.
- No P&L arithmetic (subtraction) exists anywhere in this module -- verified by AST (`test_engine_never_computes_position_level_pnl_arithmetic`); it only sums numbers `pnl.py` already computed.

## 5. Portfolio conflict intelligence (Step 5)

`detect_conflicts(open_lifecycles)` -- every finding derived from REAL, already-persisted fields, never inferred or guessed:
- `DIRECTIONAL_CONFLICT`: same `underlying_symbol`, both `BULLISH` and `BEARISH` `entry_direction` open simultaneously.
- `UNDERLYING_CONCENTRATION`: >=2 open positions on the same underlying.
- `EXPIRY_CONCENTRATION`: >=2 open positions sharing an identical leg expiry.
- `CORRELATED_EXPOSURE`: >=2 open positions where EVERY leg is `SELL` (correlated short-volatility risk).
- `GREEK_CONCENTRATION`: >=3 open positions whose OWN net delta all share the same non-zero sign (a documented, conservative default count, not an invented risk-magnitude threshold).
- `NO_CONFLICT`: returned explicitly when none of the above trigger (never an empty/ambiguous absence).

This step deliberately produces **descriptions only** -- proven by a dedicated safety test (`test_conflicts_never_produce_a_trading_action`) that no finding constant contains an action verb (HEDGE/ROLL/CLOSE/ADJUST/EXIT/EXECUTE).

## 6. Portfolio thesis synthesis (Step 6)

`_thesis_health` reads each OPEN position's own, already-recorded `final_thesis_status` (Phase 15F/15G, unchanged) -- no new thesis engine. Produces `ALL_INTACT` / `MIXED` / `DETERIORATING` (any invalidated) / `NO_OPEN_POSITIONS` / `UNKNOWN` (no position resolved), plus an explicit intact/weakening/invalidated/unknown count breakdown. Management recommendations are summarized the same way from `latest_management_recommendation` (Phase 15I, unchanged).

## 7. Portfolio P&L (Step 7)

`_build_pnl` sums each position's already-computed `structured_exit` gross/net/fees/slippage -- `KNOWN` only when every contributing position's figure resolved, `UNKNOWN` when none did, `PARTIAL` otherwise. `unrealized_pnl` is **always `None`/`UNKNOWN`** in a pure snapshot (Step 7's own explicit instruction: it requires a live market price this snapshot never has) -- proven by a dedicated test. Per-position `realized_pnl` attribution is exposed directly (`pnl.per_position[position_id]`).

## 8. Replay/recovery proof (Step 8) -- 8/8 tests, all passing

`tests/test_portfolio_intelligence_replay.py`, reusing `EventStore` + `hydrate_position_lifecycles` directly, no new persistence format: uninterrupted replay with multiple positions; checkpoint/resume (hydrate mid-stream, append more events, re-hydrate -- consistent superset, since `PortfolioSnapshot` has no state of its own to diverge); restart produces a byte-identical snapshot (`to_dict()` equality); duplicate events deduped before the snapshot ever sees them; malformed record degrades safely (the one valid position still recovered); truncated JSONL degrades safely (`RECOVERY_PARTIAL`); same-symbol concurrent positions replay correctly into one concentration group; mixed open/closed positions replay with correct P&L attribution to only the closed one.

## 9. Real-data validation (Step 9)

**Result: `NO_REAL_PORTFOLIO_AVAILABLE`.** No persisted session contains real `position_lifecycle` (`position_id`-keyed) events at all -- consistent with every prior phase's own finding (15G-15L) that no real position has ever been opened+closed under this architecture.

One disclosure, not glossed over: `shadow_sessions/DRY_RUN_2026-05-25/` DOES contain a real, genuinely-executed multi-stage paper trade (`lifecycle.jsonl`, `orders.jsonl`, real `POSITION_VALUATION_UPDATED` P&L) -- but under the LEGACY `trading_brain` journal's `position_group_id` identity scheme, the exact same finding Phase 15L's own audit made. No compatibility bridge exists between that scheme and `position_id`/`leg_id`; per this phase's explicit instruction, it is **not** treated as valid evidence for the new architecture.

- **REAL**: `EventStore` ordering/replay determinism (reused, unchanged); the underlying `LegRecord.entry_delta`/`entry_premium` fields that feed exposure aggregation are the SAME real fields Phase 15E-15K already validated against live market data where available.
- **FIXTURE**: the full portfolio aggregation chain -- multi-position discovery, exposure/Greek aggregation, concentration/conflict detection, thesis/management synthesis, P&L aggregation, replay determinism -- proven deterministically against real reducer/replay code, never presented as real-market evidence.
- **NOT AVAILABLE**: any real multi-position book actually opened, monitored, and closed inside a persisted, unattended session under this architecture. None exists yet.

## 10. Safety boundary (Step 10) -- 10/10 tests, all passing

`tests/test_portfolio_intelligence_safety.py`: no forbidden imports (execution/risk/trading_brain/capital_brain/fyers/strategy-selection/trade-intent/position-management, **plus `bujji.broker` entirely** -- this module needs no broker access at all); no forbidden order calls; no broker/network reference; no P&L subtraction arithmetic and no reference to `pnl.py`'s own compute functions (aggregation only); no direct `EventStore`/`PersistedEvent` usage (must consume already-hydrated state only); no `apply_event`/`STATUS_OPEN`/`STATUS_CLOSED` redefinition (no second state machine); `CapitalOverlay()` defaults to `None`/`NOT_SUPPLIED`, never fabricated; no reference to `assess_position_management`/`build_management_assessed_payload`/`TradeIntent` (no management-action creation); no capital/risk/execution packages touched (git diff); every conflict finding constant is purely descriptive, never an action verb.

## 11. Tests (Step 11) -- 41 new tests, all meaningful

23 model/exposure/conflict/thesis/management tests (`test_portfolio_intelligence.py`) + 8 replay/recovery tests + 10 safety tests. Each proves a distinct invariant (listed in Steps 4-6, 8, 10 above) -- no inflation; every scenario the mission explicitly listed in Step 11 is covered exactly once.

## 12. Full regression (Step 12)

**4953 passed, 0 failed** (up from the 4912 baseline; 41 new tests this phase, 1 pre-existing unrelated deprecation warning). **Zero bugs discovered this phase** -- every new test file passed on its first or second attempt (no defect required a fix, unlike Phase 15K's `_apply_outcome_attributed` bug or Phase 15L's minor API-name mismatches).

### Files changed
New: `bujji/portfolio_intelligence/__init__.py`, `bujji/portfolio_intelligence/models.py`, `bujji/portfolio_intelligence/engine.py`, `tests/test_portfolio_intelligence.py`, `tests/test_portfolio_intelligence_replay.py`, `tests/test_portfolio_intelligence_safety.py`.
Modified (additive only, both already covered by existing regression): `bujji/position_lifecycle/models.py` (+`EntrySnapshot.underlying_symbol`), `bujji/position_lifecycle/engine.py` (`build_entry_snapshot_for_position` now also captures it).

## 13. Verdict

**`PORTFOLIO_INTELLIGENCE_TRUST: TRUSTED`** -- for the scope actually built.

- Multi-position aggregation proven correct: YES (single, multiple, multi-leg, same-underlying-concurrent, mixed open/closed -- all reducer-driven, not hand-constructed).
- Exposure/Greek aggregation proven correct AND honestly bounded: YES -- delta proven exact; gamma/theta/vega proven both for the resolvable case and the honestly-unresolvable (strike-mismatch) case, never approximated.
- Conflict/concentration detection proven, real-field-derived, action-free: YES.
- Thesis/management synthesis proven, no new engine: YES.
- P&L aggregation proven correct, unrealized honestly always UNKNOWN: YES.
- Replay/recovery proven, including checkpoint/resume, restart, corruption, duplicates: YES (8 tests).
- Safety boundary proven, including the two NEW risk classes this phase could have introduced (a second state machine; a competing P&L calculation) and did not: YES (10 tests).
- No fabricated real-market or real-portfolio evidence: YES -- `NO_REAL_PORTFOLIO_AVAILABLE` reported honestly, including the DRY_RUN_2026-05-25 legacy-system disclosure.
- No execution capability added, no broker access at all: YES.

**Scope boundary, explicit**: this verdict covers the AGGREGATION INFRASTRUCTURE -- proven correct for turning any real, individually-reconciled `PositionLifecycle` set into a coherent book-level view. It does NOT mean any real multi-position book has ever existed end-to-end under this architecture (none has) -- that depends on the shadow runtime actually opening and managing more than one real position in a live session, still untested territory regardless of this phase's own correctness.

## 14. Fresh architecture gap audit (Step 13)

Re-inspecting the full system from scratch, not reusing the Phase 15L ranking. Following the mission's own progression -- *Observe -> Perceive -> Understand Structure -> Understand Regime -> Find Opportunity -> Select Strategy -> Form Trade Intent -> Construct Position -> Monitor Thesis -> Manage Position -> Understand Portfolio -> Measure Outcome -> Learn -> Improve* -- Phases 9-15M have now built EVERY stage through "Understand Portfolio" and "Measure Outcome" (Phase 15J). The two stages still genuinely empty are **Learn** and **Improve**.

- Market flow / liquidity / premium dynamics / regime: mature, multi-phase-deep, real-data validated repeatedly (Phases 9-12, 15E).
- Opportunity -> Strategy Selection -> TradeIntent -> Position Construction: connected and taxonomy-mapped (Phase 14B), stable since.
- Position monitoring / adaptive management: mature (15F, 15I), consumed at the portfolio level now (15M).
- Portfolio risk: **now exists** (this phase) -- exposure, conflict, concentration, thesis/management synthesis, P&L, all real-field-derived.
- Execution quality: `AVAILABLE` as raw material (Phase 15L's `ExecutionReport`/fees/slippage/latency), but **nothing yet SYNTHESIZES it** -- no code asks "was this fill good or bad relative to the reference price / expected slippage," across one fill or across a session.
- Outcome memory / learning / strategy feedback: **confirmed still absent** (unchanged finding since Phase 15J, reconfirmed by direct inspection this phase) -- `bujji.outcome_attribution` produces a real, structured `PositionOutcomeAttribution` per position, and now Portfolio Intelligence can aggregate MANY of them -- but **nothing persists attributed outcomes into a form that influences a FUTURE session's strategy selection, eligibility, or position sizing.** This is the one remaining stage in the mission's own progression (*Learn -> Improve*) with zero implementation anywhere in the codebase.
- Live-vs-replay parity: proven repeatedly (15H, 15K, 15L, 15M) at the position and now portfolio level -- mature.
- Observation richness: mature, deep multi-phase investment (Phases 9-11).

**The single highest-value remaining gap is Outcome Memory / Learning** -- specifically, a durable, session-spanning store of attributed outcomes (win/loss, thesis-correctness, management-recommendation-correctness, by strategy family/regime/direction) that a FUTURE session's strategy selection can read. This is a genuine PREREQUISITE for several other things the mission lists (adaptive management improving over time, execution-quality feedback becoming actionable, strategy feedback existing at all) -- without it, every attributed outcome Phase 15J/15M can now compute is immediately discarded at session end, and Bujji re-learns nothing between sessions no matter how many more phases add analysis on top of a single session's data.

**Recommended Phase 15N: Outcome Memory Layer** -- a durable, cross-session store (reusing `EventStore`'s own append-only file discipline, one new event-sourced domain, same pattern as every prior memory phase: `RegimeMemoryState` 15B, `ObservationMemory` 15D) that persists each `PositionOutcomeAttribution` (15J) keyed by strategy family / regime / direction / thesis-correctness, and exposes READ-ONLY aggregate statistics (e.g. "LONG_DIRECTIONAL in RANGING regime: 3 wins, 5 losses, thesis correct 60% of the time") for a FUTURE session to consult. Critically, per the mission's own "do not rush toward live execution" principle, this phase should build the MEMORY and its READ interface only -- not yet wire it into strategy selection's actual decision logic (that would be Phase 15O or later, its own separate, carefully-audited step, since it is the first phase that would let past outcomes influence a live decision at all).

Per your instruction, Phase 15N has not been started.
