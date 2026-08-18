# Phase 17H.2.0.3 — Market Reality Source Architecture Decision

**Status: AUDIT + ARCHITECTURE DECISION. No code. No schema changes. No
adapters. No provider implementation.**

Every claim below is tagged **VERIFIED** (read directly in code this
session), **EXISTING** (a real component/mechanism already in the
codebase, cited by path), **PROPOSED** (this document's own
recommendation, not yet built), or **UNKNOWN** (genuinely unestablished).

---

## Part 0 — The finding that reframes this whole document

**This project already has a canonical, documented reality hierarchy —
`docs/MOF_V1_FOUNDATION.md` (Engineering Series 72), predating this
engagement's own 17E-onward work — and Part 3's "design the conceptual
model" task is substantially already done, not a blank page.**

**VERIFIED** — MOF v1 Deliverable 1 defines, in its own words:

> "The four-layer distinction... generalizes MOF's own scope, and
> matches — deliberately — the layering already proven in MIC v2 and
> specified for MSI in Series 71, so all three systems share one
> vocabulary": **Observation → Derived Evidence → Intelligence →
> Decision.**

`bujji/market_observation/models.py`'s own module docstring confirms it
**is** the concrete implementation of MOF v1's Observation layer:
*"Implements MOF v1's... Observation-layer ontology."*

**This maps directly onto the "Reality → Memory → Understanding →
Intelligence → Strategy" language this engagement has used since Phase
17E — they are not two competing hierarchies, they are the same lineage,
refined:**

| MOF v1 (Series 72, earlier) | This engagement's terms (17E+) | Concrete implementation |
|---|---|---|
| Observation | **Reality** (Layer 0) + **Memory** (Layer 1) | `bujji.market_observation` (MOC) wrapped by `bujji.market_reality` (Layer 0), further materialized by `bujji.market_timeseries` (Layer 1) |
| Derived Evidence | (spans the Memory/Understanding boundary) | `market_timeseries.Candle`/`FuturesStatistics` sit here — deterministic, disclosed-rule computations, still "free of judgment about what it means structurally" per MOF's own definition |
| Intelligence | **Understanding** + **Intelligence** | MSI (`bujji.msi_*`), MIC v2 |
| Decision | **Strategy** | Trading Brain |

**Architectural risk, named plainly:** this mapping is this document's
own reconstruction — **no code or doc anywhere states it explicitly**.
Two vocabularies for what is substantially one hierarchy is exactly the
kind of drift that produces "wait, is Understanding the same as Derived
Evidence?" confusion later. **PROPOSED**: this document's Part 3
restates the hierarchy using the newer, more-precise five-layer language
(since Reality/Memory splitting MOF's single "Observation" into two
tiers is a genuine, useful refinement this engagement made — Layer 0's
immutability + certification gate is stricter than anything MOF v1
specified), but explicitly cross-referenced to MOF v1 rather than
silently supplanting it.

---

## Part 1 — Current source provenance handling (audited, not assumed)

### Where source information exists today

**VERIFIED**, three distinct, only-partially-overlapping mechanisms:

1. **`bujji.market_observation.models.ObservationIdentity.source`** — a
   bare string field on every MOC `Observation`. Present since MOF v1's
   implementation.
2. **`bujji.market_observation.models.ObservationProvenance`** —
   `originating_source`, `acquisition_timestamp`, `normalization_timestamp`,
   `origin` (`LIVE`/`REPLAY`/`HISTORICAL_RECONSTRUCTION`),
   `provenance_version`, `transformation_history`. Richer than
   `ObservationIdentity.source` alone.
3. **`bujji.market_reality.models.Layer0Lineage`** (17E, layered ON TOP
   of the above two, wrapping the same MOC `Observation`) —
   `source`, `access_method`, `event_timestamp`, `capture_timestamp`,
   `certification_status`, `certification_ref`, `confidence`,
   `transformation_history` (its own, separate tuple from MOC's).

### A real, concrete redundancy found by checking actual construction code, not assumed

**VERIFIED** — `bujji/market_reality/capture.py::build_raw_observation()`
constructs the wrapped MOC `Observation` with several of its OWN quality/
provenance fields **hardcoded to placeholder values**, never real ones:

```python
completeness=1.0,                                  # always 1.0, never computed
freshness=0.0,                                      # always 0.0, never computed
confidence=None,   # "MOC's source-published confidence: FYERS publishes none."
missing_fields=(),                                  # always empty
validation_status=moc_taxonomy.VALIDATION_UNKNOWN,  # always UNKNOWN
source_quality=moc_taxonomy.SOURCE_QUALITY_UNKNOWN, # always UNKNOWN
```

**This means MOC's `ObservationQualityMetadata` is structurally inert for
every Layer-0-captured record today** — the REAL provenance (which
source, which access method, when, with what certification) lives
exclusively in the separate `Layer0Lineage` block, not in the MOC layer
these fields nominally belong to. Two provenance mechanisms exist; only
one is populated meaningfully. **Architectural risk, restated in Part 5.**

### Does `RawObservation` already carry enough provenance for multi-source work?

**VERIFIED — yes, for the Reality/Memory tiers specifically.**
`Layer0Lineage.source` + `.access_method` already distinguish "FYERS via
`direct_sdk_fyers_broker_py`" from "FYERS via `fyers_websocket`" (proven
concretely by the certification-gate collision fix, this session's own
prior work — two real, coexisting certifications for the same instrument,
different access methods, now correctly indexed as distinct). **A third
source (Bhavcopy) would be a third distinct `source`/`access_method`
pair — no new field is needed to distinguish it structurally.**

**What is genuinely missing**: a `source` value has never been assigned
to a Bhavcopy-originated record through this mechanism, because Bhavcopy
ingestion (`options_observation`/`futures_observation`) **does not go
through `market_reality` at all** — it builds `OptionObservation`
directly via its own `build_option_observation()`, using MOC's
`ObservationProvenance` (item 2 above), not `Layer0Lineage` (item 3).
**Bhavcopy-sourced records and FYERS-Layer-0-sourced records currently
use TWO DIFFERENT provenance mechanisms, not the same one with different
values.** This is the real architectural gap this document must resolve,
not a hypothetical.

### Does any component currently mix sources without declaring it?

**VERIFIED — no component currently combines Bhavcopy and FYERS data at
all**, so no live mixing-without-declaration bug exists today. But this
is worth stating precisely: `ReplayChainProvider` (Bhavcopy-only) and
`FuturesStatistics`/`Candle` materializers (Layer-0/FYERS-only) are
completely separate pipelines today — the absence of a mixing bug is
because **nothing has been built yet that would need to mix them**, not
because a source-fusion safeguard exists and is working. The moment
`LiveMarketDataProvider` (FYERS) and Bhavcopy-sourced historical data are
consumed by the SAME downstream component (e.g. a future backfill
pipeline populating `market_timeseries.CandleStore` from BOTH sources),
this becomes a live risk with no existing guardrail. **PROPOSED**: named
explicitly in Part 4 (conflict handling) and Part 5 (risks).

### Which layer should know what — answered against real code, not designed fresh

| Concern | VERIFIED layer that already owns it | Notes |
|---|---|---|
| Raw source (which system) | `Layer0Lineage.source` (Reality) | Already correct; Bhavcopy path needs the same treatment (Part 6) |
| Broker/exchange identity | `ObservationIdentity.exchange`/`.segment` (MOC, Observation) | Already present, broker-agnostic by design |
| Access method | `Layer0Lineage.access_method` (Reality) | Already the exact mechanism that distinguishes REST from websocket for the SAME broker — proven this session |
| Timestamp semantics | `Layer0Lineage.event_timestamp`/`.capture_timestamp` (Reality) | Already the dual-bound mechanism (17F.5); already proven to handle "no event timestamp available" (option chain, Gate B) correctly via `Optional[str] = None` |
| Confidence/completeness | **Currently split, inconsistently, between MOC (inert placeholders) and Layer0Lineage (`confidence` only, no `completeness`)** | Real gap — see Part 5 |

---

## Part 2 — Existing schema boundaries, audited component by component

- **`RawObservation`/`Layer0Lineage`** (`market_reality`): the correct,
  already-proven home for Reality-tier provenance. **EXISTING, reusable
  as-is.**
- **`OptionObservation`** (`options_observation`): wraps a MOC
  `Observation` too, but via `build_option_observation()`
  (`options_observation/engine.py`), a **separate construction path**
  from `market_reality.capture.build_raw_observation()`. Both ultimately
  produce a MOC `Observation`, but neither goes through the other.
  **EXISTING, but structurally parallel to `RawObservation`, not beneath
  it** — `OptionObservation` is not itself a `RawObservation`; it is an
  independent wrapper around the same underlying MOC layer.
- **`FuturesStatistics`** (`market_timeseries`): does not wrap a MOC
  `Observation` at all — it is its own dataclass with
  `source_observation_ids` (a tuple of Layer 0 observation IDs it was
  computed from) and its own `calc_version`/`materializer_id`. **VERIFIED
  correct for its purpose**: it's a Derived-Evidence-tier record, and per
  MOF's own discipline ("Derived Evidence... computed from one or more
  Observations by a deterministic, disclosed rule"), referencing source
  IDs rather than re-wrapping them is the right shape.
- **`MarketState`/MSI packages**: **VERIFIED, previously audited (17F.5
  Part 3)** — zero of the six MSI/legacy Understanding-tier packages
  import `market_reality` or `market_timeseries` at all. **This means
  there is currently no live path by which source provenance could even
  reach the Intelligence tier** — not a source-fusion risk yet, because
  nothing downstream consumes multi-source Reality data at all yet.
- **Memory-related packages** (`outcome_memory`, `portfolio_intelligence`,
  `observation_memory`-shaped components referenced in project memory):
  **UNKNOWN, not read this session** — flagged as unaudited rather than
  assumed compatible or incompatible. These were built across earlier
  phases (15B–15N per project history) predating the Layer 0/1 Reality
  work entirely; whether they have their own third provenance convention
  is a real open question this document does not resolve.

---

## Part 3 — The reality hierarchy (restating MOF v1, not replacing it)

**PROPOSED**, cross-referenced to Part 0's mapping:

```
RAW REALITY (MOF: "Observation")
    |
    |  bujji.market_reality (Layer 0) -- immutable, certification-gated,
    |  dual-bound (event_time/knowledge_time), one record per direct
    |  broker/exchange capture. Multiple sources coexist HERE, each its
    |  own (source, access_method) pair -- proven structurally sound by
    |  the certification-gate fix this session.
    v
DERIVED REALITY (MOF: "Observation" tier's materialized subset +
                 start of "Derived Evidence")
    |
    |  bujji.market_timeseries (Layer 1) -- Candle, FuturesStatistics.
    |  Deterministic, disclosed-rule computations FROM Layer 0 records,
    |  referencing source_observation_ids, never re-observing. THIS IS
    |  WHERE MULTI-SOURCE FUSION HAPPENS, if it happens at all (Part 4).
    v
UNDERSTANDING (MOF: "Derived Evidence" -> "Intelligence" boundary)
    |
    |  Not yet fed by Layer 0/1 at all (17F.5 finding, still true).
    |  Where structural interpretation (regime, structure, zones) would
    |  live, per the standing "not yet" architectural boundary.
    v
INTELLIGENCE / STRATEGY (MOF: "Intelligence" -> "Decision")
    |
    |  MSI, MIC v2, Trading Brain. Out of scope for this document
    |  entirely, restated per the standing constraint.
```

**Where source fusion happens: Layer 1 (Derived Reality), and ONLY
there — never at Layer 0.** This is not a new invention; it follows
directly from Layer 0's own, already-established immutability rule: a
Layer 0 record is one source's direct capture, permanently, and can
never be retroactively "merged" with another source's record without
violating immutability. Fusion — e.g. a `FuturesStatistics` row computed
from a FYERS-live futures Candle AND a Bhavcopy-derived spot Candle for
basis — happens at the MATERIALIZER, which reads multiple Layer 0/1
inputs and produces one output record **that itself declares its
multi-source lineage** (Part 4).

---

## Part 4 — Source conflict handling

### Are conflicts errors?

**PROPOSED, and the recommendation is: no, not by default — they are
separate, both-preserved observations, with reconciliation deferred to
the consumer, never silently resolved by the capture layer.**

Reasoning, grounded in what already exists: Layer 0's own immutability
rule already answers this question in spirit, just not yet for
cross-source conflicts specifically. A FYERS futures price of 24,580 and
a Bhavcopy settlement of 24,560 for what might be "the same" moment are
**not actually the same observation** — they have different
`(source, access_method)` identity, almost certainly different
`event_timestamp`/`capture_timestamp` (a live tick vs. an EOD settlement
computed hours later), and different meaning (a traded price vs. an
exchange-computed settlement value, which are not even the same concept
— settlement is itself often a volume-weighted or committee-determined
value, not simply "the last trade"). **Treating this as an "error"
would require deciding one source is wrong, which neither this document
nor any component in this codebase is positioned to judge.** Both are
real, both get stored as separate Layer 0 records (once Bhavcopy is
routed through the same Reality-tier mechanism, Part 6), and a consumer
computing basis or cross-checking values makes an explicit, disclosed
choice about which it's using — never a silent default.

### Different timestamps (exchange / receipt / candle-window)

**EXISTING, already solved, not a new problem**: `Layer0Lineage` already
carries this exact distinction — `event_timestamp` (exchange-reported,
when available) vs. `capture_timestamp` (receipt time, always present).
`Candle`'s own `window_start`/`window_end` (nominal) vs.
`first_event_time`/`last_event_time` (actual observed span) is a THIRD,
already-built distinction for the materialized tier (17F.1's own design).
**No new timestamp concept is needed** — a cross-source conflict is
just two records, each with their own already-correct timestamp triple,
never conflated.

### Who owns reconciliation?

**PROPOSED**: the **materializer** that reads multiple sources, never
the capture/collector layer. A collector's only job (per every collector
built this engagement — the depth poller, the discovery scripts) is to
record what it observed, honestly, from one source. Deciding "which
source wins" for a given derived value (e.g. "use Bhavcopy settlement
for daily basis, FYERS live for intraday basis") is a **materializer
design decision**, explicit and disclosed in that materializer's own
`calc_version`-hashed definition — exactly the same discipline
`futures_stats_materializer.py` already uses for its OHLC-fold rule.
**No new architectural mechanism is needed here either** — `calc_version`
already exists specifically to make "which rule, over which inputs" a
reproducible, content-hashed fact rather than an implicit assumption.

---

## Part 5 — Source capability profiles: should this replace the Bhavcopy-specific mandatory-field assumption?

**PROPOSED — yes, and this was already decided, not newly proposed
here.** `docs/PHASE_17H2_0_LIVE_PROVIDER_CONTRACT_DECISIONS.md` Decision
1 (previous phase, this session) already locked exactly this:
`ObservationSourceProfile`, generalizing the existing
`MANDATORY_OPTIONS_OBSERVATION_FIELDS`/`KNOWN_UNAVAILABLE_FROM_BHAVCOPY`
pattern to be source-keyed. This document extends that decision to a
third, now-evaluated source:

```
PROFILES (PROPOSED, not built):
  bhavcopy:
    OHLC: yes | settlement: yes | volume: yes | OI: yes
    intraday: no | depth: no | bid_ask: no | LTP-distinct-from-close: no
  fyers_historical:
    OHLC: yes | settlement: no | volume: yes | OI: no
    intraday: yes | depth: no | bid_ask: no | LTP-distinct-from-close: no
  fyers_live_optionchain:
    OHLC: no | settlement: no | volume: yes | OI: yes
    intraday: n/a (point-in-time) | depth: no | bid_ask: yes | LTP: yes
  fyers_live_depth:
    OHLC: no | settlement: no | volume: no | OI: yes
    intraday: n/a | depth: yes (full ladder) | bid_ask: yes (via ladder) | LTP: yes
```

Every row above is **VERIFIED** against Gate B's real captures and the
17H2.0.2 historical-source audit — not invented for this document. Two
sources (`fyers_historical`, futures specifically) remain **UNKNOWN**
per that same audit (§1.1) — this profile table inherits that
uncertainty rather than resolving it.

### Architectural risks (consolidated from Parts 1–5)

1. **Two parallel provenance mechanisms** (MOC's own
   `ObservationProvenance`/`ObservationQualityMetadata` vs.
   `Layer0Lineage`), with the former structurally inert wherever Layer 0
   builds a record (Part 1). Multi-source work should not deepen this
   split by adding a THIRD mechanism for Bhavcopy — Part 6 addresses
   this directly.
2. **Bhavcopy and FYERS currently use genuinely different construction
   paths** (`options_observation.build_option_observation()` vs.
   `market_reality.capture.build_raw_observation()`) that happen to
   converge on the same underlying MOC `Observation` type but never
   share a common Reality-tier record. A future backfill mixing both
   has no existing guardrail against silently treating them as
   interchangeable.
3. **No completeness/confidence field is populated meaningfully at the
   Reality tier at all today** (Part 1) — a future source-fusion
   materializer choosing between two conflicting values has no existing
   per-record quality signal to weight that choice by, beyond
   `certification_status` (which answers "is this access path
   certified," not "how good is this specific value").
4. **The MOF v1 vs. this-engagement's-vocabulary drift** (Part 0) is a
   documentation risk, not a code risk — but a real one, given how many
   design documents this engagement alone has produced under the
   Reality/Memory/Understanding language without ever citing MOF v1.

---

## Part 6 — Canonical Bujji market reality model: (A) one universal schema, or (B) observations + profiles + derived intelligence?

**Decided: (B), unambiguously — and this is not a new architectural
choice, it is the one already made and proven by Layer 0/1's own design
across this entire engagement, now explicitly confirmed as the right
call for multi-source work too.**

**Reasoning, against the long-term goal stated in the prompt:**

- **(A) "one universal Observation schema"** was tried, in effect,
  exactly once in this codebase's history: `capture.py`'s hardcoding of
  MOC's quality fields to placeholders (Part 1) is what happens when a
  single schema is forced to represent every source uniformly — the
  fields that don't apply to a given source don't get removed, they get
  filled with a meaningless default (`completeness=1.0` always,
  regardless of whether that's true). This is a **real, observed
  failure mode of option (A)**, not a hypothetical one.
- **(B) "raw observations + source capability profiles + derived
  intelligence"** is what Layer 0/1 already does correctly: one
  observation shape (`RawObservation`), with per-record lineage
  declaring what's actually known (`event_timestamp: Optional[str] =
  None` when genuinely absent — proven correct for the option-chain
  endpoint's missing timestamp, Gate B), and a SEPARATE, source-keyed
  profile (Decision 1, prior phase) declaring what's structurally never
  available from a given source — exactly the shape needed to support
  "reasoning from incomplete information" (the prompt's own stated
  long-term goal) honestly, rather than papering over incompleteness
  with a placeholder.

**For Bujji's stated long-term goals specifically:**

- **Understanding market structure**: needs Derived Reality (Layer 1)
  computed consistently regardless of which Raw Reality source fed it —
  (B)'s `calc_version`-hashed materializers already do this.
- **Remembering behaviour**: needs Layer 0's immutability — (A) has no
  bearing on this either way, but (B) is what's already built and
  proven (17F.5's replay-determinism tests).
- **Adapting to regimes**: needs the Understanding tier fed by
  consistent Derived Reality regardless of source mix — (B)'s profile
  system is what lets a regime materializer say "I need volatility
  history; I don't care if it came from Bhavcopy or FYERS, as long as
  the profile says this source can supply it."
- **Reasoning from incomplete information**: is precisely (B)'s central
  design property and (A)'s central failure mode, per the
  `completeness=1.0`-always finding above.

**No implementation is authorized by this conclusion.**

---

## Part 7 — Final recommendation, summarized

1. **Reality hierarchy**: adopt the five-tier restatement (Part 3),
   explicitly cross-referenced to MOF v1 rather than silently
   supplanting it. **PROPOSED — recommend a small, future documentation
   task (not this document) that adds this cross-reference to MOF v1
   itself, so future readers don't discover the two vocabularies
   independently, the way this document just did.**
2. **Source fusion happens at Layer 1 (materializers) only, never at
   Layer 0.** Already true by construction; this document makes it
   explicit.
3. **Conflicts are never errors — they are separate, fully-preserved
   observations.** Reconciliation is an explicit, `calc_version`-hashed
   materializer decision, never implicit.
4. **`ObservationSourceProfile`** (already decided, prior phase) is
   confirmed as the correct mechanism, extended here to
   `fyers_historical`/`fyers_live_optionchain`/`fyers_live_depth`
   alongside the existing `bhavcopy` profile.
5. **Bhavcopy ingestion should eventually route through the same
   Reality-tier (`Layer0Lineage`) mechanism FYERS-sourced data already
   uses**, rather than remaining a structurally separate provenance path
   — named as a real architectural gap (Part 1/5), not resolved or
   scheduled by this document.
6. **Canonical model: (B) — raw observations + source capability
   profiles + derived intelligence.** Not a new choice; a confirmation
   of the pattern already built, now validated against a concrete
   alternative it was never explicitly compared to before.

**Nothing in this document authorizes building `LiveMarketDataProvider`,
a Market Memory Layer, or any adapter.** It is the architectural
foundation those phases should be built against, per the prompt's own
framing.
