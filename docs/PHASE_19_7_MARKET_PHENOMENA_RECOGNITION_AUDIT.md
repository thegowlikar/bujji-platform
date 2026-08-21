# Phase 19.7 — Market Phenomena Recognition Audit

**Audit only, performed before any implementation, per this phase's own explicit instruction to search
`market_perception/`, `phenomena_bus`, `ontology`, and `trading_brain` first.**

## Finding: a real, mature, already-shipped phenomena classifier exists — `bujji/msi_market_phenomena/`

Exactly the situation the user predicted ("high probability some pieces already exist as orphaned
concepts, exactly like `mil_next`"). Direct inspection:

- `taxonomy.py` — **Market Phenomena Classifier (MPC), Series 103.** Ten classifiable phenomena
  (`TREND_EXPANSION`, `TREND_FAILURE`, `RANGE_COMPRESSION`, `VOLATILITY_EXPANSION`,
  `VOLATILITY_COMPRESSION`, `MOMENTUM_PERSISTENCE`, `MOMENTUM_EXHAUSTION`, `MEAN_REVERSION`,
  `GAP_CONTINUATION`, `GAP_FAILURE`), plus an **honestly disclosed unclassifiable list**
  (`FALSE_BREAKOUT`, `LIQUIDITY_VACUUM`, `PRICE_ACCEPTANCE`, `PRICE_REJECTION`, `PREMIUM_EXPANSION`,
  `PREMIUM_COLLAPSE`, `STRIKE_ROTATION`, `DELTA_MIGRATION`, `THETA_DOMINANCE`, `GAMMA_ACCELERATION`,
  `TREND_REVERSAL`) with a stated reason ("requires real options-chain Greeks time-series or tick-level
  order-book data... not built rather than fabricated") — the same "never fabricate, disclose the gap"
  discipline this entire project has followed since Phase 17E.
- `models.py` — `MarketSnapshot` (a plain translation type, NOT the Production type itself — mirrors
  `msi_strategy_selector`'s own sibling-isolation convention), `Phenomenon` (`phenomenon_id`,
  `phenomenon_type`, `evidence_references`, `confidence`, `duration_seconds`, `affected_instruments`),
  `MarketPhenomenaReport` (one report per day, discloses `not_classifiable` every time, never silently
  omitted).
- `engine.py` — one `_rule_<phenomenon>()` function per classifiable phenomenon, each returning
  `(bool, evidence_tuple)`; `_PHENOMENON_RULES` dict asserted (`assert set(_PHENOMENON_RULES) ==
  set(taxonomy.ALL_CLASSIFIABLE_PHENOMENA_V1)`) to stay in sync with the taxonomy — a real,
  enforced consistency guard.
- Real importers exist: `bujji/msi_opportunity_assessment/models.py` imports from it, and
  `tests/test_msi_market_phenomena.py` + `tests/test_mpc_isolation.py` exercise it — **not fully
  orphaned** like `mil_next` was, but operating on a structurally different input pipeline.

### Why the code is not directly reusable

MPC's `MarketSnapshot` translation type is built from **real PSI/MSSI/VSB/MDI assessment fields**
(`msi_price_structure`, `msi_market_structure`, `msi_volatility_structure`, `msi_market_direction` — the
"MSI" Production Intelligence family) — a different, parallel pipeline from the one Phase 19.0–19.6 built
(`bujji.intelligence.*_brain.py` → `MarketIntelligenceSnapshot` → `DecisionContext` →
`DecisionIntelligenceSnapshot`). `engine.py` never imports the MSI assessment types directly either —
by design, translation happens once, upstream, in `translate.py`. Importing MPC's `engine.py`/`models.py`
directly into this phase would either (a) silently require constructing a fake MSI `MarketSnapshot` from
non-MSI data (a translation this project's own discipline would call fabrication), or (b) require wiring
the entire separate MSI pipeline into a phase whose explicit input is `DecisionIntelligenceSnapshot`.
Neither is honest reuse.

### What IS reused — vocabulary, per the user's own explicit instruction

Two of MPC's ten classifiable phenomenon type strings name the exact same real-world concept this phase
also needs: `"VOLATILITY_EXPANSION"` and `"VOLATILITY_COMPRESSION"`. **Phase 19.7 uses these identical
string values**, not a differently-spelled alternative (`VOL_EXPANSION`, `VOLATILITY_INCREASE`, etc.) —
same real-world phenomenon, same name, project-wide, even though the detecting engine differs. This is a
deliberate, disclosed vocabulary convergence, not a rename and not a collision: two independent detectors
(MPC over MSI fields, this phase over `bujji.intelligence` brain readings) may, on a given day, both or
separately report `VOLATILITY_EXPANSION` — that is two independent lines of real evidence for the same
real phenomenon, which is exactly what having two genuinely different input pipelines should produce, not
a bug to engineer away.

Also reused as design precedent (not code):
- The `phenomenon_id` / `Phenomenon` / evidence-tuple shape.
- The `NONE`/`LOW`/`MODERATE`/`HIGH` confidence scale (same scale used project-wide, e.g.
  `msi_strategy_selection_foundation.taxonomy.CONFIDENCE_*`).
- The "honestly disclose what cannot be classified, with a stated reason, every time" discipline —
  `MarketPhenomenaAssessment` (this phase) follows the same pattern for any of the 5 target phenomena it
  cannot support on a given input.
- The `_PHENOMENON_RULES` dict + `assert set(...) == set(taxonomy.ALL...)` consistency-guard pattern,
  reused verbatim as a design technique in this phase's own `detectors.py`.

## `market_perception/` — different layer, no phenomenon-shaped concept found

`bujji/market_perception/` (option chain adapters, quote/greeks adapters, market data adapter) is a data
**acquisition** layer, not an interpretation layer — confirmed by directory listing and prior phases'
own repeated characterization of this package (Phase 19.0.1, 19.1). No phenomenon/event-classification
concept exists there. Nothing to reuse or conflict with.

## `bujji/trading_brain/ontology/` — a different ontology, no phenomenon concept found

`trading_brain/ontology/` (`models.py`, `taxonomy.py`, `query.py`, `runner.py`, `serialization.py`,
`config.py`) was greped directly for "phenomen" — zero matches. This is the trading brain's own decision/
execution ontology (order construction, position sizing, capital allocation concepts per its sibling
directories in `trading_brain/`), a different concern entirely. Nothing to reuse or conflict with.

## `phenomena_bus` — does not exist

No file or directory anywhere under `bujji/` matches `*phenomena_bus*`. Not found; nothing to audit.

## Conclusion

One real precedent found and partially reused (vocabulary + design discipline from `msi_market_phenomena`,
never its code — different input pipeline, never imported). No conflict. `bujji/market_phenomena/` (this
phase) is a new, genuinely necessary package: the same real-world phenomena, detected from a different,
already-validated evidence source (`bujji.intelligence` → `MarketIntelligenceSnapshot` →
`DecisionIntelligenceSnapshot`, Phase 19.3/19.6), not a duplicate intelligence tree.
