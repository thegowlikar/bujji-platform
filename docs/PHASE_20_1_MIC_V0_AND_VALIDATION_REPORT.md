# Phase 20.1 — MIC v0 + Mandatory MIC Validation (20.1B): Final Report

**Bujji Trading Intelligence Roadmap v1.2, Cycle 1.**

---

## 1. Architecture Summary

Two new packages, both additive, neither touching any Phase 19.19/19.20 file:

```
bujji/mic_v0/                          -- classification only (Step 2-3 of the charter)
    models.py            MarketState, ConfidenceInfo, EventContext
    volatility_classifier.py   VIX-percentile LOW/NORMAL/HIGH (new)
    risk_classifier.py         VIX-level NORMAL/ELEVATED/EXTREME (reuses event_brain thresholds)
    engine.py             compose_market_state() -- the only orchestration layer

bujji/mic_v0_validation/               -- the mandatory 20.1B validation harness
    models.py             DayClassification, RegimeGroupStats, ValidationReport
    validation.py          classify_day(), build_validation_report()

scripts/run_mic_v0_validation.py       -- read-only entrypoint, no broker, no live wiring
```

`market_timeseries/indicators.py` gained one new function, `adx()` — extending the existing indicators module rather than creating a parallel one, per Step 1's explicit instruction.

**Composition, not reinvention.** `mic_v0.engine.compose_market_state()` calls `bujji.intelligence.regime_brain.RegimeBrain.analyze()` **unmodified** for `market_regime`. It does not recompute efficiency ratio or realized volatility. The only genuinely new classification logic is (a) VIX-percentile `volatility_state` (the existing `volatility_brain` is options-IV-based and structurally cannot run without historical options data, which does not exist per Phase 20.0), (b) `risk_state`, which reuses `event_brain`'s own threshold constants and adds one new disclosed EXTREME tier, and (c) the `recommended_environment` rule, deliberately not calling `market_environment.classify_environment()` because that function depends on options-derived fields Cycle 1's data scope doesn't have.

---

## 2. Step 1 Audit Findings (what was reused, what duplicates, what was missing)

| Existing capability found | Verdict |
|---|---|
| `bujji.intelligence.regime_brain.RegimeBrain` | **Reused verbatim.** Already computes TRENDING/RANGING/VOLATILE/COMPRESSED/TRANSITIONING/UNKNOWN via Kaufman Efficiency Ratio + realized volatility. Its own docstring already called for the validation this phase performs. |
| `bujji.intelligence.event_brain` | **Threshold constants reused** (`VIX_LOW_THRESHOLD`, `VIX_ELEVATED_THRESHOLD`). Its own docstring already disclosed the exact same "no macro-event calendar exists" finding this phase's charter independently required. |
| `bujji.intelligence.volatility_brain` | **Not reused** — deliberately. It answers a different question (options IV richness) requiring historical options data Cycle 1 does not have. Using it would mean fabricating options-shaped inputs. |
| `bujji.market_environment.classify_environment()` (Phase 19.9) | **Not reused.** Already implements almost the exact TREND_FOLLOWING/MEAN_REVERSION/STAND_ASIDE decision this phase needed, but is entangled with the full `MarketStateNode`/`DecisionIntelligenceSnapshot` pipeline and several options-dependent fields. Reusing it would have required fabricating those fields. |
| `bujji.market_state_graph` (Phase 19.8) | Audited — a sequencing/linking layer over already-computed fields, not a computation layer. Not directly applicable to Cycle 1's scope. |
| ADX | **Missing — confirmed by repository-wide search.** Added to `market_timeseries/indicators.py`. |
| **Naming collision, disclosed** | "Market Intelligence Core" is already the self-description of `regime_brain`/`volatility_brain`/`event_brain`, and a *separate, external* `/opt/bujji-mic-v2/` process uses "MIC" for an unrelated system. The charter's "MIC v0" name is kept (frozen charter) but documented in `bujji/mic_v0/__init__.py` as an additive layer over the existing brains, not a competing concept. |

No parallel framework was created. No existing module was duplicated.

---

## 3. Data Sources Used

NIFTY futures (`NIFTY_FUT_CONTINUOUS`) 5-minute candles and India VIX (`NSE:INDIAVIX-INDEX`) daily closes, both from `HistoricalObservationStore` — the same real data inventoried in Phase 20.0. No options data. No external or unverified feed.

---

## 4. MIC v0 Output Example (real data, not synthetic)

Composed live against the real, verified 2026-08-14 session:

```json
{
  "as_of_time": "2026-08-14T15:30:00+05:30",
  "market_regime": "RANGE",
  "volatility_state": "LOW",
  "risk_state": "NORMAL",
  "recommended_environment": "MEAN_REVERSION",
  "confidence": {"level": "LOW", "sample_size": 0, "method": "pending Phase 20.1B validation", "evaluation_window": null},
  "event_context": {"status": "NOT_AVAILABLE"},
  "data_quality": "SUFFICIENT"
}
```

Full evidence trail (real numbers): `efficiency_ratio=0.0852` (RegimeBrain's own reason: `<= threshold 0.3` → RANGING), real VIX `11.42` at the `10.1th` trailing percentile → LOW, VIX `< 20.0` → risk NORMAL.

---

## 5. Validation Results (Phase 20.1B) — the real, substantive finding of this phase

Ran against **2,110 real trading days**, 2018-02-01 through 2026-08-13 — the complete available NIFTY futures history.

```
TREND samples:  n = 0
RANGE samples:  n = 1655   avg ADX=25.14   avg efficiency_ratio=0.119   avg reversal_freq=0.521   avg persistence=1.92
UNCLEAR samples: n = 455
```

**Zero of 2,110 real trading days were ever classified TREND at full-session granularity, under RegimeBrain's current, disclosed-as-first-pass threshold (`ER_TRENDING_THRESHOLD=0.60`).**

A follow-up direct measurement of the efficiency-ratio distribution across all 2,113 real days confirms this is not a bug in composition or aggregation — it is a genuine property of the threshold applied to full-day windows:

| Statistic | Value |
|---|---|
| Median daily efficiency ratio | 0.116 |
| Mean | 0.136 |
| p90 | 0.275 |
| p99 | 0.428 |
| Maximum (single best day, 2025-10-21) | 0.751 |
| Days with ER ≥ 0.60 | **1 out of 2,113** |
| Days with ER ≥ 0.50 | 4 out of 2,113 |
| Days with ER ≥ 0.40 | 30 out of 2,113 |

Even the single day that cleared the 0.60 threshold (2025-10-21) did not surface as a TREND-classified day in the full pipeline run — `RegimeBrain`'s classification priority checks realized volatility and intra-day compression/expansion *before* the efficiency-ratio comparison, so a high-ER day can still resolve to VOLATILE/COMPRESSED/TRANSITIONING if it also trips one of those earlier, higher-priority checks.

**Conclusion, stated exactly as the charter requires — no fabrication, no forcing a result:**

> **INCONCLUSIVE for the TREND side.** RANGE classifications show internally plausible, coherent statistics (efficiency ratio, reversal frequency, and persistence all sit in a sensible, mutually-consistent range for genuinely oscillating price action). But zero real trading days in 8+ years were ever labeled TREND at the full-session granularity this validation tested, so **no separation claim between TREND and RANGE can honestly be made** — there is no TREND group to compare against.

This is not a failure of the validation harness — it is exactly the kind of finding Phase 20.1B exists to surface honestly rather than let stand undiscovered inside an unused module. `RegimeBrain`'s own docstring already flagged its thresholds as "a documented first pass... to be revisited once real history accumulates" — this phase is the first time that revisiting has actually happened, with real, complete data.

**What this means for Cycle 1, stated plainly, not silently patched:** `ER_TRENDING_THRESHOLD=0.60` appears calibrated for a shorter or differently-scoped window than "an entire 09:15–15:30 trading session." Three honest paths forward exist, and none has been chosen unilaterally here:
1. Recalibrate the threshold downward based on this real distribution (e.g., a value nearer the observed p90/p99) — but this is a substantive change to a Phase 19.2.2 module used elsewhere, and shouldn't be made silently inside a Cycle 1 side-project.
2. Change MIC v0 to classify over a shorter, rolling intraday window rather than the full day, closer to what `RegimeBrain`'s own docstring ("call `analyze(candles)` with the session's candles so far") seems to have originally intended.
3. Accept that, at full-day granularity, genuine single-direction trend days are simply rare in NIFTY (consistent with well-known equity-index behavior), and design Cycle 1's strategy universe around that reality rather than expecting frequent TREND_FOLLOWING recommendations.

No threshold was adjusted to manufacture a passing result. This finding is reported exactly as measured.

---

## 6. Tests

90 new tests, all passing: `mic_v0` (31 — models, volatility classifier, risk classifier, engine, safety boundary), `mic_v0_validation` (17 — reversal/persistence math on known sequences, `classify_day`, report aggregation including the "no separation" and "small sample → PRELIMINARY" paths, safety boundary), plus 3 new ADX tests added to the existing `tests/test_market_timeseries.py` (not a new file, extending the existing suite per Step 1's own instruction).

---

## 7. Limitations Discovered

1. **The central limitation is §5 above** — MIC v0's TREND/RANGE split cannot yet be validated as meaningfully separated, because the underlying regime engine's current calibration essentially never fires TREND at the granularity this phase tested it at.
2. `RegimeBrain`'s internal confidence (a continuous heuristic) is completely different in kind from MIC v0's own sample-size-gated confidence — the two must never be conflated, and this report confirms (via `test_regime_brains_internal_confidence_never_surfaces_as_mic_confidence`) that they aren't.
3. `event_context` is unconditionally `NOT_AVAILABLE` — confirmed structurally, not just by convention (`test_no_forbidden_module_imports`-style AST checks plus a dedicated test asserting no macro-event term ever appears in risk-classifier evidence).
4. ADX and RegimeBrain's efficiency ratio are two independent trend-strength measures computed from the same candles — useful as a cross-check, but both are still descriptive statistics over price only; neither has been checked against any forward-looking outcome (correctly out of scope for this phase, per the charter's own "never use strategy P&L" rule).

---

## 8. Regression Result

| | Before Phase 20.1 | After Phase 20.1 |
|---|---|---|
| Passed | 6057 | 6107 |
| Skipped | 1 | 1 |
| Failed | 0 | 0 |

Net +50 tests, 0 regressions.

---

## 9. Protection Confirmation

Phase 19.19 core file hashes unchanged (`guard.py`, `run_daily_intelligence_session.py`, `daily_session.py`, `completeness.py` all byte-identical to their commissioned state). Systemd unchanged — same two units, timer still `enabled`/`active`, nothing new installed. Independent grep sweep across both new packages: zero occurrences of `place_order`/`modify_order`/`cancel_order`.

---

## 10. What Happens Next

Per Step 7's own boundary, this phase built only MIC v0 and its validation harness — no strategy selection engine, no execution simulator, no paper trading, no options intelligence. The §5 finding needs your review and a decision on which of the three paths (recalibrate / shorten the window / accept TREND-following is rare) to take before Phase 20.2 (Execution Reality Engine) or any strategy-selection work proceeds — building an execution engine or a paper campaign around a regime classifier whose TREND side is currently unvalidated would repeat exactly the mistake this whole roadmap redesign was meant to prevent.
