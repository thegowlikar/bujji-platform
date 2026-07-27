# Directional Ownership Investigation (DOI v1) — Engineering Series 84

Status: INVESTIGATION ONLY. No code, schema, or field changes made anywhere
in this sprint. This document is the sole artifact produced.

## 0. Method

Every claim below was verified by reading the actual current source at
`/opt/bujji/app` on the BUJJI host, not inferred from docstrings or prior
series' descriptions. File:line citations are given throughout.

## 1. End-to-end information audit

| Layer | File | Does directional (UP/DOWN sign) information exist? | Evidence |
|---|---|---|---|
| 73A-76 sensing (`market_observation`, `futures_observation`, `options_observation`, `live_observation`, `live_market_events`, `market_episode`) | various | Raw signed data (price deltas, OI deltas) genuinely exists at this layer as inputs, but these layers do not classify or expose a "bullish/bearish" concept themselves — they emit observations/events, not opinions. | Not the locus of the gap; the gap is in interpretation, not sensing. |
| 78 Price Structure | `bujji/msi_price_structure/engine.py:66-71` (`_sign`), `:129-149` (`derive_trend_state`) | **Sign is computed internally, then discarded.** `_sign(d)` returns +1/-1/0 per delta; `derive_trend_state` feeds only `_trailing_run_length(signs)` (the length of a same-sign streak) into the public taxonomy. The returned values — `TREND_NONE / TREND_EMERGING / TREND_ESTABLISHED / TREND_WEAKENING` — encode *persistence/strength of trend*, never *which way*. A market grinding steadily up and one grinding steadily down for the same number of bars produce the identical `trend_state` string. | `engine.py:66-71`, `:129-149` |
| 79 Market Structure | `bujji/msi_market_structure/engine.py:284-343` (support/resistance/breakout/breakdown), `:408-433` (`derive_structure_location`) | **Partially directional, inconsistently.** `LOCATION_ABOVE_RESISTANCE` (from `BREAKOUT_CONFIRMED`, line 421-422) and `LOCATION_BELOW_SUPPORT` (from `BREAKDOWN_CONFIRMED`, line 423-424) are genuinely directional events — a confirmed breakout above resistance is an unambiguous bullish structural fact, a confirmed breakdown below support is unambiguous bearish. But `LOCATION_NEAR_RESISTANCE` / `LOCATION_NEAR_SUPPORT` (lines 429-432) are purely spatial proximity facts with no directional implication attached — "near resistance" is not exposed as "likely to reject down" vs. "likely to break up"; the taxonomy makes no such call. So structure_location is a **mix**: two of its values (ABOVE_RESISTANCE, BELOW_SUPPORT) are genuinely directional signals; the rest (NEAR_*, INSIDE_RANGE, AT_RETEST) are not. |  `engine.py:408-433` |
| Legacy `bujji/intelligence/regime_brain.py` | `regime_brain.py:90-96` (`net_move`), `:120` (`_efficiency_ratio`), `:158-199` (`_classify`) | **Sign discarded, same pattern as trend_state.** Efficiency Ratio is computed as `|net directional move| / path_length` (docstring line 19-21) — the classification (`TRENDING`/`RANGING`/`VOLATILE`/`COMPRESSED`/`TRANSITIONING`) uses the *magnitude* of ER only. `net_move` (a signed quantity) is computed at line 90 but never reaches the classifier or the output regime label. Confirmed: no `TRENDING_UP`/`TRENDING_DOWN` distinction exists anywhere in this file. | `regime_brain.py:90,120,158-199` |
| Legacy `bujji/intelligence/structure_brain.py` | `:90-118` (`_classify_proximity`), OI wall logic | No directional classification exposed. `put_call_oi_ratio` (line 107) is a real, signed-adjacent skew metric (PE OI / CE OI) but is only surfaced as a raw ratio in evidence — it is never thresholded into a directional read (e.g. "put-heavy = bullish support conviction"). `proximity` output is spatial (NEAR_RESISTANCE/NEAR_SUPPORT/BETWEEN), directly analogous to 79's non-directional NEAR_* values. | `structure_brain.py:90-118` |
| Legacy `bujji/intelligence/greeks_brain.py` | `:92-131` | **This IS genuinely directional — but it is directional about the position, not the market.** `position_delta = -(delta_ce + delta_pe)` (line 101) is a real, signed number computed from live Black-Scholes deltas; `_classify_exposure` (line 128-131) returns `NET_LONG_EXPOSURE` / `NET_SHORT_EXPOSURE` / (implicitly) `DELTA_NEUTRAL` from its sign. This is real, working, signed directional computation. Its scope, however, is the live hardcoded straddle's own net delta exposure (a fact about the position BUJJI already holds), not a market-wide bullish/bearish read that could feed Strategy Selector's entry decision — reusing it for market direction would require re-deriving it from a market-representative synthetic position/ATM straddle rather than a live open position, which is a different (if related) computation. | `greeks_brain.py:92-131` |
| 81 Consensus | `bujji/msi_consensus/engine.py:69` (comment only: "a directional winner from a genuine tie") | No `direction` field on `ConsensusAssessment`. The one occurrence of "directional" in the file is a comment about tie-breaking between competing *signals*, not about market direction. | `engine.py:69` |
| 77 Decision Synthesis | `bujji/msi_decision_synthesis/engine.py:22-23,50` | `DomainSignal` (`models.py:59`) has no direction field. Explicit design note at lines 22-23: certain domain concerns are deliberately treated as "not a signal about direction" and excluded from a "directional vote" — confirming the authors of 77 were aware direction was a candidate concept and chose NOT to build it in, rather than having forgotten it. | `engine.py:22-23,50` |
| 82 Strategy Eligibility | (not separately audited in depth — no directional fields found in a targeted grep of the module) | No directional fields present. | grep across `msi_strategy_eligibility/*.py` |
| 83 Trade Intent | `bujji/msi_trade_intent/engine.py:47-119`, `taxonomy.py:65-119` | `market_bias` always returns `MARKET_BIAS_DELTA_NEUTRAL` (`engine.py:119`), explicitly and honestly documented as a stub: "neither [Decision Synthesis nor Consensus] exposes a real aggregate directional-lean field at all" (`engine.py:47-50`). This is the symptom Series 83 surfaced, not the cause — the cause is the absence of a signed directional concept at every upstream layer. | `engine.py:47-119` |

**Headline fact-check (the hypothesis's load-bearing claim):** the hypothesis
that "Price Structure's `trend_state` and Market Structure's
`structure_location` already contain directional information" is **only
half true, and the true half is not the half the hypothesis emphasized**.

- `trend_state` (78): **FALSE**. It is sign-blind by construction — `_sign()`
  is computed and immediately thrown away in favor of run-length. There is
  no raw internal sign surviving anywhere that could be cheaply exposed;
  reusing it for direction requires a genuine code change to
  `derive_trend_state`, not just exposing an existing hidden value.
- `structure_location` (79): **PARTIALLY TRUE**. Two of its ~7 values
  (`ABOVE_RESISTANCE`, `BELOW_SUPPORT`) are already genuinely directional
  facts, produced as a side effect of the breakout/breakdown confirmation
  logic. The majority of its values (proximity-based) are not directional.

This means Candidate A (Price Structure as sole owner) is **weaker** than
the hypothesis assumed — its flagship field (`trend_state`) would need real
new logic, not exposure of an existing value. Candidate B (Market
Structure) is **partially cheaper** than assumed for the breakout/breakdown
sub-case only.

## 2. Candidate ownership analysis

**Candidate A — Price Structure (78) owns direction.**
- Pro: trend concept is the most natural home for "directional lean" as a
  concept; the module already computes signed deltas internally.
- Con: `derive_trend_state` currently discards sign entirely; giving it
  direction is not "surface a hidden field," it is "add a new computation
  path" (a real schema/logic change, not a metadata exposure). Also:
  trend direction alone says nothing about proximity to a wall about to
  reverse it — a lone Price Structure signal would be blind to the
  Market Structure context that might contradict it.

**Candidate B — Market Structure (79) owns direction.**
- Pro: `LOCATION_ABOVE_RESISTANCE`/`BELOW_SUPPORT` are already genuinely
  directional and require no new logic to expose for the breakout/
  breakdown case specifically.
- Con: covers only the breakout/breakdown sub-case. The majority of real
  market states (inside a range, near but not through a level) produce
  no directional read at all under 79's current logic. A "Market
  Structure owns direction" design would need new logic for the common
  case, same cost problem as Candidate A, just for a different subset of
  states.

**Candidate C — Consensus (81) owns direction.**
- Pro: 81 already exists specifically to reconcile/aggregate signals
  from multiple domains (a proven pattern for exactly this kind of
  cross-domain synthesis).
- Con: Consensus today reconciles *coherence/contradiction* between
  existing fields, not raw price/OI data — it has no direct line of
  sight to the underlying signed deltas. Reusing it for direction means
  its upstream feeders (78, 79) must first emit *some* signed lean for
  it to aggregate. Consensus cannot manufacture direction from
  currently-directionless inputs; it can only combine directional
  inputs that don't yet exist.

**Candidate D — Decision Synthesis (77) owns direction.**
- Pro: sits centrally, already synthesizes `DomainSignal`s into an
  opportunity assessment.
- Con: 77's own code (`engine.py:22-23`) shows its authors explicitly
  and deliberately excluded certain concerns from "the directional
  vote" as a design choice, not an oversight — meaning direction was
  considered and pushed out of scope for this layer on purpose. Reversing
  that design decision needs justification beyond "77 is central."

**Candidate E — new dedicated Direction Engine.**
- Pro: clean home for a concept that (per Deliverable 3 below) is
  plausibly a small multi-domain synthesis problem in its own right,
  analogous to what Consensus already does for coherence. Isolates the
  real new logic (signed classification) from the existing modules
  rather than smuggling schema changes into 78/79 whose existing test
  suites and replay determinism guarantees would otherwise need
  re-certifying.
- Con: adds a new module/series to an already deep pipeline (78→79→81→
  77→82→83); needs its own owner discipline (determinism, replay
  fingerprinting) built from scratch rather than inherited.

## 3. First-principles definition (per `docs/MSI_V1_FOUNDATION.md`)

`docs/MSI_V1_FOUNDATION.md` Deliverable 1 distinguishes Trend, Swing,
Impulse/Correction, Compression/Expansion, Momentum/Exhaustion as
*structural* concepts, separate from options/futures positioning concepts.
Applying that vocabulary here, "directional lean" is not one concept —
real, distinct sub-concepts exist that can and do disagree:

- **Structural direction** — where price sits relative to a level
  (79's ABOVE_RESISTANCE/BELOW_SUPPORT).
- **Trend direction** — the sign of the recent persistent price walk
  (78's `trend_state`, if given a sign).
- **Momentum direction** — rate-of-change of the trend itself (not
  currently computed anywhere in the audited code).
- **Options positioning direction** — e.g. put/call OI skew
  (`structure_brain.put_call_oi_ratio`, currently unclassified).
- **Futures positioning direction** — OI buildup on long vs. short side
  (not found computed anywhere in the audited modules — `futures_observation`
  emits raw OI observations but no directional classification).
- **Auction/order-flow direction** — not present in any audited module.

**Finding:** a short-horizon structural lean (from price/market structure,
minutes-to-hours) and a longer-horizon positioning lean (from options/
futures OI, session-to-multi-day) are genuinely different signals with
different time horizons, different data sources, and a real, legitimate
possibility of disagreement (e.g., structure breaking down while OI still
shows call-heavy conviction). **The honest answer is that BUJJI needs
multiple directional concepts, not one** — and reconciling them across
domains is itself a synthesis problem structurally similar to what
Consensus (81) already does for coherence. This directly complicates,
rather than confirms, the "single owner" framing implicit in Candidates
A/B/C/D as originally posed.

## 4. Dependency analysis (future consumers)

| Future module | Directional need | Granularity needed |
|---|---|---|
| Strategy Selector | UP/DOWN/NEUTRAL lean, likely per-horizon (structural vs. positioning) | Categorical + confidence; magnitude not required for family selection |
| Strike Selection | Directional lean to choose OTM side | Categorical is likely sufficient, but a magnitude/strength scalar (not just sign) would materially improve strike distance choice |
| Position Construction | Directional lean + confidence | Categorical + confidence, to decide symmetric vs. skewed structures |
| Dynamic Adjustments / Rolling / Hedging | Change-in-direction detection over time | Needs a time series of directional reads, not a single snapshot — implies whatever owns direction must be replay-queryable historically, not just a live scalar |
| Delta Management | A market-direction lean distinct from `greeks_brain.position_delta` (which is about the position, not the market) | Categorical/signed magnitude, to know which way the *market* is leaning independent of current position exposure |

## 5. Historical validation — feasibility finding

**Not feasible within this investigation's scope, and reporting this
honestly rather than fabricating a result.** Two real facts were checked
directly on the host, without modifying any code:

1. `/tmp/m1/` on the remote host contains real NSE F&O bhavcopy CSVs
   (`BhavCopy_NSE_FO_0_0_0_2026....csv`) — genuine historical futures data
   exists.
2. `reports/historical_campaign_*.json` (multiple campaign files exist,
   e.g. `historical_campaign_broadened_corpus.json`,
   `historical_campaign_v2.json`) confirm a real historical qualification
   corpus exists for *other* parts of the pipeline.

However, per every MSI series' own Deliverable 10 pattern (confirmed by
inspecting `tests/test_msi_price_structure_intelligence.py`, which builds
its `MarketEvent` fixtures by hand rather than from any corpus loader),
**78 and 79 have never been run against the real historical corpus** —
only against hand-authored synthetic scenarios in their own unit tests.
Wiring 78/79's real public functions (`derive_trend_state`,
`derive_structure_location`, etc.) against `/tmp/m1/` bhavcopy data or the
`reports/historical_campaign_*.json` corpus would require writing new
integration/glue code to convert raw bhavcopy rows or campaign JSON into
real `MarketEvent`/`_Level` objects — which is explicitly out-of-scope
implementation work for this documentation-only sprint. No genuine
zero-code-change historical check was available, so none is reported as
a finding; doing so would have meant fabricating a result.

## 6. Existing intelligence reuse — findings

- `bujji/intelligence/regime_brain.py:90,120,158-199` — computes a signed
  `net_move` internally but classifies only on `|ER|`; no TRENDING_UP vs
  TRENDING_DOWN distinction exists. **Not directly reusable as-is**; would
  need the same kind of new logic as 78.
- `bujji/intelligence/structure_brain.py:90-118` — `put_call_oi_ratio` is
  real, computed from live FYERS optionchain OI, and is directionally
  *adjacent* (skew), but is never thresholded into a bullish/bearish
  classification in the current code — only surfaced as a raw evidence
  number. **Partially reusable**: the raw signal exists; the
  classification logic does not yet.
- `bujji/intelligence/greeks_brain.py:92-131` — `position_delta` and
  `NET_LONG_EXPOSURE`/`NET_SHORT_EXPOSURE` are real, signed, working
  directional computation, already exercised against live data. **This is
  the strongest "reuse" candidate found in the entire audit** — but it is
  scoped to the live hardcoded straddle's own delta, not a market-wide
  read. Reuse would mean adapting the same Black-Scholes-delta-sign
  technique to a market-representative synthetic position (e.g. ATM
  straddle skew), not literally reusing the position's own delta.
- No `volatility_brain.py` content was found to be directionally relevant
  beyond the IV magnitude it already computes (no smile-skew-direction
  classification found).

**This meaningfully affects the recommendation**: real, working signed
directional computation already exists in the codebase
(`greeks_brain._classify_exposure`), just scoped to the wrong subject
(current position, not market). This is evidence *for* building a new,
narrow Direction concept that reuses this proven technique, rather than
evidence that an existing field just needs to be "exposed."

## 7. Architecture decision

**Recommendation: Candidate E — a new, narrow Direction synthesis
component, structured as a mini-consensus (per Deliverable 3), NOT a
field bolted onto 78 or 79.**

Reasoning, grounded in evidence above:
- The hypothesis's central premise (that 78/79 already contain reusable
  directional signal needing only aggregation) is **only partially true**.
  `trend_state` is genuinely sign-blind; only two of `structure_location`'s
  values are genuinely directional. Real new classification logic is
  required regardless of where it lives — so the choice is not "cheap
  reuse vs. expensive build," it is "where does new logic best live."
- Deliverable 3 established that direction is legitimately multi-domain
  (structural vs. positioning horizons can disagree) — this rules out a
  single-field bolt-on to any one existing assessment (Candidates A/B) as
  architecturally incomplete from the start, independent of implementation
  cost.
- Consensus (81) is the closest existing analog in *pattern* (cross-domain
  reconciliation) but cannot be the owner (Candidate C) because it has no
  direct access to raw signed inputs today — it would need upstream
  signed feeders first, i.e., it is a consumer of a Direction layer, not
  a substitute for one.
- Decision Synthesis (77) explicitly and deliberately excluded directional
  voting by design (`engine.py:22-23`) — reversing that is a bigger
  architectural reversal than introducing a new component.
- Real, proven signed-classification code already exists
  (`greeks_brain._classify_exposure`) and is directly reusable as a
  *technique* (not as literal code) by a new Direction component, lowering
  the actual cost of Candidate E versus what it would look like built from
  nothing.

## 8. Migration impact (for the recommended path)

- `bujji/msi_price_structure` (78): **no change** required to ship
  Candidate E — direction lives elsewhere and simply consumes 78's
  existing public fields (signed deltas exist in event data; 78's own
  taxonomy need not change).
- `bujji/msi_market_structure` (79): **no change** required; the new
  component would read `structure_location` and treat
  ABOVE_RESISTANCE/BELOW_SUPPORT as directional evidence, other values
  as directionally neutral — pure consumption, not a schema edit.
- `bujji/msi_consensus` (81) / `bujji/msi_decision_synthesis` (77) /
  `bujji/msi_strategy_eligibility` (82): **additive field only**, and only
  in a later sprint once the new Direction assessment exists — each would
  gain an optional field carrying the new assessment's ID/summary, not a
  schema bump, preserving existing replay fingerprints for all current
  fields.
- `bujji/msi_trade_intent` (83): `derive_market_bias` would gain a real
  branch once a Direction assessment exists to consult — additive logic
  change, not a schema bump to `TradeIntentAssessment` (the `market_bias`
  field already exists; only its derivation logic changes).
- New Direction module: brand new series, own schema, own replay
  determinism/fingerprint discipline built from day one — no legacy
  replay compatibility burden since nothing consumes it yet.

## 9. Future architecture diagram

```
73A-76 (sensing: market/futures/options/live observation, events, episode)
        |
        v
   +---------------------+     +------------------------+
   | 78 Price Structure   |     | 79 Market Structure    |
   | (trend/swing/comp/   |     | (support/resistance/   |
   |  exp/balance)        |     |  breakout/breakdown)   |
   +----------+-----------+     +-----------+------------+
              |                              |
              +---------------+--------------+
                              |
                              v
              +----------------------------------+
              | NEW: Direction Engine (Series 8x)|
              | - structural lean (from 78/79)   |
              | - positioning lean (from options_ |
              |   observation OI skew / greeks-   |
              |   brain-style signed classifier)  |
              | - reconciles the two (mini-       |
              |   consensus), may legitimately    |
              |   report "diverging"              |
              +----------------+------------------+
                              |
                              v
                    81 Consensus (consumes Direction
                    as one more coherence input, same
                    as any other domain signal)
                              |
                              v
                    77 Decision Synthesis
                              |
                              v
                    82 Strategy Eligibility
                              |
                              v
                    83 Trade Intent (market_bias now
                    has a real, non-default source)
```

This differs from a naive reading of the spec's implied diagram (direction
as a sub-field of Price Structure or Market Structure) because the
evidence in Deliverables 1 and 3 rules that placement out: the signal
doesn't cheaply exist inside either module today, and the concept is
multi-domain by nature.

## 10. Final recommendation

Build a **new, dedicated Direction Engine** (Candidate E) as its own MSI
series, positioned after 78/79 and before 81, structured internally as a
small two-input reconciliation (structural lean vs. positioning lean) —
not a single flat field — because Deliverable 3 shows these two lenses
can legitimately disagree and collapsing them into one value would
silently discard real information, the same anti-pattern 78's own
`structure_state`/`compression_state` split was designed to avoid.

Rejected alternatives, with reasons:
- **Candidate A (Price Structure owns it)** — rejected because
  `trend_state` is sign-blind by construction (`_sign()` computed then
  discarded); "expose an existing field" is not actually available, real
  new logic is needed, and putting it there ignores the positioning-lean
  half of the problem entirely.
- **Candidate B (Market Structure owns it)** — rejected for the same
  reason as A, plus its directional coverage is narrower (only the
  breakout/breakdown confirmed cases), leaving most market states with no
  read.
- **Candidate C (Consensus owns it)** — rejected because Consensus has no
  access to raw signed inputs; it is a natural *consumer* of a Direction
  assessment, not a place direction can be manufactured from nothing.
- **Candidate D (Decision Synthesis owns it)** — rejected because 77's
  own code explicitly and deliberately excludes directional voting by
  design; reversing that is a larger, unjustified architectural reversal.

This recommendation **contradicts the strict form of the initial
hypothesis** ("78's trend_state and 79's structure_location already
contain directional information; the gap is just that nothing aggregates
it") — the investigation found that claim is largely false for 78 and
only partially true for 79, and additionally found the problem is
multi-domain rather than single-owner. It **partially confirms** the
hypothesis's instinct that aggregation/synthesis (Consensus-like
reconciliation) is central to the right answer — that instinct was
correct, just misattributed to the wrong existing module; the
reconciliation needs to happen in a new component fed by both structural
and positioning inputs, not inside 81 itself and not by simply exposing
values 78/79 already compute.
