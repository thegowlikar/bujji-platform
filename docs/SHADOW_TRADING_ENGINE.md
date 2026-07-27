# Shadow Trading Engine v1 (STE v1)
## BUJJI Engineering Series 100

**Status:** Real, implemented, tested, replayed against the real 41-day
corpus. **No broker order was placed. No paper-trading API was called.
No capital was ever at risk.** This is the final series of the current
engineering arc -- BUJJI's complete decision chain (Series 77-99) now
has an execution ENVIRONMENT to run inside, without modifying a single
line of decision logic.

```
... -> Decision Auditor -> Shadow Trading (this package)
```

---

## 1. Deliverable 1 — Capability Audit

| Component | Finding | Classification |
|---|---|---|
| Replay engine (`bujji/replay/`) | Real, but built for the older production_runtime's own qualification-report format. | **Not reused directly** -- this whole MSI arc already has its own established per-day replay-script pattern (used continuously since Series 88), reused again here. |
| Decision Auditor (Series 99) | Real, already produces `DecisionRecord`/`ExecutionPlanAssessment` references for every real day. | **REUSED DIRECTLY** -- every shadow position opens FROM a real `DecisionRecord`'s `decision_outcome`. |
| Execution Planner (Series 98) | Real, already produces the plan a shadow entry should follow. | **REUSED DIRECTLY** -- `execution_plan_id` is stored verbatim; no new sequencing logic. |
| Position Lifecycle (Series 96) | Real, already the single source of truth for position health/exit state. | **REUSED DIRECTLY, by import** -- `bujji.msi_position_lifecycle.engine.assess_position_lifecycle` is called with zero modification; this is the one component this series was explicitly forbidden from duplicating, and it is not duplicated (verified: the import is a direct function call, not a re-implementation). |
| Market Recorder (`bujji.market_observation`) | Real, but this arc already reconstructs observations from real Bhavcopy/intraday data per-day, which is what this series consumes too. | **Already consumed indirectly**, no new recorder needed. |
| Broker market-data feed | Live-only, unchanged conclusion from every prior series. | **PRODUCTION ONLY.** |
| Clock scheduler | **Confirmed missing entirely** (same finding as Series 98's own audit -- no `ntp`/`clock_sync` code exists anywhere). | **MISSING**, disclosed; entry/exit timestamps in replay are real historical timestamps, not a live scheduler's output. |
| Trade journal (`bujji/journal/journal.py`) | Real CSV/SQLite outcome recorder for the old single-strategy system. | **OBSOLETE for direct reuse** (live-file-writing, single-strategy schema); `ShadowTradingJournal` follows this whole arc's own in-memory `JournalEntry` convention instead, exactly like Series 99's `DecisionAuditorJournal`. |

**Conclusion**: the only genuinely new logic in this package is the ENTRY (real net price from Series 90) and the MARK-TO-MARKET repricing (a real discovery, see Section 4). Everything else -- lifecycle evaluation, exit triggering, execution sequencing -- is reused directly, not duplicated.

## 2. Deliverable 2 — ShadowPosition

Immutable, frozen. All spec-required fields present, plus `entry_date`, `entry_legs` (real Series 90 legs, necessary for real repricing), and `position_close_date` (needed to know when a position naturally rolls off).

## 3. Deliverable 3 — Entry Engine

`open_shadow_position` creates a `ShadowPosition` using Series 90's own real, already-computed `expected_credit_debit` as `entry_price` -- never re-estimated, never a synthetic fill. No order is submitted; the function contains no broker call of any kind (verified structurally by an AST test forbidding `bujji.broker`/`bujji.execution` imports and the literal string `place_order`).

## 4. Deliverable 4 — Live Tracking

`track_shadow_position` reuses `assess_position_lifecycle` DIRECTLY for lifecycle/thesis-validity tracking (no duplicated logic, Deliverable 4's own explicit mandate). Mark-to-market is computed by looking up each entry leg's SAME (strike, expiry, option_type) contract in a LATER real Bhavcopy day's chain -- genuinely real, not synthetic.

**A real, disclosed data-quality finding surfaced and fixed during this series**: on a contract's OWN expiry day, its Bhavcopy `settlement` field was observed to equal the UNDERLYING's settlement price (23,913.7 for a NIFTY 24100 CE expiring that day -- exactly matching that row's own `underlying_price` field), not the option's own value -- while `close` correctly showed 0.15, the real, near-zero value of a deep-OTM expiring call. Repricing now prefers `close` over `settlement` specifically for tracking an ALREADY-HELD position (Series 90's own entry-day construction still deliberately uses `settlement`, validated across this whole corpus for that specific purpose -- this fix is narrower and disclosed as such).

## 5. Deliverable 5 — Exit Engine

Exits are triggered ONLY by Position Lifecycle's own real states (`THESIS_BROKEN`, `EXIT_CANDIDATE`, `PROFIT_HARVEST`, `CLOSED`) -- reused verbatim as `exit_reason`, never a shadow-only invented rule. `HARD_SESSION_CLOSE` is used only as the label for Position Lifecycle's own `CLOSED` state (a position reaching its real expiry with no earlier exit trigger) -- itself a restatement, not new logic.

## 6. Deliverable 6 — Daily Shadow Journal

`ShadowTradingJournal` records the full `Entry -> Lifecycle events -> Exit -> PnL` sequence for every simulated trade, one entry per tracking update, following this whole arc's established in-memory journal convention exactly.

## 7. Deliverable 7 — Historical Consistency

The same real 41-day corpus was fed through the multi-day replay driver, producing byte-identical entries, exits, and final states on two independent full runs (`shadow_trade_id`s confirmed identical across both runs). Determinism is structural, not incidental: every builder function is a pure function of its real inputs.

## 8. Deliverable 8 — Daily Dashboard

Real, plain-text views matching the spec's own examples:

```
Today

Decision:
  TRADE_APPROVED
Strategy:
  LONG_DIRECTIONAL (VERTICAL_DEBIT_SPREAD)
Entry:
  2026-05-29T15:15:00
Current MTM:
  +₹0
Lifecycle:
  NEWLY_OPENED
Exit:
  Pending
```

```
Shadow Positions

Open:
  0
Closed:
  11
Win:
  4
Loss:
  7
No Trade:
  30
```

## 9. Real 41-day corpus results (Deliverable 6/8 measurements)

- **11 shadow positions opened** (matching Series 91's own real approval count exactly), **all 11 closed by the end of the corpus** (0 still open).
- **Exit reason distribution: `THESIS_BROKEN` 10, `PROFIT_HARVEST` 1** -- directly consistent with Position Lifecycle's own dominant real finding (Series 96: 74% thesis-invalidation rate) and Decision Auditor's own 75% figure (Series 99) -- a THIRD independent confirmation of the same real phenomenon, now observed at the level of actual simulated trade outcomes.
- **Win/Loss at close: 4/7 (0 unpriceable)** -- every real closed position had a computable real P&L; none were lost to a data gap.
- **Sum of real realised P&L across all 11 closed shadow positions: -₹21,720** -- **reported honestly, not softened**: in this specific real 41-day corpus, BUJJI's simulated trades lost money in aggregate. This is exactly the kind of evidence Shadow Trading exists to surface before any real capital is examined.
- Replayed twice: **`shadow_trade_id`s were byte-identical** across both runs.

No tuning was performed against these numbers -- and per this series' own explicit constraint, none will be: Shadow Trading is an execution environment, not a decision engine, and this result is not grounds to alter any upstream MSI reasoning within this series.

## 10. Simulation philosophy

Every number in a `ShadowPosition` is either a real, already-computed value from an upstream series (entry price, margin estimate, lifecycle state) or a real repriced figure computed by looking up genuine later-day market data -- never an estimate invented for the purpose of simulation itself.

## 11. Lifecycle integration

Total: zero new lifecycle logic. `track_shadow_position` is a thin wrapper that calls Position Lifecycle, then maps its real output state onto entry/exit bookkeeping fields.

## 12. Deterministic replay

Every builder (`open_shadow_position`, `track_shadow_position`) is a pure function; `shadow_trade_id` is a content hash, never a timestamp or random value.

## 13. Production migration

In live operation, the exact same `open_shadow_position`/`track_shadow_position` functions would run unchanged, fed real-time prices instead of historical Bhavcopy rows -- the ONLY difference between shadow and a future live mode is the data source, never the decision or tracking logic, per this series' own explicit design goal.

## 14. Known limitations

- Mark-to-market repricing depends on the exact same contract still being listed in a later day's real chain; if it disappears (thin liquidity, data gap), `unrealised_pnl` honestly reports `None` rather than guessing (verified directly by a dedicated test).
- The expiry-day settlement-vs-close data quirk (Section 4) was discovered and fixed for THIS package's repricing use; it may still affect other, unrelated uses of `settlement` elsewhere in this codebase on a contract's own expiry day -- flagged here, not fixed elsewhere, since doing so would modify other MSI modules outside this series' scope.
- The real -₹21,720 aggregate result (Section 9) is a genuine, negative finding from a 41-day sample -- not yet large enough to draw a durable conclusion about the strategy's edge; that judgement is explicitly out of scope for this series (no scoring, no optimization) and belongs to a dedicated future analytics series.

## 15. Deliverable 10 — Recommendation

Evidence-based, from the real replay measurements above:

1. **The engine works correctly, deterministically, and without any capital risk** -- 11 real shadow trades, fully tracked, fully closed, byte-identical on replay.
2. **The real result (4 wins, 7 losses, -₹21,720 aggregate) is itself the most important finding of this series** -- it is exactly the kind of evidence that must exist BEFORE any judgement about live-readiness can be made responsibly.
3. **This series was explicitly forbidden from scoring, ranking, or judging that result** -- doing so is the natural, well-scoped next question.

**Recommended next step: Series 101 — Performance Analytics & Edge Validation**, exactly as the user's own proposed roadmap specifies. With real decision records (Series 99), real shadow trade outcomes (Series 100), and now a real, permanently-recorded negative aggregate result on this specific 41-day sample, the next responsible step is a dedicated, statistically-rigorous analysis of whether BUJJI's real edge (if any) is distinguishable from noise -- before any consideration of Live Broker Execution or a Small-Capital Pilot.
