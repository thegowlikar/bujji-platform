# Phase 17J.2 — Similarity/Comparison Design

**Status: DESIGN ONLY. No code.** This document surfaces a real,
material finding before proposing anything: this exact capability was
already designed once, in detail, under a different numbering scheme,
and that prior design disagrees with the pace this conversation's 17J
thread has been moving at.

---

## 1. Prior art found: this is already "Domain G" in `PHASE_17G_MARKET_UNDERSTANDING_ARCHITECTURE_REVIEW.md`

That document (design-only, never implemented — confirmed: no
`bujji.market_understanding` package or Domain A–F code exists anywhere
in this repo) designed a full Understanding layer, Domains A–F
(structure, zones, liquidity, auction, volatility regime, derivatives
posture), with **Domain G — Market Memory (Situational Recall)**
explicitly positioned **last**:

> "This domain is deliberately last, and deliberately thin at first. It
> consumes the *outputs of Domains A–F* ... and performs similarity
> lookup against historically-recorded situations built from the same
> features ... It must be built last, after Domains A–F are validated
> individually ... and its acceptance criteria should require the same
> adversarial scrutiny this project applies to certification findings."

The document names the exact risk this matters for (§5.4, verbatim):
"Domain G's similarity index, if built early or without discipline,
will find 'similar' situations that are actually coincidental —
high-dimensional feature spaces make spurious similarity easy to
manufacture and hard to notice."

**This is a direct, material disagreement with the pace of this
conversation's own 17J thread**, which has moved from Reality (17I) to
literal-facts Memory (17J.1) and is now being asked to design
similarity (17J.2) — skipping the Domain A–F structural/zone/liquidity/
auction/volatility-regime/derivatives-posture layer entirely. 17G's own
numbering (`17G.A` → `17H` Intelligence → `17I+` Strategy) was
superseded by the actual numbering this engagement used instead (17H
became Historical Reality, 17I became Live Reality expansion, 17J is
this thread) — the phase *numbers* in that document are stale, but its
**risk analysis and sequencing argument are not**, and nothing since has
addressed or overturned them.

## 2. What exists today vs. what 17G's Domain G assumed as input

| 17G's Domain G expects | What actually exists (2026-08-14) |
|---|---|
| Domain A output: structural pivots, trend phase, range boundaries | Does not exist |
| Domain B output: zone context | Does not exist |
| Domain C output: liquidity pools | Does not exist |
| Domain D output: auction acceptance/rejection | Does not exist |
| Domain E output: volatility regime (realized-vol-based, IV forbidden) | Does not exist |
| Domain F output: OI/price joint facts, premium behaviour | Does not exist |
| A validated, classified feature vector per historical date | Does not exist |
| **What DOES exist**: `RealityMemoryEvent` — literal OHLC/VIX/OI facts only, zero classification, zero computation (17J.1) | ✅ |

A similarity engine built today would necessarily compare **raw levels
and simple arithmetic over them** (VIX close, % change, futures basis),
not the richer, already-interpreted structural feature vector 17G's
design assumed. This is not a disqualifying gap — but it means anything
built now is a **materially thinner, earlier-stage capability** than
what "Domain G" was designed to be, and should not be presented or
named as though it were the same thing.

## 3. The fork, stated plainly

**Option A — Build a narrow MVP now, over literal facts + simple
arithmetic, explicitly labeled as a prototype/stepping-stone.**
Pragmatic, usable immediately against the 28-year Reality corpus
already certified. Real cost: inherits exactly the spurious-similarity
risk 17G's own §5.4 warns about, since there's no structural/regime
classification to ground the comparison in anything beyond raw levels.
Mitigable but not eliminated by disciplined feature selection (§5 below)
and mandatory adversarial validation before trusting any result.

**Option B — Hold similarity/comparison, resume 17G's original
sequence.** Scope 17J.2 down to *design only* (which this phase already
is) and explicitly defer implementation until at least a first
structural domain (17G's Domain A — Price Structure, "foundational to
the others," per that document) exists and is independently validated.
This respects the prior design's own stated risk analysis and
sequencing argument, at the cost of real elapsed time before any
similarity capability exists at all.

Neither is assumed here. This is precisely the kind of fork this
engagement has resolved via an explicit decision before, not a silent
default (17H.6's Reality-only decision, 17J.0's Reality-only decision).

## 4. If Option A is chosen — MVP design (not yet approved, not yet built)

### 4.1 Feature vector — literal facts + simple, non-lookahead arithmetic only

| Feature | Computed from | Lookahead-safe? |
|---|---|---|
| VIX close (raw) | `RealityMemoryEvent.vix_close` | Yes — literal fact |
| Spot 1-day % change | `(today.spot_close - yesterday.spot_close) / yesterday.spot_close` | Yes — only uses data at/before the target date |
| Futures basis (points) | `futures_close - spot_close`, same date | Yes — same-date literal facts |
| Spot N-day realized volatility (stdev of daily returns) | Trailing window of `RealityMemoryEvent.spot_close` values ending at the target date | Yes, IF the window is strictly trailing (never includes a future date) |
| Distance from N-day high/low | Trailing window max/min of `spot_close` vs. today's close | Yes, same trailing-window discipline |

**Explicitly excluded, per every standing rule in this project (17E
onward, restated in 17G §2.5's own "IV forbidden" clause)**: implied
volatility, any indicator (RSI/EMA/MACD/Supertrend), any label
("trending," "bullish"), and — new to this phase, stated because it is
the single most dangerous mistake a similarity engine could make —
**any feature computed from data AFTER the target date.** A "5-day
forward return" is an Outcome-tier concept (already exists elsewhere in
this project, Phase 15J's Outcome Attribution) and must never appear in
a feature vector used for retrieval, or every similarity match becomes
a leaked answer rather than a genuine comparison.

### 4.2 Missing-dimension handling

Mirrors `volatility_brain.py`'s existing discipline ("reports None
explicitly, rather than fabricating a number"): a date missing VIX
(pre-2008) or missing futures (pre-2018) contributes `None` on that
dimension. Two sub-options, needing their own explicit decision, not
assumed:
- Exclude that dimension from the distance calculation for that
  specific comparison (asymmetric, but never fabricates).
- Exclude the candidate date entirely if it's missing any dimension in
  the current feature vector (simpler, more conservative, shrinks the
  usable historical corpus for dates before VIX/futures existed).

### 4.3 Distance metric

Standardized (z-score, computed over the full available historical
distribution of each feature) Euclidean or cosine distance across the
chosen dimensions. Normalization choice (z-score vs. percentile rank)
is itself a modeling decision — 17G's own point in §2.7 ("a similarity
metric is itself a modeling choice") — and should be picked
deliberately, not defaulted.

### 4.4 Storage — first durable Understanding-tier computed value in this engagement

Per 17J.0 §4's Option B analysis: this is the first computed/derived
value ever durably persisted above Reality here. Needs its own new
package and store — proposed `bujji/reality_similarity/` (not
`bujji.market_understanding`, to avoid falsely implying this satisfies
17G's fuller Domain A–F design), holding a `SituationFeatureVector` per
date with full `calc_version` + lineage back to the `RealityMemoryEvent`
rows it was computed from — reusing `epistemics.Lineage`'s existing
pattern, per 17G §2.0's own lineage requirement.

### 4.5 Mandatory validation before trusting any result (17G §2.7/§5.4's own bar)

Before this MVP is treated as usable for anything downstream: run it
against the exact three historical pairs already discussed in this
conversation — 2020-03-23 (COVID crash), 2018-10-26 (October 2018
selloff), 2015-08-24 (China crash selloff) — and have a human
adversarially check whether the returned "similar" dates are genuinely
comparable or coincidental matches on a thin feature set. A distance
metric that returns a mathematically-close but substantively
nonsensical match (e.g., a low-volume holiday-adjacent day matching a
crash purely because both have a small feature-vector norm) is a real
failure mode this specific check exists to catch.

## 5. Recommendation

Given the real, cited risk in 17G §5.4 and the fact that nothing has
changed since that document was written to reduce it (no structural
domain exists yet), **Option B (hold, resume 17G's sequence) is the
more defensible default** — but Option A is a legitimate, real choice
if the priority is a usable capability now over the more rigorous
original sequencing.

**Decision (2026-08-14): Option B — hold.** Similarity/comparison
implementation is deferred. No `bujji/reality_similarity/` package, no
`SituationFeatureVector`, no distance metric is built in or after this
phase until 17G's own sequencing is resumed: at minimum, Domain A
(Price Structure — "foundational to the others," per 17G §2.1) exists
and is independently validated against real data first. This phase
(17J.2) ends as design-only, matching 17J.0/17J.1's own audit-before-code
discipline, with the additional finding that the *right* next
structural step is Domain A, not similarity, per the prior-art sequence
this document surfaced.
