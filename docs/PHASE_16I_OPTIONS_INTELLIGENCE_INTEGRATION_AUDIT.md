# Phase 16I — Options Intelligence Integration Audit

**Audit only. No code, no new packages, no schema implementation. Regression unchanged at 5,167 / 0 failed.**

---

## 0. THE HEADLINE

> **MSI already declares every domain options intelligence needs. Four of them have never been populated.**

```
ALL_MSI_DOMAINS  (9 declared)
  PRICE_STRUCTURE            ✅ populated
  SUPPORT_RESISTANCE         ✅ populated
  MARKET_DIRECTION           ✅ populated
  OPTIONS_MARKET_STRUCTURE   ✅ populated — but only positioning_bias
  VOLATILITY_STRUCTURE       ⚠️ populated for opportunity, MISSING for consensus
  FUTURES_STRUCTURE          ❌ never populated
  LIQUIDITY                  ❌ never populated   (PRECONDITION domain)
  TIME_STRUCTURE             ❌ never populated   (PRECONDITION domain)
  CROSS_ASSET                ❌ never populated
  REGIME_INTELLIGENCE        ❌ never populated
```

Verified on real data (cycle 100):
```
participating: [MARKET_DIRECTION, OPTIONS_MARKET_STRUCTURE, PRICE_STRUCTURE, SUPPORT_RESISTANCE]
missing      : [CROSS_ASSET, FUTURES_STRUCTURE, LIQUIDITY, REGIME_INTELLIGENCE,
                TIME_STRUCTURE, VOLATILITY_STRUCTURE]
```

**Options intelligence does not need new domains. It needs to fill empty ones.**

This is the sixth time this session that a capability I was about to design already existed. The pattern is now the single most reliable finding in this codebase.

---

## 1. MSI compatibility

| Required state | Existing model | Verdict |
|---|---|---|
| **Volatility state** | `DOMAIN_VOLATILITY_STRUCTURE` + `msi_volatility_structure` (VSB) — `volatility_regime` ∈ {COMPRESSED, HIGH_VOLATILITY, STABLE, TRANSITIONING, UNKNOWN}, already in `STATE_LEAN_MAP` | ✅ **exists** |
| **Option positioning state** | `DOMAIN_OPTIONS_MARKET_STRUCTURE` + `msi_participant_positioning` (MPPI) — `positioning_bias` ∈ {BULLISH/BEARISH/NEUTRAL/MIXED/UNKNOWN_POSITIONING}, mapped | ✅ **exists** |
| **Liquidity state** | `DOMAIN_LIQUIDITY` declared as a **PRECONDITION domain**; `LiquidityBrain` computes readings | ⚠️ **declared, never fed** |
| **Option flow state** | — | ❌ **genuine gap** |
| **Derivatives regime** | `DOMAIN_FUTURES_STRUCTURE` declared | ⚠️ **declared, never fed** |

### The precondition-domain design is already correct

`PRECONDITION_DOMAINS = (LIQUIDITY, TIME_STRUCTURE)` — and `_lean_for_signal()` forces these to `LEAN_AMBIGUOUS` **regardless of state**. They gate; they never vote.

That is exactly right for liquidity: illiquid conditions should *block* a trade, not *argue* for a direction. The architecture anticipated this and no one has connected it.

### Smallest extension

1. **Populate `DOMAIN_LIQUIDITY`** from real option bid/ask/depth. Zero taxonomy change — it already gates correctly.
2. **Populate `DOMAIN_FUTURES_STRUCTURE`** from `FuturesObservation` (basis, futures OI).
3. **Enrich `OPTIONS_MARKET_STRUCTURE`** beyond `positioning_bias` — that single field is the whole options voice today.
4. **Fix the consensus/opportunity asymmetry** (§3).
5. **Add option-flow states** to `STATE_LEAN_MAP` — the only genuine vocabulary addition.

---

## 2. Phenomena ownership

`msi_market_phenomena` has 10 types, **all price/trend/volatility — none options-specific**:

```
TREND_EXPANSION · TREND_FAILURE · RANGE_COMPRESSION
VOLATILITY_EXPANSION · VOLATILITY_COMPRESSION
MOMENTUM_PERSISTENCE · MOMENTUM_EXHAUSTION
MEAN_REVERSION · GAP_CONTINUATION · GAP_FAILURE
```

### Recommendation: **extend `msi_market_phenomena`, do not create a parallel package**

| Requested phenomenon | Disposition |
|---|---|
| **IV expansion** | ⚠️ **already covered** by `VOLATILITY_EXPANSION` — *if* it is fed IV rather than price vol. Adding `IV_EXPANSION` alongside it would create two names for one concept with different inputs. **Resolve the input, don't duplicate the name.** |
| **IV crush** | same reasoning → `VOLATILITY_COMPRESSION`, or a distinct type only if crush is genuinely event-driven (post-expiry/post-event) rather than merely compression |
| **Call writing** | **NEW** — OI↑ + price↓ at strike |
| **Put writing** | **NEW** |
| **Short covering** | **NEW** — OI↓ + price↑ |
| **Long buildup** | **NEW** — OI↑ + price↑ |
| **OI-price divergence** | **NEW** |
| **Liquidity stress** | **NEW** — but feeds the LIQUIDITY *precondition*, not the directional vote |

**Why extend rather than fork:** the phenomenon layer's value is that one engine sees all evidence types. A separate `option_phenomena` package would mean nothing could express *"volatility compression **and** call writing at resistance"* — which is precisely the kind of composite an options desk needs.

**The four OI/price phenomena share one detection primitive** (OI direction × price direction), so they are one rule family, not four independent additions.

---

## 3. Decision synthesis impact

### Does `synthesize()` already consume derivatives/volatility/positioning evidence?

**Yes — for volatility and positioning. No — for liquidity, futures, or flow.**

`STATE_LEAN_MAP` already contains:
```
COMPRESSED           → OPPORTUNITY_FORMING, VOLATILITY_OPPORTUNITY
HIGH_VOLATILITY      → OPPORTUNITY_FORMING, VOLATILITY_OPPORTUNITY
STABLE               → NEUTRAL_FORMING,     NEUTRAL_OPPORTUNITY
BULLISH_POSITIONING  → OPPORTUNITY_FORMING, DIRECTIONAL_OPPORTUNITY
BEARISH_POSITIONING  → OPPORTUNITY_FORMING, DIRECTIONAL_OPPORTUNITY
NEUTRAL_POSITIONING  → NEUTRAL_FORMING,     NEUTRAL_OPPORTUNITY
MIXED_POSITIONING    → AMBIGUOUS
```

**`synthesize()` needs no structural change.** Phase 15P proved every documented state reachable and the engine correct.

### A real defect worth surfacing

**Consensus and opportunity see different domain sets.**

| Path | Builder | Domains |
|---|---|---|
| Opportunity | `build_domain_signals(psi, mssi, mdi, mppi, **vsb**)` | **5** |
| Consensus | `build_domain_assessment_views(psi, mssi, mdi, mppi)` | **4** — no VSB |

Confirmed in real data: `VOLATILITY_STRUCTURE` appears in consensus's `missing_domains` while simultaneously feeding the opportunity assessment.

So **volatility evidence influences the opportunity but is invisible to the consensus that gates eligibility.** For an options desk — where volatility regime is arguably the primary signal — that asymmetry is significant. It is a pre-existing defect, not one this phase introduces, and it should be resolved deliberately rather than inherited.

### The smallest integration seam

```
Option features ──► DomainSignal(
                      domain_name = OPTIONS_MARKET_STRUCTURE | VOLATILITY_STRUCTURE
                                    | LIQUIDITY | FUTURES_STRUCTURE,
                      state       = <vocabulary term>,
                      confidence  = float,
                      evidence_ids = (option observation ids…))
                  ──► synthesize()   [UNCHANGED]
```

**One seam. No new engine, no parallel decision path.** The only additions are new `state` strings in `STATE_LEAN_MAP` for option-flow terms.

---

## 4. Memory impact

> *"Historically, when similar option positioning appeared, what happened next?"*

**Answer: no — and the gap is structural, not incidental.**

`outcome_memory.filter_records()` supports exactly:
```
session_id · strategy_family · entry_regime · entry_direction · underlying_symbol
```

Missing for the question asked:
- no positioning field
- no volatility-state field
- no IV/OI context
- no liquidity state
- **no similarity search** — only exact-match filtering

### What exists that helps

`OutcomeMemoryRecord` stores `lifecycle_snapshot` and `attribution_snapshot` **verbatim**, and `EntrySnapshot` carries `entry_greeks` and `entry_premium_behaviour`. **The raw material is already captured** — it simply is not indexed or queryable.

### Missing pieces, in dependency order

1. **Query-convenience extracts** for positioning / volatility state / IV context (the data is in the snapshots; promoting a few fields to top level is the cheap part)
2. **Similarity search** — "most similar historical setup" needs a distance metric; only exact-match exists anywhere in the codebase
3. **Sample sufficiency at cohort level** — `MIN_SAMPLE_SIZE=3` exists, but conditioning on positioning × volatility × regime will shrink cohorts fast, and `INSUFFICIENT_HISTORY` must fire honestly rather than answer from two observations
4. **A real population** — zero records exist today

**Point 3 is the one most likely to be got wrong later.** Conditioning on more dimensions feels like more rigour; it is actually fewer samples per cell. The existing `INSUFFICIENT_HISTORY` discipline must survive that pressure.

---

## 5. Final architecture boundary

### EXISTING CAPABILITY — reuse unchanged

| | |
|---|---|
| 9 MSI domains incl. options/volatility/liquidity/futures | `msi_decision_synthesis.taxonomy` |
| Volatility state model | `msi_volatility_structure` (VSB) |
| Positioning state model | `msi_participant_positioning` (MPPI) |
| Precondition gating (liquidity never votes) | `PRECONDITION_DOMAINS` |
| Volatility + positioning already in `STATE_LEAN_MAP` | `msi_decision_synthesis.config` |
| Phenomenon engine | `msi_market_phenomena` |
| `synthesize()` | 15P-verified, **unchanged** |
| Option identity + OI fields | `options_observation` |
| Verbatim memory snapshots | `outcome_memory` |
| Uncertainty / lineage | `epistemics` |

### EXTENSION REQUIRED

| # | Extension | Size |
|---|---|---|
| 1 | Populate `DOMAIN_LIQUIDITY` from option bid/ask/depth | small |
| 2 | Populate `DOMAIN_FUTURES_STRUCTURE` from futures | small |
| 3 | Enrich `OPTIONS_MARKET_STRUCTURE` beyond `positioning_bias` | medium |
| 4 | **Fix consensus/opportunity VSB asymmetry** | small, **high value** |
| 5 | Option-flow states in `STATE_LEAN_MAP` | small |
| 6 | 5 OI/price phenomena in `msi_market_phenomena` | medium |
| 7 | Memory query extracts for positioning/volatility | small |

### NEW CAPABILITY

| # | Capability | Why nothing exists |
|---|---|---|
| 1 | **Option feature layer** (spread, IV, Greeks, premium velocity, OI analytics) | no owner — `msi_greeks` is ATM-only, per-cycle |
| 2 | **Option flow state** | no domain vocabulary |
| 3 | **Similarity search** | only exact-match filtering exists anywhere |
| 4 | `MarketDepthObservation` | verified absent (16H) |

---

## 6. Verdict

**Options intelligence is ~70% an integration problem and ~30% new construction.**

The architecture anticipated an options desk: it declared `OPTIONS_MARKET_STRUCTURE`, `VOLATILITY_STRUCTURE`, `FUTURES_STRUCTURE` and `LIQUIDITY` domains, made liquidity a non-voting precondition, and pre-mapped volatility and positioning states into the opportunity resolver. **Four of those domains have simply never been fed.**

Genuinely new: the option feature layer, option-flow vocabulary, similarity search, depth observations.

### Two findings I would act on before any building

1. **The consensus/opportunity VSB asymmetry** (§3) — volatility evidence currently shapes the opportunity but is invisible to the consensus that gates eligibility. On an options desk that is backwards, and it is a pre-existing defect worth resolving explicitly rather than inheriting.
2. **`IV_EXPANSION` vs `VOLATILITY_EXPANSION`** (§2) — resolve whether these are one concept with two inputs or two concepts. Adding a second name for the same phenomenon would fragment the layer that exists precisely to unify evidence.

### Recommended next, none requiring Gate 1

1. Resolve the VSB consensus/opportunity asymmetry
2. Decide IV-vs-price volatility phenomenon semantics
3. Design the option feature layer contract (with `Lineage` + `Uncertainty`)
4. Design option-flow state vocabulary + `STATE_LEAN_MAP` entries
5. Design memory query extracts for positioning/volatility

---

**No code written. Frozen untouched: TickStore · storage backend · watermark value · ingestion topology · Stack A. MSI/phenomena/memory diff: 0 lines. Regression 5,167 / 0 failed.**
