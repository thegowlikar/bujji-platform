# Window-Length Sensitivity Sweep

**BUJJI Options OS v3 — following Series 66's recommendation**

## Status: Complete — real, evidence-backed, important finding

## Method

5 real corpora, all real NSE Bhavcopy data, all ending 2026-07-22,
with real governance/certification evidence (Series 65): window
lengths 13, 31, 51, 71, 81 trading days. Same code, same pipeline, same
qualification framework, run once each (no code changes this sprint).

## Result: completion rate is non-monotonic and highly sensitive to window length

| Window | Sessions | Completed | Completion rate |
|---|---|---|---|
| 13 days | 13 | 3 | 23.1% |
| 31 days | 31 | 4 | 12.9% |
| 51 days | 51 | 42 | **82.4%** |
| 71 days | 71 | 1 | **1.4%** |
| 81 days | 81 | 2 | 2.5% |

**This is not a monotonic trend in either direction.** Completion rate
swings wildly — from 1.4% to 82.4% — depending on exactly where the
window starts, all else being equal (same end date, same real market
data, same code).

## Root cause, consistent with Series 66's Observatory finding

`market_context` (and downstream `calibration`) are MIC v2's own
rolling-window classifications. Across all 5 windows, `TRANSITION` is
the dominant `market_context` value (5/13 at the low end, up to 57/81
at the high end) — an ambiguous regime the Strategy Selector declines
to trade. The 51-day window happens to land on a period where fewer
sessions fall into `TRANSITION` (33/51, vs. 52/71 and 57/81) and more
land on directional/`SIDEWAYS` regimes the Strategy Selector can act
on — producing the 82.4% spike. This is a real property of the
underlying market data and MIC v2's deterministic classification
logic, not noise or a bug — but it means **no single window is
representative of system readiness**.

## Governance/certification: stable across all windows

`governance` remained 0% `REJECTED` and `certification` remained 100%
`CERTIFIED` at every window length (13 through 81 days) — confirming
Series 65's fix is robust to window length, unlike strategy-selection
outcomes.

## Verification

Qualification fingerprint (`baselines.json`, `2328e0f77ec312eeca318946df293d91`)
unchanged. No code modified this sprint — pure evidence gathering using
already-frozen Series 58–66 tooling.

## Recommendation

**Completion rate, on its own, is not a valid readiness metric while
it is this sensitive to an arbitrary parameter (corpus start date).**
Before any further readiness judgment:

1. **Do not cite any single window's completion rate** (including the
   71%/82.4% figures already on record) as representative.
2. **Investigate why `TRANSITION` dominates** across most windows and
   most window lengths — this is the actual lever controlling
   completion rate, not window length per se. A future sprint should
   use the Observatory (Series 66) to explain, session by session,
   what specifically pushes `market_context` into `TRANSITION` vs. a
   directional/`SIDEWAYS` regime.
3. **Treat window length as a confound to control for**, not a lever
   to tune for a better-looking number — cherry-picking a favorable
   window (e.g. 51 days) would materially misrepresent system
   readiness.
