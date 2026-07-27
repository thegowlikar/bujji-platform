# Expression Coverage Investigation (ECI)
## BUJJI Engineering Series 94

**Status:** Investigation and measurement only. No production code was
modified. `bujji/msi_strategy_expression/`, `bujji/msi_strategy_selector/`,
and every prior MSI module are byte-for-byte unchanged from Series 93
(confirmed: the full 2566-test suite still passes with zero
modifications to any production file in this series).

**Bottom line, up front:** the 6/41 -> 19/41 no-selection increase is
**mostly (but not entirely) genuine, disciplined selectivity**, not a
modelling defect. Every rejection in the real corpus fails on **two or
more** characteristic axes simultaneously -- not one. But the
investigation also surfaces one real, concrete taxonomy gap
(`SYNTHETIC` is completely orphaned) and a real concentration finding
(2 families account for over half of all rejections) that argue for
narrow, evidence-justified extensions later -- not for undoing Series 93.

---

## 1. Deliverable 1 — Coverage Audit

Computed directly from `bujji.msi_strategy_expression.engine.derive_strategy_expression`, one synthetic thesis per real thesis type (no replay needed for this part -- this is a static property of the taxonomy/config, true on every day):

| Thesis | Compatible families | Count |
|---|---|---|
| `TREND_CONTINUATION` | `LONG_DIRECTIONAL` | 1 |
| `TREND_REVERSAL` | `LONG_DIRECTIONAL` | 1 |
| `BREAKOUT` | `LONG_DIRECTIONAL` | 1 |
| `FAILED_BREAKOUT` | `LONG_DIRECTIONAL` | 1 |
| `MEAN_REVERSION` | `LONG_DIRECTIONAL` | 1 |
| `RANGE_PERSISTENCE` | `BUTTERFLY, IRON_CONDOR, IRON_FLY, NEUTRAL_PREMIUM_SELLING, VOLATILITY_COMPRESSION` | 5 |
| `VOLATILITY_EXPANSION` | `CALENDAR, LONG_DIRECTIONAL, NEUTRAL_PREMIUM_BUYING, VOLATILITY_EXPANSION` | 4 |
| `VOLATILITY_COMPRESSION` | `BUTTERFLY, COVERED, IRON_CONDOR, IRON_FLY, NEUTRAL_PREMIUM_SELLING, RATIO, SHORT_DIRECTIONAL, VOLATILITY_COMPRESSION` | 8 |
| `EVENT_RISK` | `BUTTERFLY, CALENDAR, IRON_CONDOR, IRON_FLY, LONG_DIRECTIONAL, NEUTRAL_PREMIUM_BUYING, VOLATILITY_EXPANSION` | 7 |
| `NO_TRADE` | (none, by design) | 0 |

Every incompatible family's exact reason was captured directly from the engine's own `Explanation.why_families_incompatible` (e.g. *"SHORT_DIRECTIONAL rejected: provides UNDEFINED_RISK, NEGATIVE_CONVEXITY, which contradicts the thesis; missing required DEFINED_RISK, POSITIVE_CONVEXITY"*) -- full per-thesis incompatible lists with reasons are in the replay output, omitted here for brevity but none were circular or ambiguous (see Deliverable 8).

**Orphaned theses**: none among the 9 real thesis types -- every one has at least 1 compatible family (`NO_TRADE` has 0 by explicit design, not an oversight).

**Orphaned families**: **`SYNTHETIC` is compatible with ZERO theses** -- the single clearest finding of this investigation. Its characteristic set (`DIRECTIONAL, UNDEFINED_RISK, UNLIMITED_LOSS, UNLIMITED_PROFIT`) was declared without any volatility/theta/convexity tag at all (Series 93's own `config.py` comment left these three axes unassigned for `SYNTHETIC`, reflecting real option-theory ambiguity: a synthetic long stock's vega and theta are each close to zero by construction, not cleanly `LONG_`/`SHORT_` on either axis). Because every thesis rule requires or forbids at least one volatility/convexity characteristic, and `SYNTHETIC` has neither, it can never satisfy a `required` set that includes either -- structurally excluded from every thesis, not from a scoring competition it lost.

**One-to-many mappings**: `VOLATILITY_COMPRESSION` (8 families), `EVENT_RISK` (7), `RANGE_PERSISTENCE` (5), `VOLATILITY_EXPANSION` (4) -- these four theses are richly served.

**Many-to-one mapping (concentration risk)**: `LONG_DIRECTIONAL` is the ONLY compatible family for 5 of 9 real theses (`TREND_CONTINUATION`, `TREND_REVERSAL`, `BREAKOUT`, `FAILED_BREAKOUT`, `MEAN_REVERSION`) -- all five share the identical `_DIRECTIONAL_RULE` in `config.py`. This is a real structural finding: over half of BUJJI's thesis vocabulary currently has exactly one possible implementation.

## 2. Deliverable 2 — Expression Matrix Analysis

Over the 9 real (non-`NO_TRADE`) thesis types:

- **Average compatible families per thesis: 3.22**
- **Minimum: 1** (5 of 9 theses: all directional ones)
- **Maximum: 8** (`VOLATILITY_COMPRESSION`)
- **Median: 1**

**5 of 9 real theses have only ONE implementation available** -- a median of 1 is itself a notable finding: the "typical" thesis in this taxonomy is currently a single-point-of-failure mapping, not a rich one-to-many relationship, despite the whole premise of Series 93 being that a thesis "can legitimately be expressed by multiple strategy families."

## 3. Deliverable 3 — Historical Replay Analysis (full detail, not aggregated)

Replayed the real 41-day corpus with per-day, per-family detail captured for every no-selection day (19 total). Representative entries (full detail for all 19 days was generated and reviewed; the complete list is reproducible via `/tmp/run_series94_investigation.py` on the remote host):

```
2026-05-26  thesis=RANGE_PERSISTENCE  required=(DELTA_NEUTRAL, SHORT_VOLATILITY)  forbidden=(DIRECTIONAL, LONG_VOLATILITY)
  SSF-suitable: (COVERED,)
  COVERED: missing_required=[DELTA_NEUTRAL]  incompatible_present=[DIRECTIONAL]

2026-06-08  thesis=TREND_CONTINUATION  required=(DIRECTIONAL, DEFINED_RISK, POSITIVE_CONVEXITY)  forbidden=(UNDEFINED_RISK, NEGATIVE_CONVEXITY)
  SSF-suitable: (BUTTERFLY, RATIO, SHORT_DIRECTIONAL)
  BUTTERFLY: missing_required=[DIRECTIONAL, POSITIVE_CONVEXITY]  incompatible_present=[NEGATIVE_CONVEXITY]
  RATIO: missing_required=[DEFINED_RISK, POSITIVE_CONVEXITY]  incompatible_present=[UNDEFINED_RISK, NEGATIVE_CONVEXITY]
  SHORT_DIRECTIONAL: missing_required=[DEFINED_RISK, POSITIVE_CONVEXITY]  incompatible_present=[UNDEFINED_RISK, NEGATIVE_CONVEXITY]

2026-07-14  thesis=VOLATILITY_EXPANSION  required=(LONG_VOLATILITY, POSITIVE_CONVEXITY)  forbidden=(SHORT_VOLATILITY, NEGATIVE_CONVEXITY)
  SSF-suitable: (BUTTERFLY, COVERED, NEUTRAL_PREMIUM_SELLING)
  All three: missing_required=[LONG_VOLATILITY, POSITIVE_CONVEXITY]  incompatible_present=[SHORT_VOLATILITY, NEGATIVE_CONVEXITY]
```

**Precise split of the 19 no-selection days** (computed by comparing the baseline/unfiltered run against the expression-filtered run, both against SSF's real suitability output):

- **6 days were ALREADY no-selection before the expression filter ran at all** (`2026-06-01, 06-11, 06-29, 07-08, 07-13, 07-22`) -- either zero SSF-suitable families that day, or MSS's own market-state disqualification already eliminated every candidate. These 6 are **unrelated to Series 93** and must not be attributed to the expression filter.
- **13 days genuinely became no-selection BECAUSE of the expression filter** (baseline had a real selection; post-filter does not): `2026-05-26, 06-02, 06-03, 06-04, 06-08, 06-16, 06-22, 06-23, 06-25, 07-02, 07-07, 07-14, 07-15`.

This precise split matters: the *true* attributable effect of Series 93 is **13/41 (32%)**, not 19/41 -- the raw 6->19 comparison in Series 93's own report conflated pre-existing gaps with newly-introduced ones. This correction is itself a finding of this investigation.

## 4. Deliverable 4 — Counterfactual Investigation

For every rejected SSF-suitable family on every no-selection day, the exact number of characteristic-level violations (missing required + present forbidden) was counted.

**Result: zero days had a family that was only ONE relaxation away from compatibility.** Every single rejection in this real corpus involved **2, 3, or 4** simultaneous characteristic mismatches. This is a materially reassuring finding: the expression filter is not excluding trades on a hair-trigger, borderline basis anywhere in this corpus -- every exclusion reflects a genuinely multi-dimensional mismatch between what the family provides and what the thesis requires.

**Testing the user's own two illustrative examples directly:**
- **"COVERED added to RANGE_PERSISTENCE"**: `COVERED` is missing `DELTA_NEUTRAL` AND actively provides the forbidden `DIRECTIONAL` characteristic -- a 2-axis gap. Naively adding `COVERED` to `RANGE_PERSISTENCE`'s compatible list (without changing `COVERED`'s own declared characteristics) would contradict `COVERED`'s own correct classification as a directional expression. The real fix, if any, is not "add the family" but "reconsider whether `COVERED` is mis-tagged as `DIRECTIONAL`" -- a materially different, more careful question than the counterfactual implied.
- **"BUTTERFLY added to TREND_REVERSAL"**: `BUTTERFLY` is missing `DIRECTIONAL` and `POSITIVE_CONVEXITY`, and provides the forbidden `NEGATIVE_CONVEXITY` -- a 3-axis gap, the widest of any counterfactual examined. This one is a poor fit even directionally: a butterfly is fundamentally a pinning/range structure, not a trend-reversal expression, and the data supports keeping it excluded.

**Aggregate characteristic-mismatch frequency** (across all rejected SSF-suitable families, all 19 no-selection days):

| Missing required (count) | Forbidden-but-present (count) |
|---|---|
| `POSITIVE_CONVEXITY`: 25 | `NEGATIVE_CONVEXITY`: 25 |
| `DEFINED_RISK`: 15 | `UNDEFINED_RISK`: 15 |
| `DELTA_NEUTRAL`: 9 | `DIRECTIONAL`: 9 |
| `LONG_VOLATILITY`: 6 | `SHORT_VOLATILITY`: 6 |
| `DIRECTIONAL`: 5 | `LONG_VOLATILITY`: 1 |
| `SHORT_VOLATILITY`: 1 | |

**`POSITIVE_CONVEXITY`/`NEGATIVE_CONVEXITY` and `DEFINED_RISK`/`UNDEFINED_RISK` dominate overwhelmingly** -- together accounting for the large majority of all rejections. This directly traces back to the shared `_DIRECTIONAL_RULE` (Section 1's "many-to-one" finding): every directional thesis demands both, and only `LONG_DIRECTIONAL` provides both among the 13 real families.

**Rejected-family frequency**: `RATIO` (10 rejections), `SHORT_DIRECTIONAL` (8), `COVERED` (6), `BUTTERFLY` (5), `VOLATILITY_COMPRESSION` (3), `NEUTRAL_PREMIUM_SELLING` (1), `LONG_DIRECTIONAL` (1). **`RATIO` and `SHORT_DIRECTIONAL` alone account for 18 of the 34 total rejection instances (53%)** -- these two families are disproportionately proposed by MSS's market-state scoring on directional-thesis days, then disproportionately screened out by the expression filter for the same reason every time (`UNDEFINED_RISK`/`NEGATIVE_CONVEXITY`).

## 5. Deliverable 5 — Taxonomy Completeness

The 16 existing characteristics were checked against the investigation's own findings for gaps:

- **`LIMITED_PROFIT`/`UNLIMITED_PROFIT`/`LIMITED_LOSS`/`UNLIMITED_LOSS` already fully capture** "limited upside"/"limited downside"/"asymmetric payoff" concepts under different names -- **no gap found here**, contrary to what the example list in the spec might suggest.
- **`THETA_NEUTRAL` and `VOLATILITY_NEUTRAL` are genuinely missing**, and this is directly, concretely evidenced by Section 1's `SYNTHETIC`-orphaning finding: a synthetic long/short position has real, close-to-zero theta and vega by construction (a long call's decay is largely offset by a short put's decay, and their vegas largely cancel), but the current taxonomy only offers `POSITIVE_THETA`/`NEGATIVE_THETA` and `LONG_VOLATILITY`/`SHORT_VOLATILITY` -- forcing an honest "neither" case to be silently unrepresented rather than explicitly stated. **Recommended for future addition, justified directly by replay/matrix evidence (not speculatively)** -- not implemented in this series (investigation only).
- **"Asymmetric payoff" as its own tag** was considered and rejected as a genuine gap -- it is already fully derivable from the existing `LIMITED_`/`UNLIMITED_` pair (e.g. `RATIO`'s `UNLIMITED_LOSS` + `LIMITED_PROFIT` already IS an asymmetric-payoff declaration).

## 6. Deliverable 6 — Professional Desk Comparison (concepts only)

BUJJI's current expression vocabulary covers: direction, volatility direction, risk definition, time decay sign, convexity sign, payoff shape, premium flow. Compared against institutional desk practice, three conceptual gaps were identified (documented only, not built):

- **Expressing carry**: professional desks distinguish "carry" (the cost/benefit of holding a position through time, independent of a realized move) as its own concept -- e.g. a calendar spread or covered call is fundamentally a carry trade. BUJJI currently only approximates this via `POSITIVE_THETA`, which conflates "collects time decay" with the richer, distinct "carry" concept (which also involves relative term-structure pricing).
- **Expressing term structure / skew**: real desks routinely express a view on the SHAPE of the volatility surface (term structure, skew), not just its overall level. This codebase has no real term-structure or skew data source at all (already disclosed in Series 88: `TERM_STRUCTURE_UNKNOWN`/`SKEW_UNKNOWN` are always returned) -- reconfirmed here as a genuine, still-open conceptual absence, not a new finding.
- **Expressing a hedge vs. expressing a view**: this entire Thesis -> Expression -> Selection arc is built exclusively around expressing a market VIEW. Institutional desks also routinely construct positions purely to hedge existing portfolio exposure (Portfolio & Risk Construction, Series 91, tracks portfolio state, but nothing in this arc lets a "thesis" be "reduce existing delta/vega exposure" rather than "I believe X will happen"). A real, disclosed conceptual gap for a future series, not this one.

## 7. Deliverable 7 — Coverage Metrics (replayed twice, byte-identical)

- **Thesis coverage**: 9/9 real thesis types (100%) have at least one compatible family; `NO_TRADE` has 0 by design.
- **Expression coverage (replay-days with a thesis-to-family path in principle)**: 40/41 real days (97.6%) -- the sole exception is the one real `NO_TRADE` day.
- **Family utilisation (static, matrix-level)**: 12/13 real families (92.3%) appear in at least one thesis's compatible list -- only `SYNTHETIC` is orphaned.
- **Family utilisation (actual replay selections)**: dropped from **7/13 families (53.8%) pre-filter** to **5/13 families (38.5%) post-filter** -- `RATIO` and `VOLATILITY_COMPRESSION` were selected pre-filter but never survive post-filter anywhere in this corpus.
- **Rejection concentration**: `RATIO` + `SHORT_DIRECTIONAL` = 53% of all rejection instances (Section 4).
- **Dominant missing characteristics**: `POSITIVE_CONVEXITY` / `DEFINED_RISK` (and their forbidden-present counterparts `NEGATIVE_CONVEXITY` / `UNDEFINED_RISK`) -- Section 4.
- **Determinism**: replayed twice; expression `assessment_id`s and post-filter selected families were byte-identical across both runs (confirmed programmatically, not just visually inspected).

## 8. Deliverable 8 — Explainability Review

All 19 no-selection days' rejection explanations (Section 3's full detail) plus 1 additional changed-but-not-none day (`2026-05-29`, `VOLATILITY_COMPRESSION -> LONG_DIRECTIONAL`) were reviewed -- 20 representative days total, as specified.

**Finding: every single explanation was immediately understandable by a human reviewer** -- each states, in plain terms, exactly which characteristic(s) the rejected family lacks and which forbidden characteristic(s) it provides (e.g. *"RATIO: missing_required=[DEFINED_RISK, POSITIVE_CONVEXITY], incompatible_present=[UNDEFINED_RISK, NEGATIVE_CONVEXITY]"*). **No explanation was found to be circular, ambiguous, or incomplete.** The one caveat worth flagging (not a defect, a scope note): the explanation never states WHY `DEFINED_RISK`+`POSITIVE_CONVEXITY` were chosen as the requirement for directional theses in the first place -- that reasoning lives in Series 93's own documentation and config comments, not in the per-day `Explanation` object itself. This is consistent with the established convention (thresholds/policy choices are documented at the config level, not re-explained on every assessment) and is not treated as a defect here.

## 9. Conceptual diagram

```
                    Thesis                    Compatible families (real, from replay)
   ─────────────────────────────────────────────────────────────────────────────────
   TREND_CONTINUATION  ────────────────────►  LONG_DIRECTIONAL                    (1)
   TREND_REVERSAL      ────────────────────►  LONG_DIRECTIONAL                    (1)
   BREAKOUT            ────────────────────►  LONG_DIRECTIONAL                    (1)
   FAILED_BREAKOUT     ────────────────────►  LONG_DIRECTIONAL                    (1)
   MEAN_REVERSION      ────────────────────►  LONG_DIRECTIONAL                    (1)
   RANGE_PERSISTENCE   ──┬─────────────────►  BUTTERFLY, IRON_CONDOR, IRON_FLY,
                         │                    NEUTRAL_PREMIUM_SELLING,
                         │                    VOLATILITY_COMPRESSION              (5)
   VOLATILITY_EXPANSION ─┼─────────────────►  CALENDAR, LONG_DIRECTIONAL,
                         │                    NEUTRAL_PREMIUM_BUYING,
                         │                    VOLATILITY_EXPANSION                (4)
   VOLATILITY_COMPRESSION┼─────────────────►  BUTTERFLY, COVERED, IRON_CONDOR,
                         │                    IRON_FLY, NEUTRAL_PREMIUM_SELLING,
                         │                    RATIO, SHORT_DIRECTIONAL,
                         │                    VOLATILITY_COMPRESSION              (8)
   EVENT_RISK           ─┴─────────────────►  BUTTERFLY, CALENDAR, IRON_CONDOR,
                                              IRON_FLY, LONG_DIRECTIONAL,
                                              NEUTRAL_PREMIUM_BUYING,
                                              VOLATILITY_EXPANSION                (7)
   NO_TRADE            ────────────────────►  (none, by design)                   (0)

                                              SYNTHETIC  ── never appears above ── ORPHANED
```

## 10. Deliverable 10 — Recommendation: **No change**, with two narrowly-scoped follow-ups flagged for later

Evidence-based, from the measurements in Sections 1-8 only:

1. **The core finding is reassuring, not alarming**: zero no-selection days in this corpus were a single-characteristic edge case (Deliverable 4) -- every rejection reflects a genuine, multi-axis mismatch between what a family provides and what a thesis requires. This is disciplined selectivity working as designed, not noise.
2. **The corrected attribution matters**: only 13/41 (32%), not 19/41 (46%), of no-selection days are actually caused by the expression filter -- 6 were already unselectable upstream. Re-litigating Series 93 based on the uncorrected 19/41 figure would be responding to an inflated number.
3. **Two real, narrow gaps were found, but neither justifies immediate action**: `SYNTHETIC`'s orphaning (Section 1/5) is real and directly evidenced, but fixing it requires extending the taxonomy first (`THETA_NEUTRAL`/`VOLATILITY_NEUTRAL`) -- itself a deliberate, separate, evidence-justified change, not a quick patch. The `RATIO`/`SHORT_DIRECTIONAL` rejection concentration (53% of all rejections) is worth monitoring on a larger corpus before concluding whether it reflects a real strategy-selection tendency worth addressing upstream (in MSS's market-state scoring) or is simply this corpus's particular day-mix.
4. **Neither of the constraints ("extend taxonomy" or "expand mappings") is supported strongly enough by THIS corpus alone** -- 41 days is a real but modest sample; the counterfactual analysis (Deliverable 4) found no single mapping change would have altered a single day's outcome.

**Recommendation: proceed to Position Construction** (i.e. continue the existing roadmap) with the expression layer AS-IS. Flag `SYNTHETIC`'s taxonomy gap and the `RATIO`/`SHORT_DIRECTIONAL` rejection concentration as items for a **future, expanded-corpus** re-investigation (not immediate action) -- consistent with "measure first, then build," and with the measurement here showing the current model is not obviously deficient enough to warrant changing it before more data exists.
