# Phase 17J.2 (revisit) — Mandatory Validation Finding

**Status: BUILT, TESTED, BUT DOES NOT YET PASS ITS OWN VALIDATION BAR.**
Per the design doc's §6 commitment: *"a similarity engine that can't
tell a crash apart from a quiet day has failed regardless of what its
distance number says."* Run against real data, this is currently true
of the implementation. Reporting it honestly rather than declaring
success.

---

## What was run

Three real, structurally distinct sessions, all at 15:25 IST close,
`lookback_bars=75`:

| Date | Real character | `vix_band` |
|---|---|---|
| 2020-03-23 | COVID crash, sustained decline + late reversal | EXTREME (71.99) |
| 2018-10-26 | October 2018 correction, sharp breakdown | ELEVATED (19.23) |
| 2019-06-14 | Ordinary session (negative control) | NORMAL (13.9) |

## Result — discrimination did not hold

```
covid vs selloff:  0.500
covid vs quiet:    0.500
selloff vs quiet:  0.5625
```

**The quiet day scored MORE similar to the crash-adjacent selloff day
(0.5625) than the two real crisis days scored to each other (0.500).**
This is the exact failure mode the design's own validation
requirement existed to catch.

## Root cause, diagnosed from the per-dimension breakdown

| Dimension | covid | selloff | quiet |
|---|---|---|---|
| `compression_state` | NOT_DETECTED | NOT_DETECTED | NOT_DETECTED |
| `expansion_state` | EARLY | EARLY | EARLY |
| `structure_integrity` | COHERENT | COHERENT | COHERENT |
| `structural_balance` | UNBOUNDED | UNBOUNDED | UNBOUNDED |
| `breakdown_state` | DEVELOPING | DEVELOPING | DEVELOPING |
| `rejection_state` | STRONG | STRONG | STRONG |

**6 of the 16 compared dimensions were identical across all three
dates**, regardless of whether the session was a historic crash or an
ordinary day. Every one of those contributes a "free" match to every
pairwise comparison, diluting the genuinely discriminating dimensions
(`trend_state`, `swing_state`, `support_state`, `resistance_state`,
`vix_band`, `structure_location`, `breakout_state`, `retest_state`,
`balance_state`, `structure_state` — all of which DID vary sensibly
across the three dates).

This looks like a systematic issue, not a 3-date coincidence: 6/16
identical across three deliberately different regimes is a strong
signal that these particular dimensions rarely leave their default/most
-common state at `lookback_bars=75`, 5-minute resolution — whether
because the underlying `msi_price_structure`/`msi_market_structure`
thresholds for compression/expansion/rejection rarely trigger anything
else at this window size, or because `structural_balance`/
`structure_integrity` are close to structurally constant by
construction (e.g. `structure_integrity` is "COHERENT" whenever there
are zero contradictions, which may be the common case regardless of
regime). Not fully diagnosed here — would need a larger date sample to
confirm which.

## What this means

**The mechanics are correct** — `compare()`, `vix_band()`,
`find_similar()`, missing-dimension exclusion, self-exclusion, ranking
— all behave exactly as designed (10 passing unit tests). **The
discrimination quality of the exact-match-fraction metric over these
16 specific dimensions is not yet demonstrated to be trustworthy.**
This is a finding about the metric/dimension choice, not a code defect.

## Options, not decided here

1. **Exclude the low-variance dimensions** from `ALL_DIMENSIONS`
   (`compression_state`, `expansion_state`, `structural_balance`,
   `breakdown_state`, `rejection_state`, and re-check
   `structure_integrity`) — a data-driven simplification, but needs a
   larger sample than 3 dates to confirm these really are low-variance
   in general, not just on this sample.
2. **Weight dimensions by informativeness** (inverse to how often they
   take their most common value across a real historical sample) —
   more principled, but is itself a new statistical-modeling decision
   this project has repeatedly been cautious about introducing without
   real justification.
3. **Run a larger validation sweep** (10-20 real dates spanning several
   regimes) before deciding anything — establishes whether the 6
   near-constant dimensions really are structurally uninformative at
   this window size, rather than concluding from 3 dates.
4. **Reconsider `lookback_bars`** — a longer or shorter window might
   change how often compression/expansion/rejection actually vary;
   not tested here.

**Recommendation: option 3 first** — a larger real-data sweep is cheap
(compute-only, no new architecture) and would turn "this looks
systematic" into either a confirmed finding (supporting option 1) or a
correction (if the 3-date sample was simply unlucky). Nothing else
should be decided before that.

## What is NOT being claimed

`find_similar()` is built and mechanically correct, but **its output
should not be trusted for anything downstream until this
discrimination-quality question is resolved.** No prediction, no
probability, and no strategy work was ever in scope here regardless —
this finding only concerns whether the "similar days" it returns are
actually similar.
