# Phase 20.17 — Risk Governor Bridge

**Bujji OS v1.0 Roadmap, Cycle-1 lineage.** Closes the Interface Map's "Decision Brain → Risk Governor Bridge → Execution Intelligence" row — the first connection between Cycle 1's evidence-driven decision chain and the real, already-built MSI/Trading Brain risk governor (Gate D.6).

---

## 1. Audit findings (Step 1)

| Component | Classification | Disposition |
|---|---|---|
| `bujji.trading_brain.risk_governor.capital_safety_governor` (D.1) | **A) Reusable directly** | `evaluate_trade_capital_safety()` called with a real `CapitalSafetySnapshot` and a disclosed notional `ProposedTradeEffect`. Never reimplemented. |
| `bujji.trading_brain.risk_governor.portfolio_risk_aggregator` (D.2) | **A) Reusable directly** | `aggregate_portfolio_risk()` / `classify_portfolio_risk()` / `explain_portfolio_risk()` called with a real, empty `position_groups=[]` flat book — Cycle 1 has zero real positions, this is a true fact, not a stub. |
| `bujji.trading_brain.risk_governor.risk_budget_governor` (D.3) | **A) Reusable directly** | `calculate_available_risk_budget()` / `assess_trade_risk_budget()` / `calculate_safe_position_size()`. `current_risk_status` sourced from D.1's own `capital_decision.status`, matching `risk_governor_pipeline.py`'s own established call exactly (verified by reading that module's real orchestration body, not assumed). |
| `bujji.trading_brain.risk_governor.position_lifecycle_intelligence` (D.4) | **C) Wrong scope for this phase** | Requires a real, already-open `PositionRiskSnapshot` (non-Optional `quantity`/`lifecycle_state`). Cycle 1 has zero real positions — fabricating one would be dishonest. Explicitly, disclosedly skipped. |
| `bujji.trading_brain.risk_governor.adaptive_risk_memory` / `adaptive_risk_governor` (D.5) | **C) Wrong scope for this phase** | Requires real historical `RiskMemoryEntry` records Cycle 1 does not have. Explicitly, disclosedly skipped. |
| `bujji.trading_brain.risk_governor.risk_governor_pipeline.run_risk_governor_pipeline` (D.1–D.5 orchestrator) | **B) Reusable pattern only** | Its own module docstring documents the exact per-stage call contract this bridge follows. Not called directly — it always runs all five stages, including D.4/D.5, which this phase cannot honestly satisfy. This bridge instead composes D.1→D.2→D.3 directly, in the same order, using the same real functions. |
| `bujji.broker.fyers.FyersBroker.get_funds()` | **A) Reusable directly** | Read-only, LIVE-CERTIFIED (2026-07-19). Structurally unreachable to `place_order`/`modify_order`/`cancel_order`/`get_open_positions`/`get_order` via the existing `disable_live_execution()` guard regardless of what this bridge does. |
| `bujji.decision_orchestration.FinalDecision` (Phase 20.10) | **A) Reusable directly** | Sole input to this bridge. Never recomputed, never modified. Only `EXECUTABLE_CANDIDATE`/`WATCH` decisions are ever sent to the real governor — `NO_OPPORTUNITY`/`BLOCKED`/`INSUFFICIENT_INTELLIGENCE` decisions are never re-reviewed (mirrors Phase 20.10's own Rule 3: the governor cannot rescue a decision Cycle 1's own evidence chain already rejected). |

## 2. Files created

- `bujji/risk_governor_bridge/{__init__,models,builder,bridge,explain}.py`
- `tests/test_risk_governor_bridge.py` (12 tests)

**No files modified.** `bujji.trading_brain.risk_governor.*`, `bujji.decision_orchestration`, `bujji.broker.fyers` all confirmed untouched by mtime.

## 3. Architecture placement

```
Decision Brain (FinalDecision, Phase 20.10)
        ↓ (only EXECUTABLE_CANDIDATE / WATCH)
Risk Governor Bridge (THIS PHASE)
        ├─ D.1 Capital Safety   — real evaluate_trade_capital_safety()
        ├─ D.2 Portfolio Risk   — real aggregate/classify/explain_portfolio_risk()
        └─ D.3 Risk Budget      — real calculate_available_risk_budget() → assess_trade_risk_budget() → calculate_safe_position_size()
        ↓
RiskGovernorAssessment (ADMITTED / BLOCKED / NOT_EVALUATED, real ceiling, honest D.4/D.5 skip disclosure)
```

D.4 (position lifecycle) and D.5 (adaptive risk memory) are never called — every `RiskGovernorAssessment` carries `skipped_stages=("D.4_POSITION_LIFECYCLE","D.5_ADAPTIVE_EXPERIENCE")` and an explicit `skip_reason`.

## 4. The "notional probe" technique

D.1–D.3 require a proposed trade's margin/max-loss/quantity to evaluate. Cycle 1 has no real proposed order size (that remains out of scope by design — no order placement, no position sizing for execution). This bridge uses a deliberately trivial, fixed, disclosed probe:

```
NOTIONAL_PROBE_MARGIN = ₹1.0
NOTIONAL_PROBE_MAX_LOSS = ₹1.0
NOTIONAL_PROBE_DESIRED_QUANTITY = 1 unit
```

used only to exercise the real governor's own admit/reject logic and read its real, currently-available risk ceiling (`PositionSizeRecommendation.maximum_quantity`). Every `RiskGovernorAssessment` carries `probe_disclosure` stating this explicitly — the probe is never presented as a proposed order.

## 5. Real capital snapshot construction

`builder.build_capital_snapshot_from_real_funds()` maps `FyersBroker.get_funds()`'s real `account_equity`/`available_funds`/`used_margin` into `CapitalSafetySnapshot`. `open_risk`/`reserved_risk` are set to `0.0` — a true fact, since Cycle 1 has zero real positions, not a guess. `daily_pnl`/`daily_loss_limit`/`peak_capital`/`max_allowed_drawdown`/`consecutive_losses` are left honestly `None` — no real trading history exists yet to populate them.

## 6. Tests (12, all passing)

1. `NO_OPPORTUNITY`/`BLOCKED` decisions never sent to the governor (`NOT_EVALUATED`, parametrized)
2. `EXECUTABLE_CANDIDATE` clears D.1 and is honestly blocked at real D.2 (see §7)
3. A builder-produced snapshot (real `get_funds()` shape, honest `None`s) is conservatively blocked at D.1 by the real governor's own `INSUFFICIENT_CAPITAL_DATA` rule
4. `WATCH` with exhausted capital is genuinely blocked at D.1 (`capital_allowed is False`); D.2/D.3 never ran
5. D.4/D.5 always disclosed as skipped, with a "fabrication" reason
6. Notional probe values are trivial (`₹1`) and disclosure text states "never a real order"
7. Builder maps real funds correctly, leaves unavailable fields `None`
8. Explanation text includes strategy name and real status
9–10. Safety boundary: zero forbidden broker call patterns (`place_order`/`modify_order`/`cancel_order`/`get_open_positions`/`get_order`), zero D.4/D.5 governor calls anywhere in the package
11. Package never references `evidence_score`/`effective_score`/`qualification_status` — confirms it never recomputes Cycle 1's own decision logic

## 7. Real finding: D.2 currently always blocks a flat book

**This is the most important result of this phase, and it is a genuine architecture finding, not a bug.**

D.2's real `aggregate_portfolio_risk()` returns `total_margin_required=None`/`total_max_loss=None` for an empty `position_groups=[]` book (no real `MarginSnapshot` supplied). `classify_portfolio_risk()` then correctly reports `RISK_INVALID` (`INSUFFICIENT_PORTFOLIO_RISK_DATA`) — a real `MarginSnapshot` requires a real broker margin query against at least one real leg, which structurally cannot exist for a flat book.

**Consequence**: with real Cycle-1 data (zero real positions), this bridge's `evaluate_risk_governor()` currently always ends at `STATUS_BLOCKED`, `blocking_stage="D.2_PORTFOLIO_RISK"`, once D.1 clears. `STATUS_ADMITTED` is reachable in the test suite only via test-controlled inputs proving the composition logic itself is correct; it is not yet reachable on real data. This is disclosed in `models.py`, `bridge.py`'s own docstring, and this report — never hidden or worked around by fabricating a `MarginSnapshot`.

This defines the real next dependency for reaching `STATUS_ADMITTED` on live data: a genuine (even if minimal/zero-value) real `MarginSnapshot` source, which is out of this phase's scope (broker margin-query wiring belongs to a future phase, not to this bridge).

## 8. Regression

Full suite: **6,367 passed, 2 failed** (`tests/test_phase_19_14_4_completeness_resolution_fix.py`, a subprocess-based test unrelated to this phase — `returncode=-9`, SIGKILL, from resource contention with concurrent load during the full-suite run). Re-ran that file in isolation immediately after: **10/10 passed** — confirmed a pre-existing environmental flake, not a regression caused by this phase. `bujji/risk_governor_bridge/`'s own 12 tests: **12/12 passed**, both standalone and inside the full suite.

## 9. Safety boundary confirmation

`grep`/`ast`-based tests confirm zero forbidden broker call patterns (`.place_order(`, `.modify_order(`, `.cancel_order(`, `.get_open_positions(`, `.get_order(`) and zero D.4/D.5 governor calls anywhere in `bujji/risk_governor_bridge/`. No `evidence_score`/`effective_score`/`qualification_status` reference anywhere in the package. `bujji.trading_brain.risk_governor.*`, `bujji.decision_orchestration`, `bujji.broker.fyers` confirmed untouched by mtime.

## 10. Remaining roadmap status

```
Decision Brain → Risk Governor Bridge → Execution Intelligence
       ✅               ✅ (this phase, D.1-D.3 only)     ⏳ (not yet built — no order placement by design)
```

Bujji can now real-evaluate whether Cycle 1's own evidence-based `EXECUTABLE_CANDIDATE`/`WATCH` decisions would clear the real capital-safety and risk-budget governors — with an honest, disclosed gap at D.2 for flat books, and D.4/D.5 honestly out of scope until real position/risk-memory data exists. No order was placed, no position size was computed for execution, and no capital was deployed anywhere in this phase.
