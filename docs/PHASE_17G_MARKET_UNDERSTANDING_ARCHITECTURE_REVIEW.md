# Phase 17G — Market Understanding Architecture Review

**Status: ARCHITECTURE REVIEW AND DESIGN ONLY. No code. No implementation. No tests.**

This document audits whether Layers 0–2 (as designed through 17F.0.4) are
sufficient for Bujji to understand NIFTY the way an experienced
discretionary trader does, identifies exactly what's missing, and designs
the perception layer — Market Understanding — that must exist between
memory and intelligence.

> Think like designing the brain's sensory and memory system before
> intelligence.

---

## Part 1 — Can Layers 0–2 Reconstruct Complete Market Reality Right Now?

**No. Not because the design is wrong, but because most of it isn't built
yet, and one certification is still outstanding.** This section separates
those two very different kinds of "no."

### 1.1 Current build state, stated plainly

| Layer | Designed? | Implemented? | Populated? |
|---|---|---|---|
| Layer 0 (Raw Observation Store) | Yes (17D) | **Yes** (17E) | **No — empty.** Nothing has been captured yet; websocket certification hasn't run, no collector is live. |
| Layer 1 (Time Series) | Yes (17F) | Schema exists (`market_timeseries`, Phase 15Q) but **candle provenance/lineage fields not yet added** (17F.0 spec) | No — zero production `.db` file exists |
| Layer 2 (Market Memory) | Yes (17F §4) | **Not built** | N/A |

So the honest answer to "can Bujji reconstruct why NIFTY behaved a
certain way at 10:30 AM on a specific day" is: **not today, for any day,
because nothing has been recorded yet** — this is a capture-timeline
problem, not an architecture problem. The more useful question is: *once
capture starts and runs for a while, will the architecture be sufficient?*
Answered item-by-item below, against your exact example.

### 1.2 Item-by-item: what the architecture *would* be able to answer, once built and running

| Requirement | Status once Layers 0–2 are implemented and running | Gap |
|---|---|---|
| Previous day structure (prior session's H/L/close, key levels) | **Yes** — Layer 1 daily candles + Layer 2 price-level visit history | None, once backfilled |
| Weekly/monthly structure | **Yes, if backfilled deep enough** | Backfill depth was never decided (flagged again below, §3) — a 2-week backfill cannot answer a monthly-structure question no matter how good the architecture is |
| Important support/resistance | **Partially** — Layer 2's price-level visit memory (17F.4) gives touch counts, rejection/acceptance measurements, and forward outcomes. **What it does not give is the level itself as a *structural* concept** (a swing high, a range boundary) — Layer 2 works in fixed price bands, not trader-meaningful pivots. This is exactly the gap Part 2's Domain A closes. |
| Demand/supply zones | **No — does not exist at Layer 0–2 at all.** This is a genuinely new domain (Part 2, Domain B), not a materialization of something already captured. |
| Liquidity pools (equal highs/lows, stop clusters) | **No — new domain** (Part 2, Domain C). Layer 0 has the raw depth facts; nothing yet interprets them into pools. |
| Volume behaviour | **Yes, raw** — Layer 1 candle volume, Layer 2's `volume_during`/`volume_vs_trailing_median` per interaction. Interpretation ("is this accumulation") is explicitly a Layer 3+ judgment, correctly deferred. |
| Volatility state | **No — blocked, not just undesigned.** India VIX has **no certification artifact** at all — confirmed by direct inspection of `market_reality/certification.py`'s `INSTRUMENT_TYPE_TO_CERT_KEY`, which has no entry for `INSTRUMENT_INDEX`. VIX cannot even be written to Layer 0 until it is certified. This is a concrete, immediate gap (§3). |
| Option positioning | **Partially** — Layer 0/1 can hold full chain snapshots (OI, volume, top-of-book) once captured; **positioning as a narrative** ("dealers are net short gamma," "call writers defending 24,500") is Domain F territory and does not exist yet, and per the Broker Reality Contract (17F.0.4 §2), cannot be *proven* from FYERS data — at best inferred, and inference here is a genuine causal-reasoning risk (§5). |
| Futures positioning | **Yes for the raw facts** (OI change, volume) once depth-polling cadence is decided (still undecided, §3) | Cadence decision |
| Acceptance vs. rejection | **No — new domain** (Part 2, Domain D — Auction Market Understanding). Layer 2's `interaction_outcome` (17F.4) is the closest existing concept but is a simpler, price-only measurement, not a volume-weighted auction-theory concept. |
| Genuine vs. false breakout | **No — new domain**, and one of the harder ones. Requires Domain A (structure) + Domain C (liquidity, since false breakouts are frequently liquidity sweeps) + Domain D (acceptance/rejection) working together. Nothing currently in the architecture attempts this, correctly — it's a synthesis judgment, not a raw or memory fact. |

### 1.3 The headline finding

**Layers 0–2, as designed, are the correct foundation but are not
sufficient by themselves — and they were never meant to be.** They answer
"what happened, factually, and how often" (Memory). They do not and
should not answer "what does this mean, structurally" (Understanding) —
that's a distinct cognitive layer a professional trader also has, sitting
between raw memory and decision-making. Its absence is not a defect in
17E/17F; it's the next, not-yet-designed phase. **That is precisely what
this document now designs.**

---

## Part 2 — The Market Understanding Layer (Layer 3)

### 2.0 What this layer is, and the one rule that governs everything in it

**Market Understanding is perception, not prediction.** A professional
trader looking at a chart doesn't compute a moving average — they *see*
"the market made a higher low and broke structure." That seeing is
already an interpretation (Layer 0 recorded no such thing — it recorded
prices), but it is a **backward-looking structural classification of what
already happened**, not a forward-looking bet on what happens next. This
layer produces exactly that kind of label — never a signal, a score, a
prediction, or a directional bias field.

**The governing test, applied to every field this layer ever produces:**
*does this describe something that has already, observably occurred in
the stored data, or does it assert something about the future?* The
former belongs here. The latter — always — belongs to Layer 4+
(Intelligence/Strategy), which does not yet exist and is not being built.

**Every output of this layer is DERIVED, never RAW**, and must carry full
lineage back to the Layer 0 observation ids and Layer 1/2 facts it was
computed from — reusing `epistemics.Lineage` and the `calc_version`
discipline already established for candles (17F.0). This is not optional
polish; §5 explains exactly what breaks if it's skipped.

### 2.1 Domain A — Price Structure Intelligence

**What it perceives:** swing highs/lows, market structure breaks
(higher-high/higher-low sequences and their failure), trend phase
(trending vs. ranging), consolidation range boundaries, range
expansion/compression, and whether multiple timeframes agree or conflict.

**Inputs:** Layer 1 multi-timeframe candles only. No Layer 2 dependency —
this domain can be built as soon as Layer 1 is populated across enough
timeframes.

**Why it's foundational to the others:** Domain B's zones are usually
anchored to structural pivots this domain identifies. Domain D's
acceptance/rejection concept is most meaningful *relative to* a
structural level from here. This is very likely the first domain to
build, not because the user's ordering implied it, but because the other
domains' outputs would otherwise have nothing to anchor to.

**The critical, non-obvious design requirement — confirmation lag.** A
swing high is, by definition, only recognizable after enough subsequent
bars exist to confirm the prior bar was indeed the local peak. This means
every structural label needs **two timestamps**, not one: the
`event_time` of the pivot itself, and a `confirmed_at` time — when enough
subsequent data existed for the label to be assigned. A bitemporal query
for "what did Bujji understand at 10:30" must **never** see a swing high
whose `confirmed_at` is later than 10:30, even though the pivot's own
`event_time` is earlier. This is the single most dangerous hindsight-leak
vector in this entire design (elaborated in §5.3) and must be a hard
schema requirement, not a convention.

### 2.2 Domain B — Supply/Demand Memory

**What it perceives:** zones where price previously reacted sharply
(candidate "institutional reaction" zones — named cautiously, see below),
zones never revisited, zones tested repeatedly, and whether a zone's
apparent strength is decaying with each retest.

**Inputs:** Layer 2's `price_level_visits` (17F.4) directly — this domain
is close to a re-interpretation of Layer 2's raw visit facts through a
structural lens (Domain A's pivots) rather than a new data channel.

**A naming caution, stated explicitly:** "institutional reaction zone" is
a *hypothesis about who caused a move*, which nothing in this data source
can verify — see §5.5 (correlation vs. causation). This domain should
name zones by their **observed behaviour** ("a zone that produced a sharp
reversal on N of M tests, average reaction magnitude X") — never by an
attributed cause. The zone's strength decay across repeated tests is a
measurable, defensible fact (declining reaction magnitude across
successive visits, from Layer 2's own outcome linkage); "why" it decays
is not something this layer may assert.

### 2.3 Domain C — Liquidity Intelligence

**What it perceives:** equal highs/lows (candidate stop clusters), prior
significant highs/lows as liquidity references, futures order-book depth
patterns, and liquidity withdrawal (a depth level's size collapsing
between consecutive polls).

**Inputs:** Layer 1 candles (for equal-highs/lows detection) + Layer 0
`MARKET_DEPTH` observations directly (for futures order-book patterns).

**Hard constraint, restated from the Broker Reality Contract (17F.0.4
§4):** every liquidity observation in this domain **must** be scoped to a
named derivative instrument — "NIFTY Futures liquidity withdrew at level
X." There is no such thing as "NIFTY liquidity" (the index has no book).
Any implementation of this domain that produces an unscoped liquidity
field is a contract violation, not a stylistic issue, and should fail
review on that basis alone.

**A genuine, currently-open dependency:** option-side liquidity pools
(equal strikes with unusually high OI, say) depend on per-strike option
depth, which is `UNKNOWN_PENDING_CERTIFICATION` (17F.0.4 §1). This part of
the domain is correctly blocked, not incompletely designed.

### 2.4 Domain D — Auction Market Understanding

**What it perceives:** acceptance (price spending sustained time/volume
at a level, implying agreement on value) vs. rejection (price rapidly
reversing away, implying disagreement), balance (range-bound, two-sided
trade) vs. imbalance (directional, one-sided), and value migration (where
the market's "fair value" area has moved session over session).

**Inputs:** Layer 1 candles (price+volume) + Domain A's structural levels
+ Layer 2's existing `interaction_outcome` primitive (17F.4), which this
domain extends rather than replaces — Layer 2's version is a simpler,
price-only rule; this domain's is volume-weighted and structurally aware.

**This is one of the two hardest domains to build honestly** (the other
is F). "Acceptance" implicitly claims to know *why* the market stayed —
that's an inference from *how long and how much volume* it stayed, not a
directly observed fact. The domain must report the measurement (time
spent, volume-weighted price distribution across the level) and the
classification under an explicit, versioned rule (exactly as Layer 2's
`interaction_outcome` already does, per 17F §4.2) — never a bare
adjective.

### 2.5 Domain E — Volatility Intelligence

**What it perceives:** volatility regime (expanding/contracting/stable),
whether current realized movement is abnormal relative to recent history,
and expected-movement context.

**Inputs:** India VIX (once certified — currently blocked, §3), realized
volatility computed from Layer 1 candle returns, and (once available)
option premium behaviour from Domain F.

**Explicit boundary:** this domain may compute **realized volatility**
(a statistical measure of past price dispersion — this is arithmetic over
stored prices, analogous to the already-approved `oi_change`-from-two-OI-
snapshots pattern) but must **never** compute or store implied volatility
— IV remains permanently forbidden at every layer, per the standing rule
first established at Layer 0 (17E) and restated at every subsequent
phase. IV, if ever computed, belongs to a distinct, explicitly-modeled
Layer 4 component with its own `calc_version`, not folded silently into
"volatility intelligence."

### 2.6 Domain F — Derivatives Reality (Interpreted)

**What it perceives:** futures OI/price joint behaviour (e.g., "OI rose
while price rose" — reported as the joint fact, deliberately not named
"long buildup," see §5.5), option chain strike concentration and its
evolution, and premium behaviour relative to underlying movement.

**Inputs:** Layer 0 `MARKET_DEPTH` (futures OI) + `OPTION_CHAIN` snapshots
directly, plus Domain E's realized volatility for premium-behaviour
context.

**"Dealer positioning (only if inferable)" — audited explicitly, per your
request.** It is not reliably inferable from this data source. Dealer
positioning inference in practice requires either (a) knowing which side
initiated each option trade (aggressor data — permanently unavailable,
17F.0.4 §2), or (b) a options-market-maker delta-hedging model applied to
OI changes, which requires IV/Greeks as *inputs*, not just OI — pushing
this squarely into speculative-model territory this project has
repeatedly and correctly refused to build without direct verification.
**Recommendation: do not build a dealer-positioning field.** Report OI
and premium changes as joint raw facts; let Layer 4+ (if ever) attempt
the inference explicitly, visibly, and separately, never disguised as
observed reality.

### 2.7 Domain G — Market Memory (Situational Recall)

**What it perceives:** "when NIFTY behaved like this before, what
happened" — the closest domain to what most people mean by "trading
experience."

**This domain is deliberately last, and deliberately thin at first.** It
consumes the *outputs of Domains A–F* (structural state, zone context,
liquidity state, auction state, volatility regime, derivatives posture)
as a feature vector describing "the current situation," and performs
similarity lookup against historically-recorded situations built from the
same features.

This is exactly the "Similarity Index" already named and deliberately
deferred in the original Market Memory design (17C §4.2), for the same
reason restated here: **a similarity metric is itself a modeling choice**,
and it is the one domain in this entire layer with real risk of smuggling
a judgment into what's supposed to be perception. It must be built last,
after Domains A–F are validated individually (each is independently
checkable against raw data; a similarity index is not, until the features
feeding it are trustworthy), and its acceptance criteria should require
the same adversarial scrutiny this project applies to certification
findings — multiple independent "does this actually look similar"
sanity checks against real historical pairs, not just a distance-metric
implementation matching its own math.

---

## Part 3 — What Must Be Captured Now, Before It's Too Late

Classified per your instruction — not "capture everything," but exactly
what professional-grade understanding requires, with source/frequency/
layer for each.

### MUST HAVE

| Item | Why needed | Source | Frequency | Layer |
|---|---|---|---|---|
| Multi-timeframe NIFTY spot candles (1m/5m/15m/1H/D/W/M) | Domain A is the foundation of nearly everything else; without weekly/monthly bars, "monthly structure" is definitionally unanswerable | Tick/quote aggregation (live) + historical backfill (REST) | Continuous (live) / one-time+ongoing (backfill) | Layer 1 |
| Historical backfill depth — **a number must be chosen** | Weekly/monthly structure needs months to years of history; this was flagged as an open review question in 17E's implementation plan and **was never resolved** | FYERS REST `historical` | One-time | Layer 0/1 |
| NIFTY Futures depth (5-level book + OI), regular cadence | The *only* source of futures OI (17F.0.4 §1) and the *only* real liquidity-observable NIFTY-linked instrument (§4) — Domains C, D, F all depend on this | `depth()` REST poll | **Undecided — must be set before capture starts** (repeated finding, 17E §8, 17F.0.3 §11, 17F.0.4 §9) | Layer 0 |
| Full option chain snapshots, wide strike band | Domain F, and Domain E's premium-behaviour context, both need chain history, not point-in-time snapshots | `optionchain()` REST poll | Needs an explicit cadence decision (not yet made) | Layer 0 |
| **India VIX certification** | Domain E cannot exist without it, and it is currently **not even certifiable** — `market_reality/certification.py` has no `INSTRUMENT_TYPE_TO_CERT_KEY` entry for `INSTRUMENT_INDEX` | New certification artifact required (extends the 17A.5 framework) | Daily/continuous once certified | Certification prerequisite, then Layer 0 |
| Trading holiday/session calendar | Weekly/monthly structure and session-phase context (Domain A/D) are wrong without it — flagged repeatedly since Phase 16 audits, still missing | Reference data, not a market observation | Static, low-frequency updates | Reference data, outside Layer 0 (per 17F.0.3 §7) |
| Websocket certification (tick-level capture) | Domain A's finer-timeframe structure and Domain C's liquidity-withdrawal detection both benefit materially from tick resolution over REST-poll resolution | Operator action, market hours | One-time (plus periodic re-certification) | Certification prerequisite, then Layer 0 |

### NICE TO HAVE

| Item | Why useful, not essential | Notes |
|---|---|---|
| Per-strike option depth (beyond top-of-book) | Would sharpen Domain C's option-side liquidity pools | Status `UNKNOWN_PENDING_CERTIFICATION` (17F.0.4 §1) — worth a narrow, cheap certification attempt, but nothing in Domains A–E blocks on it |
| Multiple futures contract months (term structure) | Could inform Domain E's volatility-regime context with futures curve shape | Only the near-month contract has ever been tested; not currently in scope |
| Cross-asset context (Bank Nifty, sector indices, global cues) | A professional trader does use these | Entirely outside the current broker-contract scope; would need its own certification track — genuinely deferred, not urgent |

### IMPOSSIBLE

| Item | Why | Consequence for the design |
|---|---|---|
| Trade prints | Not present at any FYERS endpoint audited (17F.0.4 §2) | No domain may claim to observe individual executions |
| Aggressor side (buy/sell-initiated) | Same root cause | Domain D's "acceptance/rejection" must be inferred from time+volume-at-level, never from trade-side data, because none exists |
| Reliable dealer positioning | Requires aggressor data or IV/Greeks-based hedging models, both unavailable/forbidden | Domain F must not build this field (§2.6) |

---

## Part 4 — Dependency Order

Your proposed chain:

```
Raw Reality → Time Series → Market Memory → Market Understanding
            → Market Intelligence → Strategy Selection → Trade Execution
```

**This order is correct**, and it matches what was already independently
derived across 17C and 17F — this document is not revising the chain, it
is filling in the layer this ordering left unnamed.

**Why Memory before Understanding, specifically (the one non-obvious
ordering choice, worth justifying rather than just asserting):** Layer
2's price-level memory (17F.4) uses fixed-width price bands — a dumb,
robust, non-judgmental indexing scheme that requires no structural
interpretation to build. Domain A's swing/structure detection could in
principle be built directly on Layer 1 without Layer 2 at all — but
Domains B and D explicitly *consume* Layer 2's visit/outcome facts
(§2.2, §2.4). Building Memory first means Understanding has real,
already-validated historical facts to interpret, rather than having to
invent its own historical bookkeeping. The order is correct because
Memory is simpler, more mechanically verifiable (per-visit facts, testable
against raw data directly), and a true prerequisite for at least two of
the seven Understanding domains — not merely first because it happens to
be layer 2.

**Internal ordering within Layer 3 itself** (not specified by your chain,
but necessary): Domain A first (foundational to B and D). B, C, E, F can
proceed largely in parallel once A exists — they have no dependencies on
each other. D depends on A. G is last, strictly, for the reasons in §2.7.

**The one existing violation of this chain, restated from 17C:**
`bujji/context_window/` is already built, and is founded on
`IntelligenceCycleRecorder`'s per-cycle *output* — i.e., it is a "Context"
component sitting on top of Intelligence, inverting the intended
Memory → Understanding → Intelligence direction. This was flagged and
deliberately not fixed in 17C, on the grounds that it does no active harm
today. **It remains unfixed. It should not be allowed to become the model
for how Domain G or any Understanding-layer component gets built** — a
future implementer reaching for `context_window` as a pattern to copy
would be reproducing the exact inversion this whole architecture exists
to avoid.

---

## Part 5 — Dangerous Mistakes to Audit Against

Each risk, made concrete against this specific design rather than left
abstract.

### 5.1 Derived data pretending to be raw data

**Concrete risk in this phase:** a Domain A "swing high" field or a
Domain D "acceptance" flag, if stored without lineage, would be
indistinguishable from a Layer 0 fact to anything reading it later.
**Mitigation, mandatory:** every Layer 3 output carries `calc_version`
and `source_observation_ids`/`source_fact_ids` back through Layer 1/2 to
Layer 0, exactly as already specified for candles (17F.0) — no exception
for "it's obviously derived, everyone will know." Everyone will not know,
six months and three engineers later.

### 5.2 Indicators replacing understanding

**Concrete risk:** Domain A's "market structure break" is one threshold
rule away from being a plain trend-following indicator with extra words.
**Mitigation:** the governing test in §2.0 — does this describe something
that already, observably happened, or predict something? A structure
break, correctly implemented, reports *"price closed beyond the prior
swing high, confirmed at time T"* — a fact about history. It becomes an
indicator the moment it also asserts *"…therefore expect continuation"* —
which this layer must never do, and which should be an explicit review
checklist item for every domain's implementation.

### 5.3 Hindsight leakage

**The single most dangerous risk in this entire layer**, because
structural labels are *inherently* retrospective — a swing high cannot be
known until after it happens, by definition. §2.1's `confirmed_at`
requirement is the mitigation, and it must be non-negotiable: **any
Layer 3 fact usable in a bitemporal query must carry the time it became
knowable, not just the time of the event it describes.** This applies to
every domain, not just A — Domain B's zone-strength-decay claim, Domain
D's acceptance classification (which may need several subsequent bars to
confirm "sustained" time-at-level), all have the same structure.

### 5.4 Overfitting historical patterns

**Concrete risk:** Domain G's similarity index, if built early or
without discipline, will find "similar" situations that are actually
coincidental — high-dimensional feature spaces make spurious similarity
easy to manufacture and hard to notice. **Mitigation:** already specified
in §2.7 — build it last, validate features individually first, and
require the adversarial-verification discipline this project already
applies elsewhere (independent checks trying to *refute* a claimed
similarity, not just confirm it).

### 5.5 Confusing correlation with causation

**Concrete risk, already surfaced twice above (§2.2, §2.6):** "long
buildup" (OI up + price up, narratively implying new longs are driving
the rally) is a causal story bolted onto a correlation of two raw
deltas. Open interest rising alongside price rising is equally consistent
with new longs entering *or* short covering by a different set of
participants *or* unrelated hedging flow — this data source cannot
distinguish those. **Mitigation:** Domain F reports the joint fact ("OI
delta: +X%, price delta: +Y%, over window W") and stops there. Any
causal-sounding label is a Layer 4+ hypothesis, not a Layer 3 observation,
and must be built (if ever) as a separately-versioned, explicitly-labeled
model — never presented with the same confidence as a directly observed
fact.

### 5.6 Building strategies before perception

**Status: correctly not violated, so far, across this entire engagement.**
17C's original audit recommended a moratorium on new `msi_*`/strategy
work until Layers 0–2 pass validation. No strategy work has occurred
since. **This document's own recommendation (Part 6) is to extend that
moratorium through Market Understanding as well** — the same reasoning
that justified pausing before Memory applies with equal force to pausing
before Intelligence: a strategy built on top of Understanding domains
that haven't themselves been validated against real data would repeat
exactly the mistake this whole Phase-17 arc exists to correct, one layer
higher up.

### 5.7 Storing only what Bujji trades instead of what the market does

**Status: correctly addressed by existing design, worth restating.**
17C explicitly distinguished `market_memory` (what the market did) from
`outcome_memory` (what *we* did) precisely to prevent this — Layer 2 and
the Understanding domains above must be populated from **every** observed
market situation, independent of whether Bujji held a position at the
time. A Domain G similarity index seeded only from traded situations
would be a biased sample by construction, and would systematically miss
exactly the situations Bujji most needs to learn about — the ones it
didn't recognize well enough to trade.

---

## Part 6 — Final Recommendation

### Should Bujji build strategies now, or complete Market Reality + Understanding first?

**Complete Market Reality + Market Understanding first. This is not a
close call**, for reasons already established and reconfirmed by this
audit:

1. Layers 0–2 are designed but **largely unimplemented and entirely
   unpopulated** — there is currently no historical data for any strategy
   to be validated against, regardless of what layer sits above it.
2. The Understanding layer this document designs **does not exist at
   all** — every one of the seven domains is a genuine gap, not a
   materialization of something already captured.
3. Two concrete, currently-open items (VIX certification, backfill depth)
   block entire domains (E, and effectively A/B for anything beyond
   recent history) outright — no strategy work can outrun these; the data
   simply won't exist to support it.
4. The project's own standing moratorium (17C) already established this
   principle correctly; this audit finds no reason to weaken it and one
   more reason (the newly-designed Understanding layer) to extend it.

### Architecture roadmap

```
17F.0.1   Websocket certification              operator action, market hours — outstanding
17F.0.4a  VIX certification                     NEW — extends the certification framework to INSTRUMENT_INDEX for VIX specifically
17F.1     Materializers                          candle provenance/lineage, futures stats, option chain, price-level
                                                  memory — per existing 17F design; gated on the REPLAY_VERIFIED proof
17F.2     Historical backfill                    depth decision required first; populates Layer 1/2 with real history
17G.A     Domain A — Price Structure             build first; foundational to B and D
17G.BCEF  Domains B, C, E, F                      parallelizable once A exists; each independently validated against
                                                  raw data before integration
17G.D     Domain D — Auction Understanding        depends on A
17G.G     Domain G — Market Memory (similarity)    last; adversarially validated; the one domain with real overfitting risk
─────────────────────────────────────────────────────────────────────────
17H       Market Intelligence                    NOT started. Only begins once 17G is validated. This is where the
                                                  existing, currently-frozen msi_* packages could finally be re-pointed
                                                  at real Understanding/Memory instead of live-cycle-only inputs —
                                                  ending the moratorium, deliberately, at this specific point and no
                                                  earlier.
17I+      Strategy Selection / Execution         Already exist, remain frozen. Untouched by this document.
```

**The moratorium on strategy/indicator/signal work remains in force
through 17H.** Nothing in this roadmap authorizes starting it earlier.

### What Bujji will and will not be, at the end of this roadmap

At the end of 17G, Bujji will have — for every moment in its captured
history — a structural read of price (Domain A), zone context (B),
scoped liquidity awareness (C), auction state (D), volatility regime (E),
and derivatives posture (F), each traceable back to the exact raw
observations it came from, each honest about what it does and doesn't
know, and a memory (G) that can retrieve genuinely similar past
situations rather than superficially similar ones.

That is not a trading system. **It is the sensory and memory apparatus a
trading system would need before it could be trusted to reason about a
market at all** — which is exactly what was asked for, and exactly what
should be built next, in this order, before anything resembling a
strategy.

---

## Gate Status

| Gate | Status |
|---|---|
| Layers 0–2 sufficiency audit (Q1) | **Complete** — Part 1; foundation correct, largely unbuilt, one certification gap |
| Market Understanding Layer design, 7 domains (Q2) | **Complete** — Part 2 |
| Must-have / nice-to-have / impossible capture audit (Q3) | **Complete** — Part 3 |
| Dependency order verification (Q4) | **Confirmed correct**, with internal Layer-3 ordering added — Part 4 |
| Dangerous-mistake audit (Q5) | **Complete**, 7 risks each made concrete to this design — Part 5 |
| Final recommendation + roadmap (Q6) | **Complete** — Part 6: complete Reality + Understanding before any strategy work |
| Review | **Pending — awaiting operator decision on VIX certification and backfill depth, the two items now blocking the most domains** |
