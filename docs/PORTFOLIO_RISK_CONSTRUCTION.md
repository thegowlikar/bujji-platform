# Portfolio & Risk Construction v1 (PRC v1)
## BUJJI Engineering Series 91

**Status:** Real, implemented, tested, replayed against the real 41-day
corpus, driven directly by Series 90's own real `TradeConstructionAssessment`
output. This is the portfolio-admission gate: given a fully constructed
trade, decides APPROVE / REJECT / DEFER, and if approved, the largest
defensible position size. **Not execution. Not order placement.**

---

## 1. Deliverable 1 — Capability Audit

| Capability | Finding | Classification |
|---|---|---|
| Capital engine (`bujji.capital.engine.CapitalManagementEngine`) | Real, production-grade, but fully async and broker-coupled (`get_funds()`, wall-clock `now_ist()`) | Its **sizing arithmetic** (`usable_margin = available_margin × safety_buffer`; `maximum_safe_lots = floor(usable_margin / margin_per_lot)`; `approved_lots = min(configured_max, max(0, maximum_safe_lots))`) is **REUSABLE DIRECTLY** once extracted as a pure function -- done here (`compute_lot_sizing`, additive, zero behavior change to `approve_trade`, verified: 105/105 existing capital tests still pass unchanged). This is now the single sizing implementation shared between replay and production. |
| Margin estimation | No deterministic/replay-safe margin source exists anywhere (Series 90's own finding, confirmed again here) -- only a live FYERS SPAN call. **However**, `bujji.capital.providers`/`policy` already define a real, config-driven **SIMULATION tier** (`simulated_margin_per_lot: 100000` in `config/config.yaml`) explicitly for "replay and pure development." | **REPLAY-ONLY APPROXIMATION, but the VALUE is reused verbatim** from this existing config constant, not invented fresh. |
| Available capital | No config key, no paper-trading starting-balance constant, no deterministic source of any kind exists anywhere in this codebase for "how much capital is available." | **UNAVAILABLE.** A new, explicitly temporary constant (`REPLAY_ASSUMED_TOTAL_CAPITAL = 2,000,000`) is introduced because nothing exists to reuse -- see Section 6 for the exact production integration path. |
| Risk engine / portfolio exposure / Greeks aggregation / concentration limits / existing position handling | None exist anywhere in this codebase (`bujji/intelligence/risk_brain.py` does not exist despite a `tests/test_risk_brain.py` file name; grepped the whole repo for "portfolio"/"concentration"/"net_delta" -- no hits outside unrelated packages). | **UNAVAILABLE.** Built fresh in this package, reusing only the existing pure Black-Scholes primitives (`solve_implied_volatility`, `_bs_delta`, `_bs_gamma`, `_bs_theta`, `_bs_vega`) for per-leg Greeks -- the same functions Series 90/VSB already reuse, called again here, not reimplemented. |

## 2. Deliverable 2 — PortfolioConstructionAssessment

Immutable, frozen. All spec-required fields present, plus `position_size_lots` (int or the literal `SIZE_UNKNOWN`) since Deliverable 5 explicitly requires a sizing answer. `required_margin` is **always `None`** (Series 90's own finding still holds: no certified source exists); `estimated_margin` carries the config-sourced ESTIMATED-tier figure actually used for sizing, clearly distinguished from `required_margin` at the type level so no caller can mistake one for the other.

## 3. Deliverable 3 — Admission Engine

Evaluated in a fixed, disclosed priority order (first failing check wins):
1. Construction itself must have succeeded (`CONSTRUCTION_NOT_SUCCESSFUL`).
2. `UNDEFINED_RISK_POLICY` -- structurally undefined-risk families rejected by default (`config.ALLOW_UNDEFINED_RISK = False`).
3. `INSUFFICIENT_CONFIDENCE` -- the real MSS `selection_confidence` must reach a configured floor (`MODERATE`).
4. `GREEKS_UNAVAILABLE` (**DEFER**, not reject) -- if the day's real premiums don't yield a solvable IV, admission is deferred, not permanently refused; genuinely different evidence tomorrow could change the answer.
5. `MISSING_MARGIN_INFORMATION` -- if no margin estimate is configured at all, fails closed rather than guessing.
6. `INSUFFICIENT_CAPITAL` -- either a hard `capital_available <= 0`, or `compute_lot_sizing` itself returns 0 lots; both report a real, determinate `position_size_lots = 0` (not `SIZE_UNKNOWN` -- we DO know the size, it's zero).
7. `EXCESSIVE_CONCENTRATION` -- per strategy-family / expiry / underlying position counts.
8. `EXCESSIVE_DIRECTIONAL_EXPOSURE` / `EXCESSIVE_VOLATILITY_EXPOSURE` -- portfolio-after `|delta|`/`|vega|` against configured caps.

Only if none of the above trip does the trade **APPROVE**.

## 4. Deliverable 4 — Portfolio Simulation

`PortfolioState` is threaded explicitly by the caller across days (mirroring the Series 88/89/90 corpus scripts' own `prev_chain`/`prev_closes` threading pattern) -- this package holds no hidden internal state. Each held position is pruned from the "active" book once its `position_close_date` passes (a definitional fact, not a trade-management/rolling decision).

**Explicit, disclosed limitation**: portfolio Greeks are a **static entry-Greeks aggregation** -- each already-held leg contributes the Greeks it had AT ITS OWN ADMISSION TIME, never mark-to-market repriced against today's spot/IV. A full live portfolio risk view would require a repricing engine, which is Trade Management's job, explicitly out of scope for this series.

Concentration is measured on 3 dimensions (strategy family, expiry, underlying) with configured caps; all are structural desk-risk choices, never fit to replay outcomes.

## 5. Deliverable 5 — Position Sizing

Sizing calls `bujji.capital.engine.compute_lot_sizing` directly -- the exact same function production's `CapitalManagementEngine` uses. When no margin estimate is configured, **`position_size_lots = "SIZE_UNKNOWN"`** is returned explicitly rather than any invented number; this happened correctly in every rejection path that never reached the sizing step at all (construction failure, policy rejection, confidence rejection -- sizing was never attempted, so the honest answer is "unknown," not "zero").

## 6. Deliverable 6 — Real 41-day corpus replay

Driven directly by Series 90's own real `TradeConstructionAssessment` per day:

- **35 of 41 days reached a portfolio decision** (the other 6 are Series 89's own no-selection days).
- **Approval state distribution: REJECTED 22, APPROVED 10, DEFERRED 3.**
- **Dominant rejection reason: `UNDEFINED_RISK_POLICY`, 20 of 35 decisions (57%)** -- this is the single largest driver by a wide margin, and it directly confirms Series 90's own real finding (59% of constructed trades were structurally undefined-risk) flowing through into real admission outcomes here, not a new coincidence.
- Other reasons: `GREEKS_UNAVAILABLE` (3, deferred), `INSUFFICIENT_CONFIDENCE` (1), `CONSTRUCTION_NOT_SUCCESSFUL` (1).
- **10 approved trades, every one sized at exactly 1 lot, every one with `capital_required = ₹100,000`** -- a real, disclosed limitation: because the margin estimate is a single flat config constant (not family-specific), a naked short strangle and a single long call currently cost the SAME simulated margin. This is a genuine fidelity gap, not glossed over.
- **Final cumulative `risk_budget_used` (last approval, 2026-07-21): 0.15** -- only 15% of the placeholder ₹2,000,000 capital pool committed across the whole real replay.
- **Portfolio delta ranged 28.98–91.08, vega ranged 80.89–1938.43 across all 35 decisions -- neither ever approached the configured caps (500 / 5,000).** Concentration and exposure limits were **never** the binding constraint in this real corpus; the undefined-risk policy dominated overwhelmingly.
- Replayed twice: **portfolio decision `assessment_id`s were byte-identical** across both runs.

No tuning was performed against these numbers -- the risk policy, confidence floor, and exposure caps were fixed before this replay ran.

## 7. Deliverable 7 — Explainability

Every `PortfolioConstructionAssessment.explanation` answers:
- **Why approved?** `why_approved` -- states which checks passed and the sized lot count.
- **Why rejected?** `why_rejected` -- the specific real evidence that failed (e.g. exact `|portfolio_vega_after|` vs. the configured limit).
- **Which constraint dominated?** `dominant_constraint` -- the single reason evaluation stopped on.
- **What would need to change?** `what_would_change_for_approval` -- concrete, actionable (e.g. "selection confidence would need to reach MODERATE").

## 8. Deliverable 8 — Full pipeline integration

Demonstrated end-to-end on real data, no live broker calls anywhere:

```
Observation (real Bhavcopy) -> Events -> Episodes -> MSI (PSI/MSSI/MDI/MPPI/VSB/Consensus)
  -> Strategy Selection (MSS) -> Trade Construction (Series 90) -> Portfolio Construction (Series 91)
```

Sample real day: `2026-05-25: MDI=STRONG_BULLISH -> MSS selected=LONG_DIRECTIONAL -> TC constructed=True (2026-05-26 expiry, 1 leg) -> PRC=REJECTED (INSUFFICIENT_CONFIDENCE, size=SIZE_UNKNOWN)`. This particular day happens to illustrate a rejection (real MSS confidence was `LOW` that day) -- shown honestly rather than cherry-picking an approval for the demo.

## 9. Relationship with the existing capital engine

`bujji.capital.engine.compute_lot_sizing` is now called by BOTH `CapitalManagementEngine.approve_trade` (production, live) and `bujji.msi_portfolio_construction.engine.evaluate_trade` (replay) -- one implementation, two callers, exactly the long-term goal stated in the Series 91 brief. What still differs between them, disclosed precisely:
- **Margin source**: production uses a live/certified `MarginProvider`; replay uses a fixed config estimate.
- **Capital source**: production uses a live `CapitalSnapshot.available_margin`; replay uses a fixed placeholder constant (no deterministic equivalent exists yet).
- **Position tracking**: production has no persistent multi-position portfolio state today (each `approve_trade` call is independent, single-straddle); this package introduces the first portfolio-state concept (`PortfolioState`/`AdmittedTrade`), currently only exercised in replay.

## 10. Production integration plan

1. Replace `REPLAY_ASSUMED_TOTAL_CAPITAL` with a real `CapitalSnapshot.available_margin` read (via `bujji.capital.broker_adapter.build_snapshot`) once this package is invoked from a live scheduler -- the adapter boundary is exactly `evaluate_trade`'s `total_capital` parameter, already isolated for this swap.
2. Replace `REPLAY_MARGIN_PER_LOT_ESTIMATE` with a real `MarginProvider` call (ideally per-family, not one flat figure -- see Deliverable 10) via the same `margin_per_lot_estimate` parameter boundary.
3. Persist `PortfolioState` across live sessions (currently only threaded in-memory through a replay script) -- needs a real store, out of scope here.

## 11. Known limitations

- Margin is a single flat number regardless of strategy family -- the single largest fidelity gap (Section 6).
- Available capital has no deterministic real-world source at all (Section 1) -- a placeholder, not a measurement.
- Portfolio Greeks are static (entry-time), never mark-to-market repriced (Section 4).
- Concentration/exposure caps were never exercised as the binding constraint in this real corpus -- their calibration remains untested against a scenario that would actually trigger them.

## 12. Deliverable 10 — Recommendation: **Margin Bridge**

Evidence-based, from the real replay measurements above:

1. **Every single admission decision in this corpus depended on one flat, non-differentiated margin estimate** (Section 6) -- `capital_required` was identical (₹100,000) across every approved family, from a single long call to a naked strangle. This is the one input that affects **100% of decisions**, more than any other gap measured.
2. **Concentration and exposure limits were never binding** (delta/vega never approached their caps) -- tightening or building further logic there would change nothing observed in this corpus.
3. **Liquidity Intelligence's gap affected only 1/35 days** (per Series 90) -- real, but a much smaller-frequency problem than the margin gap.
4. **Execution Planning / Trade Management / Shadow Trading are all premature** while every position's true capital cost is unknown -- shadow trading in particular would misrepresent real capital utilization if run against today's flat estimate.

**Recommended next step: Series 92 — Margin Bridge**, building a real (or at minimum family-differentiated, evidence-based) margin estimation path -- investigating first whether a deterministic, replay-safe SPAN-like calculation can be derived from real Bhavcopy data (e.g. exchange-published margin files, if they exist, the same way Bhavcopy itself was discovered and reused), before falling back to a better-calibrated per-family config estimate, per the same "investigate reuse before approximating" discipline this series itself followed.
