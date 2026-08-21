# Phase 20.3 — Strategy Research Engine + Attribution Framework

**Bujji Trading Intelligence Roadmap v1.2, Cycle 1.**
**Question answered: "Given a market condition, can Bujji select an appropriate strategy and determine whether the decision was correct?"** Not whether any strategy is profitable — this phase builds the evaluation loop.

---

## 1. Step 1 audit — what existing strategy code was reused, what wasn't

| Code | Classification | Disposition |
|---|---|---|
| `bujji/msi_strategy_eligibility/`, `msi_strategy_expression/`, `msi_strategy_optimization/`, `msi_strategy_selection_foundation/`, `msi_strategy_selector/`, `strategy_taxonomy_bridge/` | Real, tested, declarative options multi-leg strategy pipeline ("Series 82–108"), consuming MDI/MSSI/Consensus/VSB — not MIC v0. Not wired into `production_runtime`/`shadow_lifecycle` (the live path). | **Not reused.** Wrong domain (options, not futures) and wrong upstream signal (not MIC v0). The roadmap already deferred options intelligence to Cycle 2+ — disclosed as the future candidate, not duplicated or touched. |
| `bujji/signal/`, `bujji/trade/`, `bujji/core/orchestrator.py`, `bujji/app.py` | Self-documented `[DEPRECATED / LEGACY]`, systemd unit `bujji-orb-vwap-legacy.service` confirmed **disabled**. | **Not reused.** |
| `bujji/replay_engine/`, `bujji/replay/`, `bujji/mic_replay/`, `bujji/msi_counterfactual_replay/` | Session-forensic reconstruction (replays a live session's own persisted artifacts), not a strategy backtester. | **Not reused** (confirmed already in Phase 20.2's own audit). |
| `bujji/execution_backtest/driver.py` (Phase 20.2) | Already implements `FAMILY_A_TREND_FOLLOWING`/`FAMILY_B_MEAN_REVERSION` signal stubs, MIC-window routing, `simulate_round_trip_trade`. | **Reused directly, unmodified.** `strategy_research.families` wraps its functions; no strategy logic reimplemented. |
| `bujji.mic_v0_validation.intraday_validation.classify_intraday_window`/`generate_rolling_windows` (Phase 20.1C) | Validated (PASS) intraday MIC classification. | **Reused directly, unmodified.** |
| `bujji.execution_profiles` (Phase 20.2), `bujji.position_lifecycle.pnl.compute_net_pnl` (Phase 20.2.1-corrected) | Execution realism + correct net P&L accounting. | **Reused directly, unmodified.** |

## 2. What was newly created

- **`bujji/strategy_research/models.py`** — `StrategyFamily` (name, `market_conditions_required`, `incompatible_conditions`, `generate_signal`, `simulate_position`, `explain_reason` — every callable field a reference to an existing function, never new strategy logic), `AttributedTrade`.
- **`bujji/strategy_research/families.py`** — `TREND_FOLLOWING`, `MEAN_REVERSION`: the two Cycle-1 `StrategyFamily` instances, delegating entirely to `execution_backtest.driver`.
- **`bujji/strategy_research/eligibility.py`** — declarative `ELIGIBILITY_MATRIX` (research metadata only, no execution wiring): `TREND_UP`/`TREND_DOWN → TREND_FOLLOWING`, `RANGE → MEAN_REVERSION`, every other regime (`TRANSITION`, `VOLATILITY_EXPANSION/COMPRESSION`, `UNKNOWN`, `BREAKOUT_ATTEMPT`, `NO_TRADE`) `→ NO_TRADE`, per the charter's own "TRANSITION = capital preservation" rule extended honestly to every regime this phase's two families were never validated against.
- **`bujji/strategy_research/attribution.py`** — the four-quadrant attribution engine, and the **lookahead-avoidance design** this phase is built around (§3).
- **`bujji/strategy_research/research_driver.py`** — `run_research_day()`: one pass over consecutive rolling-window pairs per real day.
- **`tests/test_strategy_research.py`** — 24 tests (interface, routing, attribution correctness, net P&L integration).
- **`scripts/run_phase20_3_research.py`** — read-only real-data entrypoint.

## 3. The lookahead problem — how this phase's design avoids it

`classify_intraday_window` derives a window's regime from that window's own realized candles. If a strategy were entered and exited on that *same* window, "MIC said TREND_UP" and "the trade made money" would be nearly tautological. **Cycle-1's first fix:** MIC classifies window *N*; the trade is placed on window *N+1* (a regime-persistence test); *N+1* is then independently reclassified to check whether the persistence assumption held. This makes the **MIC-call-vs-outcome relationship genuinely non-circular** — confirmed below by a persistence accuracy far from 100%.

**A second circularity was found and has since been fixed (this section originally flagged it as an open limitation; it is now closed).** The original `generate_signal` (delegated to Phase 20.2's `execution_backtest.driver._family_signal`) picked BUY/SELL for window *N+1* from window *N+1*'s **own** start/end prices ("buy the winner") — making the *strategy signal itself* close to lookahead-guaranteed to be directionally correct on the window it traded, independent of whether MIC's window-*N* call was right. The first real-data run showed the signature of this: Family A's theoretical gross P&L was strongly positive with zero "MIC-correct-but-strategy-failed" trades, and "MIC-wrong-but-strategy-worked-anyway" dominated — not evidence of strategy skill.

**Fix, implemented in `bujji/strategy_research/signals.py`:** `generate_trend_following_signal`/`generate_mean_reversion_signal` now decide direction *entirely* from window *N*'s own net price move (fully known before window *N+1* begins) — Trend Following follows it, Mean Reversion fades it. Window *N+1* supplies *only* the entry/exit reference prices actually transacted, never information used to decide direction. This does not touch `execution_backtest.driver` (Phase 20.2's own single-window design and its tests are untouched) or any MIC classification logic — it is a strategy-signal change only, verified directly by a new test (`test_generate_signal_direction_is_decided_from_window_n_not_n1`) that flips window *N+1*'s own price action and confirms the chosen side does not change. §5 below reports the real-data results under this corrected signal.

## 4. Strategy Eligibility Matrix (research metadata only)

| MIC intraday state | Strategy |
|---|---|
| TREND_UP / TREND_DOWN | Trend Following |
| RANGE | Mean Reversion |
| TRANSITION | NO_TRADE (capital preservation) |
| VOLATILITY_EXPANSION / VOLATILITY_COMPRESSION / UNKNOWN / BREAKOUT_ATTEMPT / NO_TRADE | NO_TRADE (never validated for these regimes) |

## 5. Real-data research findings

Read-only, `scripts/run_phase20_3_research.py`, **rerun after the §3 signal fix**. NIFTY futures, 72 real trading days (2026-05-01 → 2026-08-13), 30-minute windows, NORMAL execution profile (Phase 20.2), 1 lot = 50, seed=42.

**Trades tested:** 4,911 window-*N*/*N+1* pairs evaluated; 3,723 correctly routed to NO_TRADE (TRANSITION/other, per the eligibility matrix); 1,188 real round-trip trades simulated and attributed (474 Trend Following, 714 Mean Reversion).

| Family | n | Theoretical gross | Net P&L | Execution drag | MIC persistence accuracy |
|---|---:|---:|---:|---:|---:|
| Trend Following | 474 | ₹8,27,370 | ₹5,51,028 | ₹2,76,342 | 107/474 = **22.6%** |
| Mean Reversion | 714 | −₹1,85,785 | −₹6,01,914 | ₹4,16,129 | 163/714 = **22.8%** |

**Attribution quadrants:**

| Family | A (MIC✓, worked) | B (MIC✓, failed) | C (MIC✗, worked) | D (MIC✗, failed) |
|---|---:|---:|---:|---:|
| Trend Following | 107 | 0 | 283 | 84 |
| Mean Reversion | 14 | 149 | 119 | 432 |

**MIC accuracy is unchanged by the signal fix, as expected** (it depends only on classification, not signal direction): regime persistence from window *N* to *N+1* holds only ~23% of the time at 30-minute granularity — far from a reliable "the state I just saw will hold" assumption. Genuine and non-circular (§3).

**What changed, and what it means:**
- **Mean Reversion now shows all four quadrants** (14/149/119/432) instead of the pre-fix all-losing distribution — direct evidence the fix removed a real distortion, not just changed numbers cosmetically. Quadrant D dominates (432): when the regime changed AND the fade bet was wrong, it lost, as expected. Quadrant B (149, MIC-correct-but-fade-failed) is the mechanically expected outcome of fading a *persisting* move. Theoretical gross improved (−₹1.86L vs the pre-fix −₹5.94L) but is still net negative — fading recent drift is still not a good rule on this data, now measured honestly rather than through a distortion that made it look uniformly worse.
- **Trend Following still shows zero quadrant-B trades** (MIC correct, strategy failed) — but this is no longer suspicious the way it was before the fix: the signal now follows window *N*'s own direction (essentially the same evidence MIC's own TREND_UP/DOWN classification is built from), so "MIC correct" (regime persisted into *N+1*) and "momentum continued" are closely related by construction — not identical, since MIC's classification also depends on efficiency ratio and volatility, not net-move sign alone. Quadrant C still dominates (283/474): the momentum signal often still profits on *N+1* even when *N+1* itself doesn't independently reclassify as TREND — i.e. price frequently keeps drifting the same direction without doing so *strongly* enough to re-trigger a TREND label. This is now a genuine, testable momentum-continuation finding, not a lookahead artifact.
- **Execution drag remains large relative to theoretical edge for both families** (₹2.76L on ₹8.27L for Trend Following; ₹4.16L against an already-negative ₹1.86L for Mean Reversion) — the Phase 20.2 conclusion (execution reality materially changes theoretical results) holds under the corrected signal too.

## 6. Whether the MIC decision loop works

**Yes, structurally, and now on genuinely non-circular evidence on both axes:**
1. **The MIC→family routing loop works** — every window is deterministically routed via the eligibility matrix, every routed trade produces a full attribution record.
2. **The MIC-correctness axis is non-circular**: ~23% persistence accuracy from one 30-minute window to the next — a real, sobering measurement, unaffected by the §3 fix.
3. **The strategy-outcome axis is now also non-circular** (post-fix): direction is decided from window *N*, transacted on window *N+1* — verified directly by test, not just argued. Mean Reversion's newly-populated all-four-quadrant distribution is the clearest evidence the fix mattered.
4. **Neither family is validated as a real strategy yet** — Trend Following's positive net P&L reflects a genuine (if untuned) momentum-continuation rule under real execution costs; Mean Reversion's negative net P&L reflects a genuine (if untuned) fade rule that loses under real conditions. Both are still Cycle-1 placeholders, not researched/tuned strategies — this phase's boundary (no optimization) was held throughout.

## 7. Test evidence

- 28 tests, all passing (interface: 8, eligibility/routing: 4, attribution correctness: 6, net-P&L/research-driver integration: 7, signal lookahead-fix verification: 3 — including a direct test that flipping window *N+1*'s own price action does not change the chosen side).
- Full regression: **6,183 passed, 0 failed** (6,179 from the initial Phase 20.3 build + 4 new from the signal redesign).
- Structural guard confirmed by test: no file in `bujji/strategy_research/` contains `PaperBroker` or `place_order` — no order placement, no broker dependency.
- No parameter tuning, threshold search, or variant sweep performed anywhere in this phase, including the signal redesign — the redesign changed *what information the signal is allowed to see*, not any numeric threshold.

## 8. Governance

Per the newly-active permanent rule: **no new intelligence modules until Cycle 1 completes and produces measured strategy-selection outcomes.** This phase added zero intelligence layers — `classify_intraday_window` and the whole MIC v0/v0_validation stack are consumed unmodified. What remains before Cycle 1 can be called complete is strategy-signal design (§5–6), not more classification capability.
