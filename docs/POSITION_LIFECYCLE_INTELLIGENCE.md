# Position Lifecycle Intelligence v1 (PLI v1)
## BUJJI Engineering Series 96

**Status:** Real, implemented, tested, replayed against the real 41-day
corpus, driven directly by Series 89/91/92/95's real outputs, threading
open-position state across days for the first time in this arc. Sits
after Portfolio Construction, before a future Execution layer:

```
... -> Position Construction -> Portfolio Construction -> Position Lifecycle -> (future) Execution
```

This layer decides how an already-admitted position should be treated
GOING FORWARD -- healthy, deteriorating, an adjustment/exit/harvest
candidate, or thesis-broken -- never placing an order, never touching
a real strike/premium.

---

## 1. Deliverable 1 — Capability Audit

| Source | Finding | Classification |
|---|---|---|
| Production `TradeManager` (`bujji/trade/manager.py`) | Real, live-only (imperative `reassess()` per candle, wall-clock `hard_exit` cutoff), and hardcoded to ONE strategy (a short straddle, VWAP-of-combined-premium breach exit) -- the same single-strategy limitation this whole MSI arc was built to replace (first identified in this project's very first code-review series) | **OBSOLETE for direct reuse** -- incompatible with the now much richer 13-family arc. Its THREE checks (`_check_hard_exit` = time cutoff, `_check_vwap_breach` = a specific profit/exit rule, `_check_risk` = MTM loss cap) informed this package's `expiry_policy`/`profit_policy`/`loss_policy` concepts BY ANALOGY, not by code reuse -- disclosed here explicitly, not silently reinvented. |
| VWAP exit engine (`bujji/signal/vwap_audit.py`, `PremiumVwapTracker`) | Real, but computes a live premium VWAP requiring real streaming premiums | **UNAVAILABLE** -- no real streaming premium data exists in this replay arc (same conclusion as Series 90/91's own margin/liquidity findings). |
| Hard stop / MTM protection | Embodied in `TradeManager._check_risk` (a configured `max_mtm_loss` cap) -- real but requires real, live MTM tracking | **UNAVAILABLE for direct reuse**; conceptually mirrored by this package's `loss_policy` (`EXIT_ON_THESIS_BROKEN`), which substitutes real thesis-invalidation evidence for a P&L figure this layer cannot compute. |
| Greeks engine | Real Black-Scholes Greeks, needs a real premium/IV | **NOT CALLED DIRECTLY** -- this package consumes Series 91's own already-computed real `portfolio_delta_after`/`portfolio_vega_after` rather than re-deriving Greeks itself. |
| Portfolio Construction (Series 91) | Real portfolio-level Greeks after each admission | **REUSED DIRECTLY** -- `HARD_MAX_ABS_PORTFOLIO_DELTA`/`VEGA` are imported verbatim from Series 91's own `MAX_ABS_PORTFOLIO_DELTA`/`VEGA`; this package's own advisory WATCH thresholds are a disclosed 60% fraction of them, not independently invented numbers. |
| Position Construction (Series 95) | Real `construction_type` per admitted position | **REUSED DIRECTLY** -- drives which adjustment triggers apply (Deliverable 4) and which lifetime/profit-policy convention applies. |

**Conclusion**: no logic was duplicated. The one genuinely new capability -- comparing an entry-day thesis against a fresh, current-day thesis to determine if the original belief still holds -- did not exist anywhere in this codebase before this series.

## 2. Deliverable 2 — PositionLifecycleAssessment

Immutable, frozen. All spec-required fields present: `lifecycle_id`, `position_state`, `expected_lifetime`, `monitoring_requirements`, `adjustment_policy`, `profit_policy`, `loss_policy`, `expiry_policy`, `emergency_policy`, `thesis_invalidation`, `explanation`, `provenance`, `schema_version`. Each policy field is its own small, frozen dataclass (`AdjustmentPolicy`, `ProfitPolicy`, `LossPolicy`, `ExpiryPolicy`, `EmergencyPolicy`, `ThesisInvalidation`) so each can be reasoned about and explained independently (Deliverable 7).

## 3. Deliverable 3 — Lifecycle Taxonomy

All 9 required states implemented: `NEWLY_OPENED`, `HEALTHY`, `IMPROVING`, `AT_RISK`, `ADJUSTMENT_CANDIDATE`, `THESIS_BROKEN`, `PROFIT_HARVEST`, `EXIT_CANDIDATE`, `CLOSED`. Derived in a fixed, disclosed priority order (Section on Deliverable 5 below) -- never a blend or a numeric score.

## 4. Deliverable 4 — Adjustment Policies

Declarative, per Series 95 `construction_type`, in `taxonomy.py::CONSTRUCTION_TYPE_TRIGGERS`. Every trigger is explicitly marked either **actionable** (real, replay-computable data exists -- e.g. `DELTA_DRIFT` from Series 91's real portfolio delta) or **monitoring-only** (a real, standard trigger with NO real data source in this codebase -- e.g. `WING_BREACH` requires real strikes this layer never sees). Examples exactly matching the spec:
- **Iron Condor / Iron Fly**: `DELTA_DRIFT` (actionable), `WING_BREACH` (monitoring-only), `VOLATILITY_EXPANSION` (actionable, via Series 91's real portfolio vega), `TIME_DECAY` (actionable, via real DTE).
- **Vertical Debit/Credit Spread**: `TARGET_REACHED` (monitoring-only -- no real P&L), `THETA_DETERIORATION` (monitoring-only), `THESIS_INVALIDATION` (actionable -- Deliverable 5's real thesis comparison).
- **Calendar**: `TERM_STRUCTURE_COLLAPSE` (monitoring-only -- no real term-structure data anywhere in this codebase, the same gap Series 88 already disclosed), `IV_CONTRACTION` (actionable, via the thesis's own real `volatility_expectation` trend).

This honesty split (actionable vs. monitoring-only) is itself the key design decision of this series -- it would have been easy to silently pretend every classic trigger is "checked" when several genuinely cannot be, with the data that exists today.

## 5. Deliverable 5 — Thesis Monitoring

`_thesis_compatible(entry_thesis_type, current_thesis_type)` -- a fixed, declarative `COMPATIBLE_THESIS_TRANSITIONS` table (e.g. `RANGE_PERSISTENCE` stays compatible with `RANGE_PERSISTENCE`/`MEAN_REVERSION`, but is invalidated by `BREAKOUT`/`TREND_REVERSAL`). `EVENT_RISK` is handled specially: since it represents genuine, resolvable uncertainty, resolving into ANY real thesis (other than falling back to `NO_TRADE`) is treated as healthy, never as an invalidation -- disclosed and unit-tested explicitly. Every `ThesisInvalidation` record states BOTH the entry and current thesis type plus the exact compatibility verdict -- never inferred silently.

## 6. Deliverable 6 — Real 41-day corpus replay

Position state was threaded day-over-day for the first time in this arc (a local, script-level extension of Series 91's own `PortfolioState` threading pattern -- no package was modified to support this):

- **27 total per-day lifecycle assessments** across all real still-open positions in the corpus.
- **Position-state distribution: `THESIS_BROKEN` 20, `PROFIT_HARVEST` 3, `HEALTHY` 3, `AT_RISK` 1.**
- **Thesis-invalidation frequency: 20/27 (74%)** -- the single largest, most important real finding of this series: **the large majority of positions held even one additional day see their entry thesis invalidated by the next real MSI reading.** This is fully consistent with earlier findings in this arc (Series 89's own real corpus replay found 37/40 day-transitions changed the selected strategy family) -- thesis persistence across days is measurably low in this real corpus.
- **Expected-exit frequency (`EXIT_CANDIDATE` + `THESIS_BROKEN`): 20/27 (74%)**, identical to the invalidation rate -- no case in this corpus reached a HARD exposure breach severe enough to escalate a `THESIS_BROKEN` position to the more urgent `EXIT_CANDIDATE`.
- **Expected profit-harvest frequency: 3/27** -- all three occurred at the real, DTE-based near-expiry trigger for credit-collecting/defined-risk-decay shapes, exactly as designed.
- **Expected-adjustment frequency: 0/27** and **expected-emergency frequency: 0/27** -- in this real corpus, portfolio-level delta/vega never drifted past even the ADVISORY watch thresholds (themselves already a conservative 60% of Series 91's hard reject caps) for any tracked position. A real, honest null result, not evidence the trigger logic is unreachable in general.
- Replayed twice: **lifecycle `lifecycle_id`s were byte-identical** across both runs.

No tuning was performed against these numbers -- the compatibility table, watch thresholds, and DTE policy were fixed before this replay ran.

## 7. Deliverable 7 — Explainability

Every `PositionLifecycleAssessment.explanation` answers all four required questions: `why_this_adjustment_policy` (which triggers are declared, how many are monitoring-only, which fired), `why_this_profit_policy` (the construction-type-specific harvest convention, and whether today meets it), `why_this_invalidation_rule` (both thesis types and the compatibility verdict), `why_this_emergency_policy` (the exact real portfolio Greek that breached, or the honest absence of one).

## 8. Deliverable 8 — Integration

Demonstrated end-to-end on real data: `Observation -> Events -> Episodes -> MSI -> Trade Thesis -> Strategy Expression -> Strategy Selection -> Position Construction -> Portfolio Construction -> Position Lifecycle`. No orders placed, no MSI module modified, no Strategy Selection redesign anywhere in this replay -- verified both by the constraint discipline followed while writing this series and by the full pre-existing test suite (2612 tests) passing unchanged.

## 9. Interaction with Portfolio Construction

One-directional: this package reads Series 91's real `PortfolioConstructionAssessment.portfolio_delta_after`/`vega_after` for its own advisory watch-threshold checks. Series 91 itself is completely unmodified and has no awareness this package exists.

## 10. Future relationship with Execution

A future Execution Planning layer would consume `PositionLifecycleAssessment.position_state`: `HEALTHY`/`IMPROVING`/`NEWLY_OPENED` require no action; `ADJUSTMENT_CANDIDATE` would route to an adjustment-order-construction step (not built anywhere yet); `PROFIT_HARVEST`/`THESIS_BROKEN`/`EXIT_CANDIDATE` would route to an exit-order-construction step. This package produces the DECISION of what should happen; it deliberately stops there.

## 11. Known limitations

- Several classic lifecycle triggers (`WING_BREACH`, `TARGET_REACHED`, `THETA_DETERIORATION`, `TERM_STRUCTURE_COLLAPSE`) have no real data source anywhere in this codebase and are honestly disclosed as monitoring-only, not evaluated (Section 4).
- Portfolio Greeks used for the delta-drift/volatility-expansion checks are a PORTFOLIO-WIDE reading (Series 91's own static, entry-time aggregation), not a per-position mark-to-market view -- a position's OWN individual contribution to a drift is not isolated from the rest of the book.
- Zero adjustment/emergency events occurred in this specific 41-day corpus -- the trigger logic is implemented and unit-tested directly, but has no real-corpus exercise of a POSITIVE case yet.
- The thesis-compatibility table (Section 5) is a declarative, standard-market-narrative judgment, never empirically validated against real outcomes (deliberately -- this codebase does not optimize against historical returns).

## 12. Deliverable 10 — Recommendation

Evidence-based, from the real replay measurements above, and endorsing the ordering the user themselves proposed:

1. **The 74% thesis-invalidation rate is the single most important finding of this entire arc's lifecycle behavior** -- it demonstrates real, measurable value from having built this layer BEFORE execution: without it, a position entered on real evidence would silently be held for days after that evidence reversed, with no mechanism to notice.
2. **Margin Bridge remains the largest measured fidelity gap overall** (Series 91's finding, still unaddressed, affecting 100% of portfolio decisions) -- but the user's own stated rationale for sequencing Position Lifecycle Intelligence BEFORE it is now independently supported by this series' own real finding: a complete decision chain (market -> thesis -> expression -> selection -> construction -> admission -> lifecycle) is now real and demonstrated end-to-end, exactly the "complete end-to-end decision model" milestone the user described.
3. **Zero real adjustment/emergency events in this corpus** means those specific code paths, while implemented and tested, remain unexercised by real data -- a reasonable, disclosed gap for a future, larger-corpus investigation (mirroring Series 94's own precedent), not a reason to delay the next milestone.

**Recommended next step: Margin Bridge**, exactly as the user's own reasoning anticipated -- the decision engine is now complete end-to-end; what remains (Margin Bridge, Execution Planning, Shadow Trading, Paper Trading, Live Integration) is connecting this already-complete reasoning chain to real capital and broker infrastructure, not inventing further decision logic.
