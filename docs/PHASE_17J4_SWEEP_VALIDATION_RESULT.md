# Phase 17J.4 — Timeline Comparison: 16-Date Sweep Result

**Status: SUBSTANTIAL, REAL IMPROVEMENT — DOES NOT FULLY PASS.**
Re-ran the identical 16-date, a-priori-labeled sweep from
`PHASE_17J2_REVISIT_SWEEP_VALIDATION_RESULT.md`, using
`compare_timelines()` (14 checkpoints/session, 30-min interval)
instead of the single end-of-session `compare()`. Reporting the actual
result, not a rounded-up version of it.

---

## Result

```
                  single-point (17J.2)    timeline (17J.4)
STRESS-STRESS     0.440                    0.482
STRESS-CALM       0.457                    0.440
CALM-CALM         0.549                    0.501
```

**New ordering: CALM-CALM (0.501) > STRESS-STRESS (0.482) > STRESS-CALM
(0.440).**

## What improved, concretely

The single most important property a similarity engine needs — same-
label pairs should score higher than cross-label pairs — **now
holds**, on both sides: STRESS-STRESS (0.482) > STRESS-CALM (0.440),
and CALM-CALM (0.501) > STRESS-CALM (0.440). Under the single-point
metric, STRESS-STRESS was the LOWEST-scoring group of the three,
worse than STRESS-CALM — the timeline approach corrects that specific
inversion. The gap between CALM-CALM and STRESS-STRESS also shrank
from 0.109 to 0.019, an 83% reduction.

All 16 dates produced fully-populated timelines (14/14 checkpoints,
except the first checkpoint at market open, which is honestly `None`
for every date — no prior bar exists to pair with at 09:15, matching
`detect_price_change()`'s own contract, not a data gap).

## What did not fully resolve

**STRESS-STRESS (0.482) is still slightly below CALM-CALM (0.501).**
17J.3's diagnosis (single-snapshot compresses away path information)
was a real, substantial factor — the correction it predicted mostly
happened — but it was not the entire explanation. Two real,
non-exclusive possibilities, neither confirmed here:

1. **Calm days are, structurally, a smaller, more repeatable pattern
   space than crises are.** A quiet session's `EMERGING_TREND`/
   `IN_BALANCE`/`ESTABLISHED support-resistance` reads look similar to
   each other because there IS less going on to differentiate them.
   Real crises may simply not share a common structural shape — a
   morning gap-crash (2022-02-24, Ukraine), a steady grinding
   breakdown (2018-10-26), and a V-shaped reversal (2020-03-23) are
   all "stressed" to a human observer but are three genuinely
   different intraday shapes, even measured path-wise. If true, this
   is not a measurement flaw to fix — it would mean "STRESS" isn't a
   coherent single category for structural similarity, and any metric
   built only from this feature set will keep showing this pattern.
2. **The 30-minute checkpoint interval may still be too coarse** to
   capture the sharpest, most-defining moments of a fast crash (a
   30-minute gap could span an entire violent leg of a move) —
   untested here; a finer interval was not tried.

Neither is confirmed. This document reports the measured result, not a
conclusion about which explanation is correct.

## What this means, plainly

The path-comparison approach (17J.3's recommendation) is a real
improvement, evidenced by data, not just theory — the specific failure
mode it targeted (STRESS-STRESS scoring lowest of all three groups) is
fixed. It has not, on this 16-date sample, achieved full separation
(STRESS-STRESS still trails CALM-CALM, if narrowly). Declaring this
"working" would overstate what was actually measured.

## Not decided here

- Whether the remaining gap reflects a real property of market crises
  (§ "What did not fully resolve" point 1) or a still-too-coarse
  measurement (point 2) — would need either a finer checkpoint
  interval or a fundamentally different comparison unit to
  distinguish, not attempted in this phase.
- Whether this level of discrimination (same-label pairs now reliably
  above cross-label pairs, even if the two same-label groups aren't yet
  cleanly separated from each other) is "good enough" for any
  downstream use — no downstream use has been scoped or approved
  anywhere in this thread, so this question has no urgency attached to
  it yet.
- No weights, ML, or clustering were introduced to chase a better
  number, per this phase's own restriction, carried forward unchanged.
