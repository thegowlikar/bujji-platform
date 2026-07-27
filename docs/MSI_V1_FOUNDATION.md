# Market Structure Intelligence Foundation (MSI v1)
## Engineering Series 71 — Research & Architecture Specification

**Status:** Design only. No code, no pseudo-code, no implementation, no modifications to MIC v2/Trading Brain/Runtime/Replay/Qualification. This document is the architectural foundation that future engineering series will implement incrementally.

**Relationship to existing systems:** MSI v1 is designed as a peer to MIC v2, not a replacement or extension of it. MIC v2 answers "what is the market currently doing, and how confident/stable is that read?" (narrative, hypothesis, qualification, memory, context, stability). MSI answers a different question: "what is the market's *structure* — where are the levels, zones, and regimes that give MIC v2's narrative meaning?" MIC v2 tells you the market is transitioning; MSI tells you it is transitioning *away from a two-week balance area, into an unresolved impulse leg, with OI migrating out of the previous acceptance zone*. Both are necessary; neither substitutes for the other. Series 69/70 proved MIC v2's chain is deterministic and explainable end-to-end — MSI is designed from day one to meet that same bar, using the same design language (Evidence → Reasoning Object → Published Intelligence, full provenance, replay parity), so it composes with the existing Observatory rather than requiring a second one.

---

## Deliverable 1 — First Principles

These are *concepts*, not indicators. An indicator is a computed transform of price (a moving average, an oscillator). A concept is a *state of the market* that a discretionary trader recognizes and that a computed transform can, at best, provide *evidence* for. The distinction matters because indicators are outputs of arithmetic; these are outputs of interpretation. MSI's raw-observation layer is allowed to compute arithmetic (a swing high, a volume delta); its reasoning layer is never allowed to *equate* an indicator value with a structural conclusion. An RSI reading is not "overbought" — overbought is a market condition that RSI is sometimes weak evidence for.

| Concept | Definition | Why it exists |
|---|---|---|
| **Trend** | A directional sequence of higher (or lower) swing structure that the market is currently extending. | Markets spend a meaningful fraction of time in persistent directional agreement across participants; a trend is the structural signature of that agreement. Without a trend concept, MSI cannot distinguish "moving" from "going somewhere." |
| **Swing** | A local extreme (high or low) confirmed by subsequent opposing structure, forming the atomic unit that trend/correction/impulse are built from. | Price structure is not continuous — it is a discrete sequence of turning points. A swing is the smallest unit of structural memory; everything else (trend, correction, structure break) is a relationship between swings. |
| **Impulse** | A swing-to-swing move that is structurally decisive: it travels with minimal internal overlap and resolves an existing balance or contested zone. | Impulses mark the moments the market actually *decides* something — as opposed to drifting. Distinguishing impulse from drift is what separates "the market broke out" from "price went up a bit." |
| **Correction** | A counter-trend move that retraces part of the prior impulse without invalidating its structural conclusion. | Corrections are necessary to distinguish "the trend paused" from "the trend ended." Without this concept, every pullback looks identical to a reversal. |
| **Compression** | A structural state where range, volatility, and participation are all contracting simultaneously, and the market is building potential energy rather than expending it. | Compression precedes expansion with high enough regularity that professional traders treat it as a distinct phase, not merely "low volatility." It is a *setup* concept, not a value. |
| **Expansion** | The structural release of compression — range, volatility, and directional conviction all increasing together. | Expansion is the counterpart to compression; recognizing the transition between the two is more informative than measuring either state alone. |
| **Acceptance** | Price and time spent by the market at a level such that participants have transacted there in volume sufficient to treat it as fair value, evidenced by returning auction activity, not merely by touching it once. | Distinguishes "price visited this level" from "the market agreed this level was fair" — the latter is what makes a level structurally significant going forward. |
| **Rejection** | Price visiting a level and being quickly, forcefully returned from it, with participation evidence (volume/OI/speed) indicating disagreement rather than acceptance. | The mirror of acceptance. A level that is repeatedly rejected is structurally different from one that is repeatedly accepted, even if both are "touched" the same number of times. |
| **Balance** | A market condition where two-sided participation has produced a bounded range that price is not currently attempting to escape, and structure inside the range shows repeated acceptance at multiple prices. | Balance is the market's default resting state and the necessary precondition for meaningfully defining a breakout. Without a balance concept, "breakout" cannot be rigorously defined — there is nothing to break out *of*. |
| **Imbalance** | A state where one side of the market (buyers or sellers) is transacting with insufficient opposition, evidenced by directional acceptance without corresponding two-sided rejection. | Imbalance is what impulses and expansions are made of. It is the causal precursor concept; impulse is its price-structure consequence. |
| **Auction** | The continuous process by which the market searches for a price that balances buyers and sellers, moving away from unfair prices and toward fair ones. | This is the unifying process concept underneath nearly everything else in this table — trend, balance, acceptance, and rejection are all *outcomes* of the auction process, not independent phenomena. Treating auction as the root concept keeps the rest of the ontology internally consistent rather than a loose bag of terms. |
| **Momentum** | The rate and consistency of directional progress, distinct from direction itself — a trend can lose momentum while direction is unchanged. | Momentum decay is frequently the first observable evidence that a trend's imbalance is fading, well before price structure itself breaks. It is a leading, not lagging, structural signal when defined this way (as a structural read, not as an oscillator value). |
| **Exhaustion** | A structural state where an impulse's supporting participation (volume, OI, breadth, momentum) is declining even as price continues advancing — the imbalance funding the move is running out. | Exhaustion is what allows MSI to flag "this move is structurally fragile" before a reversal is confirmed by price alone, which is generally too late for actionable positioning. |
| **Participation** | The observable evidence of *how many* and *how committed* market participants are, at a given level or during a given move (volume, open interest change, breadth, order flow proxies). | Every other concept in this table needs participation evidence to be more than a shape drawn on a price chart. A "breakout" with no participation evidence and a "breakout" with heavy participation are structurally different events, even though price action looks identical. |

---

## Deliverable 2 — Intelligence Domains

Each domain is defined by purpose, inputs, outputs, and reasoning responsibility — never by algorithm.

### 1. Price Structure Intelligence
- **Purpose:** Establish the skeleton every other domain hangs off of — where are the swings, what is the trend/correction/impulse state, is the market in balance or imbalance.
- **Inputs:** OHLC price series (any timeframe), time.
- **Outputs:** Swing sequence, trend state, structural phase (impulse/correction/compression/expansion), balance/imbalance classification.
- **Reasoning responsibility:** Interprets raw price into the Deliverable 1 concepts. Does not consume options, futures, or volatility data — this domain is price-only by design, so its conclusions are independently falsifiable against every other domain (a critical property for the Evidence Graph in Deliverable 4).

### 2. Support & Resistance Intelligence
- **Purpose:** Identify where the market has previously demonstrated acceptance or rejection, and characterize the strength/quality of those levels, so structural context exists for strike selection and trade management.
- **Inputs:** Price Structure Intelligence's swing sequence, plus participation evidence (volume/OI where available) at those swings.
- **Outputs:** A ranked set of active levels, each tagged with strength (acceptance vs. rejection evidence), age, and test count.
- **Reasoning responsibility:** Determines which historical price levels remain structurally *live* versus stale, and how a current price approach to a level should be interpreted (as a retest of acceptance, or a probe of prior rejection).

### 3. Options Market Structure Intelligence
- **Purpose:** Read the options market's own structure — where OI is concentrated, how it is migrating, where the option market itself implies acceptance/rejection (distinct from price's own S/R) — since options participants often price structural conclusions before spot price fully confirms them.
- **Inputs:** Option chain snapshots over time (strike, OI, OI change, IV per strike, bid/ask), spot price.
- **Outputs:** OI concentration map, OI migration direction, max-pain-adjacent structural zones (described as evidence, never treated as a target), put/call structural skew, gamma-concentration zones (described structurally, not as a Greek computation prescription).
- **Reasoning responsibility:** Distinguishes options structure that is *confirming* price structure from options structure that is *diverging* from it — divergence between price structure and options structure is one of the highest-value evidence signals this domain can produce.

### 4. Futures Structure Intelligence
- **Purpose:** Read futures positioning (basis, rollover behavior, OI buildup/unwind) as an independent structural read on institutional directional conviction.
- **Inputs:** Futures OI, futures price vs. spot (basis), rollover-period behavior.
- **Outputs:** Basis regime (premium/discount/flat and its trend), OI buildup classification (long buildup / short buildup / long unwinding / short covering — described structurally, not as a rule), rollover conviction signal.
- **Reasoning responsibility:** Provides an institutional-positioning cross-check that is structurally independent of both price and options domains, increasing the Evidence Graph's overall falsifiability.

### 5. Volatility Structure Intelligence
- **Purpose:** Characterize the *regime* of volatility (not merely its current level) — expanding, contracting, mean-reverting, or structurally elevated/depressed relative to recent history — since regime, not level, is what governs strategy and strike selection appropriateness.
- **Inputs:** Realized volatility series, India VIX / IV term structure, IV skew.
- **Outputs:** Volatility regime classification, term-structure shape (contango/backwardation), skew regime, regime transition evidence.
- **Reasoning responsibility:** Determines whether current option pricing is structurally consistent with realized behavior (mispricing evidence) and whether volatility is in a state (compression) that historically precedes expansion, per Deliverable 1's compression/expansion concepts.

### 6. Liquidity Intelligence
- **Purpose:** Assess whether the market (spot, futures, and specific option strikes) currently has the depth/participation to support intended trade size and structure without materially moving price against the position.
- **Inputs:** Bid/ask spreads, order book depth where available, traded volume, OI depth per strike.
- **Outputs:** Liquidity state per instrument/strike (adequate/thin/illiquid), liquidity trend (improving/deteriorating).
- **Reasoning responsibility:** The sole domain responsible for answering "can this be executed and managed as sized" — a structural precondition, not a signal about market direction.

### 7. Time Structure Intelligence
- **Purpose:** Characterize where the market is in recurring time-based structural cycles — session phase (opening range, mid-session, closing), day-of-week/expiry-week effects, and time-to-expiry structural implications for options.
- **Inputs:** Timestamp, session calendar, expiry calendar.
- **Outputs:** Session phase classification, expiry-proximity structural state (e.g. gamma-concentration risk near expiry, described structurally), time-of-day participation expectations.
- **Reasoning responsibility:** Provides context that changes how *all other domains'* evidence should be weighted (e.g. an opening-range breakout is structurally different from the same shape appearing at 3:20pm) without itself producing directional or structural conclusions about price.

### 8. Cross-Asset Intelligence
- **Purpose:** Read structural signals from related markets (index constituents, sector indices, global indices, currency, rates) that historically lead or confirm the primary instrument's structure.
- **Inputs:** Price/structure state of correlated instruments.
- **Outputs:** Cross-asset confirmation/divergence evidence, relative-strength structural read.
- **Reasoning responsibility:** Supplies an externally-sourced structural cross-check, analogous to how Options/Futures domains cross-check Price Structure, but from outside the primary instrument entirely — the highest-independence evidence source in the ontology.

### 9. Regime Intelligence
- **Purpose:** Synthesize the outputs of all other domains into a single, coherent structural regime classification (e.g. trending/balancing/transitioning, at the *structural* level — distinct from and complementary to MIC v2's own market_context/context_stability, which operate on narrative/evidence confidence rather than structural composition).
- **Inputs:** Published intelligence from all 8 domains above.
- **Outputs:** A composite structural regime classification with full provenance back to which domain(s) drove the conclusion.
- **Reasoning responsibility:** The only domain permitted to consume other domains' *published* (not raw) intelligence — this is the synthesis layer, and its position at the top of the Dependency Graph (Deliverable 5) is deliberate: it must never be a raw-observation consumer itself, so its conclusions are always traceable through the domains beneath it.

---

## Deliverable 3 — Market Ontology

The vocabulary below is deliberately domain-tagged so that provenance (which domain asserted which term) is unambiguous in the Evidence Graph and Observatory.

**Price Structure domain:**
- `StructureState` — {IMPULSE, CORRECTION, COMPRESSION, EXPANSION, BALANCE, TRANSITIONING, UNKNOWN}
- `TrendStrength` — qualitative strength of current directional structure, evidenced by swing-progression consistency
- `SwingQuality` — how cleanly a swing is confirmed (decisive vs. marginal), evidenced by overlap and follow-through
- `MomentumState` — {ACCELERATING, STEADY, DECELERATING, DIVERGENT}
- `ExhaustionState` — {NONE, EARLY, CONFIRMED} evidenced by participation decline against continued price progress

**Support & Resistance domain:**
- `SupportStrength` / `ResistanceStrength` — evidenced by test count, acceptance/rejection history, and age decay
- `AcceptanceZone` — a price band (not a single level) where repeated two-sided transaction has occurred
- `BreakoutQuality` — evidenced classification of a level break as structurally decisive vs. probe/failed

**Options Structure domain:**
- `OIMigration` — direction and magnitude of open-interest concentration shift across strikes/expiries over time
- `OptionsStructureBias` — the options market's own directional lean, evidenced independently of price structure
- `StructuralDivergence` — a flagged state where Options Structure and Price Structure conclusions disagree

**Futures Structure domain:**
- `BasisRegime` — {PREMIUM, DISCOUNT, FLAT} plus trend direction
- `PositioningState` — evidenced classification (e.g. long buildup, short covering) from OI + price co-movement, described only as an evidenced label, not an algorithm

**Volatility Structure domain:**
- `VolatilityRegime` — {EXPANDING, CONTRACTING, ELEVATED_STABLE, DEPRESSED_STABLE, TRANSITIONING}
- `TermStructureShape` — {CONTANGO, BACKWARDATION, FLAT}
- `SkewRegime` — evidenced characterization of put/call IV skew relative to its own recent history

**Liquidity domain:**
- `LiquidityState` — {ADEQUATE, THIN, ILLIQUID} per instrument/strike
- `LiquidityTrend` — {IMPROVING, DETERIORATING, STABLE}

**Time Structure domain:**
- `SessionPhase` — {OPENING_RANGE, MID_SESSION, PRE_CLOSE, CLOSE}
- `ExpiryProximityState` — structural risk classification as expiry nears

**Cross-Asset domain:**
- `ParticipationState` — breadth/participation evidence, defined once in Deliverable 1, instantiated per-domain where participation evidence applies (Price Structure, Options Structure, Futures Structure, and Cross-Asset all produce their own `ParticipationState` reading — this is intentional: the same concept, independently evidenced from different data sources, is what makes cross-domain confirmation/divergence meaningful)
- `CrossAssetAgreement` — {CONFIRMING, DIVERGENT, NEUTRAL}

**Regime domain (synthesis):**
- `CompositeStructuralRegime` — the top-level synthesis output, always published with full domain-provenance

This list is intentionally incomplete — new terms should be added only when a domain's reasoning genuinely needs a distinct concept, never speculatively.

---

## Deliverable 4 — Evidence Graph

The same four-layer shape used successfully by MIC v2 (Evidence → Narrative/Hypothesis → Qualification → Memory, proven deterministic and explainable in Series 66–70) is reused here, generalized to MSI's domains. No algorithms — only relationships.

```
Raw Observations
  (price candles, option chain snapshots, futures OI/price, VIX/IV term
   structure, order book/volume, session calendar)
        ↓
Derived Evidence
  (per-domain, evidenced facts directly computable from raw observations:
   "a swing high formed here", "OI increased 12% at strike X between
   snapshots", "basis moved from discount to premium", "IV term structure
   inverted" — each Derived Evidence item MUST cite exactly which raw
   observations produced it, mirroring MIC v2's Evidence provenance
   discipline)
        ↓
Reasoning Objects
  (per-domain interpretation of Derived Evidence into ontology terms from
   Deliverable 3: StructureState, SupportStrength, OIMigration,
   VolatilityRegime, etc. — each Reasoning Object cites exactly which
   Derived Evidence items it was built from, and is scoped to ONE domain)
        ↓
Published Intelligence
  (the domain's final, externally-consumable conclusion for a given cycle —
   analogous to MIC v2's PublishedState — always carrying full provenance
   back through Reasoning Objects to Derived Evidence to Raw Observations)
```

Two properties are non-negotiable, both carried over directly from what made MIC v2 auditable in Series 69/70:

1. **No layer may skip a layer.** A Reasoning Object may never cite a Raw Observation directly — it must cite Derived Evidence. This is what made Series 69/70's "first divergence" analysis possible for MIC v2, and MSI must preserve it for the same reason: without it, root-cause tracing collapses back into guessing.
2. **Published Intelligence from one domain is Raw-Observation-equivalent input to Regime Intelligence only** (Deliverable 2, domain 9) — no other domain may consume another domain's Published Intelligence as if it were its own Derived Evidence. This constraint is what keeps the Dependency Graph (Deliverable 5) acyclic and each domain's conclusions independently falsifiable.

---

## Deliverable 5 — Dependency Graph

```
Price Structure ─────────────┐
                              ├──▶ Support & Resistance ─────┐
Futures Structure ────────────────────────────────────────────┤
                                                                ├──▶ Options Market Structure ──┐
Volatility Structure ─────────┬────────────────────────────────┘                                │
                               ├──▶ Liquidity ─────────────────────────────────────────────────┤
Time Structure ────────────────────────────────────────────────────────────────────────────────┤
                                                                                                  │
Cross-Asset ──────────────────────────────────────────────────────────────────────────────────┤
                                                                                                  ▼
                                                                                        Regime Intelligence
                                                                                                  │
                                                                                                  ▼
                                                                                    (future) Trading Brain domains
                                                                                    per Deliverable 7
```

Justification for each edge:
- **Price Structure → Support & Resistance:** S/R is defined structurally in terms of swings (Deliverable 1), which only Price Structure produces. This is a hard dependency, not a convenience.
- **Futures Structure → Options Market Structure:** Options Structure's interpretation of OI migration is materially different depending on whether futures positioning confirms or contradicts it (e.g. call OI buildup alongside futures short-covering reads differently than the same call OI buildup alongside futures long-unwinding) — Options Structure needs Futures Structure's Published Intelligence as an input evidence source, one layer removed (per the Deliverable 4 rule, this flows in as Options Structure's own Derived-Evidence-equivalent input, sourced from Futures Structure's Published Intelligence — the one sanctioned exception alongside Regime Intelligence, because options-vs-futures divergence is itself a first-class evidence signal per Deliverable 2, domain 3).
- **Volatility Structure → Liquidity:** Liquidity conditions (spread width, depth) are structurally coupled to volatility regime — thin liquidity during volatility expansion is a different structural state than thin liquidity during compression.
- **Time Structure → (Liquidity, and every domain via weighting, not data dependency):** Time Structure does not feed evidence into other domains' conclusions; it changes how their evidence should be *weighted* downstream, which is why it is drawn separately from the main evidence-flow chain — this avoids forcing a cyclic or awkward dependency where every domain would otherwise need to formally "depend on" Time Structure.
- **Everything → Regime Intelligence:** by design, per Deliverable 2 domain 9 and the Deliverable 4 synthesis-layer rule.

**No cycles exist in this graph**, and none are justified at this stage. If a future domain proposal requires a cycle (e.g. Options Structure wanting to feed back into Price Structure), that is a signal the two domains are not actually well-separated and should be reconsidered before implementation, not resolved by permitting a cycle.

---

## Deliverable 6 — Replay & Observability

Every MSI intelligence object, at every layer (Derived Evidence, Reasoning Object, Published Intelligence), must support the following properties by construction — described here as requirements, not implementations:

- **Replay:** Given the same Raw Observations and the same replay-time inputs (identical to how MIC v2's replay mode must receive real `option_chain`/`vix_level` as of Series M1), MSI must be able to reconstruct the exact same sequence of Derived Evidence, Reasoning Objects, and Published Intelligence for any historical window, in a mode structurally identical to (and reusing, not duplicating) BUJJI's existing replay infrastructure.
- **Explanation:** Every Published Intelligence object must be traceable, layer by layer, back to the specific Raw Observations that produced it — this is a direct requirement of the Evidence Graph's no-skip-a-layer rule (Deliverable 4), not an additional feature to be bolted on.
- **Qualification:** MSI's Published Intelligence must be consumable by BUJJI's existing Qualification Recording pattern (Series 69/70's `QualificationRecord`/`sessions[]` schema) as additional, clearly domain-prefixed fields — never as a parallel, separate recording system. This preserves the single-Observatory principle stated in the relationship note at the top of this document.
- **Historical comparison:** MSI's `CANONICAL_FIELD_ORDER`-equivalent must integrate into the existing Observatory's comparison logic (`compare_sessions`/`first_divergence`), positioned according to the Dependency Graph in Deliverable 5 — e.g., a Price Structure field must be capable of being reported as an earlier divergence than an Options Structure field, mirroring exactly how Series 70 positioned `context_stability`'s causal inputs ahead of it.
- **Determinism:** No MSI computation may depend on wall-clock time, random state, or any non-reproducible input, matching the hard constraint that has governed every prior series in this project (`hashlib.md5`-style deterministic IDs, no `uuid4()`, no unseeded randomness).
- **Provenance:** Every object at every layer must carry a reference to exactly which upstream objects produced it (Raw Observations for Derived Evidence, Derived Evidence for Reasoning Objects, Reasoning Objects for Published Intelligence), in a structurally identical shape to MIC v2's existing `provenance` fields already present on `Evidence`, `Hypothesis`, `MarketNarrative`, etc. (confirmed present on essentially every MIC v2 model in the Series 70 Phase 1 investigation) — MSI should reuse that same provenance shape, not invent a new one.

A concrete implication worth flagging now, before any implementation series: **Series 69/70's `sessions[]` schema and `CANONICAL_FIELD_ORDER` mechanism already generalize to this** — they are not MIC v2-specific despite having been built to solve a MIC v2 problem. The recommended integration path (a decision for a future series, not this one) is to extend those same mechanisms with MSI-domain-prefixed fields (e.g. `structure_trend_strength`, `sr_nearest_support_strength`) rather than building a second observability system.

---

## Deliverable 7 — Trading Decisions

| Domain | Consumed by |
|---|---|
| Price Structure | Strategy Selection, Trade Management, Exit Management |
| Support & Resistance | Strike Selection, Exit Management, Roll Decisions |
| Options Market Structure | Strategy Selection, Strike Selection, Roll Decisions |
| Futures Structure | Strategy Selection, Hedge Decisions |
| Volatility Structure | Strategy Selection, Strike Selection, Risk |
| Liquidity | Strike Selection, Capital, Trade Management |
| Time Structure | Trade Management, Roll Decisions (expiry-proximity logic) |
| Cross-Asset | Strategy Selection (confirmation only — never sole driver) |
| Regime Intelligence | Strategy Selection (primary consumer — the composite regime is intended as the top-level structural gate before strategy selection runs, complementing rather than replacing MIC v2's existing `market_context`/`context_stability` gate) |

No domain is mapped to **None** — this was deliberately checked: if a proposed domain has no eventual Trading Brain consumer, it does not belong in this foundation and should be cut rather than included for completeness.

---

## Deliverable 8 — Future Brains Roadmap

Recommended build order, with dependency justification drawn directly from the Deliverable 5 graph (a domain should not be built as a "brain" before the domains it structurally depends on exist, at least in a minimal published form):

1. **Brain 1 — Price Structure Brain.** No dependencies on any other MSI domain (it is the root of the Dependency Graph). Build first because every other domain either depends on it directly (S&R) or benefits from having a real structural read available to cross-check against during their own development and validation.
2. **Brain 2 — Support & Resistance Brain.** Depends only on Brain 1. Second because it is the most direct, highest-value consumer of Price Structure output (Strike Selection has immediate use for it) and validates the Price Structure → S&R edge of the Dependency Graph before more domains are added on top.
3. **Brain 3 — Volatility Structure Brain.** No hard dependency on Brains 1–2 (only a downstream dependency from Liquidity). Built third rather than in parallel with Brain 2 to keep the always-narrow, one-domain-at-a-time discipline this project has maintained since Series 62 — parallel brain development multiplies the surface area for a Series-70-style threading bug before the Observatory-integration pattern is proven for MSI at all.
4. **Brain 4 — Options Market Structure Brain.** Depends on Brain 1 (indirectly, via price context) and benefits from Brain 3 (volatility regime materially affects OI-migration interpretation, per Deliverable 5's Volatility→Liquidity edge and Options Structure's general sensitivity to vol regime). Fourth because it is the domain BUJJI (an options strategy engine) most directly needs, but building it before Price Structure/Volatility exist would leave it without cross-checking evidence, repeating the exact "evidence discarded before reaching the decision" failure mode Series 69/70 diagnosed in MIC v2 — except structurally, not just at the recording layer.
5. **Brain 5 — Futures Structure Brain.** Depends on Brain 1 conceptually (futures structure interpretation benefits from spot structure context) but has no hard technical dependency; placed fifth because its primary consumer (Hedge Decisions, per Deliverable 7) doesn't yet exist as a Trading Brain component, so building it earlier would front-run its own consumer.
6. **Brain 6 — Liquidity Brain.** Depends on Brain 3 (Volatility) per the Dependency Graph. Sixth, positioned just before Regime synthesis because liquidity is the last domain needed before Strike Selection/Capital have a complete evidence picture.
7. **Brain 7 — Regime Intelligence (synthesis).** Must be built last among the "read" domains by definition — it is the only domain permitted to consume other domains' Published Intelligence (Deliverable 4), so it structurally cannot exist meaningfully before at least Brains 1–3 are producing real output, and ideally not before most of Brains 1–6.
8. **Brain 8 — Time Structure and Cross-Asset** (grouped last, lower priority): both are legitimate domains (Deliverable 2) but neither has a hard dependency edge feeding anything else, and their consumers (per Deliverable 7) are refinements (weighting, confirmation) rather than gates — appropriate to add once the core structural chain is proven, not before.
9. **Brain 9 — Trade Management** (a Trading Brain component per Deliverable 7, not an MSI domain itself): intentionally last. It is the first component that *consumes* MSI's output for live position decisions (roll/hedge/exit), and per this project's own established discipline (measure before optimizing, observe before acting — Series 69/70's explicit mandate), it should not be built until there is a full corpus of MSI Published Intelligence to validate against historically, exactly as Series 65–70 insisted on real historical corpora before any reasoning change.

---

## Deliverable 9 — Gap Analysis

| Missing capability | Severity | Dependencies | Architectural impact | Belongs in |
|---|---|---|---|---|
| Swing/trend/impulse/correction structural read | **High** | None (root capability) | New domain, new models, new replay path — large but additive | MSI (Brain 1) |
| Dynamic support/resistance with strength/age evidence | **High** | Price Structure | Moderate — mostly new models consuming Brain 1's output | MSI (Brain 2) |
| Options OI migration / structural divergence detection | **High** | Price Structure (context), ideally Volatility | Moderate-large — needs option chain time-series storage beyond current single-snapshot `OptionLiquiditySnapshot` (Series 64/65) | MSI (Brain 4) |
| Futures basis/positioning structural read | Medium | None hard; benefits from Price Structure | Moderate — needs futures OI/price time-series ingestion not currently in BUJJI's real-data pipeline | MSI (Brain 5) |
| Volatility *regime* classification (vs. today's single VIX scalar) | Medium-High | None | Moderate — VIX is already ingested (Series M1); this is a reasoning layer on top, not new data plumbing | MSI (Brain 3) |
| Liquidity depth/spread structural assessment | Medium | Volatility regime | Moderate — needs bid/ask depth data; BUJJI's current `OptionLiquiditySnapshot` already carries bid/ask (Series 64), so partial raw data exists | MSI (Brain 6) |
| Session/expiry time-structure weighting | Low-Medium | None | Small — mostly a weighting/context layer, not new data | MSI (Brain 8) |
| Cross-asset confirmation | Low | None | Small-Moderate — needs new external data ingestion (correlated instruments) not currently sourced | MSI (Brain 8) |
| Composite structural regime synthesis | **High** (but only once inputs exist) | All of the above | Large, but purely synthesis — no new raw data | MSI (Brain 7) |
| MSI → Trading Brain wiring (Strategy/Strike/Risk/etc. actually consuming MSI output) | **High** (end-state gate) | All MSI brains | Large — genuine Trading Brain changes, explicitly out of scope for THIS series | **Trading Brain** (future series, not MSI) |
| MSI Published Intelligence recorded in Qualification/Observatory | Medium (observability debt, not a functional gap) | At least one MSI brain existing | Small-Moderate, by design (Deliverable 6 — reuses existing `sessions[]`/`CANONICAL_FIELD_ORDER` mechanism) | **Qualification/Observatory** (extension, not a new system) |
| Replay-mode data availability for options/futures/cross-asset time-series (vs. today's single-snapshot real data) | **High** (blocking, foundational) | None | Large — this is a data-acquisition gap, not a reasoning gap; MSI's reasoning layers cannot be validated historically without it, mirroring exactly the Series 65/68/M1 pattern where reasoning work was blocked on real historical evidence actually reaching the pipeline | **Runtime/data acquisition**, not MSI reasoning itself |

The last row is deliberately called out as the most consequential finding of this Gap Analysis: **this project's own history (Series 65, 68, M1) shows that reasoning-layer work without real historical data plumbing in place first produces exactly the kind of "evidence exists but never reaches the record" failure this project has now spent three series (66/69/70) diagnosing and fixing for MIC v2.** The Future Brains Roadmap (Deliverable 8) should not be read as "build reasoning first, source data later" — the recommended near-term next step, ahead of Brain 1 itself, is a data-acquisition sprint (in the style of Series 65's Data Acquisition Sprint C) establishing real historical option-chain-over-time, futures OI-over-time, and (if pursued) cross-asset series, so that Price Structure and every subsequent brain can be built against real data from day one rather than retrofitted later.

---

## Deliverable 10 — Guiding Principles

1. **Never use indicators as intelligence.** An indicator is a computed transform; intelligence is an interpreted conclusion. Where an indicator is used at all, it must be labeled and treated as a single piece of Derived Evidence, never as a Reasoning Object or Published Intelligence in its own right.
2. **Prefer evidence over thresholds.** Where a threshold is unavoidable (as MIC v2's qualification/stability engines already use, transparently, per Series 69/70's investigation), it must be disclosed and explainable, never hidden inside an opaque computed score.
3. **Separate observation from reasoning.** The Evidence Graph's layering (Deliverable 4) is not a documentation convenience — it is the mechanism that made MIC v2 auditable, and MSI inherits it as a hard structural requirement, not a style guideline.
4. **Every decision must be explainable.** Directly inherited from this project's foundational discipline (Series 66 Observatory's entire purpose) — extended here to mean every MSI Published Intelligence object, not just Trading Brain decisions.
5. **Deterministic replay.** No wall-clock, no unseeded randomness, no non-reproducible inputs — non-negotiable, consistent with every prior series in this project.
6. **Market-first, strategy-second.** MSI's domains are defined and validated on their own terms (does this structural read match what a discretionary trader would independently conclude?) before any Trading Brain component is allowed to consume them — mirroring how MIC v2 itself was validated as market intelligence before BUJJI's Trading Brain existed.
7. **Reason before execution.** No MSI domain may take or influence an execution action directly; MSI produces intelligence, Trading Brain consumes it, Runtime executes — the three-layer separation this project already enforces stays intact.
8. **Intelligence modules must compose, not duplicate.** The Dependency Graph (Deliverable 5) is acyclic and each domain has exactly one clear producer — no two domains should ever independently compute the same conclusion from the same evidence; if they would, that is a signal they are actually one domain.
9. **Measure before optimizing; observe before acting.** Directly carried over from Series 65–70's explicit, repeated mandate ("do not tune, do not optimize, only measure") — MSI's reasoning layers should be measured against real historical data before any Trading Brain component is permitted to act on them.
10. **No silent data gaps.** Per Series 69/70's own history (the Phase 4 threading bug), any MSI field that is genuinely unavailable must surface as `None`/absent, never as a default, zero, or inferred value — fabricated-looking data is worse than honestly-missing data.
11. **One domain, one brain, one series at a time.** Per the Future Brains Roadmap's Brain 3 justification — this project's demonstrated discipline of narrow, sequential, fully-verified sprints (rather than broad parallel changes) is itself a guiding principle worth stating explicitly, not just an operational habit.
12. **Provenance is not optional at any layer.** Every object, at every layer, in every domain, carries its own upstream references — this is what makes both Explanation (Deliverable 6) and Gap Analysis (Deliverable 9) possible to perform honestly in the future, the same way Series 70's Phase 1 investigation was only possible because MIC v2's objects already carried `provenance` fields.

---

## Summary

MSI v1 is designed as nine intelligence domains (Price Structure, Support & Resistance, Options Market Structure, Futures Structure, Volatility Structure, Liquidity, Time Structure, Cross-Asset, and a Regime Intelligence synthesis layer), built on a fourteen-concept first-principles vocabulary (Deliverable 1) and a domain-tagged ontology (Deliverable 3), connected by an acyclic Dependency Graph (Deliverable 5) and a uniform four-layer Evidence Graph (Deliverable 4) deliberately reusing the shape that made MIC v2 explainable. The Gap Analysis's central finding is that the most urgent near-term work is not reasoning at all — it is historical data acquisition (option-chain-over-time, futures-OI-over-time), because this project's own history shows reasoning built ahead of real data availability produces exactly the "evidence discarded before persistence" failure mode that Series 66/69/70 spent three series diagnosing in MIC v2. The recommended build order (Deliverable 8) accordingly treats a data-acquisition sprint as the true first step, followed by Price Structure, then Support & Resistance, then Volatility, then Options Structure, then Futures Structure, then Liquidity, then Regime synthesis, then Time Structure/Cross-Asset, with Trade Management last and gated on a full historical corpus — consistent with this project's established discipline of measuring before optimizing and observing before acting.
