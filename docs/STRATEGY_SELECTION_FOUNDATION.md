# Strategy Selection Foundation (SSF v1)
## BUJJI Engineering Series 87

**Status:** Real, implemented, tested. Answers only "which strategy families are structurally suitable for current market conditions, and why" — never which one to trade. No scoring, ranking, or choice exists anywhere in this module.

---

## 1. Philosophy — suitability vs. ranking

Every prior MSI series (78-86) answered a descriptive or reconciliation question. Series 87 is the first to touch strategy at all, and it deliberately stops one full step short of choosing anything. **Suitability is a gate; ranking is a decision.** A future Strategy Selector will consume this module's output and decide among the SUITABLE set — but that selection logic (necessarily involving some notion of "which is best," however it's built) does not exist here, and this module's own data model has no field capable of expressing it (no `score`, no `rank`, structurally verified by test).

This mirrors the same discipline Series 82 (Strategy Eligibility) established at the coarser family-group level — SSF is the per-strategy-family refinement of that same idea, now with a full 13-family taxonomy and explicit per-family evidence requirements.

## 2. Ownership

- **What SSF owns**: for each of 13 strategy families, an independent, evidence-backed answer to "is this family's objective and required market conditions currently met — or is the evidence to even ask the question missing?"
- **What SSF does not own**: choosing among SUITABLE families (future Strategy Selector), strike/expiry selection, execution, or any notion of expected return/probability of profit.
- **What SSF consumes**: real `MarketDirectionAssessment` (85), `MarketStructureAssessment` (79), and `ConsensusAssessment` (81) directly — a deliberate downstream-consumption exception (SSF sits strictly downstream of all three), mirroring Series 82/85's own established precedent for this kind of dependency.

## 3. The 13 strategy families and their evidence requirements

| Family | Required evidence | Readiness |
|---|---|---|
| LONG_DIRECTIONAL | Direction, Consensus | **READY_TODAY** |
| SHORT_DIRECTIONAL | Direction, Consensus | **READY_TODAY** |
| COVERED | Direction | **READY_TODAY** |
| SYNTHETIC | Direction, Liquidity | REQUIRES_LIQUIDITY |
| NEUTRAL_PREMIUM_SELLING | Direction, Volatility | READY_AFTER_VOLATILITY_BRIDGE |
| NEUTRAL_PREMIUM_BUYING | Volatility | READY_AFTER_VOLATILITY_BRIDGE |
| VOLATILITY_EXPANSION | Volatility | READY_AFTER_VOLATILITY_BRIDGE |
| VOLATILITY_COMPRESSION | Volatility | READY_AFTER_VOLATILITY_BRIDGE |
| RATIO | Direction, Volatility | READY_AFTER_VOLATILITY_BRIDGE |
| BUTTERFLY | Market Structure, Volatility | READY_AFTER_VOLATILITY_BRIDGE |
| IRON_CONDOR | Direction, Volatility, Liquidity | REQUIRES_DATA_NOT_YET_AVAILABLE |
| IRON_FLY | Direction, Volatility, Liquidity | REQUIRES_DATA_NOT_YET_AVAILABLE |
| CALENDAR | Volatility (term structure, a richer ask than plain IV level) | REQUIRES_DATA_NOT_YET_AVAILABLE |

Every family also declares an `objective`, `required_market_conditions` (as required/forbidden direction leans and a minimum consensus rank), `required_confidence`, and `consumes` list — see `bujji/msi_strategy_selection_foundation/taxonomy.py::STRATEGY_DEFINITIONS`, pure declarative data, no logic.

## 4. Explainability (Deliverable 4)

Every `StrategySuitabilityAssessment` carries a genuinely computed `Explanation`: `why_suitable`/`why_unsuitable` (populated based on which real check passed/failed), `supporting_evidence`/`rejecting_evidence` (real upstream assessment ids), and `missing_evidence` (domain names, populated whenever a required domain is genuinely unavailable — never silently omitted). Verified by test that every assessment's explanation is non-empty in the branch that applies to its own suitability value.

## 5. Independence (Deliverable 5)

`assess_all_families` is a pure map over `ALL_STRATEGY_FAMILIES` — no comparison, no sort, no "winner." Verified structurally: `StrategySuitabilityAssessment` has no score/rank field at all (test asserts `hasattr(a, "score")` is False), and a real coherence check confirms `LONG_DIRECTIONAL` and `SHORT_DIRECTIONAL` (mutually-exclusive-by-construction, via required/forbidden direction leans) were never both `SUITABLE` on the same real day across the full 105-day corpus (0/105).

## 6. Historical qualification (Deliverable 6) — real 105-day corpus, real FYERS intraday data

**Determinism confirmed**: full corpus replayed twice, byte-identical.

| Family | Distribution across 105 real days |
|---|---|
| LONG_DIRECTIONAL | SUITABLE 41, UNSUITABLE 64 |
| SHORT_DIRECTIONAL | SUITABLE 33, UNSUITABLE 72 |
| COVERED | SUITABLE 62, UNSUITABLE 43 |
| SYNTHETIC | INSUFFICIENT_EVIDENCE 105/105 |
| NEUTRAL_PREMIUM_SELLING, NEUTRAL_PREMIUM_BUYING, VOLATILITY_EXPANSION, VOLATILITY_COMPRESSION, RATIO, BUTTERFLY | INSUFFICIENT_EVIDENCE 105/105 each |
| IRON_CONDOR, IRON_FLY, CALENDAR | INSUFFICIENT_EVIDENCE 105/105 each |

**10 of 13 families are honestly `INSUFFICIENT_EVIDENCE` on every single real day** — not because the market never met their conditions, but because the evidence to even evaluate them (Volatility, Liquidity, or both) does not exist anywhere in this codebase yet. This is the single clearest, most concrete measurement this sprint produces: the strategy taxonomy is broad, but BUJJI's actual evidence base today only supports assessing 3 of 13 families at all.

## 7. Replay/live equivalence

Same single-snapshot framing precedent as Series 82/83/85/86 (`(mdi, mssi, consensus)` in, tuple-of-assessments out) — batch and streaming entrypoints delegate to the identical `engine.assess_all_families` function, proven byte-identical by test, and separately proven byte-identical across a full independent double-run of the entire 105-day corpus.

## 8. Future selector architecture

A real Strategy Selector, when built, should: (1) call `assess_all_strategy_suitability`, (2) filter to `suitability == SUITABLE`, (3) apply whatever ranking/scoring logic it introduces — entirely new code, entirely out of SSF's scope — over that already-filtered, already-explained set. Because SSF has already done the hard, evidence-gating work, the selector's own logic can be small and fully testable in isolation, exactly as this sprint's own stated rationale predicted.

## 9. Recommendation (Deliverable 10) — evidence-based, not opinion

**The Volatility Bridge is the single highest-leverage next step.** It is the only investment that unlocks more than one family — 6 of the 10 currently-blocked families (`NEUTRAL_PREMIUM_SELLING`, `NEUTRAL_PREMIUM_BUYING`, `VOLATILITY_EXPANSION`, `VOLATILITY_COMPRESSION`, `RATIO`, `BUTTERFLY`) become assessable the moment real IV/volatility-regime data is bridged in (the real Black-Scholes/IV engine already exists in `bujji/intelligence/volatility_brain.py`/`greeks_brain.py`, per this project's own standing roadmap decision — this is a bridging task, not new modeling work). By contrast, `REQUIRES_LIQUIDITY` unlocks only `SYNTHETIC` (1 family), and `REQUIRES_DATA_NOT_YET_AVAILABLE` (Liquidity + Volatility jointly, or term structure specifically) unlocks `IRON_CONDOR`/`IRON_FLY`/`CALENDAR` (3 families) but only after Volatility is already bridged anyway.

**One piece of context this recommendation does not ignore**: the 3 currently-ready families (`LONG_DIRECTIONAL`, `SHORT_DIRECTIONAL`, `COVERED`) all depend on `MarketDirectionAssessment`'s `overall_direction` — the same signal whose predictive value was tested four independent ways in the immediately-preceding investigation (`docs/SERIES_86_PREDICTIVE_VALUE_NEGATIVE_RESULT.md`) and not found, with a real, still-open, asymmetric hypothesis (bullish reads specifically may be miscalibrated) left undecided. This document does not treat that as a blocker — building continues per direct instruction — but a future Strategy Selector consuming `LONG_DIRECTIONAL`/`SHORT_DIRECTIONAL` suitability should be aware that the directional gate itself has not yet been validated as informative, only as internally coherent and structurally well-defined.

---

## 10. Update — Volatility Structure wired in (Series 88 follow-up)

`bujji.msi_strategy_selection_foundation` now consumes real
`VolatilityStructureAssessment` (Series 88's Bridge) via an optional,
backward-compatible `vsb` parameter on `assess_strategy_suitability`/
`assess_all_families`. See `docs/VOLATILITY_STRUCTURE_BRIDGE.md`
Section 11 for the full writeup (taxonomy changes, per-family
volatility rules, and honest real-corpus re-qualification numbers).

**6 of 13 families are now genuinely assessable** (up from 3/13):
`LONG_DIRECTIONAL`, `SHORT_DIRECTIONAL`, `COVERED` (unchanged),
`NEUTRAL_PREMIUM_SELLING`, `VOLATILITY_COMPRESSION`, `RATIO`,
`BUTTERFLY` (new — real SUITABLE/UNSUITABLE reads). `NEUTRAL_PREMIUM_BUYING`
and `VOLATILITY_EXPANSION` are technically now assessable too but read
`UNSUITABLE` on every one of the 41 real corpus days — traced to
Section 8 of the Bridge doc's own disclosed calibration-mismatch
limitation, not a new defect. `IRON_CONDOR`/`IRON_FLY` (Liquidity),
`CALENDAR` (term structure), and `SYNTHETIC` (Liquidity) remain
`INSUFFICIENT_EVIDENCE`, now gated on exactly one remaining domain
each rather than a compound gap.

This changes Deliverable 10's recommendation calculus but not its
conclusion: Strategy Selection is still not yet supported by validated
evidence — more families are assessable, but the underlying signals'
predictive value (Direction) and calibration (Volatility regime proxy)
remain open questions this follow-up did not address.
