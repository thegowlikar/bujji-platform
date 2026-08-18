# Bujji Research Campaign v1.0 — Final Charter (Corrected)

**Mission:** Answer one question — does Bujji have a strategy with positive expectancy after realistic execution costs? Nothing else.

## Permanent Governance Rule (Locked)

No new intelligence infrastructure until Bujji completes one full research cycle with measured outcomes.

---

## Timeline (Corrected — Item 1)

The prior draft claimed "12 weeks ≈ 60 sessions, therefore aligned." That was wrong: it silently assumed Phase 20.0-20.3 (data audit, execution engine, backtest, risk declaration) take zero time and that 60 paper sessions occur with zero missed trading days. Neither holds. The corrected budget times every phase explicitly, and separates the **build clock** from the **paper-trading clock**:

| Sub-phase | Budget | Type |
|---|---|---|
| 20.0 — Reality Audit | 5 trading days | Build |
| 20.1 — Execution Reality Engine (skeleton + backtest/paper sharing) | 5 trading days | Build |
| 20.2 — Strategy Backtest (rolling walk-forward, gate check) | 10 trading days | Build |
| 20.3 — Risk Definition + tail-risk stress spec | 2 trading days | Build |
| **Build subtotal** | **22 trading days (~4.5 weeks)** | |
| 20.4 — Paper Reality | 60 trading sessions minimum (~12 weeks at ~20 sessions/month, zero-slippage-on-calendar assumption) | Paper |
| 20.5 — Risk Fire Drill | 2 trading days, run *during* 20.4's window, not after it | Build (parallel) |
| 20.6 — Experiment Governance | ongoing throughout, no separate clock | — |
| 20.7 — Final Decision Gate | 3 trading days | Build |

**Total honest campaign length: build subtotal + paper clock + gate ≈ 4.5 + 12 + 0.5 ≈ 17 weeks**, not 12. The "12-week" figure from the prior draft is retained only as the label for the **paper-trading clock** (20.4), since that is the phase the original 12-week number was actually describing. The full campaign, start to Outcome decision, is budgeted at **17 weeks**, stated honestly up front rather than discovered under pressure in week 10.

If build phases (20.0-20.3) run under budget, that time is *not* reallocated to extending paper trading — it shortens the total campaign. It is never spent extending the research clock further, per the permanent governance rule.

---

## Capture Continuity During the Freeze (Corrected — Item 2)

"Freeze observation infrastructure" means **no new engineering work** on the observation layer — no new resolutions, no new stores, no new frameworks. It does **not** mean the existing, already-built capture stops running.

Explicit rule: the Phase 19.19 daily intelligence session and the Phase 19.20 microstructure capture (once validated) continue running operationally, unattended by further development, throughout the entire Phase 20 research campaign. This is required for the charter's own execution-model confidence rule to ever be satisfiable — the "20 live captured sessions OR 400+ observed option-leg minutes" threshold in Phase 20.1 can only be met if real capture keeps accumulating data in the background while the research campaign proceeds.

No new capability is added to the capture layer during Phase 20. It simply keeps doing what it was already built and validated to do.

---

*(All other sections of the charter — Phase 20.0 through 20.7, the Final Decision Gate, and the closing philosophy — remain as finalized in the prior round, unchanged.)*
