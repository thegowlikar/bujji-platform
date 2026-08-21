# Phase 20.4 — Strategy Forecast Validation

**Bujji Trading Intelligence Roadmap v1.2, Cycle 1.**
**Question answered: "Can a simple, non-lookahead strategy forecast the next market behavior and survive realistic execution costs?"**

---

## 1. Audit findings (Step 1, mandatory, done before any code was written)

| Component | Classification | Disposition |
|---|---|---|
| `bujji.market_timeseries.indicators`: `sma`, `ema`, `rsi`, `atr`, `bollinger`, `realised_volatility` (Phase 15Q) | **A) Reusable** | Pure functions, duck-type compatible with `bujji.core.models.Candle` (confirmed directly: only reads `.close`/`.high`/`.low`, no type-specific coupling). **Reused unmodified** for both Family A (EMA/SMA) and Family B (Bollinger) signals. |
| `bujji.msi_performance_analytics.engine._drawdown_and_streaks` (Series 101) | **A) Reusable** | Generic, pure function over `Sequence[float]` — no dependency on `ShadowPosition`/options types. **Reused unmodified** for max drawdown / streak stats. |
| `bujji.mic_v0_validation.intraday_validation`, `bujji.strategy_research` (eligibility, attribution, `StrategyFamily`, `research_driver`), `bujji.execution_backtest.driver`, `bujji.execution_profiles`, `bujji.position_lifecycle.pnl` | **A) Reusable** | The entire Phase 20.1C–20.3.1 pipeline. **Reused unmodified** — this phase extends it, never bypasses it. |
| `bujji.msi_market_structure.engine.derive_breakout_state` | **B) Wrong domain** | Consumes support/resistance `_Level` objects from the options-MSI price-structure pipeline (Series ~79) — session-scoped, not MIC-window-level. Not reused. |
| `bujji.msi_market_phenomena.engine._rule_mean_reversion`/`_rule_momentum_exhaustion` (Phase 19.7) | **B) Wrong domain** | Boolean rules over a session-level `MarketSnapshot` (structure_state/structural_balance fields) — wrong upstream (not MIC v0). Not reused. |
| `bujji.msi_performance_analytics.engine.build_trade_analytics`/`build_edge_validation_report` | **B) Wrong domain (partially)** | Tightly coupled to `ShadowPosition`/`DecisionRecord` (options types this phase's futures research never constructs). Not reused wholesale — only the one generic sub-function (`_drawdown_and_streaks`) was extracted. |
| `bujji.signal.indicators.VwapTracker`/`PremiumVwapTracker`, `bujji.trade.manager` | **D) Legacy/deprecated** | Confirmed already in Phase 20.3's audit: `[DEPRECATED / LEGACY]`, systemd unit disabled. Not reused. |
| RSI, ATR, SMA/EMA, Bollinger elsewhere in the codebase | **C) Duplicate — none found** | `market_timeseries.indicators` is the single source of truth; no second implementation exists. |

**Missing, and built as the smallest possible addition:** two indicator-based signal functions (`generate_ma_alignment_signal`, `generate_bollinger_reversion_signal`), a `forecast_correct` attribution field, a `stats` module (win rate/profit factor/expectancy — trivial one-liners; drawdown reused), and a `periods` module (train/validation/out-of-sample date split). No existing equivalent for any of these four was found.

## 2. Strategy hypotheses tested

**Family A — Trend Continuation:** *moving-average alignment*. Short EMA(9) above long SMA(20), both computed from real candles up to T → BUY; below → SELL. Standard textbook periods, never tuned to this data.

**Family B — Mean Reversion:** *Bollinger-band overshoot fade*. Last real close at T above the upper Bollinger(20, 2σ) band → SELL (fade); below the lower band → BUY (fade); inside the bands → no signal. Standard textbook parameters, never tuned.

One hypothesis per family, as instructed — no variant search, no parameter sweep.

## 3. Data used

Real only. NIFTY futures (`NIFTY_FUT_CONTINUOUS`), 5-minute candles, `HistoricalObservationStore`, full available range: **2018-02-01 → 2026-08-13** (157,889 real rows, 2,105 real trading days with ≥25 candles). No synthetic data, no simulated markets, in this validation run.

## 4. Information boundary proof

Decision timestamp T = the last real candle of window N. `research_driver.run_research_day` computes `history_upto_t = [c for c in day_candles if c.timestamp <= T]` — a plain timestamp filter over already-real, already-ordered data; it is structurally impossible for this to include a candle from window N+1 or later. Both signal functions receive `(history_upto_t, window_n1)` — `window_n1` supplies *only* the entry/exit reference prices actually transacted, never read to decide direction. **Proven directly, not just argued**, by two tests: `test_ma_alignment_signal_direction_is_decided_from_history_upto_t_not_n1` and `test_bollinger_reversion_signal_direction_is_decided_from_history_upto_t_not_n1` — each flips window N+1's own price action and confirms the chosen side does not change. The Phase 20.3.1 lookahead-prevention tests remain in the suite, unmodified, alongside these.

## 5. Methodology

Unchanged from Phase 20.3/20.3.1: MIC classifies window N; the eligibility matrix (unmodified) routes TREND→Family A, RANGE→Family B, everything else→NO_TRADE; the selected family's signal is evaluated on `history_upto_t`; the trade transacts on window N+1 via `simulate_round_trip_trade` (Phase 20.2's execution simulator, NORMAL profile, unmodified) and `compute_net_pnl` (Phase 20.2.1-corrected accounting, unmodified); window N+1 is independently reclassified to check MIC's persistence call. Phase 20.4 adds `forecast_correct` (theoretical gross P&L > 0 — direction right, before costs) alongside the existing `outcome_worked` (net P&L > 0 — profitable after costs), directly answering "did execution destroy the edge."

## 6. Results

30-minute windows, NORMAL execution profile, 1 lot = 50, 2,105 real trading days.

| Family | n | Win rate | Avg gross | Avg net | Profit factor | Max drawdown | Expectancy |
|---|---:|---:|---:|---:|---:|---:|---:|
| Trend Following (MA alignment) | 11,278 | 66.5% | ₹949 | ₹517 | **2.30** | ₹32,360 | ₹517/trade |
| Mean Reversion (Bollinger fade) | 150 | 0.0% | −₹1,218 | −₹1,664 | 0.00 | ₹2,49,592 | −₹1,664/trade |

**Trend Following: totals** — gross ₹1,07,06,692, net ₹58,28,074 over all 11,278 trades. **Mean Reversion: totals** — gross −₹1,82,735, net −₹2,49,592 over 150 trades (every single trade lost).

## 7. Attribution analysis

**MIC persistence accuracy is low for both families** (Trend Following 21.7%, Mean Reversion 16.0%) — consistent with Phase 20.3's finding that a 30-minute regime rarely persists unchanged into the next window. **This does not track with strategy performance**, and that gap is itself the key finding: Trend Following's `forecast_correct` rate is 75.8% — the MA-alignment signal does not require MIC's *exact* regime label to repeat, only that price keeps moving the same direction relative to its own recent averages, a materially weaker and more often-true condition. **MIC persistence and signal forecast correctness are answering different questions**, and this phase's data shows the strategy's edge comes from the latter, not the former.

Regime breakdown (Trend Following): TREND_DOWN (n=5,177, net ₹31,61,214) and TREND_UP (n=6,101, net ₹26,66,859) both contribute positively, no directional asymmetry. Mean Reversion only ever fires in RANGE (n=150, by construction of the eligibility matrix) — and loses in RANGE uniformly.

## 8. Execution impact

| Family | Before costs (gross) | After costs (net) | Difference |
|---|---:|---:|---:|
| Trend Following | ₹1,07,06,692 | ₹58,28,074 | **₹48,78,619** (45.6% of gross) |
| Mean Reversion | −₹1,82,735 | −₹2,49,592 | ₹66,857 (costs deepen an already-losing position) |

Execution destroys a real edge in 9.3% of Trend Following trades (forecast correct, net not profitable) — real, but the minority case; the strategy survives realistic costs on the large majority of its correct calls. Execution never rescues Mean Reversion — it was wrong before costs in 96% of its 150 trades and costs only make the other 4% worse.

**Stability (train 2018–2022 / validation 2023–2024 / out-of-sample 2025–2026):**

| Family | Period | n | Win rate | Net total | Expectancy |
|---|---|---:|---:|---:|---:|
| Trend Following | TRAIN | 6,619 | 68.2% | ₹33,89,191 | ₹512 |
| Trend Following | VALIDATION | 2,508 | 64.2% | ₹12,05,821 | ₹481 |
| Trend Following | OUT_OF_SAMPLE | 2,151 | 63.6% | ₹12,33,061 | ₹573 |
| Mean Reversion | TRAIN | 80 | 0.0% | −₹1,09,985 | −₹1,375 |
| Mean Reversion | VALIDATION | 41 | 0.0% | −₹74,063 | −₹1,806 |
| Mean Reversion | OUT_OF_SAMPLE | 29 | 0.0% | −₹65,545 | −₹2,260 |

**Trend Following is stable across all three periods** — win rate declines modestly (68.2%→63.6%) but expectancy never turns negative, and out-of-sample expectancy (₹573) is actually the *highest* of the three periods — no sign of decay or overfitting into a period this signal was never shaped by (it uses only textbook periods, fit to nothing). **Mean Reversion fails consistently in all three periods** — never a single winning trade in 8.5 years, at any period split.

## 9. Limitations

1. **Trades overlap in time and are not independent samples.** With 30-minute windows stepped every 5 minutes, consecutive window-N/N+1 pairs share most of their candles and often the same intraday move. The n=11,278 figure is real (every one of those trades is a genuine, distinct simulated transaction with its own entry/exit), but the *effective* statistical sample size for confidence-interval purposes is smaller than 11,278 independent draws — win rate and profit factor should be read as descriptive of this specific (overlapping) trading rule, not as independent-trial statistics.
2. **Mean Reversion's sample (150 trades over 8.5 years) is thin** in absolute terms, though the result is unambiguous (0% win rate, consistent across all three periods) — more data would not plausibly reverse a 0/150 outcome, but the family should not be considered thoroughly explored.
3. **Single instrument, single execution profile.** Only NIFTY futures, only the NORMAL execution profile (Phase 20.2) — STRESS/EXTREME were not run in this validation; Trend Following's real edge margin against a harsher cost profile is unverified here.
4. **MA alignment's `forecast_correct` (75.8%) exceeds `mic_correct` (21.7%)** precisely because they measure different things (§7) — this is disclosed as a finding, not smoothed over, but means the strategy's success should not be read as validating MIC's persistence accuracy.
5. **No slippage-vs-liquidity interaction modeled** beyond Phase 20.2's disclosed Level C simulation — real market impact at the volumes this strategy would need to trade at scale is unmeasured.
6. **This is one signal per family, run once** — per the phase's own "no optimization, no parameter search" boundary. A positive result here is evidence a genuinely simple, honest rule found something; it is not evidence that this is the best or only such rule.

## 10. Decision

## Trend Following (MA alignment): **PASS**

Positive expectancy after realistic execution costs (₹517/trade net, profit factor 2.30), stable across train/validation/out-of-sample with no decay into the untouched out-of-sample period (expectancy actually highest there), and non-circular by direct proof (§4). This clears this phase's own PASS bar: "positive expectancy after costs with stability."

## Mean Reversion (Bollinger fade): **FAIL**

Loses consistently — 0% win rate across all 150 trades and all three time periods, in the regime it was specifically designed for (RANGE). Not inconclusive; the sample, while small, shows no ambiguity. This specific hypothesis should be abandoned, not iterated on with parameter changes (out of this phase's scope regardless).

**Overall Cycle-1 recommendation:** continue with Trend Following as a validated, non-lookahead, cost-surviving forecast signal — the first one this engagement has produced. Per the now-active permanent governance rule, this does NOT authorize building more intelligence infrastructure or a new strategy ecosystem; the next legitimate step is a scoped decision by the user on what to do with one validated signal (position sizing research, live paper deployment consideration, or broader out-of-sample confirmation), not further signal search.
