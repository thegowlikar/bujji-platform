# Phase 17J.4 — 10-Minute Checkpoint Interval Result

**Status: NEGATIVE RESULT for the "interval too coarse" hypothesis.**
Same 16-date sweep, same labels, `interval_minutes=10` (40 checkpoints/
session vs. 30-minute's 14) instead of the untested-but-flagged 30-minute
default from `PHASE_17J4_SWEEP_VALIDATION_RESULT.md`.

---

## Result

```
                  30-min (prior)    10-min (this run)
STRESS-STRESS     0.482              0.470
STRESS-CALM       0.440              0.438
CALM-CALM         0.501              0.499
```

All 16 dates again produced fully-populated timelines (40/40
checkpoints each — the 10-minute schedule has no equivalent to the
30-minute schedule's single unavoidable `None` at market open, since
09:15 is still excluded the same way but the denominator is larger).

**Finer resolution did not help — the gap between CALM-CALM and
STRESS-STRESS widened slightly** (0.019 → 0.029), the opposite of what
the "coarse interval hides the sharpest leg of a crash" hypothesis
predicted. All three group averages moved down by roughly the same
small amount (~0.01–0.012), consistent with adding more checkpoints
that are, on average, no more discriminating than the ones already
sampled — not with unlocking previously-hidden signal.

## What this means for the two live hypotheses from the prior report

1. **"Crises don't share a common structural shape"** — not
   contradicted by this result; if anything, mildly supported by
   elimination, since the alternative explanation (interval coarseness)
   just failed to produce the predicted improvement.
2. **"30-minute interval was too coarse"** — **not supported.** Going
   nearly 3x finer (14 → 40 checkpoints) produced no meaningful change,
   and the small change that did occur went the wrong direction.

This does not prove hypothesis 1 correct — a genuinely different
comparison unit (not just a finer version of the same one) could still
reveal something neither interval setting can. But the specific,
cheap fix of "just sample more often" has now been tried and does not
resolve the gap.

## Not decided here

Whether to try an even finer interval (e.g. 5-minute, matching the raw
bar resolution exactly) is not recommended as a next step given this
result already shows diminishing/negative returns from finer sampling
— but is not ruled out either. No weighting, ML, or clustering
introduced. No conclusion is drawn about whether the current
discrimination level is sufficient for any purpose, because no
downstream use has been scoped.

## Decision (2026-08-14): stop here — treat this as the current ceiling

Further tuning of checkpoint interval is not pursued. The 30-minute
`EpisodeTimeline`/`compare_timelines()` implementation
(`bujji/market_understanding/timeline.py`) stands as built and tested,
with its real, measured discrimination quality documented honestly
across `PHASE_17J2_REVISIT_VALIDATION_FINDING.md`,
`PHASE_17J2_REVISIT_SWEEP_VALIDATION_RESULT.md`,
`PHASE_17J3_MARKET_EPISODE_REPRESENTATION_AUDIT.md`,
`PHASE_17J4_EPISODE_TIMELINE_COMPARISON_DESIGN.md`,
`PHASE_17J4_SWEEP_VALIDATION_RESULT.md`, and this document — not
silently declared "working." No consumer should treat `find_similar()`/
`compare_timelines()` output as validated beyond what these documents
actually measured: same-label pairs reliably score above cross-label
pairs, but the two same-label groups (STRESS-STRESS, CALM-CALM) are not
yet cleanly separated from each other. Resuming this work later (a
different comparison unit, per-dimension weighting with justification,
or accepting "STRESS" needs subdividing into sub-categories with more
internally-coherent shapes) remains open, not scheduled.
