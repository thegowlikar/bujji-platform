# Strategy Expression Engine v1 (SEE v1)
## BUJJI Engineering Series 93

**Status:** Real, implemented, tested, replayed against the real 41-day
corpus. Inserted as a new layer BETWEEN Trade Thesis (Series 92) and
Strategy Selection:

```
Observation -> Events -> Episodes -> MSI -> Trade Thesis
    -> Strategy Expression -> Strategy Selection Foundation -> Strategy Selector
    -> Trade Construction -> Portfolio Construction
```

This layer translates a validated thesis into the EXPOSURE
characteristics a position should have -- never a strike, never an
expiry, never a concrete strategy choice, never a profitability score.
Neither Strategy Selection Foundation nor the Strategy Selector were
redesigned; both were extended **additively only** (Deliverable 5),
verified by their full pre-existing test suites passing unchanged.

---

## 1. Deliverable 1 — Capability Audit

- **Trade Thesis (Series 92)**: the sole real input -- `TradeThesisAssessment.thesis_type`/`directional_expectation`/`volatility_expectation`/`conviction` consumed directly, never re-derived.
- **Strategy Suitability Foundation (Series 87)**: its 13 real strategy families (`ALL_STRATEGY_FAMILIES`) are the fixed universe this package classifies -- confirmed by an equality test (`set(FAMILY_CHARACTERISTICS) == set(ssf_taxonomy.ALL_STRATEGY_FAMILIES)`). Its own suitability GATING logic is untouched.
- **Strategy Selector (Series 89)**: risk profile per family (`DEFINED_RISK_FAMILIES`/`UNDEFINED_RISK_FAMILIES`, actually sourced from `bujji.msi_trade_construction.taxonomy`) is REUSED VERBATIM, not re-classified -- a `config.py`-level assertion enforces the two sources never disagree. `select_strategy` itself gained one new optional, keyword-only parameter (`expression_compatible_families`); its default (`None`) preserves 100% of prior behavior, verified against its full 16-test suite unchanged.
- **Portfolio & Risk Construction (Series 91)**: investigated, found to need no changes at all -- it already consumes whatever `TradeConstructionAssessment` it's given, regardless of which upstream layers produced the selected family.

**Determination**: Strategy Expression CAN remain fully independent of implementation details. It imports only Series 92's real model type (a legitimate downstream-consumption exception, same pattern as every prior series) and never imports Strategy Selection Foundation's or the Selector's own reasoning modules (enforced by an AST test).

## 2. Deliverable 2 — StrategyExpressionAssessment

Immutable, frozen. All spec-required fields present: `thesis` (the real Series 92 object, embedded directly), `desired_direction`, `desired_volatility_exposure`, `desired_risk_profile`, `desired_time_decay`, `desired_convexity`, `required_characteristics`, `forbidden_characteristics`, `compatible_strategy_families`, `incompatible_strategy_families`, `explanation`, `provenance`, `assessment_id`, `schema_version`.

## 3. Deliverable 3 — Expression Taxonomy

All 16 example characteristics implemented as plain string constants describing EXPOSURE, never a named strategy: `DIRECTIONAL`, `DELTA_NEUTRAL`, `LONG_VOLATILITY`, `SHORT_VOLATILITY`, `POSITIVE_THETA`, `NEGATIVE_THETA`, `DEFINED_RISK`, `UNDEFINED_RISK`, `DEBIT`, `CREDIT`, `LIMITED_PROFIT`, `UNLIMITED_PROFIT`, `LIMITED_LOSS`, `UNLIMITED_LOSS`, `POSITIVE_CONVEXITY`, `NEGATIVE_CONVEXITY`. Each of SSF's 13 real families is declaratively tagged against this vocabulary from standard options theory (never fit to replay data) in `config.py::FAMILY_CHARACTERISTICS`.

**Disclosed limitation**: the user's own illustrative examples ("Bull Call Spread", "Debit Spread") do not correspond to any of SSF's real 13 families -- SSF has no distinct vertical-debit-spread family today. Every `compatible_strategy_families` output is necessarily a subset of the REAL 13 families, never a fabricated name.

## 4. Deliverable 4 — Expression Engine

`derive_strategy_expression(thesis, timestamp=...)` looks up a fixed, declarative per-thesis-type rule (`config.py::THESIS_EXPRESSION_RULES` -- desired direction/volatility/risk/theta/convexity plus required/forbidden characteristic sets), then filters SSF's 13 families against it: a family is compatible iff it has every required characteristic and none of the forbidden ones. No optimisation, no profitability scoring, no strike logic anywhere.

## 5. Deliverable 5 — Strategy Selector Integration (additive only)

`bujji/msi_strategy_selector/engine.py::select_strategy` gained ONE new optional, keyword-only parameter:

```python
expression_compatible_families: Optional[Tuple[str, ...]] = None
```

When provided, the already-`SUITABLE` family set is filtered down to only those names present in it, **before** market-state scoring runs -- exactly the "Suitable -> Matches Expression -> Not Forbidden -> Selected" cascade the spec asks for. The parameter is a plain `Tuple[str, ...]`, not an import of this package's own types -- MSS stays decoupled from Strategy Expression's internals, the same sibling-isolation discipline used everywhere else in this codebase. Omitting the parameter (the default) reproduces Series 89's exact prior behavior; verified against the Selector's full 16-test suite, unchanged.

The Selector still never scores by expression compatibility -- it only filters the candidate set beforehand; scoring remains the same categorical market-state match it always was.

## 6. Deliverable 6 — Real 41-day corpus replay

Both the ORIGINAL (unfiltered, Series 89 behavior) and the NEW (expression-filtered) selection were run side by side on the same real 41 days, to measure exactly what the filter changed:

- **Thesis -> compatible-family distribution**: e.g. `RANGE_PERSISTENCE` (14 real days) always maps to `{BUTTERFLY, IRON_CONDOR, IRON_FLY, NEUTRAL_PREMIUM_SELLING, VOLATILITY_COMPRESSION}`; `VOLATILITY_EXPANSION` (6 days) to `{CALENDAR, LONG_DIRECTIONAL, NEUTRAL_PREMIUM_BUYING, VOLATILITY_EXPANSION}` -- both match the user's own illustrative examples exactly.
- **Selections CHANGED by the filter: 14/41 (34%)** -- a substantial, real effect. Every change is a baseline selection (`COVERED`, `VOLATILITY_COMPRESSION`, `SHORT_DIRECTIONAL`, `RATIO`, `NEUTRAL_PREMIUM_SELLING`) that the thesis's own declared expression rejected -- e.g. `2026-06-08: baseline=SHORT_DIRECTIONAL -> post-expression=None (thesis=TREND_CONTINUATION)`: a naked directional premium sale (short volatility, negative convexity, undefined risk) is exactly the kind of expression a `TREND_CONTINUATION` thesis (which wants positive convexity, defined risk) should reject.
- **No-selection frequency: baseline 6/41 -> post-filter 19/41.** This is the single largest, most honest finding of this series: **the expression filter is substantially more restrictive than market-state scoring alone.** In 13 of the 14 changed days, no compatible family survived at all (only 1 day, `2026-05-29`, found an alternative compatible family -- `LONG_DIRECTIONAL` -- instead of losing the selection entirely).
- **Unchanged selections: 27/41 (66%)** -- the majority of days, the market-state-scored winner already happened to satisfy its own thesis's expression.
- Replayed twice: **expression `assessment_id`s and post-filter selected families were byte-identical** across both runs.

No tuning was performed against these numbers -- `FAMILY_CHARACTERISTICS` and `THESIS_EXPRESSION_RULES` were fixed before this replay ran.

## 7. Deliverable 7 — Behaviour Validation

Both required behaviors proven directly against real data:
- **One thesis, multiple acceptable expressions**: `VOLATILITY_EXPANSION` maps to 4 real families (`CALENDAR`, `LONG_DIRECTIONAL`, `NEUTRAL_PREMIUM_BUYING`, `VOLATILITY_EXPANSION`) -- matching the user's own "Long Straddle / Long Strangle / Calendar" example. `RANGE_PERSISTENCE` maps to 5 (including `NEUTRAL_PREMIUM_SELLING`, the short-strangle analog, exactly as the user specified it should be included).
- **Incompatible expressions correctly excluded**: for `TREND_CONTINUATION`, `NEUTRAL_PREMIUM_SELLING` and `SHORT_DIRECTIONAL` are both explicitly listed as incompatible, with the exact contradicting characteristic cited (`NEGATIVE_CONVEXITY`/`UNDEFINED_RISK`) -- matching the user's own success-criterion example verbatim in spirit (their Short Strangle / negative convexity rejection).

## 8. Deliverable 8 — Integration

Demonstrated end-to-end on real data: `Observation -> Events -> Episodes -> MSI -> Trade Thesis -> Strategy Expression -> Strategy Selection`. Trade Construction (Series 90) is completely unaffected -- it still consumes only `mss.selected_strategy_family`, exactly as built; no changes were made to that package in this series.

## 9. Interaction with Trade Thesis

Strictly one-directional: Strategy Expression reads `TradeThesisAssessment` and produces nothing that feeds back into Trade Thesis. Series 92's package was not touched in this series (confirmed by an AST test forbidding this package from importing Trade Thesis's *engine* logic -- only its real output *type* is consumed, per the downstream-consumption exception).

## 10. Interaction with Strategy Selector

One new optional parameter only (Section 5) -- MSS's own market-state derivation, scoring, and tie-break logic are all completely unchanged. The Selector still never scores by expression compatibility; Strategy Expression only shrinks the candidate pool before scoring runs.

## 11. Future Position Construction relationship

Trade Construction (Series 90) already reads `mss.selected_strategy_family` and nothing else from this arc -- no change needed there. A future refinement could have Trade Construction also read the winning `StrategyExpressionAssessment.desired_convexity`/`desired_time_decay` to bias which strike/wing-width sub-choice it makes WITHIN a family (e.g. a wider wing when `desired_convexity=POSITIVE_CONVEXITY` demands more optionality) -- not built here, out of this series's scope, but a clean, additive next step consistent with this whole arc's layering discipline.

## 12. Known limitations

- `FAMILY_CHARACTERISTICS` is a declarative, standard-options-theory classification -- correct in the textbook sense, but never empirically validated against real P&L (deliberately; this codebase does not optimize against historical returns).
- The user's own "Bull Call Spread"/"Debit Spread" examples have no corresponding SSF family (Section 3) -- `LONG_DIRECTIONAL` (a single long option) is the closest real analog available today.
- The expression filter turned out to be quite restrictive in this real corpus (19/41 no-selection days, up from 6/41) -- this is reported as a genuine finding, not softened, and is the direct basis for Deliverable 10's recommendation below.

## 13. Deliverable 10 — Recommendation

Evidence-based, from the real replay measurements above:

1. **The no-selection rate more than tripled (6/41 -> 19/41) purely from the expression filter** -- the single largest measured effect of this entire series. Before adding any further layers, this magnitude of behavior change deserves its own dedicated investigation: is the filter correctly conservative (rejecting genuinely thesis-contradicting expressions), or is `FAMILY_CHARACTERISTICS`/`THESIS_EXPRESSION_RULES` too strict in places (e.g. should `RANGE_PERSISTENCE` also accept `COVERED`, which lost its slot on 2 real days)?
2. **13 of 14 changed days found NO alternative family at all**, rather than a different compatible one -- suggesting SSF's per-day SUITABLE set and the expression-compatible set often barely overlap, a structural gap worth measuring precisely before building further downstream.

**Recommended next step: Series 94 — Expression Coverage Investigation** (not a new build -- an investigation, mirroring Series 84's own precedent): measure, across an expanded real corpus, exactly which SSF-suitable families are being lost to expression mismatch and why, before deciding whether `FAMILY_CHARACTERISTICS` needs a second, independent domain-expert review or whether the 19/41 no-selection rate is the correct, conservative behavior this layer was designed to produce.
