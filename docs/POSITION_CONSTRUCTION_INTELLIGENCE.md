# Position Construction Intelligence v1 (PCI v1)
## BUJJI Engineering Series 95

**Status:** Real, implemented, tested, replayed against the real 41-day
corpus, driven directly by Series 89/92/93's real outputs. Inserted as
a new PLANNING layer between Strategy Selection and Series 90's real
Trade Construction:

```
... -> Strategy Selection -> Position Construction -> Trade Construction -> Portfolio Construction
```

This layer decides WHICH CONCRETE SHAPE best expresses a selected
strategy family, given the thesis and expression that justified it --
never a real strike, never a real expiry date, never a real premium.
Series 90's Trade Construction, Series 91's Portfolio Construction,
Series 92's Trade Thesis, and Series 93's Strategy Expression /
Strategy Selector are all completely unmodified in this series.

---

## 1. Deliverable 1 — Capability Audit

| Source | Finding | Reused as |
|---|---|---|
| Series 90 Trade Construction | Real expiry DTE window (`DEFAULT_MIN_DTE`/`DEFAULT_MAX_DTE`), real per-family delta targets (`FAMILY_DELTA_TARGETS`), real wing-width policy (`WING_WIDTH_FALLBACK_POINTS`/`WING_WIDTH_EXPECTED_MOVE_MULTIPLIER`), and real risk-family classification (`DEFINED_RISK_FAMILIES`/`UNDEFINED_RISK_FAMILIES`) | **REUSABLE DIRECTLY** -- imported verbatim (`bujji.msi_position_construction.config` re-exports `bujji.msi_trade_construction.config`'s objects by identity, verified by an `is` test, not just equality). Series 90's real strike/expiry SELECTION functions themselves (which touch a real option chain) are **NOT reusable here** -- they require real chain data this planning layer deliberately does not have. |
| Series 91 Portfolio & Risk Construction | Portfolio-level, strictly downstream of position construction | **No reuse needed** -- nothing in Series 91 informs a construction plan; it consumes whatever `TradeConstructionAssessment` results from Series 90, unaffected by this series. |
| Legacy production strike-selection (`bujji/broker/instrument_master.py`) | Confirmed still LIVE-ONLY (async, network, wall-clock) -- same finding as Series 90's own audit | **UNAVAILABLE** for deterministic replay, unchanged conclusion. |
| Greeks engine (`bujji.intelligence.greeks_brain`) | Computes real numeric Greeks from a real IV | **NOT NEEDED** -- this package has no real premiums to solve an IV from; `expected_delta`/`gamma`/`theta`/`vega` are QUALITATIVE SIGNS ONLY (`POSITIVE`/`NEGATIVE`/`NEUTRAL`), derived from Strategy Expression's own already-computed `desired_*` fields, never a real Black-Scholes call. |
| Volatility Structure (VSB) | Real `expected_move_pct` | **NOT imported directly** -- already flows through `TradeThesisAssessment.expected_move` (a real pass-through Series 92 already exposes), so this package reuses it via the thesis object rather than re-importing VSB. |
| Options observations | Real per-contract chain rows | **NOT NEEDED** -- no chain data exists at this planning layer. |

**Conclusion**: no logic was duplicated. Everything Series 90 already knows how to do with real data (DTE window, delta targets, wing-width policy, risk classification) is imported and reused by reference; only the ONE genuinely new decision -- which concrete SHAPE to build within a family -- is new to this package, because no prior module had any notion of "which shape" at all (Series 90 always builds exactly one hardcoded shape per family, unconditionally).

## 2. Deliverable 2 — PositionConstructionAssessment

Immutable, frozen. All spec-required fields present: `selected_strategy_family`, `construction_type`, `expiry_plan` (rule + DTE window + reasoning), `strike_plan` (target delta + reasoning), `wing_plan` (plan + width source + reasoning), `risk_profile`, `payoff_profile`, `adjustment_readiness`, `expected_delta`/`gamma`/`theta`/`vega` (qualitative signs), `explanation`, `provenance`, `assessment_id`, `schema_version`.

## 3. Deliverable 3 — Construction Policies

Declarative, in `config.py`, never tuned against replay outcomes:
- **`FAMILY_DEFAULT_CONSTRUCTION_TYPE`**: the natural shape Series 90 already builds for each of the 13 real families (e.g. `IRON_CONDOR -> IRON_CONDOR_SHAPE`, `VOLATILITY_EXPANSION -> LONG_STRADDLE`).
- **Refinement rules, deliberately scoped to the two single-leg directional families only** (a disclosed scope boundary, not a blanket policy):
  - `LONG_DIRECTIONAL`: `HIGH` conviction -> `SINGLE_LEG` (max, uncapped upside); `MODERATE`/`LOW` conviction -> `VERTICAL_DEBIT_SPREAD` (cheaper, lower theta cost, still defined risk either way) -- exactly the user's own success-criterion example.
  - `SHORT_DIRECTIONAL`: if Strategy Expression demands `DEFINED_RISK` -> `VERTICAL_CREDIT_SPREAD` (caps the naked short); otherwise stays the naked `SINGLE_LEG` default.
- **`ADJUSTMENT_READINESS_BY_CONSTRUCTION_TYPE`** and **`PAYOFF_BY_CONSTRUCTION_TYPE`**: declarative, standard-options-theory tables, one entry per construction type.

## 4. Deliverable 4 — Position Constructor

`construct_position(selection, expression, thesis, timestamp=...)` consumes exactly the three real assessments the spec lists, produces exactly one `PositionConstructionAssessment`. No optimisation, no profitability ranking, no execution logic, no real chain/strike/premium anywhere in the engine.

## 5. Deliverable 5 — Construction Explainability

Every assessment's `explanation` answers all four required questions: `why_this_construction_style` (the exact conviction/risk-profile reasoning that picked this shape), `why_this_expiry_philosophy` (reused DTE window, cited as reused not re-derived), `why_this_strike_philosophy` (reused delta target, cited as reused), `evidence_that_drove_the_design` (thesis type/conviction, expression's desired axes, selected family).

## 6. Deliverable 6 — Real 41-day corpus replay

Driven directly by the real, expression-filtered `StrategySelectionAssessment` (Series 93's post-filter selection, not the pre-filter baseline):

- **Construction-type distribution**: `NONE 19, SINGLE_LEG 7, VERTICAL_DEBIT_SPREAD 5, BUTTERFLY_SHAPE 5, SHORT_STRANGLE 4, COVERED_SHAPE 1`.
- **Expiry-style distribution**: `NEAREST_WEEKLY 22, NONE 19` -- `CALENDAR_NEAR_FAR` never triggered in this corpus (`CALENDAR` was never selected, matching Series 89-94's own repeated finding).
- **Risk-profile distribution**: `UNKNOWN 19 (no construction), DEFINED_RISK 16, UNDEFINED_RISK 6`.
- **Payoff-profile distribution**: `UNKNOWN 19, LIMITED_PROFIT_LIMITED_LOSS 10, UNLIMITED_PROFIT_LIMITED_LOSS 6, LIMITED_PROFIT_UNLIMITED_LOSS 6`.
- **Adjustment-readiness distribution**: `NOT_APPLICABLE 19, ADJUSTMENT_LIMITED 12, ADJUSTMENT_FRIENDLY 10`.
- **No-construction frequency: 19/41** -- identical to Series 93/94's own no-selection rate, correctly cascading (no family selected -> nothing to construct, an honest pass-through, not a new gap).
- Replayed twice: **position-construction `assessment_id`s were byte-identical** across both runs.

No tuning was performed against these numbers -- `FAMILY_DEFAULT_CONSTRUCTION_TYPE` and the refinement rules were fixed before this replay ran.

## 7. Deliverable 7 — Behaviour Validation

**One family, multiple construction styles, driven by real conviction**: `LONG_DIRECTIONAL`'s real conviction/construction-type pairing across the corpus was exactly `{(HIGH, SINGLE_LEG): 6, (MODERATE, VERTICAL_DEBIT_SPREAD): 4, (LOW, VERTICAL_DEBIT_SPREAD): 1}` -- **zero exceptions**: every `HIGH`-conviction day produced `SINGLE_LEG`, every `MODERATE`/`LOW`-conviction day produced `VERTICAL_DEBIT_SPREAD`. This is direct, real evidence of the declared rule operating exactly as designed, with no ambiguous or contradictory cases in this corpus.

**Construction never contradicts the selected family**: every constructed shape is either the family's own declared default or one of its two explicitly-declared refinements (`LONG_DIRECTIONAL`/`SHORT_DIRECTIONAL` only) -- verified structurally (the engine has no code path that can assign a construction type unrelated to the input family).

**Construction never bypasses Strategy Expression**: `expected_theta`/`expected_vega` are derived directly from `expression.desired_time_decay`/`desired_volatility_exposure` (never re-derived from the thesis or elsewhere) -- confirmed directly in a real `RANGE_PERSISTENCE` + `IRON_CONDOR` case: `expected_theta=POSITIVE`, `expected_vega=NEGATIVE`, matching the expression's own real `POSITIVE_THETA`/`SHORT_VOLATILITY` fields exactly.

## 8. Deliverable 8 — Integration

Demonstrated end-to-end on real data: `Observation -> MSI -> Trade Thesis -> Strategy Expression -> Strategy Selection -> Position Construction`, with no Portfolio-layer changes and no execution anywhere. Sample real day: `2026-05-25: Thesis=TREND_CONTINUATION -> Expression -> Selected=LONG_DIRECTIONAL -> Construction=SINGLE_LEG (risk=DEFINED_RISK, adjustment=ADJUSTMENT_LIMITED, expiry_rule=NEAREST_WEEKLY)`.

## 9. Interaction with Strategy Selection

Strictly one-directional: Position Construction reads `StrategySelectionAssessment` (the real, expression-filtered Series 89 output) and produces nothing that feeds back into it. The Selector's own scoring/tie-break logic is completely untouched in this series.

## 10. Interaction with Portfolio Construction

No interaction yet -- Series 91 still consumes `TradeConstructionAssessment` (Series 90's real output) directly, unaware this new layer exists. A future refinement could have Series 90's Trade Construction read this package's `construction_type`/`strike_plan`/`wing_plan` to decide its own real leg count and delta targets (e.g. building an actual 2-leg vertical when `construction_type == VERTICAL_DEBIT_SPREAD` instead of Series 90's current always-single-leg `LONG_DIRECTIONAL` shape) -- not built here, explicitly out of this series's scope (Series 90 was not modified), but a clean, additive next step.

## 11. Known limitations

- `expected_delta`/`gamma`/`theta`/`vega` are qualitative SIGNS ONLY, never real magnitudes -- this layer has no real premium data to compute an actual Greek value from.
- The construction-type refinement logic is scoped narrowly to `LONG_DIRECTIONAL`/`SHORT_DIRECTIONAL` only; every other family always uses its single declared default shape, even though a similar conviction-driven refinement could plausibly apply elsewhere (e.g. a lower-conviction `IRON_CONDOR` could plausibly prefer narrower wings) -- deliberately out of scope here, a real, disclosed limitation.
- Series 90's Trade Construction does not yet consume this package's output at all (Section 10) -- the "debit spread" construction type this package recommends is not yet actually built with real strikes anywhere; that remains a genuine gap between planning and execution until a future series wires them together.

## 12. Deliverable 10 — Recommendation

Evidence-based, from the real replay measurements above:

1. **`Margin Bridge` remains the most measurement-supported gap across the whole arc** -- Series 91's own real corpus finding (every approved trade priced at an identical flat ₹100,000 margin regardless of family) is untouched by this series and remains the single largest fidelity gap affecting 100% of portfolio decisions.
2. **This series' own new gap (Section 11, Trade Construction/Position Construction disconnect)** is real but narrower in impact: only 5/41 days recommended a `VERTICAL_DEBIT_SPREAD`/`VERTICAL_CREDIT_SPREAD` that Series 90 cannot yet actually build (it would still build the family's Series-90-hardcoded single-leg shape instead) -- a real but smaller-frequency gap than the margin one.
3. **Execution Planning / Trade Management / Shadow Trading remain premature** while margin fidelity is still a single flat placeholder number -- building any of them now would inherit and compound that same distortion, exactly as concluded in Series 91's own Deliverable 10.

**Recommended next step: Series 96 — Margin Bridge**, unchanged from Series 91's original recommendation and reaffirmed here: it remains the highest-measured-impact, still-unaddressed gap in the entire real corpus, touching every single portfolio decision rather than a narrower subset.
