# Volatility Structure Bridge (VSB v1)
## BUJJI Engineering Series 88

**Status:** Real, implemented, tested, replayed against the real 41-day
option-chain corpus. This is a translation layer over existing,
validated legacy math — no Black-Scholes, IV solver, or Greeks formula
was reimplemented anywhere in this package.

---

## 1. Deliverable 1 — Capability Audit (real, file:line-cited)

| Capability | Status | Evidence |
|---|---|---|
| IV (per-leg solve) | **Already reusable** | `bujji/intelligence/volatility_brain.py::solve_implied_volatility` — pure Newton-Raphson + bisection fallback, real |
| IV Rank | **Missing** | Explicitly disclosed absent in the legacy module's own docstring — needs weeks/months of historical IV not available anywhere |
| IV Percentile | **Missing** | Same as IV Rank |
| Realized Volatility | **Already reusable** | `VolatilityBrain._annualized_realized_vol` (a `@staticmethod`) — pure, log-return stdev, real |
| Historical Volatility | **Already reusable** | Same underlying computation as Realized Volatility |
| Expected Move | **Already reusable** (required a 1-line, additive extraction) | The formula existed only inline inside `VolatilityBrain.analyze()`; extracted as `compute_expected_move(spot, iv, t_years)`, zero behavior change to `analyze()` itself — verified: legacy suite 17/17 before and after |
| Skew | **Missing** | No CE/PE IV-skew computation exists anywhere — only per-leg raw IV values |
| Smile | **Missing** | No cross-strike IV curve computation exists anywhere |
| Term Structure | **Missing** | No multi-expiry IV comparison exists anywhere |
| Vega | **Already reusable** | `bujji/intelligence/greeks_brain.py::_bs_vega` (reused from `volatility_brain.py`) — pure |
| Gamma | **Already reusable** | `greeks_brain.py::_bs_gamma` — pure |
| Delta, Theta | **Already reusable** (not requested by name but load-bearing for future Greeks integration) | `_bs_delta`, `_bs_theta` — pure |
| Vanna | **Missing** | Not implemented anywhere in this codebase |
| Charm | **Missing** | Not implemented anywhere in this codebase |
| Volatility Regime | **Adapter required** | `regime_brain.py::RegimeBrain._compression_ratio`/`_log_returns`/`_stdev` — real, pure, but a **price-statistics proxy**, not a genuine IV-based signal |
| Volatility Expansion | **Adapter required** | Same reused logic, `EXPANSION_RATIO_THRESHOLD` branch |
| Volatility Compression | **Adapter required** | Same reused logic, `COMPRESSION_RATIO_THRESHOLD` branch |

**Critical finding shaping the whole architecture**: `VolatilityBrain.analyze()`, `GreeksBrain.analyze()`, and `RegimeBrain.analyze()` all call `now_ist()` (wall-clock) internally for their `as_of` field. Their wrapper classes are therefore **unsuitable for direct reuse** in this deterministic, replay-safe arc — but every one of their inner math functions is pure and genuinely reusable. This bridge imports and calls those pure functions directly, and never calls the impure `.analyze()` wrappers anywhere. Verified structurally by an AST-based test asserting no call to `now_ist()`/`datetime.now()` exists anywhere in this package.

## 2. Deliverable 2 — Architecture Decisions and Migration Plan

Per the explicit instruction that no reused component may become a "permanent temporary adapter":

| Component | Decision | Status | Reasoning |
|---|---|---|---|
| `solve_implied_volatility`, `_annualized_realized_vol`, `compute_expected_move`, `VolatilityBrain._classify_richness` | Reuse directly | **PERMANENT** | Correct, validated Black-Scholes/statistics math with no reason to move. This bridge imports it; it never forks it. |
| `_bs_delta`/`_bs_gamma`/`_bs_theta`/`_bs_vega` | Reuse directly | **PERMANENT** | Same reasoning — textbook closed-form Greeks, already sanity-checked against known ATM values in the legacy suite. |
| `RegimeBrain._compression_ratio`/`_log_returns`/`_stdev` and its threshold constants | Adapt (wrap, translate output into VSB's own taxonomy) | **TEMPORARY BRIDGE, explicitly not permanent** | This is a price-statistics proxy standing in for a genuine IV-based volatility-structure signal. It should be **replaced** once real multi-strike/multi-expiry IV time-series data exists to compute expansion/compression from IV itself, not from realized price behavior. Tracked as an open migration item below (Section 6). |

## 3. What was built

`bujji/msi_volatility_structure/` — the same 8-file structure as every prior MSI package. `VolatilityStructureAssessment`: `volatility_regime`, `iv_state` (real IV/RV richness classification), `expected_move_state` (a new, disclosed bucketing of the real expected-move percentage — classification, not new math), `skew_state`/`term_structure_state` (always `UNKNOWN` — Deliverable 1's genuine gaps), `expansion_state`/`compression_state` (independent dimensions, mirroring Series 78's own co-existence pattern), `confidence`, plus the raw evidence values (`iv_average`, `realized_vol`, `expected_move_pct`) alongside their classified states.

## 4. Deliverable 5 — Direction Integration (real, demonstrated, existing lenses untouched)

`bujji.msi_market_direction.engine.reconcile_lenses` was **not modified** — it already accepts an arbitrary-length tuple of `LensOpinion`s by design (Series 85's own extensibility mandate). A real demonstration fed all three available lenses (Price Structure, Market Structure, Volatility Structure — Volatility always contributing `NEUTRAL`/`UNKNOWN`, since it has no directional opinion by design) through this exact, unmodified function across the real 41-day corpus:

| | Distribution |
|---|---|
| 2-lens (Price + Market Structure only) | `NEUTRAL:7, BULLISH:7, STRONG_BULLISH:6, WEAK_BULLISH:5, STRONG_BEARISH:4, BEARISH:4, MIXED:3, WEAK_BEARISH:3, UNKNOWN:2` |
| **4-lens (+ Volatility)** | `NEUTRAL:16, BULLISH:7, WEAK_BULLISH:6, WEAK_BEARISH:5, BEARISH:4, MIXED:3` |

Adding a third, always-neutral-voting lens visibly dilutes band strength (no `STRONG_*` outcomes survive) and eliminates `UNKNOWN` entirely (Volatility's own `NEUTRAL` vote gives the reconciliation something to average against even when price-based lenses are uncertain) — a real, disclosed mechanical consequence of the averaging rule, not a claimed improvement in accuracy.

## 5. Deliverable 6 — Consensus Integration (unmodified, replayed twice)

`bujji.msi_consensus.engine.compute_consensus` was **not modified**. Volatility Structure participates as a `DomainAssessmentView` with `lean=LEAN_NEUTRAL` (never `LEAN_AMBIGUOUS` — "no directional opinion" and "disagreement" are different facts, and conflating them would misrepresent Volatility's real contribution). Full 41-day corpus replayed twice: **byte-identical**. Consensus level distribution over the two-domain (Direction + Volatility) set: `MODERATE_CONSENSUS: 29, UNANIMOUS_CONSENSUS: 12` — no `NO_CONSENSUS` days, `LIMITED` evidence sufficiency on all 41 (a real, disclosed consequence of only 2 domains participating).

## 6. Deliverable 7 — Strategy Suitability Re-qualification (honest, not oversold)

**Strategy Suitability distribution is unchanged from Series 87.** This is not an oversight — `bujji.msi_strategy_selection_foundation` was **not modified** in this sprint (out of scope: this sprint builds the Bridge, not the consumer), so its `STRATEGY_DEFINITIONS` still gate on `(Direction, Consensus, Market Structure)` only and have no code path that reads a `VolatilityStructureAssessment` at all. The 10 families requiring Volatility evidence remain `INSUFFICIENT_EVIDENCE` on every day, exactly as before — **the Bridge now produces real Volatility evidence, but nothing downstream consumes it yet.** Wiring `msi_strategy_selection_foundation`'s own gating logic to read real `VolatilityStructureAssessment` fields is a distinct, disclosed next step (see Section 8), not something this sprint silently skipped.

## 7. Deliverable 8 — Observatory

Comparing strategy suitability before and after this sprint: **zero changed records** — exactly consistent with Section 6's finding (SSF doesn't consume Volatility evidence yet, so nothing could change). This is the Observatory functioning correctly: confirming no silent, unintended change occurred, not a failure to produce interesting output.

## 8. A real limitation found during replay — CORRECTED

**Original diagnosis (retracted, was wrong):** this section originally attributed `volatility_regime` reading `UNKNOWN` on 37 of 41 real days to a threshold-scale mismatch between `RegimeBrain`'s live-5-min-session-tuned constants and this bridge's actual 15-minute, multi-day data shape.

**Actual root cause, found by direct measurement (not assumption) in a follow-up investigation:** 100% (31 of 31) of the `UNKNOWN` reads examined had a REAL, successfully-computed `compression_ratio` — zero were from genuinely insufficient candles. The real defect was in `derive_regime_expansion_compression`'s own classification logic: a real `compression_ratio` landing in the ambiguous middle (not extreme enough to call `COMPRESSED` or `TRANSITIONING`, and `realized_vol` below `VOL_HIGH_THRESHOLD`) was being mapped to `REGIME_UNKNOWN` — silently conflating "no evidence" with "real evidence of an unremarkable reading." `RegimeBrain`'s own original design never makes this mistake (it maps the equivalent case to `TRANSITIONING` with disclosed low confidence); this bridge's adaptation introduced the conflation.

**Fix applied:** a new taxonomy value, `REGIME_STABLE`, was added — genuinely distinct from `REGIME_UNKNOWN` (verified by a dedicated regression test asserting they are different values and that insufficient-data cases still correctly resolve to `UNKNOWN` while ambiguous-middle-with-real-data cases now resolve to `STABLE`). Verified: 18/18 own suite (17 + 1 new), 2447/2447 full suite, zero regressions. Real corpus re-measurement: `volatility_regime` distribution is now `STABLE: 37, TRANSITIONING: 4` (was `UNKNOWN: 37, TRANSITIONING: 4`), determinism reconfirmed byte-identical.

**Practical impact, measured honestly:** Strategy Suitability outcomes for the volatility-gated SSF families are essentially unchanged by this fix, because those per-family rules already read `expansion_state`/`compression_state` (`CONFIRMED`/`NOT_DETECTED`), which were never affected by this bug — only the separate, composite `volatility_regime` label was mislabeled. The real, meaningful effect is on **confidence calibration**: `compute_confidence` treats `REGIME_UNKNOWN` as "no evidence" and `REGIME_STABLE` as "real evidence," so 37 of 41 days that were previously under-reporting confidence (due to a mislabeled lack of evidence that was never actually missing) now correctly reflect that real volatility evidence exists, even when that evidence describes an unremarkable, non-extreme condition.

This section is left in place (rather than deleted) specifically to preserve the honest record that the original diagnosis was wrong and was corrected upon closer, measurement-based investigation — consistent with this project's standing discipline of disclosing mistakes rather than quietly overwriting them.

## 9. Deliverable 9 — Engineering Validation

**17/17 new tests, 2442/2442 full suite** (2425 baseline, zero regressions). Legacy `tests/test_volatility_brain.py`: 17/17 unaffected by the additive `compute_expected_move` extraction. Determinism, replay parity, no-randomness, and no-wall-clock-dependency all verified by dedicated tests (the wall-clock check is AST-based, checking actual function calls, not a naive text search — an earlier draft of that test false-positived on this very docstring's own prose explaining why `now_ist()` is avoided).

## 10. Deliverable 10 — Final Recommendation: **NOT READY**

Evidence-based, not opinion:

1. **Only 3 of 13 strategy families are assessable at all** — unchanged from Series 87, because the Bridge exists but nothing downstream reads it yet. This alone means Strategy Selection would have almost nothing to select among beyond the 3 already-assessable families.
2. **The one new signal this sprint adds (`volatility_regime`) shows real calibration mismatch** in this exact replay context (37/41 `UNKNOWN`) — using it for gating today would mean gating on noise, not signal.
3. **The underlying Direction signal's predictive value remains an open, unresolved question** (`docs/SERIES_86_PREDICTIVE_VALUE_NEGATIVE_RESULT.md`) — the 3 currently-assessable families all depend on it.

**Concrete, ordered next steps, not just "more work needed"**:
1. Wire `msi_strategy_selection_foundation`'s own `STRATEGY_DEFINITIONS`/engine to actually consume `VolatilityStructureAssessment` (a mechanical, scoped follow-up — the Bridge's output already exists in the right shape).
2. Address Section 8's calibration mismatch before trusting `volatility_regime` for any gating — either derive fresh, disclosed thresholds validated against this replay context, or replace the temporary price-based proxy with a genuine IV-based expansion/compression signal once term-structure data exists.
3. Treat the Series 86 predictive-value question as still open; Strategy Selection built on top of an unvalidated directional gate inherits that uncertainty.

None of this blocks continued building, per standing instruction — but "ready for Strategy Selection" is not yet supported by what this sprint measured.

---

## 11. Follow-up — Wiring SSF to consume Volatility Structure evidence

Per direct instruction, `bujji/msi_strategy_selection_foundation` (Series
87) was updated to actually consume `VolatilityStructureAssessment`
(this sprint's Bridge) in its per-family gating — closing the gap
Section 6 disclosed ("the Bridge now produces real Volatility evidence,
but nothing downstream consumes it yet").

### What changed

- `taxonomy.py`: `DOMAIN_VOLATILITY` moved from `UNAVAILABLE_DOMAINS` to
  `AVAILABLE_DOMAINS`. A NEW, deliberately separate domain,
  `DOMAIN_VOLATILITY_TERM_STRUCTURE`, was introduced and kept in
  `UNAVAILABLE_DOMAINS` — `CALENDAR`'s real requirement is term
  structure specifically (still always `UNKNOWN` on every real
  `VolatilityStructureAssessment`, per Section 1's own audit), and
  conflating "plain IV/regime available" with "term structure
  available" would have been a real, disclosed mistake. Readiness
  reclassified: 6 families (`NEUTRAL_PREMIUM_SELLING`,
  `NEUTRAL_PREMIUM_BUYING`, `VOLATILITY_EXPANSION`,
  `VOLATILITY_COMPRESSION`, `RATIO`, `BUTTERFLY`) moved to
  `READY_TODAY`; `IRON_CONDOR`/`IRON_FLY` moved from
  `REQUIRES_DATA_NOT_YET_AVAILABLE` to `REQUIRES_LIQUIDITY` (only
  Liquidity remains missing for them); `CALENDAR` stays
  `REQUIRES_DATA_NOT_YET_AVAILABLE`. Schema version bumped 1.0.0 ->
  1.1.0, `RECOGNIZED_SCHEMA_VERSIONS` keeps both.
- `engine.py`: `assess_strategy_suitability`/`assess_all_families` gained
  an optional, backward-compatible `vsb` parameter (every pre-existing
  caller passing only `(mdi, mssi, consensus)` still works identically
  — verified by test). A new `_VOLATILITY_RULES` dict holds one small,
  disclosed predicate per volatility-dependent family (see engine.py's
  module docstring for the full reasoning per family — e.g.
  `NEUTRAL_PREMIUM_SELLING` needs `iv_state` rich/fair AND not actively
  expanding; `VOLATILITY_EXPANSION` needs to currently be compressed,
  not already expanding, since the edge would already be spent
  otherwise).
- `runner.py`: threaded `vsb` through both entrypoints, same
  backward-compatible optionality.

### Verified

Series 87's own suite: 18/18 (14 original + 4 new, one pre-existing
test updated to reflect the intentional, disclosed taxonomy change —
`CALENDAR` now correctly reports `VOLATILITY_TERM_STRUCTURE`, not
`VOLATILITY`, as its missing domain). Full suite: **2446/2446, zero
regressions** (2442 baseline + 4 new).

### Real corpus re-qualification (same 41 real days)

| Family | Suitability distribution (now, with real vsb) |
|---|---|
| NEUTRAL_PREMIUM_SELLING | SUITABLE 5, UNSUITABLE 36 |
| NEUTRAL_PREMIUM_BUYING | UNSUITABLE 41 |
| VOLATILITY_EXPANSION | UNSUITABLE 41 |
| VOLATILITY_COMPRESSION | SUITABLE 4, UNSUITABLE 37 |
| RATIO | SUITABLE 21, UNSUITABLE 20 |
| BUTTERFLY | SUITABLE 14, UNSUITABLE 27 |
| IRON_CONDOR, IRON_FLY | INSUFFICIENT_EVIDENCE 41/41 (Liquidity only now) |
| CALENDAR | INSUFFICIENT_EVIDENCE 41/41 (term structure only now) |

**6 of 13 families now receive a genuine, evidence-backed SUITABLE/
UNSUITABLE read on every real day** — up from 3/13 before this
follow-up. Determinism reconfirmed on the full rerun.

**Honest finding, not spun**: `NEUTRAL_PREMIUM_BUYING` and
`VOLATILITY_EXPANSION` are `UNSUITABLE` on all 41 days. Investigated
directly rather than assumed: `iv_state` was `IV_CHEAP` on only 1 of 41
real days, and `volatility_regime` was almost never `REGIME_COMPRESSED`
(37/41 `UNKNOWN`) — the exact calibration-mismatch limitation Section 8
already disclosed (the reused price-based compression/expansion proxy
rarely triggers in this replay's data shape). This is a direct,
traceable consequence of an already-known limitation, not a new defect
in the wiring — and it means these two families' 0% suitability rate
in this specific sample should not be read as "the strategy is never
appropriate," only as "this proxy rarely detects the condition these
families need, in this data."

### What this does NOT change

The Observatory comparison (before vs. after this follow-up) shows a
real, simple, fully-explained first divergence for exactly the 6
reclassified families: `vsb` was `None` before, a real assessment
after — no other upstream input changed. `IRON_CONDOR`/`IRON_FLY`/
`CALENDAR`/`SYNTHETIC` show zero change, exactly as expected (their
remaining gates — Liquidity, term structure — were untouched by this
follow-up). The Series 86 predictive-value question and Section 8's
calibration-mismatch limitation both remain open, unresolved, and
unaffected by this change — wiring SSF to Volatility makes MORE
families assessable, it does not make the underlying signals more
validated.
