# Phase 17J.2 (revisit) — Similarity/Comparison Using Structure Fields

**Status: DESIGN ONLY. No code.** Revisits the held decision from
`PHASE_17J2_SIMILARITY_COMPARISON_DESIGN.md` §5, now that its own
stated precondition is satisfied.

---

## 1. Why revisiting is legitimate now, not a reopening of a closed question

The original hold (2026-08-14): *"Similarity/comparison implementation
is deferred... until 17G's own sequencing is resumed: at minimum,
Domain A... exists and is independently validated against real data
first."*

Since then, in order: `msi_price_structure`/`msi_market_structure`
(Domain-A-and-adjacent logic) were confirmed already built and reusable
(`PHASE_17GA2`), the grouping engine was fully audited
(`PHASE_17GA3`), a bridge was built feeding certified 5-minute Reality
into that logic (`PHASE_17GA4`, implemented), validated against **two**
structurally different real market regimes
(`PHASE_17GA5`), and wrapped in an explicitly Understanding-tier,
lineage-complete catalog (`IntradayStructureCatalog`, this thread's
prior turn). **The precondition is met.** This is the resumption 17G's
own roadmap anticipated, not a bypass of the hold.

## 2. What changes about the similarity design, now that structure exists

The original MVP design (Option A, deferred) proposed a feature vector
of **literal facts + simple arithmetic** (% change, basis points,
trailing realized vol) — explicitly flagged as inheriting real
spurious-similarity risk (17G §5.4) because raw levels/simple
arithmetic carry no structural grounding.

**A materially better-grounded feature vector is now possible**:
`IntradayStructureRecord`'s already-classified, already-validated
dimensions —

```
trend_state, swing_state, compression_state, expansion_state,
balance_state, structure_state,
support_state, resistance_state, breakout_state, breakdown_state,
retest_state, rejection_state, structural_balance, structure_location
```

This is much closer to 17G's own original Domain G description
("consumes the outputs of Domains A–F... performs similarity lookup
against historically-recorded situations built from the same
features") than the literal-arithmetic MVP was — comparing classified
structural states is inherently less prone to the coincidental-raw-level
matches 17G's §5.4 warned about, because two dates only match on a
dimension if the SAME underlying pure function produced the SAME
classification for both, not because two raw numbers happened to be
numerically close.

## 3. Proposed feature vector

**Structure dimensions** (from `IntradayStructureRecord`, all 14
listed above) — categorical, Understanding-tier, comparable by exact
match per dimension.

**Optionally, literal Reality facts alongside them** — VIX close level
(from `RealityMemoryEvent`, the SAME calendar date) is a real, honest,
non-derived fact; including it in a SIMILARITY feature vector does not
reverse the Reality-only guarantee on `RealityMemoryEvent` itself (that
model is untouched either way) — it is a separate, new artifact
explicitly permitted to combine Reality-tier facts with Understanding-
tier classifications, the same way `MarketRealitySnapshot` already
combines multiple Reality-tier instruments into one view without
becoming Understanding-tier itself. Whether to include it is a real
scope choice (§5), not assumed here.

## 4. The one genuine, unresolved fork: how to measure distance over categorical states

Structure dimensions are categorical, not continuous — Euclidean/cosine
distance (the original MVP's proposal) doesn't directly apply. Two
real options:

**Option A — exact-match fraction (simplest, no ordinality assumed).**
`similarity = (dimensions matching exactly) / (total dimensions
compared)`. No claim about "how different" a mismatch is — `TREND_ESTABLISHED`
vs. `TREND_NONE` counts the same as `TREND_ESTABLISHED` vs.
`TREND_EMERGING` (a near-miss). Simple, defensible, but coarser than it
could be.

**Option B — explicit ordinal distance per dimension.** Requires
manually ranking each taxonomy's states (e.g., `TREND_NONE < TREND_WEAKENING
< TREND_EMERGING < TREND_ESTABLISHED`) and computing a graded distance
per dimension, not just match/mismatch. More informative, but **is
itself a new modeling judgment for every single taxonomy value** — 14
dimensions' worth of ordinal rankings to define and defend, each one a
place a wrong assumption could quietly bias every similarity result.
17G's own words apply directly here: *"a similarity metric is itself a
modeling choice."*

**Not resolved here.** Given the real cost asymmetry (Option A: one
sentence of logic, zero new judgment calls; Option B: 14 separate
ordinal-ranking decisions, each a place to get it wrong), Option A is
the more defensible starting point — but this is stated as a
recommendation, not a decision, consistent with how every other fork
in this thread has been handled.

## 4a. Decisions (2026-08-14)

- **Distance metric: Option A, exact-match fraction.**
- **VIX included.** Since exact-match fraction requires categorical
  values, VIX close is bucketed into disclosed bands (not raw
  Euclidean distance) so it participates in the same match/mismatch
  scheme as the 14 structure dimensions: `<12` LOW, `12-15` NORMAL,
  `15-20` ELEVATED, `20-30` HIGH, `30+` EXTREME — first-pass,
  disclosed thresholds (same convention as
  `bujji.intelligence.volatility_brain.RICHNESS_RICH_THRESHOLD`), not a
  statistically calibrated conclusion.

## 5. Scope questions still open, listed rather than defaulted

- Include VIX level (Reality-tier) in the vector, or keep it
  structure-only (Understanding-tier fields exclusively)?
- Compare at a fixed daily query point (e.g., every session's 15:25
  close, mirroring the validation runs) or allow arbitrary
  intraday `as_of` queries?
- Missing-dimension handling for dates where `IntradayStructureCatalog.get()`
  returns `None` (pre-2017-07-17, or insufficient bars) — exclude the
  date from the corpus entirely, consistent with the original MVP
  design's stricter sub-option.
- Storage: on-demand recomputation (matching every precedent in this
  thread) vs. a materialized index — recompute-on-demand remains the
  default absent a stated reason to persist.

## 6. Mandatory validation, unchanged requirement

Per every precedent in this thread (17H.9, 17GA5, the original 17J.2
design): before any similarity result is trusted, run it against real,
named historical pairs and adversarially check plausibility — not just
confirm the code executes. 2020-03-23 and 2018-10-26 (both now
validated individually) are the natural first pair to test the
similarity function itself against, plus at least one deliberately
DISSIMILAR date (e.g., an ordinary, quiet session) as a negative
control — a similarity engine that can't tell a crash apart from a
quiet day has failed regardless of what its distance number says.
