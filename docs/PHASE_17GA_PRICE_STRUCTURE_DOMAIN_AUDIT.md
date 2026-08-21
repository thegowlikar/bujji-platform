# Phase 17G.A — Price Structure Domain Audit

**Status: AUDIT ONLY. No code.** This audit stops short of a design,
because it found something that must be resolved before any design is
safe to write: **"Price Structure" already exists, as both a name and a
mature, tested, currently-wired-in implementation.**

---

## 1. What Domain A was supposed to be (restated from `PHASE_17G_MARKET_UNDERSTANDING_ARCHITECTURE_REVIEW.md` §2.1)

Swing highs/lows, structure breaks (higher-high/higher-low sequences and
their failure), trend phase (trending vs. ranging), consolidation range
boundaries, range expansion/compression, multi-timeframe agreement —
with a mandatory `event_time`/`confirmed_at` bitemporal split to prevent
hindsight leakage (17G's own "single most dangerous risk in this entire
layer").

## 2. The headline finding: `bujji/msi_price_structure/` already does exactly this

Live-inspected, not assumed. `bujji/msi_price_structure/engine.py`
(dated 2026-07-25/26, well before this audit engagement started)
exposes:

```
derive_swing_state(price_events)
derive_trend_state(price_events)
derive_trend_direction_signal(price_events, trend_state)
derive_compression_state(price_events)
derive_expansion_state(price_events)
derive_balance_state(price_events)
derive_structure_state(trend_state, balance_state)
detect_contradictions(...)
```

Its own module docstring: *"the first genuine REASONING brain in the
project... produces `PriceStructureAssessment` — a first-principles
interpretation of price structure (Trend, Swing, Compression, Expansion,
Balance)... Purely descriptive. No strategy, strike,
direction-prediction, or probability-of-profit vocabulary anywhere in
this package."*

**This is Domain A's exact job description**, already built, with 409
lines of tests (`tests/test_msi_price_structure_intelligence.py`), and
**a sibling package, `bujji/msi_market_structure/`** (463 lines of
tests) doing related work under an adjacent name.

## 3. It is not dormant — it is wired into the live pipeline today

`msi_price_structure` is imported by, among others: `market_state/
trade_thesis_bridge.py`, `market_state/domain_view_adapter.py`,
`msi_strategy_selector/engine.py`, `msi_consensus/*`,
`msi_market_direction/engine.py`, `msi_strategy_selection_foundation/*`,
`live_pipeline_bridge.py`. This is the live, running Intelligence/
Strategy stack — not the frozen/paused code 17G's §5.6 described
("17C's original audit recommended a moratorium on new `msi_*`/strategy
work... No strategy work has occurred since"). That moratorium concerned
*new* work; this package predates the moratorium recommendation and has
continued operating as part of the existing live system throughout this
entire engagement.

## 4. The real, structural difference: substrate, not concept

`msi_price_structure` consumes `MarketEvent`/`Episode` objects
(`bujji.live_market_events`/`bujji.market_episode`) — a **live-cycle
detection substrate**: `MarketEvent.timestamp` is "the (current)
observation's own timestamp," `originating_observation_ids` reference
live Layer-0-style observation ids, and `EpisodeProvenance.detection_context`
is one of `LIVE`/`REPLAY`/`BATCH`.

It does **not** consume `HistoricalObservation`, `MarketRealitySnapshot`,
or `RealityMemoryEvent` — none of which existed when this package was
built. The `detection_context` field suggests the package may already be
capable of running over replayed/batch data in principle, but nothing
audited so far confirms whether `MarketEvent`/`Episode` objects can
honestly be constructed FROM the certified Historical Reality corpus
(1998/2008/2018→today) rather than only from live ticks — that is a real,
open, unanswered question, not yet investigated in this audit.

## 5. What this means for "build Domain A"

**Building a new, separately-named structural-perception component now
would be a severe duplicate-architecture violation** — exactly the class
of mistake this engagement's entire discipline (audit-first, reuse over
rebuild, no parallel storage/logic) exists to prevent, and at a larger
scale than any prior finding in this thread (17H.5's `MarketState` name
check, 17I.4's `MarketRealityTimeline` name check, 17J.0/17J.1's
"Memory" name checks — all caught *naming* collisions before writing new
code; this is a *logic* collision, a working implementation of the same
concept already exists).

17G's own roadmap, read again with this finding in hand, actually
anticipated something like this: *"17H Market Intelligence — NOT
started... This is where the existing, currently-frozen `msi_*` packages
could finally be re-pointed at real Understanding/Memory instead of
live-cycle-only inputs — ending the moratorium, deliberately, at this
specific point and no earlier."* 17G assumed the `msi_*` packages were
frozen and would be *re-pointed* once Domains A–F existed. What's
actually true is closer to the reverse: `msi_price_structure` may
already **be** functioning Domain-A-equivalent logic, just built on the
wrong (live-only, uncertified) substrate — meaning the real work, if any
is needed, is likely a **bridge** (constructing `MarketEvent`/`Episode`
objects honestly from certified Historical Reality) rather than a
**rebuild** of trend/swing/compression/expansion/balance logic that
already exists and is already tested.

## 6. What is NOT yet known, and must be investigated before any decision

- Whether `msi_price_structure`'s swing/trend/compression/expansion/
  balance **definitions** (the actual thresholds and rules in
  `config.py`/`engine.py`) are sound and reusable, or were tuned/designed
  assuming live-tick density that historical daily/5-min bars don't
  match.
- Whether `MarketEvent`/`Episode` construction has a documented contract
  that a Historical-Reality-backed adapter could honestly satisfy
  (real event_time, real originating_observation_ids back to
  `HistoricalObservation`/`RealityMemoryEvent` ids), or whether it
  structurally assumes live-only inputs.
- Whether `bujji/msi_market_structure/` (the sibling package, not yet
  read in this audit) is a distinct concept from `msi_price_structure`
  or an overlapping one — needs its own read before any naming/reuse
  decision.
- Whether the confirmation-lag/bitemporal (`event_time` vs.
  `confirmed_at`) discipline 17G required is already present in
  `msi_price_structure`'s design, partially present, or absent — this
  determines how much of 17G §2.1's "critical, non-obvious" requirement
  is already satisfied vs. still needed.

None of these are answered here — they are the actual next audit, and
answering them requires reading `msi_price_structure`/`msi_market_structure`
in full, not the partial inspection this document is based on.

## 7. Recommendation

**Do not design or build a new "Domain A" package.** The next real step
is a full, dedicated audit of `msi_price_structure`/`msi_market_structure`
— their exact definitions, their exact input contract, their
bitemporal safety — to determine whether Domain A's job is already done
and merely needs a Reality-backed input bridge, or whether a genuine gap
remains after all. This is a materially different (and larger) audit
than "design Domain A from the 17G spec," and should be scoped as its
own phase before any code is written.
