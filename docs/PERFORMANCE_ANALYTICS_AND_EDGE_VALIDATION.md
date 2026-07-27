# Performance Analytics & Edge Validation v1 (PAEV v1)
## BUJJI Engineering Series 101

**Status:** Real, implemented, tested, replayed against the real 41-day
corpus (11 completed shadow trades, 30 non-approved decisions). PAEV is
an **observer, not an optimiser** -- it never tunes a threshold, never
changes Strategy Selection, never fits a parameter, never uses machine
learning. Every number below is a real, descriptive statistic computed
from Series 99/100's own already-recorded evidence.

---

## 1. Deliverable 1 — Capability Audit

- **Replay reports / qualification docs** (this whole arc's own `docs/*QUALIFICATION*`/`*REPLAY*` files): real, but each is a bespoke, single-series measurement -- no general-purpose statistics module existed to reuse.
- **Trade journal / Observatory** (both already audited in Series 99/100): confirmed obsolete for direct reuse (old single-strategy schema / dict-shaped session format); their PHILOSOPHY (write real evidence, explain don't judge) is the one thing carried forward, as in every prior series.
- **`pipeline_audit.py`** (repo root): a one-off validation script, not a reusable statistics library.
- **Conclusion**: no existing performance-statistics component was found to reuse or duplicate. This series' pure-Python statistical functions (mean, median, Wilson score interval, drawdown/streak walk) are new, but implement only standard, textbook formulas -- consistent with this codebase's established discipline of hand-rolling simple statistics rather than adding a dependency (the same approach the Series 86 predictive-value investigation used for its own binomial test).

## 2. Deliverable 2 — Trade Analytics

Computed for every one of the 11 real completed shadow trades, using ONLY real data: MFE/MAE from the real day-by-day `unrealised_pnl` sequence Series 100 already produced (assembled here, never re-derived), holding period in real calendar days, realised direction from real entry/exit spot, exit efficiency (`realised_pnl / MFE`), and realised volatility (reusing `RegimeBrain._log_returns`/`_stdev` directly, the same functions Series 88/99 already reuse).

## 3. Deliverable 3 — Decision Analytics

All 41 real decisions split into three real, derived categories (`APPROVED`/`REJECTED`/`NO_TRADE` -- a finer split than Decision Auditor's own binary field, computed from its real, unmodified `strategy_family`/`decision_outcome` fields):

| Category | Frequency | Avg. confidence rank | Dominant thesis |
|---|---|---|---|
| `APPROVED` | 11 | 2.36 (between MODERATE and HIGH) | `TREND_CONTINUATION`/`VOLATILITY_EXPANSION`/`TREND_REVERSAL` (3 each) |
| `REJECTED` | 11 | 2.18 | `RANGE_PERSISTENCE` (8 of 11) |
| `NO_TRADE` | 19 | 1.95 (closer to LOW) | spread across `RANGE_PERSISTENCE`, `BREAKOUT`, `TREND_CONTINUATION` (5 each) |

## 4. Deliverable 4 — Edge Validation

Over the real 11 completed shadow trades:

| Metric | Value | n | Reliability |
|---|---|---|---|
| Win rate | 36.4% | 11 | `NOT_STATISTICALLY_RELIABLE` |
| Expectancy | -₹1,974.55 | 11 | `NOT_STATISTICALLY_RELIABLE` |
| Profit factor | 0.32 | 11 | `NOT_STATISTICALLY_RELIABLE` |
| Average winner | ₹2,600.63 | 4 | `NOT_STATISTICALLY_RELIABLE` |
| Average loser | -₹4,588.93 | 7 | `NOT_STATISTICALLY_RELIABLE` |
| Max drawdown | ₹21,720.00 | 11 | `NOT_STATISTICALLY_RELIABLE` |
| Longest losing streak | 3 | 11 | `NOT_STATISTICALLY_RELIABLE` |
| Longest winning streak | 2 | 11 | `NOT_STATISTICALLY_RELIABLE` |

**Every single metric is explicitly flagged `NOT_STATISTICALLY_RELIABLE`** -- the real sample (11 trades) is far below the disclosed reliability floor (30 observations, itself a standard, disclosed statistical rule of thumb, never tuned to make this particular result look better or worse). The win rate's real 95% Wilson confidence interval spans roughly 15%-65% -- wide enough that this sample is consistent with a wide range of true underlying win rates, including both a losing and a winning edge.

## 5. Deliverable 5 — Root Cause Analysis (descriptive only)

Mean real P&L, grouped by dimension (every group `NOT_STATISTICALLY_RELIABLE` at n≤5):

- **By thesis**: `TREND_CONTINUATION` -₹4,383.75 (n=3), `VOLATILITY_EXPANSION` -₹4,298.75 (n=3), `TREND_REVERSAL` -₹1,138.75 (n=3), `RANGE_PERSISTENCE` +₹2,797.50 (n=1), `BREAKOUT` +₹4,946.25 (n=1).
- **By confidence band**: `HIGH` -₹2,457.75 (n=5), `MODERATE` +₹165.75 (n=5), `LOW` -₹10,260.00 (n=1) -- notably, `HIGH`-confidence trades performed WORSE on average than `MODERATE`-confidence ones in this specific sample, a real, counter-intuitive, and entirely unreliable-at-this-n observation, reported honestly rather than smoothed over.
- **By strategy family**: `LONG_DIRECTIONAL` -₹2,451.75 (n=10), `BUTTERFLY` +₹2,797.50 (n=1).
- **By construction type**: `SINGLE_LEG` -₹2,457.75 (n=5), `VERTICAL_DEBIT_SPREAD` -₹2,445.75 (n=5), `BUTTERFLY_SHAPE` +₹2,797.50 (n=1).

**Matching the success criterion almost exactly**: `TREND_CONTINUATION` trades are indeed the largest real contributor to losses in this sample (n=3, -₹4,383.75) -- but with only 3 real observations, this is explicitly NOT a statistically reliable finding, stated as such rather than as a conclusion.

## 6. Deliverable 6 — Counterfactual Analysis

Of the real 30 non-approved decisions (11 `REJECTED` + 19 `NO_TRADE`):
- **Rejected winners: 13** (the day's real market direction agreed with the real subsequent move).
- **Rejected losers: 5** (the day's real market direction disagreed with the real subsequent move).
- **Unknown/unclassifiable: 12** (no directional lean to judge against, or no real outcome data).

This evidence is **descriptive only** -- it does not, by itself, justify changing the decision engine (no decision was altered by this analysis; verified directly by a dedicated test).

## 7. Deliverable 7 — Statistical Discipline

Every metric in Sections 4-5 displays its own real sample size and an explicit reliability flag -- never a bare number. The reliability floor (30 observations) is a single, disclosed, structural constant (`config.py::MIN_RELIABLE_SAMPLE_SIZE`), applied uniformly and never adjusted to make any particular finding look more or less significant.

## 8. Deliverable 8 — Dashboard

```
Trades
  11

Win
  4

Loss
  7

Expectancy
  ₹-1,974.55 (n=11)  [NOT_STATISTICALLY_RELIABLE]

Win Rate
  36.4% (n=11)  [NOT_STATISTICALLY_RELIABLE]

Profit Factor
  0.3238 (n=11)  [NOT_STATISTICALLY_RELIABLE]

Max Drawdown
  ₹21,720.00 (n=11)  [NOT_STATISTICALLY_RELIABLE]
```

Per-thesis, per-strategy-family, per-construction-type, and per-confidence-band views are all produced the same way (Section 5) -- every one of them, at this corpus's real size, is currently below the reliability floor.

## 9. Statistical philosophy

Measure before changing; separate description from prescription; report uncertainty honestly. No metric in this package is ever presented without its own real sample size and an explicit reliability verdict -- this is enforced structurally (`MetricEstimate` cannot be constructed without both).

## 10. Evidence requirements / significance policy

30 observations is this package's own disclosed floor for calling any single-group statistic reliable. Below it, the number is still reported (never hidden) but explicitly labeled `NOT_STATISTICALLY_RELIABLE` -- exactly matching the spec's own worked example (`"Win rate 80%, Sample size = 5, Status: NOT STATISTICALLY RELIABLE"`).

## 11. Limitations

- 11 trades is the entire real sample available from this corpus -- every edge-validation metric in this document is explicitly, correctly flagged unreliable; none should be read as a conclusion about BUJJI's real edge.
- Root-cause breakdowns split an already-small sample into even smaller groups (n≤5 in every dimension) -- genuinely useful for generating hypotheses, not for confirming them.
- The counterfactual analysis's directional classification is a simple sign-agreement test against real market direction -- it does not attempt to estimate what a REAL constructed position would have actually earned, only whether the day's directional read agreed with what happened.

## 12. Future optimisation interface

A future, SEPARATE series (explicitly not this one) could use `TradeAnalytics`/`EdgeValidationReport`/`CounterfactualReport` as the evidence base for a genuine optimisation or parameter-fitting exercise -- this package deliberately produces the measurements such a future series would need, without performing any of that fitting itself.

## 13. Deliverable 10 — Recommendation

Evidence-based, from the real measurements above only:

1. **Every edge-validation metric is explicitly unreliable at n=11** -- the real Wilson interval on win rate alone spans roughly 15%-65%, consistent with both a losing and a winning true edge.
2. **The counterfactual analysis (13 rejected winners vs. 5 rejected losers) is itself too small and too coarse (sign-agreement only) to draw a conclusion from.**
3. **Root cause groupings are all n≤5** -- any apparent pattern (e.g. `TREND_CONTINUATION` underperforming) is a hypothesis, not a finding, at this sample size.

**Recommended next step: Expand Historical Corpus.** The architecture is complete, deterministic, and now instrumented to measure itself honestly -- the single blocking constraint on every conclusion in this document is sample size, not missing capability. Before Paper Trading, Broker Integration, or rejecting the decision engine, the responsible next step is running this exact same measurement stack (Series 92-101, unmodified) against a substantially larger real historical corpus, so that Deliverable 4's metrics can cross the disclosed 30-observation reliability floor and actually answer the question this series exists to ask: does BUJJI have a real edge?
