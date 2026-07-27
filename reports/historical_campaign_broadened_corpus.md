# Historical Qualification — Broadened Corpus (~4 Months)

**BUJJI Options OS v3 — Corpus Window Extended from 41 to 81 Real Trading Days**

## Status: Complete — important, non-obvious finding: broadening the window *reduced* completions

## Corpus

81 real NSE trading days, 2026-03-23 to 2026-07-22 (~4 months; prior
window was 2026-05-25 to 2026-07-22, ~2 months). 85 candidate
weekdays, 7 real exchange holidays detected (03-26, 03-31, 04-03,
04-14, 05-01, 05-28, 06-26). Not synthesized. Validator: 81/81 valid.

```
corpus_id=CORPUS-4c65b939ffdfcdd2
checksum=df04b18faa889eeb8953eb736e771d85a75403b40bb20bbcfaecd08f61b1be25
report_id=QREPORT-f9103ae26f97d6c1
```

## Governance / certification: still fully evidence-driven

`governance`: 74/81 `APPROVED`, 7/81 `APPROVED_WITH_WARNINGS`, **0
`REJECTED`**. `certification`: 81/81 `CERTIFIED`. Series 65's fix holds
across the larger window.

## The important finding: completion rate dropped, not rose

| | 41-session corpus (2 months) | 81-session corpus (4 months) |
|---|---|---|
| `completed_runs` | 29/41 (71%) | **2/81 (2.5%)** |
| `COVERED_CALL` (`STRATEGY_OUT_OF_V1_SCOPE`) | 4 | 18 |
| `NO_STRATEGY` | 8 | 61 |
| `market_context` distribution | mostly real trend/regime values | 57/81 `TRANSITION` |

The same calendar dates that completed in the 41-session run (e.g.
2026-06-11 through 2026-07-15, all `DIRECTIONAL_PUT_SPREAD`) now
resolve to `NO_STRATEGY` in the 81-session run. This is not a
regression or a bug — `market_context`/`calibration` are MIC v2's own
rolling-window classifications (Series 62), so the *same date* is
classified differently depending on how much history precedes it. A
longer window did not straightforwardly improve signal quality here;
for this period it pushed more sessions into `TRANSITION` (an
ambiguous, non-actionable regime), reducing completions.

## What this means for corpus-window methodology

**Broadening the historical window is not automatically better** for
this system's current classification logic. This is a genuine,
newly-discovered evidence-based finding, not assumed: window length
itself is a variable that materially changes qualification outcomes,
and no principled way to choose it has been established yet. Recommend
this become its own evidence-driven question (e.g. sweep several
window lengths and observe completion-rate/regime-distribution
sensitivity) before treating any single window's results as
representative.

## Verification

- Qualification fingerprint (`baselines.json`): `2328e0f77ec312eeca318946df293d91` — unchanged.
- Full two-run determinism was not re-verified for this specific 81-session extension (each run took ~78s of real MIC v2 subprocess replay); determinism of this exact pipeline has already been independently confirmed twice on two different corpora (Series 64, Series 65's 41-session rerun) at this same code state.

## Recommendation

**Do not treat the 41-session corpus's 71% completion rate as
representative, and do not proceed to live-paper qualification yet.**
The dominant open question is no longer governance (resolved) or
architecture (proven) — it is **how sensitive MIC v2's classification
is to replay-window length**, and what window (if any) is
representative of real trading conditions. Recommend a dedicated
window-sensitivity study as the next evidence-gathering step, rather
than further widening the corpus blindly.
