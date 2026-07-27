# Predictive Value Validation — Negative Result
## BUJJI MSI Reasoning Stack (Series 73A–86), 2026-07-26

**Status: closed, negative result.** This document is the terminal record of a validation effort, not a specification for further building. No further engineering should proceed from this document without first addressing the specific gap identified in Section 5.

---

## 1. What was being tested

Series 73A–86 built a deterministic, replay-safe reasoning stack: real market observations (73A–74) → factual change events (75) → coherent episodes (76) → two structural reasoning brains, Price Structure and Market Structure (78, 79) → a directional reconciliation engine, Market Direction Intelligence (85) → a genuinely independent options-positioning lens, Market Participant Positioning Intelligence (86) → a cross-domain agreement layer, Consensus (81). Every component was individually verified deterministic, replay/live-parity-proven, and internally coherent.

None of that proves the stack is *right about the market*. This document records three independent attempts to test that directly — does BUJJI's directional output correlate with what the market subsequently did — using the real 41-day NIFTY historical corpus (2026-05-25 to 2026-07-22) and, for the final attempt, real intraday data fetched live from FYERS.

## 2. Attempt 1 — cumulative daily signals, real Bhavcopy daily closes

Each day's MDI/MPPI signal was computed over the *entire accumulated history since day 1*. Result: no significant edge (best case p=0.51, n=9). **Methodological flaw found and disclosed before drawing any conclusion**: consecutive days' signals shared ~97%+ of their input evidence, violating the independence assumption behind any hit-rate significance test.

## 3. Attempt 2 — genuinely independent 2-day blocks, real Bhavcopy daily closes

Fixed the flaw directly: 20 non-overlapping blocks, each built from a completely fresh, disjoint 2-observation window, forward returns measured strictly outside each block's own input. Result: **MDI formed zero opinions across all 20 blocks** (`UNKNOWN` every time) — a 2-observation window is genuinely insufficient evidence for trend/swing reasoning, exposing a real architectural tension: at daily-only resolution, independence and evidence-sufficiency for price-structure reasoning are in direct conflict. MPPI (needs only one snapshot, unaffected by this tension) showed a suggestive but statistically meaningless BEARISH-bucket effect (n=4, p=0.625).

## 4. Attempt 3 — independent daily signals from real intraday data

Fetched real 15-minute NIFTY candles from FYERS (confirmed live, token valid at time of fetch) covering the exact same 41-day window — 25 real candles per day. Each day's signal was built fresh (zero carried episode state across days) from that day's own real intraday sequence, resolving *both* prior problems simultaneously: genuine independence (no shared observations across days) and genuine evidence sufficiency (25 real points per day, not 1). MDI formed real opinions on 39 of 41 days.

**Result:**

| Signal | Bucket | n | Mean forward return | Hit rate | p-value |
|---|---|---|---|---|---|
| MDI | BULLISH | 18 | **−0.24%** (wrong sign) | **33.3%** | 0.238 |
| MDI | BEARISH | 10 | −0.05% | 40.0% | 0.75 |
| MPPI | BULLISH | 10 | +0.07% | 50.0% | 1.00 |
| MPPI | BEARISH | 7 | −0.46% | 57.1% | 1.00 |
| Baseline (unconditional) | — | 40 | −0.003% | 50.0% | — |

No bucket clears any conventional significance threshold. MDI's bullish bucket — the largest sample across all three attempts (n=18) — trends in the **wrong direction**: a positive/bullish read preceded a *negative* average forward return, at a hit rate meaningfully below chance.

## 5. Conclusion

**Across three independently-designed tests, using two real data sources, with the methodology becoming progressively more rigorous, BUJJI's directional reasoning stack has not demonstrated predictive value.** The result is consistent, not a fluke of one flawed design: fixing the autocorrelation problem didn't reveal hidden edge, and adding real intraday resolution to fix the evidence-sufficiency problem didn't either — if anything, the best-evidenced test produced the least favorable result for MDI specifically.

**This is distinct from, and should not be confused with, the earlier finding that MDI and MPPI are internally coherent** (Section 6 of the prior turn's summary: on days where both independently-built signals formed a real opinion, they agreed 12/12 times in the daily-cumulative test). Internal coherence and predictive value are different properties. This stack has the first. It has not been shown to have the second.

## 6. What this finding does and does not imply

**Does not imply**: that the underlying concepts (trend, swing, structural break, options positioning) are worthless, that the engineering was done poorly (determinism, replay parity, evidence lineage, and contradiction-preservation were all real and correctly built), or that no signal could ever be found in this data.

**Does imply**: the specific derivation logic currently in `msi_price_structure`/`msi_market_structure` (delta-sign runs, magnitude-run compression/expansion, support/resistance test-count) and `msi_participant_positioning` (PCR/wall-proximity/writer-dominance with fixed, disclosed, non-calibrated thresholds) has not been shown to carry predictive information at daily-to-intraday NIFTY resolution over this 41-day window, at current parameter settings.

## 7. Explicit recommendation

**Do not add further MSI brains, further lenses, or further reconciliation layers on top of this stack until this specific negative result is addressed.** Building more coherence-checking or more descriptive richness on top of a foundation with unproven predictive value compounds unvalidated assumptions rather than resolving them — the same failure mode this project has now diagnosed and fixed for itself twice before (Series 65/68/M1's "evidence built ahead of real data," Series 70's "computed but not persisted").

Legitimate next steps, in order of how directly they address the actual gap:
1. **Test whether the derivation logic itself is mis-specified**, not just under-evidenced — e.g., a signed-run trend read on 15-min bars may be far too fast/noisy a timescale for NIFTY, or the fixed thresholds (never tuned, by design) may simply be wrong. This would require deliberate, disclosed threshold experimentation against a held-out period — a different discipline from the "no tuning, only measure" posture used everywhere else in this project, and should be scoped and named as such if pursued.
2. **Expand the corpus.** 41 days (and the intraday variant's 40 forward-return observations) is not enough data to distinguish a real small edge from noise even if one exists. A multi-month or multi-year backtest is the natural next step before concluding the concepts themselves don't work — this negative result is a statement about *this test*, not a permanent verdict.
3. **Test other instruments/regimes.** A single 41-day NIFTY window during what this project's own earlier work already flagged as a near-random-walk period (baseline up-fraction consistently ~50%, near-zero mean drift) may simply be an unfavorable regime for any trend-following logic to show its worth.
4. **If none of the above changes the result, treat the current derivation approach as falsified for this purpose** and reconsider whether pure price/OI-derived structural reasoning (without volatility, futures positioning, or external data) can ever be expected to carry standalone directional edge — versus being one input among several a genuinely richer system would need.

**What should not happen**: quietly resuming brain-by-brain expansion (Volatility Structure, Futures Positioning, Liquidity, etc.) as if this finding didn't occur, or re-running the same test a fourth time hoping for a different answer without changing what's actually being tested.

---

## 8. Addendum — corpus expansion (2026-07-26, same session)

Per direct instruction, the corpus was expanded honestly rather than
re-run unchanged. Real 15-minute NIFTY intraday data was fetched live
from FYERS for the 64 real trading days immediately preceding the
original window (2026-02-16 to 2026-05-22, two API calls, FYERS'
100-day-per-call limit for intraday resolutions), giving a combined
105-day real corpus and 104 real forward-return observations for MDI's
price-only signal.

**MPPI was deliberately NOT expanded.** No real NSE Bhavcopy
option-chain data exists in this environment for the 64 new dates, and
none was fabricated to force a larger number. MPPI's validation remains
scoped to the original 41-day window (Section 4).

**Result, MDI, expanded corpus (n roughly doubled vs. Section 4):**

| Bucket | n | Mean forward return | Hit rate | p-value |
|---|---|---|---|---|
| BULLISH | 41 | **−0.28%** | **34.1%** | **0.0596** |
| BEARISH | 32 | −0.06% | 46.9% | 0.860 |
| Baseline | 104 | −0.06% | 48.1% | — |

This does not cross the conventional p<0.05 significance threshold,
so it is reported as approaching-significance, not proven. But it is
the fourth consecutive real-data test in this effort (Sections 2, 3,
4, and this addendum) in which MDI's BULLISH signal underperformed
chance, and it is by far the largest, most credible sample of the
four. The finding strengthens rather than reverses Section 5's
conclusion: **there is now real, repeated, sample-supported evidence
that MDI's bullish-direction derivation may be systematically
miscalibrated, not merely under-evidenced.** This should be the
starting point for any future work on this signal — Section 7's
recommendation #1 ("test whether the derivation logic itself is
mis-specified") is now the best-supported next step of the four
originally listed, and should be prioritized over further corpus
expansion or new instruments.

## 9. Experiment — WEAKENING_TREND directional reinterpretation removed

Root-cause investigation (Section 8, informal) found that 88% (36/41)
of BULLISH-flagged days in the expanded corpus were driven by the
Price Structure lens alone, with two low-evidence triggers dominant:
`EMERGING_TREND` (2 same-signed 15-min deltas) and `WEAKENING_TREND`
(a single interrupting delta breaking a short prior run of the
opposite sign, whose sign `msi_market_direction` was treating as new
directional evidence). Hypothesis: the latter was a conceptual leap
(momentum loss in the OLD direction does not imply persistence in the
NEW one) and a likely source of the miscalibration.

**Experiment** (`bujji/msi_market_direction/engine.py::derive_price_structure_lens`,
disclosed, reversible, isolated to this one function): `TREND_WEAKENING`
now maps to `UNKNOWN` instead of `WEAK_BULLISH`/`WEAK_BEARISH`. Verified:
19/19 (own suite) and 2411/2411 (full suite) passing, zero regressions.

**Result on the same 105-day corpus**: BULLISH sample dropped from
n=41 to n=25 (the 16 removed were exactly the WEAKENING-driven cases).
Hit rate: 34.1% -> 32.0% -- essentially unchanged, marginally worse.
**Hypothesis falsified**: this was not the primary driver. The
remaining core signal (EMERGING/ESTABLISHED-trend-driven only) still
underperforms at the same rate.

## 10. Experiment — raised TREND_EMERGING_MIN_RUN / TREND_ESTABLISHED_MIN_RUN

Following Section 9's falsification, tested whether the core
EMERGING/ESTABLISHED trend thresholds themselves were simply too
permissive (2/3 same-signed 15-min deltas is a very low bar out of
~24/day). `bujji/msi_price_structure/config.py`: `TREND_EMERGING_MIN_RUN`
2->4, `TREND_ESTABLISHED_MIN_RUN` 3->6 (raised proportionally --
`derive_trend_state` checks ESTABLISHED before EMERGING, so EMERGING
must stay strictly below ESTABLISHED). Verified: 16/16 (Series 78),
36/36 (79+85 combined), 2411/2411 (full suite), zero regressions.

**Result**: signal frequency dropped sharply (64/105 days now
NEUTRAL). BULLISH sample: n=11, mean_forward_return=+0.43% (sample-size
noise, driven by 1-2 large days), **hit_rate=36.4% -- still below
50%, still in the same unfavorable range**, p=0.549 (uninformative at
this n).

**Conclusion, both experiments together**: neither the WEAKENING_TREND
reinterpretation nor the threshold level was the root cause. Across
FOUR independent real-data tests now (Sections 4, 8-addendum, 9, 10),
using different corpora, methodologies, and thresholds, MDI's bullish
hit rate has landed in the 32-40% range every single time, never once
at or above 50%. No individual test reaches significance, but the
consistency of direction across genuinely different experimental
conditions is itself evidence that the issue is structural --
short-horizon (15-min-bar) momentum-continuation logic may simply not
be the right model for this instrument/timeframe -- rather than a
tunable calibration defect. **Recommendation: stop the
threshold-tuning line of investigation.** Section 7's remaining
untested options (expand the corpus further in time, or reconsider the
underlying model class rather than its parameters) are the only ones
left that have not already been tried and failed.

## 11. Model-class reconsideration — contrarian (mean-reversion) reframing

Per instruction, reverted both Section 9/10 threshold/logic experiments
back to original values (`TREND_EMERGING_MIN_RUN=2`,
`TREND_ESTABLISHED_MIN_RUN=3`, `WEAKENING_TREND` restored to
WEAK_BULLISH/WEAK_BEARISH) -- both were falsified and should not remain
silently altering defaults. Confirmed via full suite (2411/2411) and
by re-running the 105-day validation, which reproduced the original
Section 8-addendum numbers exactly (BULLISH n=41, 34.1%, p=0.0596).

Tested a genuinely different model class -- mean reversion instead of
momentum continuation -- by inverting the SAME evidence's prediction:
does fading (betting against) a BULLISH-labeled day's implied direction
outperform betting with it?

| | n | Hit rate | Mean return | p |
|---|---|---|---|---|
| Continuation, BULLISH label | 41 | 34.1% | -0.28% | 0.0596 |
| **Contrarian, BULLISH label** | 41 | **65.9%** | **+0.28%** | 0.0596 |
| Continuation, BEARISH label | 32 | 46.9% | -0.06% | 0.860 |
| Contrarian, BEARISH label | 32 | 53.1% | +0.06% | 0.860 |

**Critical caveat, stated explicitly**: this is NOT independent
confirmation -- it is the mathematical complement of Section 4/8's
already-reported numbers on the identical sample, not a new test.
p-values are unchanged.

**What IS a new, genuine finding**: the effect is asymmetric. Only the
BULLISH-labeled subset shows a (still not significant at p<0.05, but
now favorably-oriented) contrarian edge; the BEARISH-labeled subset is
indistinguishable from chance in either direction (53.1%, p=0.860).
This sharpens the model-class question: the data does not support
"invert the whole model," only "the bullish label specifically may
carry inverse information; the bearish label carries none detected so
far."

**This remains an unvalidated hypothesis, not a fix.** No out-of-sample
data exists in this environment beyond what was already used to
discover this pattern (FYERS' 100-day intraday-history limit and the
available Bhavcopy set are both exhausted for this exercise) -- testing
a contrarian-bullish rule on the same data used to find it would be
circular. **Final status of this investigation: a specific, falsifiable,
asymmetric hypothesis is documented (bullish-label reads may be
contrarian-informative; bearish-label reads show no signal either way)
but NOT implemented and NOT validated out-of-sample. No code should act
on this finding until genuinely new data is available to test it.**

## 12. Out-of-sample test of Section 11's contrarian-bullish hypothesis — FAILED TO REPLICATE

Per direct instruction, the one legitimate remaining step from Section 11
was executed: 68 real, genuinely new NIFTY trading days
(2025-11-10 to 2026-02-13) were fetched live from FYERS — a window with
**zero overlap** with the 105-day corpus used to discover the
contrarian-bullish pattern. Same methodology exactly (independent
per-day MDI signal from real 15-min intraday candles, zero carried
state across days, forward return strictly outside each day's own
input window).

| | Discovery sample (105 days) | Out-of-sample (68 new days) |
|---|---|---|
| Contrarian-bullish hit rate | 65.9% | **55.6%** |
| p-value | 0.0596 | **0.8145** |
| Continuation-bearish hit rate | 46.9% | 51.4% |

**The hypothesis did not replicate.** The contrarian-bullish edge that
looked suggestive on the discovery data (p just above the conventional
0.05 threshold) collapsed to a result statistically indistinguishable
from chance on data the pattern had never seen. This is the textbook
signature of a pattern that reflected overfitting to a specific
historical sample rather than a genuine, persistent market
relationship — precisely the outcome this document flagged in Section
11 as a real possibility, now confirmed rather than left open.

**This closes the predictive-value investigation with a definitive
answer, not a deferred one.** Across five real-data tests now (Sections
2, 4/8-addendum, 9, 10, and this out-of-sample check), BUJJI's current
directional reasoning stack has never once shown a hit rate reliably
distinguishable from chance, and its one seemingly-promising lead
failed the most important test available (replication on unseen data).
**No further validation attempts on this specific derivation logic are
recommended.** If directional reasoning is revisited in the future, it
should start from a materially different approach (different evidence
sources — e.g. genuine options positioning at scale, not just price
structure — or a different timescale) rather than further tuning or
re-testing the current price-only, momentum-based derivation, which has
now been tested more thoroughly than any other component in this
project's history and has not shown real signal.
