# Phase 17J.4 — Episode Timeline Comparison: Design

**Status: DESIGN ONLY. No code.** Defines how two Episode Timelines
(17J.3's conclusion: the correct object of comparison) get compared,
without introducing weights, scoring formulas, ML, or clustering, per
the restriction carried forward from 17J.3.

---

## 1. Formalizing "Episode Timeline"

An Episode Timeline is an ordered sequence of `SituationFeatureVector`s
(the same type already built in 17J.2-revisit — 14 structure
dimensions + `vix_band`) computed at multiple checkpoints across one
session, rather than one vector at session close.

**Sourced entirely from existing infrastructure** (17J.3 §2 confirmed
this needs zero new architecture): `IntradayStructureCatalog.get()`
(17G.A) already computes one `IntradayStructureRecord` for any
`as_of_timestamp` — a timeline is simply calling it at N timestamps
instead of one, then wrapping each result through
`build_feature_vector()` (17J.2-revisit) unchanged. No new journal
wiring is strictly required for this (17J.3's journal-based approach
would give per-EVENT granularity; the checkpoint approach below is
coarser but reuses more of what's already built and tested — see §3).

## 2. Checkpoint granularity — the first real fork

**Option A — fixed wall-clock interval (recommended).** Sample every
30 minutes from session open to close: 09:15, 09:45, 10:15, ...,
15:15, 15:40 (close) — roughly 14 checkpoints for a standard session.
Every session gets a comparable, near-identical-length timeline
regardless of how many raw `MarketEvent`s it generated (a quiet day
and a volatile day both get ~14 checkpoints), so no alignment/warping
machinery is needed for the comparison step (§4).

**Option B — per-episode-transition checkpoints** (from 17J.3's
journal-based approach): a new checkpoint every time an `Episode`
actually grows or transitions state. Captures the market's own natural
rhythm rather than an arbitrary clock, but produces a DIFFERENT
checkpoint COUNT per session (a quiet day might only transition twice;
a volatile day might transition twenty times) — directly reintroduces
a sequence-alignment problem (comparing sequences of different length
needs DTW or similar, see §4's Option B).

**Recommendation: Option A.** Simpler, reuses more already-tested code
unchanged (`structure_as_of()`/`IntradayStructureCatalog.get()` calls
require no new plumbing), and sidesteps a real algorithmic complexity
(sequence alignment) that Option B would require. The 30-minute
interval itself is a first-pass, disclosed parameter — same convention
as `lookback_bars=75` — not a validated conclusion.

**Missing checkpoints handled honestly**: the first checkpoint(s) of a
session may return `None` (insufficient trailing bars for
`lookback_bars=75` right after 09:15 open) — excluded from that
timeline, never fabricated or backfilled. A timeline is simply shorter
by however many early checkpoints genuinely lack enough history.

## 3. Timeline data structure

```python
@dataclass(frozen=True)
class EpisodeTimeline:
    instrument_identity: str
    date: str
    checkpoints: Tuple[SituationFeatureVector, ...]  # ascending time, may be shorter than the nominal checkpoint count
```

No new persistence — built on demand from `IntradayStructureCatalog`/
`RealityMemoryCatalog`, same "recompute rather than cache" precedent as
every prior artifact in this thread.

## 4. Comparing two timelines — the second real fork

**Option A — position-wise exact-match-fraction, averaged (recommended).**
Since both timelines share the same fixed checkpoint schedule (§2
Option A), checkpoint *i* in timeline 1 is directly comparable to
checkpoint *i* in timeline 2 (same nominal time-of-session, e.g. both
are the "10:15" checkpoint). Reuses `compare()` (17J.2-revisit)
UNCHANGED at each aligned position, then averages the per-checkpoint
scores:

```python
def compare_timelines(a: EpisodeTimeline, b: EpisodeTimeline) -> Optional[float]:
    scores = [compare(ca, cb) for ca, cb in zip(a.checkpoints, b.checkpoints)]
    scores = [s for s in scores if s is not None]
    return sum(scores) / len(scores) if scores else None
```

Zero new modeling judgment beyond what 17J.2 already decided
(exact-match-fraction) and a simple mean — not a weighting scheme, not
a learned aggregation.

**Option B — shape-aware alignment (e.g. Dynamic Time Warping).**
Allows two sessions whose stress arrived at different relative times
(a morning crash vs. an afternoon crash) to still align on SHAPE rather
than clock position. More faithful to "does this look similar
regardless of when it happened," but is a materially more complex
algorithm, and per 17G's own repeated caution ("a similarity metric is
itself a modeling choice"), the complexity itself is a judgment call —
DTW's warping window/step-cost parameters are new decisions this
project hasn't needed to make anywhere else.

**Recommendation: Option A first.** It directly tests whether
comparing PATHS (even naively, position-aligned) fixes the
CALM-CALM-tightest / STRESS-STRESS-loosest inversion found in
`PHASE_17J2_REVISIT_SWEEP_VALIDATION_RESULT.md`, without adding a new
algorithm class. If Option A's own mandatory validation (§5) shows
timing misalignment is the dominant remaining problem (e.g., two real
crashes score low specifically because their sharpest moves land on
different checkpoints), THAT would be real, first-hand evidence
justifying Option B later — not a default assumption now.

## 5. Mandatory validation, unchanged requirement, made concrete

Before this design is treated as validated: re-run the exact same
16-date, a-priori-labeled STRESS/CALM sweep from
`PHASE_17J2_REVISIT_SWEEP_VALIDATION_RESULT.md`, using
`compare_timelines()` instead of the single-point `compare()`, and
check whether the STRESS-STRESS / STRESS-CALM / CALM-CALM ordering
inverts back to the expected direction (STRESS-STRESS tightest,
CALM-CALM loosest or at least not tightest). **This is the actual test
of whether 17J.3's diagnosis was correct** — if timeline comparison
still fails to discriminate, the problem is not "single snapshot vs.
path" after all, and that would itself be a significant, honestly-
reportable finding (matching this thread's discipline of reporting
negative results plainly, established across 17J.2's two validation
docs).

## 6. What is explicitly not introduced

No weights (every checkpoint and every dimension counts equally, by
construction of a plain mean and `compare()`'s own equal-weighting).
No scoring formula beyond the mean itself. No ML, no clustering, no
learned parameters. No prediction or strategy output — `compare_timelines()`
returns a number, same restriction as `find_similar()` before it.

## 7. Open, undecided items — not defaulted

- Exact checkpoint interval (30 minutes proposed, not validated).
- Whether `find_similar()` (17J.2-revisit) should be updated to rank by
  `compare_timelines()` instead of/alongside the single-point `compare()`
  — a real API decision, not made here.
- Early-close session days (a shorter real trading day) would produce a
  shorter nominal checkpoint schedule — handled naturally by "missing
  checkpoints excluded" (§2), but worth confirming against a real
  early-close date during validation (§5), not assumed to work.
