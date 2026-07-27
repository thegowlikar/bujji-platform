# Market Observation Foundation (MOF v1)
## Engineering Series 72 — Research & Specification

**Status:** Architecture only. No code, no pseudo-code, no implementation, no data downloading, no connector work, no changes to MIC v2/Trading Brain/Runtime/Replay. This document defines BUJJI's sensory system — the layer every future Market Structure Intelligence (MSI, Series 71) brain must consume from rather than each inventing its own data model.

**Relationship to Series 71:** MSI's Gap Analysis concluded BUJJI's limiting factor is no longer reasoning capability — it is observation availability. This document is the direct answer to that finding: it defines what must be observed, continuously, before Brain 1 (Price Structure) or any subsequent brain can be built on real rather than retrofitted data.

---

## Deliverable 1 — Observation Philosophy

**What is an Observation?**

An Observation is a timestamped, source-attributed record of something the market did or currently is — captured with zero interpretation. A candle's OHLC values are an Observation. An option strike's open interest at a given snapshot time is an Observation. India VIX's closing value is an Observation. An Observation answers only "what happened, according to which source, at what time" — never "what does it mean."

This is a stricter bar than it sounds. Even a swing high is *not* an Observation — a swing high requires comparing multiple candles and applying a confirmation rule, which is interpretation. A swing high is Derived Evidence built *from* Observations. The dividing line is: if producing the value requires looking at more than one raw data point, or requires any rule beyond "record what the source reported," it is no longer an Observation.

**The four-layer distinction** (this generalizes MOF's own scope, and matches — deliberately — the layering already proven in MIC v2 and specified for MSI in Series 71, so all three systems share one vocabulary):

| Layer | Definition | Example | Owned by |
|---|---|---|---|
| **Observation** | A raw, source-attributed, uninterpreted fact about the market at a point or interval in time. | "NIFTY spot closed at 24,015.30 at 15:30 on 2026-07-22, source: FYERS historical API." | **MOF** (this document) |
| **Derived Evidence** | A fact computed *from* one or more Observations by a deterministic, disclosed rule, still free of judgment about what it *means* structurally. | "A swing high formed at 24,050 on 2026-07-21, confirmed by two subsequent lower closes." | MSI (Series 71) / MIC v2 |
| **Intelligence** | An interpreted, structural or narrative conclusion synthesized from Derived Evidence, carrying confidence and provenance. | "Price Structure is in an EXPANSION phase, evidenced by three consecutive impulse swings with rising participation." | MSI (Series 71) / MIC v2 |
| **Decision** | An action or action-recommendation produced by the Trading Brain, consuming Intelligence (never Observations or Derived Evidence directly). | "Select DIRECTIONAL_PUT_SPREAD." | Trading Brain |

The critical discipline this table encodes: **no layer may be skipped, in either direction.** A Reasoning/Intelligence object must never cite a raw Observation directly (it must cite Derived Evidence); a Decision must never consume an Observation directly. This is the same discipline that made Series 69/70's "first divergence" tracing possible for MIC v2 — MOF exists specifically so that discipline has something real to stand on, since Series 71's Gap Analysis found that today, most of what MSI would need at the Observation layer either doesn't exist historically or exists but isn't stored as a first-class, replayable object.

MOF's scope is precisely and only the first layer. It does not decide what a swing is, what OI migration means, or what strategy to pick. It decides: what gets observed, from where, how often, with what quality guarantee, and how it is made available — identically — to both live and replay execution.

---

## Deliverable 2 — Observation Domains

| Domain | Purpose | Observation responsibility | Consumers | Historical availability (today) | Live availability (today) | Criticality |
|---|---|---|---|---|---|---|
| **Price** | Record spot OHLC over time — the most fundamental observation in the system. | Capture open/high/low/close/volume per bar at defined resolutions. | Price Structure, Regime, virtually every MSI domain. | **Available** — real FYERS historical OHLC used throughout Series 65–70. | **Available** — FYERS `fyers_historical`/`fyers_ohlc`/`fyers_quote` (subject to live token validity, currently blocked pending re-auth). | **Critical** — foundational to everything. |
| **Price Structure (raw swing/candle geometry)** | Not itself an Observation (it's Derived Evidence per Deliverable 1) — listed here only to clarify it is explicitly OUT of MOF's scope and belongs to MSI Brain 1. | N/A — MOF supplies Price; Price Structure interpretation is MSI's job. | — | — | — | — |
| **Options Chain (structure)** | Record which strikes/expiries exist and their static contract attributes. | Capture strike, option type, expiry, contract symbol per snapshot. | Options Market Structure, Strike Selection (future). | **Available** — NSE Bhavcopy-derived, used since Series 62. | **Available** — via option chain endpoints (not yet exercised live in this project). | **Critical.** |
| **Option Open Interest** | Record OI and OI-change per strike over time — the raw material OI Migration (MSI) is built from. | Capture OI, change-in-OI per strike per snapshot. | Options Market Structure. | **Partially available** — `OptionLiquiditySnapshot` (Series 64/65) carries OI from Bhavcopy, but only as a **single end-of-day snapshot per session**, not a genuine intraday or multi-day time series. | Not currently ingested live in this project. | **Critical**, and per Series 71's Gap Analysis, the single highest-priority gap for MSI Brain 4. |
| **Option Volume** | Record traded volume per strike/expiry over time. | Capture per-strike traded volume per snapshot. | Options Market Structure, Liquidity. | **Available in raw Bhavcopy** (Bhavcopy carries volume columns) but **not currently extracted** into `OptionLiquiditySnapshot` — confirmed a real, cheap gap since the source file already has it. | Not currently ingested live. | **Medium-High.** |
| **Option Liquidity (bid/ask/spread)** | Record executable market depth per strike. | Capture bid, ask (and depth if available) per strike per snapshot. | Liquidity, Strike Selection (future). | **Partially available** — `OptionLiquiditySnapshot` already carries `bid`/`ask` fields (Series 64) but Bhavcopy is an end-of-day settlement file, not a live quote — so historical bid/ask through this source is a settlement-time approximation, not genuine intraday liquidity history. | Available via FYERS quote/LTP endpoints for live capture, not yet wired for continuous collection. | **Medium.** |
| **Futures (price/basis)** | Record futures price alongside spot to derive basis. | Capture futures OHLC + expiry per bar. | Futures Structure. | **Not currently ingested** in this project's real-data pipeline (Bhavcopy FO files contain futures rows alongside options rows, but this project's ingestion — `build_session_records_from_bhavcopy` — has been used only for the options side to date, confirmed by all prior series' scripts operating on `option_chain_entries`/`option_chain_liquidity`). | Available via FYERS for futures instruments, not yet wired. | **High** for MSI Brain 5, currently **missing source** in practice despite being physically present in files already being read. |
| **Futures Open Interest** | Record futures OI over time for positioning reads. | Capture futures OI per snapshot. | Futures Structure. | Same as Futures — present in Bhavcopy FO files, not currently extracted. | Available via FYERS, not wired. | **High**, same status as Futures. |
| **Volatility (VIX)** | Record India VIX level over time. | Capture VIX close (and ideally intraday) per snapshot. | Volatility Structure, MIC v2 (already a live consumer since Series M1). | **Available** — real VIX has been sourced via FYERS and used throughout Series M1/65–70, though reconstructed per-date rather than a continuously stored series. | **Available**, subject to live token validity. | **Critical** — already proven in production use (Series M1's before/after measurement). |
| **Volatility (IV / term structure)** | Record per-strike implied volatility and its term structure shape over time — distinct from VIX (an index-level realized-vs-implied composite), needed for Volatility Structure's `TermStructureShape`/`SkewRegime` per MSI Deliverable 3. | Capture IV per strike/expiry per snapshot. | Volatility Structure. | **Not currently ingested** — Bhavcopy FO files do not carry IV directly (it would need to be derived from settlement price, itself a Derived Evidence computation, not an Observation — or sourced from a quote feed that publishes IV directly). | Not wired. | **Medium-High**, but genuinely a **missing source** requiring a sourcing decision (compute vs. source directly), not merely missing plumbing. |
| **Market Breadth** | Record advance/decline, new-high/new-low counts across the index's constituents. | Capture breadth statistics per bar. | Regime Intelligence, Cross-Asset. | **Not currently ingested.** | Not currently sourced by any connector this project has used. | **Medium.** |
| **Sector Rotation** | Record relative performance across sector indices over time. | Capture sector index levels/returns per bar. | Cross-Asset, Regime Intelligence. | **Not currently ingested.** | Not sourced. | **Low-Medium** — valuable but not foundational; several other domains should land first. |
| **ETF Flow** | Record net creation/redemption flow for relevant index ETFs, a participation proxy. | Capture flow figures per period (typically daily, given public data granularity). | Participation-related evidence across multiple MSI domains. | **Not currently ingested**, and typically only available at daily granularity from public sources. | Not sourced. | **Low** — genuinely useful but the lowest-resolution, most peripheral domain on this list. |
| **Cross-Asset (correlated instruments)** | Record price/structure of related markets (global indices, currency, rates) for MSI's Cross-Asset domain. | Capture OHLC per correlated instrument per bar. | Cross-Asset (MSI). | **Not currently ingested.** | Not sourced (would need instruments outside FYERS' typical NSE-focused coverage for global indices). | **Low-Medium.** |
| **Macro Calendar** | Record scheduled macro/economic events (RBI policy, US Fed decisions, CPI releases) and their timestamps. | Capture event name, scheduled time, category. | Time Structure (MSI), Regime Intelligence. | **Not currently ingested** — this is reference/calendar data, not a market feed, and would need a distinct sourcing approach (calendar/reference data provider, not FYERS). | Not sourced. | **Medium** — high value relative to its low collection cost, since it's static/scheduled data rather than continuous market data. |
| **Time (session/expiry calendar)** | Record trading session boundaries, holidays, and options expiry dates. | Capture session calendar and expiry calendar as reference data. | Time Structure (MSI), and implicitly every domain that needs to know "is the market open" / "how many days to expiry." | **Available** — NSE trading-day/expiry logic is already implicit in this project's Bhavcopy-date-driven corpus construction (e.g. the 41-day and 81-day corpora already respect real trading days), though not yet exposed as an explicit, reusable `ObservationSource` in its own right. | **Available**, same caveat. | **Critical**, but mostly an **organizational** gap (formalize what's implicitly already respected) rather than a sourcing gap. |
| **Corporate Events** | Record events (results, dividends, corporate actions) for constituents, relevant mainly if BUJJI ever extends beyond a pure-index instrument. | Capture event type, affected instrument, timestamp. | Regime Intelligence (index-level effects), Cross-Asset. | **Not currently ingested.** | Not sourced. | **Low** for BUJJI's current NIFTY-index-only scope; would rise in priority only if scope broadens to single-stock options. |

---

## Deliverable 3 — Observation Ontology

Reusing existing project vocabulary wherever a direct match exists (this project already has `HistoricalSessionRecord`, `OptionLiquiditySnapshot`, `PublishedState` — MOF's ontology is designed to compose with these, not replace them):

- **`ObservationSnapshot`** — a single, timestamped capture of one or more Observation domains at one instant (or one settlement point, for EOD sources). `HistoricalSessionRecord` (Series 62+) is already a concrete instance of this concept for the price/option-chain domains; MOF generalizes the pattern rather than introducing a competing one.
- **`ObservationSeries`** — an ordered sequence of `ObservationSnapshot`s for one domain over time, at a defined `ObservationResolution`. This is the concept currently **missing** for Option OI, Option Volume, Futures, and IV (Deliverable 2) — today these exist, at best, as isolated snapshots, not as a genuine series a domain brain could reason about *evolution* from (Deliverable 6 depends on this concept existing for every domain, not just Price).
- **`ObservationSource`** — the specific origin of a given Observation (e.g. "FYERS historical API", "NSE Bhavcopy FO file"), always attached to every `ObservationSnapshot`, never inferred after the fact.
- **`ObservationQuality`** — a disclosed, evidenced assessment of how trustworthy a given Observation is (e.g. an EOD-settlement-derived bid/ask is lower quality than a live quote-derived one) — never a hidden or silently-applied adjustment, consistent with Deliverable 10's "no silent data gaps" principle carried over from MSI.
- **`ObservationFreshness`** — how recently an Observation was captured relative to the time it is being consumed, distinct from Quality (a very fresh Observation can still be low-quality, e.g. a fast but noisy feed).
- **`ObservationConfidence`** — where a source itself publishes a confidence/reliability signal (rare, but some feeds flag stale/indicative quotes), this is captured and carried forward rather than discarded — distinct from Quality, which is MOF's own assessment; Confidence is the source's self-reported assessment, if any.
- **`ObservationCompleteness`** — for domains captured as sets (e.g. an options chain snapshot), whether the full expected set was captured (all strikes, all expiries) or only a partial one — critical for correctly representing "missing" vs. "not applicable" downstream, mirroring the `None`-not-fabricated discipline already established in Series 65–70.
- **`ObservationWindow`** — the time span an `ObservationSeries` covers, distinct from its resolution — needed because a series can be high-resolution but short-windowed, or low-resolution but long-windowed, and downstream consumers (per Deliverable 5) need both properties independently.
- **`ObservationResolution`** — the time granularity of an `ObservationSeries` (tick, 1m, 5m, 15m, hourly, daily, weekly per Deliverable 5) — a first-class, explicit property of every series, never left implicit.
- **`ObservationVersion`** — where a source can revise a previously-published value (e.g. a Bhavcopy corrected after initial publication, or a provisional VIX value later finalized), the version this Observation represents — needed for determinism: a replay must be able to specify and reproduce which version of an Observation it saw, exactly as `PublishedState`'s existing fingerprint/checksum discipline already requires at the corpus level.

This ontology is deliberately observation-layer-only — it does not introduce `StructureState`, `OIMigration`, or any Deliverable-1-Intelligence-layer term (those belong to MSI, Series 71). Where MOF and MSI's vocabularies meet, the boundary is exact: MOF stops at `ObservationSnapshot`/`ObservationSeries`; MSI's Evidence Graph (Series 71, Deliverable 4) begins at Derived Evidence, built *from* an `ObservationSeries`, never from a raw feed directly.

---

## Deliverable 4 — Observation Lifecycle

```
Raw Feed
  (the literal external source — FYERS API response, NSE Bhavcopy file,
   a future calendar/reference data provider — exactly as delivered,
   before BUJJI touches it)
        ↓
Observation
  (the Raw Feed parsed into an ObservationSnapshot: source-attributed,
   timestamped, domain-tagged, still uninterpreted — this is the exact
   moment a Raw Feed becomes governed by this document's ontology)
        ↓
Validation
  (checked against ObservationCompleteness/Quality expectations for its
   domain — e.g. "does this options-chain snapshot have the expected
   strike range" — rejection or quality-flagging happens here, never
   silent correction or interpolation)
        ↓
Normalization
  (converted into the domain's canonical ObservationSnapshot shape,
   regardless of which Raw Feed/ObservationSource produced it — so a
   Price Observation from FYERS and a hypothetical future Price
   Observation from a different source are structurally identical
   downstream; this is what makes source-hierarchy fallback in
   Deliverable 7 possible without every consumer needing to know which
   source produced a given Observation)
        ↓
Storage
  (persisted as part of an ObservationSeries — requirements only, no
   implementation, see Deliverable 8)
        ↓
Replay
  (an ObservationSeries, or a windowed slice of one, made available to a
   replay run in exactly the shape and values it would have had, at
   exactly the timestamps it would have had, live — this is the
   discipline Series M1 established for MIC v2's option_chain/vix
   threading, generalized here to every MOF domain)
        ↓
Evidence
  (MSI's Derived Evidence layer, Series 71 Deliverable 4, begins
   consuming ObservationSeries here — MOF's responsibility ends at this
   boundary)
        ↓
Intelligence
  (MSI's Reasoning Objects / Published Intelligence — entirely out of
   MOF's scope, shown only to make the full chain visible)
```

Two lifecycle stages deserve explicit emphasis because they are exactly where this project's own history (Series 65/68/M1/70) has found real bugs:

- **Validation must never silently pass through incomplete data.** Series 70's Phase 4 threading bug (fields silently `None` all the way to the final report) was a *downstream* instance of a more general failure mode — data available but not verified as present at the point it should have been used. MOF's Validation stage is designed to be the place this class of bug is caught structurally, once, for every domain, rather than rediscovered domain-by-domain.
- **Replay must be a real, first-class lifecycle stage, not a fallback path bolted onto live capture.** Series M1's entire sprint existed because Replay had silently diverged from Live for MIC v2's `build_observation()` — MOF specifies Replay as a named, required lifecycle stage precisely so a future domain cannot be built with live-only support and have its replay parity discovered as a bug three series later.

---

## Deliverable 5 — Time Model

| Observation type | Tick | 1m | 5m | 15m | Hourly | Daily | Weekly | Reasoning suitability |
|---|---|---|---|---|---|---|---|---|
| Price (spot) | Not currently sourced | Available (FYERS) | Available (FYERS) | Available (FYERS) | Available (FYERS) | Available, real corpus proven (Series 65+) | Derivable from daily, not separately sourced | 1m–hourly for structure; daily for regime-level context. |
| Options Chain (structure) | N/A (static per expiry cycle) | N/A | N/A | N/A | N/A | Available (Bhavcopy) | Derivable | Daily sufficient for contract existence; OI/liquidity need finer resolution (see below). |
| Option OI | Not sourced | Not sourced | Not sourced | Not sourced | Not sourced | **Available, but only as a single EOD point per session** — no intraday OI series exists today | Derivable from daily only | Daily is usable for cross-day OI Migration reads (MSI); intraday OI migration (the more valuable read for same-day structural shifts) is a genuine, currently-missing capability. |
| Option Volume | Not sourced | Not sourced | Not sourced | Not sourced | Not sourced | Available in raw Bhavcopy, **not yet extracted** | Derivable | Daily sufficient for most Liquidity/Options Structure needs; intraday volume would mainly serve Trade Management timing, a later-stage concern. |
| Option Liquidity (bid/ask) | Not sourced | Available via FYERS quote (not yet wired for continuous capture) | Same | Same | Same | Available as EOD-settlement approximation via Bhavcopy | Derivable | Live/near-live resolution needed for genuine execution-quality liquidity reads; EOD approximation is acceptable only for coarse historical Liquidity Structure regime reads, not for Strike Selection execution decisions. |
| Futures (price/OI) | Not sourced | Available via FYERS (not yet wired) | Available via FYERS (not yet wired) | Available via FYERS (not yet wired) | Available via FYERS (not yet wired) | Available in Bhavcopy FO files, **not yet extracted** | Derivable | Daily sufficient for Futures Structure's basis-regime/positioning reads; intraday mainly useful for Hedge Decisions timing, later-stage. |
| VIX | Not sourced | Available via FYERS | Available via FYERS | Available via FYERS | Available via FYERS | Available, real corpus proven (Series M1) | Derivable | Daily has already been proven materially useful (Series M1's measured impact); intraday VIX would refine Volatility Structure's regime-transition timing but is not currently a proven gap. |
| IV / term structure | Not sourced | Not sourced | Not sourced | Not sourced | Not sourced | Not sourced (see Deliverable 2 — needs a sourcing decision) | N/A until sourced | Daily would already unlock most Volatility Structure value; this is a "missing source" gap, not a resolution gap. |
| Market Breadth | Not sourced | Not sourced | Not sourced | Not sourced | Not sourced | Typically only daily granularity available from public sources | Derivable | Daily is the natural and sufficient resolution for this domain. |
| Sector Rotation | Not sourced | Not sourced | Not sourced | Available via FYERS for sector indices (not wired) | Available via FYERS (not wired) | Available via FYERS (not wired) | Derivable | Daily-to-hourly; this is a lower-priority domain per Deliverable 2, so resolution is not currently a live design constraint. |
| ETF Flow | Not sourced | N/A | N/A | N/A | N/A | Typically only daily/end-of-period granularity from public sources | Only sensible resolution | Daily is the domain's natural ceiling — this is inherent to how flow data is published, not a gap to close. |
| Cross-Asset | Not sourced | Depends on instrument/source, not yet evaluated | — | — | — | Generally available for major global indices | Derivable | Daily sufficient for cross-asset confirmation reads (MSI's stated use, Series 71 Deliverable 2). |
| Macro Calendar | N/A (event-based, not periodic) | N/A | N/A | N/A | N/A | N/A | N/A | Event timestamp itself is the only "resolution" that matters; not a time-series domain. |
| Time (session/expiry) | N/A (reference data) | N/A | N/A | N/A | N/A | N/A | N/A | Reference/calendar data, not a time-series domain — resolution model doesn't apply. |
| Corporate Events | N/A (event-based) | N/A | N/A | N/A | N/A | N/A | N/A | Same as Macro Calendar. |

**Cross-cutting observation on this table:** the single most consequential resolution gap is **Option OI at intraday resolution.** Every other high-criticality domain (Price, VIX) already has proven historical AND live availability at fine resolution; Option OI — the domain Series 71 flagged as the single highest-priority MSI gap — is stuck at EOD-only, historically, with no live path established at all. This is the resolution gap Phase B/C of the roadmap (Deliverable 10) should treat as the priority case, not a peer among equals.

---

## Deliverable 6 — Observation Evolution

**BUJJI must reason from change, not snapshots.** This is not a preference — it is a structural necessity already proven twice in this project's own history: MIC v2's `context_stability` (Series 70) is *itself* defined as a rate of change (`transition_count / (context_lifetime - 1)`) over a *sequence* of contexts, not a property of any single context snapshot; and Series M1's entire measured improvement came from MIC v2 gaining access to option_chain/VIX *as they evolved through the replay window*, not as a single static value.

The general pattern, stated once and then instantiated per domain:

```
<Domain> Snapshot  →  <Domain> Series  →  <Domain> Evolution
  (a single point)     (an ordered           (a characterization
                        sequence of            of how the series is
                        snapshots, per          changing: direction,
                        Deliverable 3's         rate, acceleration,
                        ObservationSeries)      regime-shift — this
                                                is where MOF's scope
                                                ends and MSI's Derived
                                                Evidence layer begins,
                                                per Deliverable 1's
                                                four-layer table)
```

Instantiated across every domain from Deliverable 2:

| Snapshot | → Series | → Evolution (consumed by, in MSI) |
|---|---|---|
| Price | Price Series | Price Structure (swing/trend/impulse reads — Series 71 Brain 1) |
| Option OI | OI Series | OI Migration (Options Market Structure — Series 71 Brain 4) |
| VIX / IV | Volatility Series | Volatility Evolution (Volatility Structure — Series 71 Brain 3) |
| Option Liquidity | Liquidity Series | Liquidity Evolution (Liquidity — Series 71 Brain 6) |
| Market Breadth | Breadth Series | Breadth Evolution (Regime Intelligence) |
| Options/Futures/Price participation evidence | Participation Series | Participation Evolution (feeds multiple MSI domains — Deliverable 1's `ParticipationState`, instantiated per-domain, per Series 71 Deliverable 3) |
| Support/Resistance test history | (built from Price Series, not a separate raw Observation) | Support Evolution / Resistance Evolution (Support & Resistance — Series 71 Brain 2) — included here to make explicit that S&R Evolution is entirely derivable from Price Series alone, requiring no new Observation domain of its own |
| Futures OI/basis | Futures Series | Futures positioning Evolution (Futures Structure — Series 71 Brain 5) |

**The generalization, stated as a requirement for MOF as a whole:** every domain in Deliverable 2 that is marked with real or planned `ObservationSeries` support must be storable and replayable as a *series*, not merely as a latest-value snapshot — this is a direct, binding requirement on Deliverable 8's storage/replay specification, not an aspiration. A domain that can only ever answer "what is the current value" cannot support Evolution, and therefore cannot properly serve any MSI brain, regardless of how good its snapshot data is. This is precisely the deficiency Deliverable 2 found in today's Option OI capability (EOD-snapshot-only) and is the central architectural argument for why Phase B of the roadmap (Deliverable 10) must prioritize *series* construction, not merely more snapshots.

---

## Deliverable 7 — Data Provenance

For every observation domain, MOF requires the following to be defined and disclosed (not necessarily all populated yet — this is the schema of what provenance must eventually cover):

- **Source hierarchy:** an explicit, ordered list of acceptable sources for a domain (e.g. Price: FYERS live quote → FYERS historical API → NSE Bhavcopy-derived close, in that preference order), so that which source produced a given Observation is always a deliberate, disclosed choice, never an accident of whichever connector happened to be called.
- **Official source:** the single source treated as ground truth when multiple sources disagree (for Price/Options in this project to date, that has consistently been real NSE Bhavcopy for historical work and FYERS for live — this should remain formalized as policy, not merely as historical practice).
- **Fallback source:** what MOF falls back to when the official source is unavailable (e.g. FYERS token expiration, the exact situation blocking this project's 81-day corpus rerun as of this session) — and critically, **the fallback must be disclosed in the resulting Observation's `ObservationSource`/`ObservationQuality` fields**, never silently substituted as if it were the official source.
- **Replay source:** which stored `ObservationSeries` a replay run reads from — required to be the *same* normalized shape (per Deliverable 4's Normalization stage) as whatever live capture would have produced, so that Replay/Live parity (the exact property Series M1 fixed for MIC v2) is a property MOF guarantees at the observation layer, not something each downstream domain must re-establish for itself.
- **Confidence:** carried from the source where the source itself publishes one (Deliverable 3's `ObservationConfidence`); where a source does not publish confidence, this field is `None`, never fabricated.
- **Missing-data behavior:** an Observation that could not be captured (feed down, incomplete snapshot) must be represented as absent/`None` at every downstream layer, exactly matching this project's established, hard-won discipline (Series 65/69/70's "never fabricate, never interpolate, missing means missing" rule) — MOF is the layer where this discipline must originate, since every downstream layer inherits whatever MOF does here.
- **Versioning:** if a source revises a previously-published value, MOF must be able to represent both the original and revised `ObservationVersion` distinctly, so a replay can deterministically choose "the value as it was known at the time" versus "the value as later corrected" — a distinction this project has not yet needed (no source used to date has required revision handling) but which must be designed for before any domain with revision-prone sources (corporate actions, corrected Bhavcopy files) is added.
- **Timestamp ownership:** every Observation's timestamp must be the *source's* reported time (when did the market event happen), never the time BUJJI happened to capture or process it — this distinction matters most for live capture, where processing delay must never be conflated with market event time, and is a precondition for genuine Replay/Live parity.
- **Quality guarantees:** each domain's `ObservationQuality` must be an explicit, documented statement of what the domain does and does not guarantee (e.g. "Bhavcopy-derived bid/ask is an EOD settlement approximation, not a live quote, and must not be used for execution-quality liquidity decisions" — stated plainly per Deliverable 5's findings) rather than left for a downstream consumer to discover the hard way.

---

## Deliverable 8 — Storage & Replay Requirements

No storage implementation is specified here — only the requirements any future implementation must satisfy, stated as binding constraints on Phase A/B/E of the roadmap (Deliverable 10):

- **Replay:** for any historical time window, MOF must be able to reproduce the exact `ObservationSeries` (values, timestamps, sources) that would have been available at that time — not the *current* best-known values if they have since been revised (per Deliverable 7's Versioning requirement) — mirroring exactly the discipline Series M1 established for MIC v2's `option_chain_by_timestamp`/`vix_by_timestamp` threading, generalized to every domain in Deliverable 2.
- **Qualification:** MOF's `ObservationSnapshot`/`ObservationSeries` metadata (source, quality, completeness, freshness) must be capable of flowing into BUJJI's existing Qualification recording pattern (`QualificationRecord`/`sessions[]`, Series 69/70) as domain-prefixed fields when a decision's provenance needs to be traced all the way back to the Observation layer — not as a new, parallel recording system (the same non-duplication principle already stated for MSI in Series 71 Deliverable 6 applies here, one layer earlier).
- **Observatory:** MOF's fields, where they flow into Qualification, must integrate into the existing Observatory's `CANONICAL_FIELD_ORDER`/`first_divergence` comparison mechanism at the correct position — earlier than any MSI Derived Evidence/Intelligence field that depends on them, consistent with the Deliverable 1 four-layer ordering and the Dependency Graph precedent established in Series 71 Deliverable 5.
- **Historical comparison:** two replay runs over the same nominal historical window must be comparable at the Observation layer itself, not only at the Intelligence layer — i.e., MOF must be able to answer "did the underlying Observations differ between these two runs" independently of whether any MSI/MIC v2 reasoning also differed, which is a strictly more granular diagnostic capability than what Series 69/70's Observatory currently provides (that Observatory starts at the Qualification/Intelligence layer; MOF extends the same idea one layer down).
- **Determinism:** replaying the same `ObservationSeries` window twice must produce byte-identical results — no wall-clock dependency, no non-deterministic ordering, no randomness — the same hard requirement that has governed every layer of this project since its qualification fingerprint was first established.
- **Explainability:** every `ObservationSnapshot` must be traceable to its `ObservationSource` and, where applicable, `ObservationVersion` — sufficient that a future Series-70-style investigation ("why did this value change") can be answered by reading MOF's own stored metadata, without needing to re-derive or guess at where a value came from.

---

## Deliverable 9 — Gap Analysis

| Domain / capability | Category | Priority |
|---|---|---|
| Price (spot OHLC) — historical + live | **Already available** | Maintain. |
| Options Chain structure (strikes/expiries) | **Already available** | Maintain. |
| VIX — historical + live | **Already available** | Maintain; this project's own Series M1 result is the clearest proof of value once a domain crosses from "available" to "actually threaded through." |
| Option OI — historical | **Transported but unused as a series** (single EOD snapshot exists; no `ObservationSeries` for OI exists) | **Highest.** Directly blocks MSI Brain 4 and is Series 71's own named top gap. |
| Option Volume | **Missing storage** (present in raw Bhavcopy, not extracted into any BUJJI model) | High — cheap to close (data is already being read from the same files), disproportionate value for Liquidity/Options Structure. |
| Option Liquidity (bid/ask) — historical | **Available, but only as an EOD-approximation** (per Deliverable 5/7) | Medium — usable for coarse historical Liquidity Structure regime reads now; live/execution-quality liquidity is a separate, larger gap (below). |
| Option Liquidity (bid/ask) — live | **Missing source integration** (FYERS quote capability exists per Deliverable 2, not wired for continuous capture) | Medium-High for eventual Strike Selection execution use. |
| Futures price/OI — historical | **Missing storage** (present in the same Bhavcopy FO files already being read, not extracted) | High — same "cheap, already-in-hand" argument as Option Volume. |
| Futures price/OI — live | **Missing source integration** (FYERS futures instruments not yet wired) | Medium. |
| IV / term structure | **Missing source** (not derivable from current Bhavcopy fields without new computation, not currently sourced live either) | Medium-High, but requires a sourcing decision before it can even become a storage/replay gap. |
| Market Breadth | **Missing source** | Medium — genuinely new external sourcing required. |
| Sector Rotation | **Missing source** (though the underlying instruments are likely FYERS-reachable) | Low-Medium. |
| ETF Flow | **Missing source** | Low. |
| Cross-Asset | **Missing source** | Low-Medium. |
| Macro Calendar | **Missing source** (reference/calendar data, distinct sourcing path from market feeds) | Medium — low cost relative to value once a source is chosen. |
| Time (session/expiry calendar) | **Already available implicitly, missing observability as a first-class object** | Low effort, worth formalizing early since Phase A (Deliverable 10) benefits from it being explicit. |
| Corporate Events | **Missing source**, and low relevance at current NIFTY-index-only scope | Low, defer. |
| Replay support for OI/Futures/IV as genuine series (not snapshots) | **Missing replay** (the domains above are missing storage AND replay both — replay cannot exist before storage does) | Follows directly from the storage gaps above — sequenced, not independent. |
| MOF metadata (source/quality/completeness) flowing into Qualification/Observatory | **Missing qualification, missing observability** | Should follow, not precede, the underlying domain gaps above — there is nothing to make observable yet for domains that don't have series storage. |

**The clear priority ordering this table produces:** Option OI (series, not snapshot) first, Option Volume and Futures price/OI second (cheap, already-in-hand data), Option Liquidity live and IV third, everything else (Breadth, Sector Rotation, ETF Flow, Cross-Asset, Macro Calendar, Corporate Events) lower priority and largely independent of each other, to be sequenced opportunistically rather than on a hard critical path.

---

## Deliverable 10 — Future Roadmap

**Phase A — Observation Infrastructure.** Establish the `ObservationSnapshot`/`ObservationSeries`/`ObservationSource` shapes (Deliverable 3) as real, shared structures — not yet populated with new domains, but formalizing what already exists (Price, Options Chain, VIX, and the implicit Time/session-calendar handling) into this common shape. **Sequenced first** because every subsequent phase depends on having one consistent shape to populate, and doing this after Phase B/C would mean retrofitting newly-acquired data into a shape designed after the fact — exactly the kind of rework this project's own Gap Analysis (Series 71) warned against.

**Phase B — Historical Acquisition.** Close the historical `ObservationSeries` gaps identified in Deliverable 9, in priority order: Option OI as a genuine series (not EOD snapshot), then Option Volume and Futures price/OI (both cheap — same source files already in use), then Option Liquidity historical refinement and IV/term structure (requires a sourcing decision first). **Sequenced second, before live acquisition,** because this project's own established practice (every series from 65 through 70) has validated reasoning changes against historical corpora before ever touching live data — Observation infrastructure should follow the same discipline: prove a domain's value and correctness against history before paying the ongoing operational cost of live capture.

**Phase C — Live Acquisition.** Wire the live-capable sources already identified (FYERS quote/futures endpoints) for continuous collection of the domains proven valuable in Phase B. **Sequenced third**, after Historical, for the reason above — and because live acquisition introduces operational concerns (token refresh, the exact FYERS re-authentication issue currently blocking this project's own corpus reruns, rate limits, uptime) that are wasted effort to build before a domain has already justified its inclusion historically.

**Phase D — Observation Validation.** Implement the Validation lifecycle stage (Deliverable 4) — completeness/quality checks — across all domains brought online in Phases B/C. **Sequenced fourth, not first,** deliberately: validation rules can only be meaningfully designed once real data from Phases B/C reveals what "incomplete" or "low quality" actually looks like for each domain in practice; designing validation rules speculatively, before any real data exists to validate, risks the same fabrication-adjacent failure mode this document explicitly warns against (guessing at what "normal" looks like rather than observing it).

**Phase E — Observation Replay.** Implement genuine Replay support (Deliverable 4/8) for every domain now in storage — ensuring Replay/Live parity is designed in from the start for these new domains, rather than discovered as a gap later the way Series M1 had to discover and fix it for MIC v2's option_chain/VIX threading. **Sequenced fifth,** after Validation, because Replay's correctness depends on Validation already having defined what a "valid" stored Observation looks like — replaying invalid or unvalidated data would just reproduce bad inputs deterministically, which is not the same as genuine replay correctness.

**Phase F — Market Structure Brain 1 (Price Structure).** Only once Phases A–E are complete for at least the Price domain (already largely available, per Deliverable 9, but still needs to pass through the newly-formalized Phase A/D/E requirements to be a proper foundation) does Series 71's own roadmap begin. **Sequenced last, deliberately** — this is the direct, concrete instantiation of Series 71's own Gap Analysis conclusion: reasoning work should not begin before its observation foundation is genuine, replayable, and validated, because this project has already paid the cost of building reasoning ahead of data plumbing three times (Series 65, 68, M1) and does not need a fourth instance at the MSI layer.

**Why this order and not, say, building all domains' Phase B before any Phase C, or interleaving phases per domain:** the phase ordering (A→B→C→D→E→F) is intentionally domain-agnostic and sequential rather than domain-by-domain vertical slices, because Phase A's shared shape and Phase D/E's shared validation/replay disciplines are exactly the parts of this project's history (MIC v2, Qualification, Observatory) that proved most valuable when built once and reused, and most costly when each domain reinvented its own version. A domain-by-domain vertical-slice approach would very likely reproduce Series 70's discovery — a real capability computed correctly but not consistently threaded through to where it's needed — at the observation layer instead of the reasoning layer.

---

## Summary

MOF v1 defines Observation as the strictly uninterpreted, source-attributed base layer beneath MSI's Derived Evidence/Intelligence layers and Trading Brain's Decision layer (Deliverable 1), covering 17 observation domains (Deliverable 2) of which **Price, Options Chain structure, and VIX are genuinely production-proven today; Option OI, Option Volume, and Futures data are the highest-priority gaps** because the raw source files are already being read by this project but not fully extracted (Deliverable 9). The foundation's central architectural requirement (Deliverable 6) is that every domain must be storable and replayable as an evolving `ObservationSeries`, not a latest-value snapshot — because this project has now proven twice, independently, in MIC v2 (`context_stability` as a rate over a context sequence) and in Series M1 (option_chain/VIX evolution mattering more than any single value), that reasoning from change rather than snapshots is not a stylistic preference but the actual mechanism by which this system's intelligence layers produce value. The recommended roadmap (Deliverable 10) sequences Infrastructure → Historical Acquisition → Live Acquisition → Validation → Replay → Brain 1, treating Series 71's own Brain 1 as the deliberate last step of this document rather than the first step of the next one — because this project's history (Series 65, 68, M1) has shown three times that reasoning work started ahead of real observation plumbing produces exactly the "computed but not persisted" failure this project has spent multiple series (66, 69, 70) diagnosing and fixing after the fact.
