# Phase 17J.2 (revisit) — 16-Date Sweep Result

**Status: NEGATIVE RESULT, STRONGER THAN THE 3-DATE FINDING.** The
3-date validation (`PHASE_17J2_REVISIT_VALIDATION_FINDING.md`)
hypothesized 6 near-constant dimensions as the likely cause and
recommended a larger sweep before deciding anything. The sweep is now
run. It does not confirm that hypothesis, and it does not validate the
similarity engine — it invalidates it more directly.

---

## Method

16 real dates queried at session close (15:25 IST), `lookback_bars=75`,
labeled a priori (before running anything) by real-world market
character: 8 **STRESS** days (COVID crash x2, Black Monday oil crash,
Oct 2018 correction, Feb 2018 VIX spike, Russia/Ukraine invasion day,
2022 rate-fear selloff, 2024 India election-result crash) and 8
**CALM** days (ordinary sessions spread 2017–2023). All 16 produced a
valid feature vector — no data gaps.

## Result 1 — per-dimension variance across the real sample

Only **3 of 16** dimensions were actually low-variance at this scale
(≥85% same value): `balance_state` (88%), `structure_integrity` (88%),
`rejection_state` (100%). The other 13 — including the ones the 3-date
sample had flagged as constant (`compression_state`, `expansion_state`,
`breakdown_state`, `structural_balance`) — varied meaningfully (44–75%
top-value share) across 16 real dates. **The 3-date hypothesis was
wrong**: those dimensions aren't structurally constant; 3 dates was too
small a sample to see their real variance.

## Result 2 — the actual, more serious finding

```
STRESS-STRESS average similarity: 0.440
STRESS-CALM   average similarity: 0.457
CALM-CALM     average similarity: 0.549
```

**Calm days are the MOST self-similar group. Stress days are the
LEAST self-similar group — lower even than stress-vs-calm pairs.**
This is not a marginal miss; it is the metric behaving backwards from
what a usable similarity engine needs: crisis days should cluster
together more than they cluster with ordinary days, and here they do
the opposite.

## Why this is a more fundamental problem than 3 noisy dimensions

Removing the 3 confirmed low-variance dimensions would not fix this —
the ordering (CALM tightest, STRESS loosest) would very likely persist,
since it reflects something real about the underlying comparison, not
measurement noise from a handful of dead dimensions:

- **Ordinary days really are more alike than crises are to each
  other.** A quiet session's structure read (balanced, no strong
  trend, established support/resistance) is a small, repeatable
  pattern space. Crises are NOT alike to each other in the same way —
  a V-shaped single-day reversal (2020-03-23's late rally), a steady
  grinding breakdown (2018-10-26), and a gap-driven shock open
  (2022-02-24's Ukraine invasion) are genuinely different intraday
  *shapes*, even though a human would call all three "stressed
  markets."
- **A single end-of-session snapshot compresses away exactly the
  information that would distinguish these shapes.** `lookback_bars=75`
  at 15:25 close captures where the session ENDED UP structurally, not
  its path. 2020-03-23's real character (a violent drop THEN a sharp
  recovery) reads, by close, as a fairly contained/coherent structure —
  which is honest given what the data shows, but means the crash's
  defining moment (the 10:00 AM breakdown) isn't what this snapshot
  measures.

This reframes the problem: it is not "pick better dimensions or add
weighting" (the 3-date report's proposed options 1/2) — it is that
**a single end-of-day structure snapshot may be the wrong unit of
comparison for "was this a stressed session," regardless of which
dimensions or metric are used on top of it.**

## What is NOT recommended, given this result

- Do not remove the 3 low-variance dimensions and re-declare success —
  the sweep shows that isn't the actual problem.
- Do not add ordinal weighting to force STRESS-STRESS scores up — that
  would be tuning the metric to match a preconceived label, which is
  exactly the kind of "similarity metric as a smuggled judgment" risk
  17G's own §5.4 warned about.
- Do not proceed to build anything consuming `find_similar()`'s output
  as-is.

## Options going forward, genuinely open

1. **Compare intraday trajectories, not a single end-of-session
   snapshot** — e.g., structure state at multiple `as_of` points per
   session (open, midday, close) as a richer per-day signature. Larger
   scope, not attempted here.
2. **Accept that "stress" isn't a coherent single category for this
   kind of comparison** — a real possibility this sweep raises, not
   just a modeling gap: different crises may simply not share a common
   structural fingerprint, and a similarity engine's job may need to
   be redefined as "find sessions with a similar SHAPE," not "find
   sessions with a similar LABEL."
3. **Stop here and hold**, mirroring the original 17J.2 decision — this
   negative result is itself a legitimate reason to pause before
   Domain-G-equivalent work continues, the same way the first hold
   was triggered by 17G's own risk analysis rather than a failure.

**No option is chosen here.** This document reports what the sweep
found; it does not decide what happens next.
