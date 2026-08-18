# Phase 15L -- PaperBroker Lifecycle Bridge: Final Report

## 1. Forensic audit -- field classification

| Item | Classification | Finding |
|---|---|---|
| `client_order_id` | `AMBIGUOUS` as a natural key, `AVAILABLE` as a derived one | 100% caller-supplied on `OrderRequest`; PaperBroker never generates or interprets it. Nothing links it to `position_id`/`leg_id` by default -- but since the CALLER controls it, it can be made deterministic FROM lifecycle identity (this phase's `client_order_id_for()`). |
| `ExecutionReport` (fill price, qty, charges, slippage, latency, timestamp, stage) | `AVAILABLE`, keyed by `client_order_id` | `PaperBroker.get_execution_report(client_order_id)` -- per-order, unambiguous. Richer than any prior phase's audit assumed (Phase 15K's own PaperBroker audit already found this). |
| Order records/history | `AVAILABLE` in-memory, `MISSING` across restart | `_orders`/`_execution_reports` dicts exist and are queryable live, but have **zero persistence** -- no `restore_*` method touches them (only `_positions`/`_realized_pnl` do, Phase 15B). |
| Symbol-level realized P&L / open positions | `AMBIGUOUS` -- **must not be used** | `get_realized_pnl(symbol)`/`get_open_positions()` are netted PER SYMBOL across ALL orders, with no position_id/leg_id awareness. Two lifecycle positions trading the same symbol concurrently would blend. Confirmed by direct inspection of `_apply_fill`. |
| Partial-fill behaviour | `AVAILABLE` | `OrderStatus.PARTIAL`, `filled_quantity` on `OrderResult`, real `FillSimulator`/`PartialFillConfig` mechanics -- one order can partially fill; a second, distinct order (new `client_order_id`) is required to "top up" the same leg (PaperBroker's own idempotency guard means a *repeated* client_order_id never re-fills). |
| Multi-leg behaviour | `MISSING` at the broker level, `DERIVABLE` at the bridge level | PaperBroker has no concept of a multi-leg group at all -- every leg is an independent symbol/order. The bridge supplies multi-leg grouping entirely from `PositionLifecycle.legs`, never from the broker. |
| Existing persistence/recovery | `AVAILABLE` for `_positions`/`_realized_pnl` only (Phase 15B); `MISSING` for orders/execution reports | Confirms the bridge must operate **within a live session** (translate broker facts into lifecycle events as they happen); it cannot reconstruct broker order history after a restart. |
| `PositionLifecycle`/`ShadowTradeCandidate`/`structured_exit`/`pnl.py` | `AVAILABLE`, unchanged | All Phase 15G-15K infrastructure reused directly, byte-identical contracts (`build_structured_exit`'s `exit_prices` shape is exactly what the bridge's `build_exit_evidence_from_observations` produces). |
| `TradeIntent` | `AVAILABLE`, out of scope this phase | Confirmed to already exist (Phase 14B) as the real upstream decision; this phase does not touch it -- the bridge starts strictly AFTER a candidate/position already exists. |

**Central identity finding (Step 3)**: PaperBroker cannot supply a `position_id ↔ client_order_id` mapping -- it has no such concept and never will without becoming a different broker. The only clean, non-invented solution is to make `client_order_id` **derived from** lifecycle identity (`client_order_id_for(position_id, leg_id, sequence)`, a deterministic MD5 hash, same style as `position_id_for`/`leg_id_for`, Phase 15G), so the "mapping" requires no separate stored table at all -- it is a pure function of already-canonical identity. `client_order_id` never becomes, or influences, canonical identity; the derivation is strictly one-directional.

## 2. Bridge contract (Step 2)

New file: `bujji/position_lifecycle/paper_bridge.py`. Ownership boundary, unchanged from the mission brief:
- **PaperBroker** -> execution/fill facts (untouched, zero modifications).
- **`pnl.py`** -> economic calculation (bridge never imports or duplicates it; contains zero P&L arithmetic, verified by a dedicated safety test banning `ast.Sub` and any reference to `entry_premium`/`gross_leg_pnl`/`compute_leg_gross_pnl`/etc.).
- **`PositionLifecycle`** -> canonical position state (bridge never mutates it directly -- it only produces the `exit_prices`/`fees`/`slippage` shape that `engine.build_structured_exit` already consumes).
- **Position Intelligence / Outcome Attribution** -> untouched, no import path added.

Public surface:
- `client_order_id_for(position_id, leg_id, sequence=1) -> str` -- deterministic identity derivation.
- `observe_leg_fills(_async)(broker, position_id, leg_id, expected_qty) -> LegFillObservation` -- read-only (`get_order`/`get_execution_report` only), scans sequences 1..32 until a sequence is not found.
- `build_exit_evidence_from_observations(observations) -> (exit_prices, fees, slippage)` -- shapes observations into `engine.build_structured_exit`'s exact existing input contract.
- `reconcile_position_exit(_async)(broker, position_id, legs) -> ReconciliationResult` -- the one call sites actually use; still just a read + shape, zero writes.

No new event type, no new state machine, no new P&L engine.

## 3. Position identity mapping (Step 3) -- requirements verified

- One position, multiple legs: `client_order_id_for` takes `leg_id` explicitly -- each leg gets its own derived id. Proven by the multi-leg Iron Condor/straddle tests.
- One leg, multiple fills: `sequence` parameter; `observe_leg_fills` aggregates (weighted-average price, summed qty/charges/slippage) across however many sequential orders actually exist. Proven by `test_multiple_fills_for_one_leg`.
- Duplicate observations idempotent: re-reading the same broker state twice yields byte-identical `LegFillObservation`s (read-only, no mutation) -- proven by `test_duplicate_observation_is_idempotent`.
- Conflicting mappings rejected: not applicable by construction -- there is no separate mapping table to conflict; a second `place_order` call with the same derived `client_order_id` is rejected/ignored by PaperBroker's OWN pre-existing idempotency guard (proven by `test_conflicting_second_order_with_same_client_id_does_not_double_fill`), not by new bridge logic.
- Cross-session collisions impossible: `position_id` already embeds `session_id` (Phase 15G), so the derived `client_order_id` is automatically session-scoped -- proven by `test_cross_session_contamination_impossible` and `test_cross_session_positions_never_blend_via_bridge`.
- Broker order IDs never become canonical identity: `client_order_id` is a pure function OF `position_id`/`leg_id`, never read back INTO lifecycle identity -- proven by `test_client_order_id_never_becomes_canonical_identity`.
- No missing field was invented: `exit_bid`/`exit_ask` are honestly `None` in every bridge-produced record (PaperBroker's `ExecutionReport` has no bid/ask field, only a single fill price) -- verified directly in the end-to-end test.

## 4. Paper lifecycle ingestion tests (Step 4) -- 19/19 scenarios, all passing

`tests/test_position_lifecycle_paper_bridge.py`: single-leg profitable/losing, short option, long option (PE), multi-leg Iron Condor, straddle, partial fill, multiple fills for one leg, partial-exit-status non-implication, full exit, fees present, slippage present, duplicate observation idempotent, conflicting same-client-id order not double-filled, bridge statelessness, malformed broker state (no orders for a leg), missing fill data (rejected order), missing fee data never assumed zero, cross-session contamination impossible.

## 5. End-to-end lifecycle + replay proof (Step 5+6) -- 6/6 tests, all passing

`tests/test_position_lifecycle_paper_bridge_replay.py`, reusing Phase 15G's `EventStore` and Phase 15H's replay discipline directly:
- Full chain `OPEN -> MANAGEMENT_ASSESSED -> CLOSED(structured, broker-sourced) -> OUTCOME_ATTRIBUTED` built from a REAL `PaperBroker.place_order()` fill (not a hand-authored `exit_prices` dict) -- `structured_exit["gross_realized_pnl"]` matches the real fill price exactly; `fees` is a real `ChargesCalculator` output; `exit_bid`/`exit_ask` correctly stay `None` (genuinely unavailable, not fabricated).
- Deterministic double replay -- byte-identical `to_dict()`.
- Mid-fill restart: broker fills happen, a "restart" (fresh `EventStore`/hydration) occurs BEFORE the close event is even persisted, then reconciliation and close proceed -- final state matches an uninterrupted run exactly.
- Torn `POSITION_CLOSED` record after a real fill -- `RECOVERY_PARTIAL`, position stays `OPEN`, no data corruption.
- Duplicate close event (same `event_id`) after a real fill -- caught by `EventStore`'s own dedup, P&L not double-applied.
- Two sessions trading the identical symbol on the SAME broker instance -- structured exits stay fully independent (`200.0` vs `50.0`, never blended), directly demonstrating why the bridge deliberately avoids the symbol-netted `get_realized_pnl()`/`get_open_positions()` surface.

## 6. Real-data validation (Step 7)

Scanned every persisted session directory. **Result: `NO_REAL_PAPER_POSITION_AVAILABLE`** for this phase's own architecture (`position_id`/`leg_id`-keyed).

One genuine finding requiring honest disclosure: `shadow_sessions/DRY_RUN_2026-05-25/` DOES contain real `orders.jsonl`/`executions.jsonl`/`lifecycle.jsonl`/`positions.jsonl` artifacts, including a real `POSITION_OPENED` (`IRON_CONDOR`, `approved_quantity: 1`) and real valuation updates (`total_pnl: 1597.5`). Direct inspection confirms this is the **legacy, disconnected `bujji.trading_brain` runtime's own journal** (`journal/position_group_journal.py`), keyed by `position_group_id`/`assessment_id` -- an entirely different identity scheme with no `position_id`/`leg_id`/`client_order_id` relationship to `bujji.position_lifecycle` or this phase's bridge at all. It is **not usable evidence** for the bridge built this phase (architecturally incompatible, not merely unused), consistent with every prior phase's own finding that `trading_brain/` remains disconnected from the active intelligence chain. Reported here rather than silently passed over.

- **REAL**: `EventStore` ordering/replay determinism (reused, unchanged); PaperBroker's own real fill/charges/slippage MECHANICS, exercised directly against a live `PaperBroker` instance using real `ChargesCalculator`/`FillSimulator`/`SlippageCalculator` code paths (not mocked) throughout this phase's own test suite.
- **FIXTURE**: the full bridge reconciliation chain -- identity mapping, multi-leg, partial fills, duplicate/conflicting observation, cross-session isolation -- proven deterministically, never presented as real-market evidence.
- **NOT AVAILABLE**: any real paper position opened and closed inside a persisted, unattended `position_lifecycle`-shaped shadow session. None exists yet.

## 7. Safety audit (Step 8) -- 10/10 tests, all passing

`tests/test_position_lifecycle_paper_bridge_safety.py`: no forbidden imports (execution/risk/trading_brain/capital_brain/fyers/strategy-selection/trade-intent/**position_management**, extended this phase); no forbidden order calls (`place_order`/`modify_order`/`cancel_order`) anywhere in `paper_bridge.py`; an explicit allow-list check that every `broker.<method>` call is one of `{get_order, get_execution_report}`; a dedicated check that `get_realized_pnl`/`get_open_positions` (symbol-ambiguous) are never called; no broker/network activation reference; no P&L arithmetic (no `ast.Sub`, no reference to `entry_premium`/`gross_leg_pnl`/`compute_leg_gross_pnl`/`compute_net_pnl`); `client_order_id` proven derived-from, never becoming, canonical identity; no automatic management execution path (no reference to `position_management`/`assess_position_management`/`TradeIntent`); no capital/risk/execution packages touched (git diff, with `bujji/broker/fyers.py`'s pre-existing, unrelated, read-only `get_futures_quote` diff explicitly excluded and independently verified benign); canonical `position_id_for`/`leg_id_for` not redefined or shadowed.

No pre-existing safety test needed any exception this phase -- Phase 15K's own boundary corrections remain untouched.

## 8. Full regression (Step 9)

**4912 passed, 0 failed** (1 pre-existing, unrelated deprecation warning). 35 new tests this phase: 19 ingestion + 6 replay/recovery + 10 safety.

## 9. Files changed

New (all additive, nothing existing modified):
- `bujji/position_lifecycle/paper_bridge.py`
- `tests/test_position_lifecycle_paper_bridge.py`
- `tests/test_position_lifecycle_paper_bridge_replay.py`
- `tests/test_position_lifecycle_paper_bridge_safety.py`

Zero changes to `PaperBroker`, `PositionLifecycle`, `pnl.py`, `engine.py`, `broker/guard.py`, `broker/hybrid.py`, `broker/fyers.py`, `trading_brain/`, `risk_governor/`, `position_management/`, `msi_trade_intent/`, or any strategy-selection package.

## 10. Verdict

**`PAPER_LIFECYCLE_BRIDGE_TRUST: TRUSTED`** -- for the scope actually built.

- Identity mapping proven deterministic, session-scoped, non-invented, never promoting a broker id to canonical status: YES.
- Multi-leg, multi-fill, partial-fill, duplicate/conflicting-observation handling proven: YES (19 fixture tests).
- End-to-end chain proven using REAL PaperBroker fills, not hand-authored exit data: YES (structured exit, fees, and realized P&L all traced to real `ChargesCalculator`/`FillSimulator` output).
- Replay/recovery proven, including mid-fill restart and torn/duplicate records: YES (6 tests).
- Safety boundary proven, including the specific new risk this phase introduces (symbol-netted ledger ambiguity): YES (10 tests, including a dedicated allow-list check on every broker method call).
- No fabricated real-market evidence: YES -- `NO_REAL_PAPER_POSITION_AVAILABLE` reported honestly, including the DRY_RUN_2026-05-25 legacy-system finding disclosed rather than misrepresented as compatible evidence.
- No execution capability added: YES -- PaperBroker itself is byte-unmodified; the bridge only reads.

**Scope boundary, explicit**: this verdict covers the BRIDGE INFRASTRUCTURE -- proven correct for translating real, already-existing PaperBroker fills into lifecycle-shaped facts. It does NOT mean any real paper position has ever been opened end-to-end through the live, unattended shadow runtime yet (none has) -- that requires wiring the bridge INTO the runtime's own order-placement call sites (still using `client_order_id_for` to derive ids at placement time), which was explicitly out of scope this phase (no automatic execution added).

## 11. Architecture gap audit, re-run from scratch

Re-inspecting the full system (not reusing the Phase 15K ranking):

- **Market intelligence** (Greeks, premium behaviour, flow, regime, consensus): mature, multiple phases deep, real-data validated repeatedly.
- **Strategy selection / eligibility / TradeIntent**: connected and taxonomy-mapped (Phase 14B), stable since.
- **Position lifecycle**: now has identity, thesis monitoring, management assessment, structured exit, P&L, AND a broker reconciliation bridge -- the deepest, most-proven subsystem in the codebase.
- **P&L**: canonical, real-fill-traceable now (this phase closes the last real-data gap in the calculation path itself).
- **Portfolio intelligence**: **confirmed absent** (re-confirmed this phase, unchanged from Phase 15K's own finding) -- no code aggregates multiple `PositionLifecycle`s into a session/portfolio view (total exposure, correlated risk across concurrent positions, aggregate realized P&L). This is now the single largest un-addressed layer between "one position is fully understood" and "the system understands its own book."
- **Outcome attribution**: mature (Phase 15J), now fed by real P&L when available.
- **Learning/memory**: still absent (unchanged finding since Phase 15J) -- no mechanism feeds attributed outcomes back into future strategy/selection weighting.
- **Replay/recovery**: exceptionally mature -- now proven even through a live external system (PaperBroker) boundary, the hardest replay case in the codebase.
- **Observability**: adequate (EventStore, execution reports) but nothing aggregates it across positions (same gap as portfolio intelligence).
- **Execution**: still zero live capability, by design and by explicit repeated instruction -- correctly untouched again this phase.
- **Safety**: mature, now explicitly covers the new broker-boundary-crossing risk class (symbol-ledger ambiguity) this phase introduced.

**The bridge just built is a PREREQUISITE for portfolio intelligence**, not a substitute for it: without a reliable per-position reconciliation path, any portfolio-level aggregation would either have to (a) re-derive facts ad hoc (duplicating this phase's own work) or (b) read the broker's symbol-netted ledger directly (the exact ambiguity this phase's safety tests forbid). With the bridge now in place, portfolio-level aggregation can be built cleanly on top of `PositionLifecycle` objects alone, never touching the broker directly.

**Recommended Phase 15M: Portfolio Intelligence Layer** -- aggregate multiple `PositionLifecycle`s (all still real, individually-reconciled positions) into session/portfolio-level views: total exposure, concurrent-position correlation/overlap (e.g. two positions both short the same strike), aggregate realized/unrealized P&L across the book, and margin/capital utilization awareness (PaperBroker already simulates `account_equity`/`available_margin`, currently read by nothing at the portfolio level). This would be READ-ONLY aggregation over already-canonical lifecycle state -- no new execution capability, no change to any individual position's identity or economics -- and is the natural next foundational-intelligence gap now that individual positions are fully, honestly understood end-to-end.

Per your instruction, Phase 15M has not been started.
