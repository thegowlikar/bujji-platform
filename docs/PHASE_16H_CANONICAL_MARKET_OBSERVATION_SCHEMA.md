# Phase 16H — Canonical Market Observation Schema

**Design audit only. No code, no producers, no TickStore, no storage decisions. Regression unchanged at 5,167 / 0 failed.**

---

## 0. Design position

Bujji is an **options** market intelligence system. Two parallel observation paths, one shared identity spine:

```
                    ┌──── InstrumentIdentity (canonical, shared) ────┐
                    │                                                │
FYERS full tick ────┼──► MarketDataPoint      ──► mil_next ──────────┤
                    ├──► FuturesObservation   ──► basis features     ├──► MSI
                    ├──► OptionObservation    ──► option features    │
                    └──► MarketDepthObservation ─► liquidity features┘
```

**Guiding constraint throughout: reuse over creation.** Five audits in this session have found capability I had declared missing. This design therefore states, for every model, whether it **EXISTS**, needs **EXTENSION**, or is genuinely **NEW**.

---

## 1. Instrument Identity

### What exists

| Model | Carries | Where |
|---|---|---|
| `ObservationIdentity` | observation_id, observation_type, instrument, exchange, segment, timestamp, resolution, source, schema_version | `market_observation` ✅ |
| `OptionObservation` | + strike, expiry, option_type, underlying | `options_observation` ✅ |
| `FuturesObservation` | + expiry, underlying, instrument_symbol | `futures_observation` ✅ |
| `OptionRow` | symbol, underlying, strike, option_type, expiry_epoch, **lot_size** | `broker/instrument_master` ✅ |

**Established principle, already honoured:** identity fields are threaded explicitly at construction, *"never re-derived by string-parsing `instrument`."* This design keeps that rule absolute.

### Canonical model (proposed — consolidates, does not replace)

```
InstrumentIdentity
  instrument_id      str        surrogate, stable across symbol renames
  symbol             str        broker-facing (NSE:NIFTY2580724500CE)
  instrument_type    enum       SPOT | INDEX | FUTURE | OPTION
  underlying         str        "NIFTY"
  exchange, segment  str

  # OPTION / FUTURE only — None otherwise, never 0 or ""
  expiry_date        date       ABSOLUTE — the identity
  strike             float      OPTION only
  option_type        enum       CE | PE — OPTION only
  lot_size           int        from instrument master, never config
  tick_size          float

  # lifecycle — currently ABSENT everywhere
  first_seen         datetime
  last_seen          datetime
  expired_at         datetime | None
  settlement_price   float | None
  status             enum       ACTIVE | EXPIRED | SUSPENDED | UNKNOWN
```

### Three binding rules

1. **`expiry_date` is identity; expiry *bucket* is a query-time projection.** `CURRENT_WEEK`/`NEXT_WEEK`/`MONTHLY` are derived from `(expiry_date, as_of)` and **never stored**. Storing the label mislabels every historical query after rollover.
2. **No string parsing, ever.** Identity comes from the instrument master, not from the symbol.
3. **`lot_size` travels with the observation.** Today it exists only in `OptionRow`; exposure and margin cannot be computed at observation time without it.

### Gap

| | Status |
|---|---|
| Identity for spot/futures/options/VIX | ✅ **sufficient** |
| `instrument_type` discriminator | ⚠️ implied by wrapper class — no single enum |
| `lot_size` on observations | ❌ **missing** |
| **Contract lifecycle** | ❌ **absent entirely** — at expiry a series simply stops, indistinguishable from feed failure |

---

## 2. Raw Observation Models

**Strict rule: facts only. If the exchange did not publish it, it is not a raw observation.**

### `MarketDataPoint` — EXISTS, unchanged

```
event_time · price · volume? · symbol · sequence_no?
```

Deliberately minimal; `mil_next` folds it. **Do not extend it with option fields** — that would turn a price-series primitive into a leaky union type. Options get their own path.

### `FuturesObservation` — EXISTS, needs producer

Wraps `Observation` + expiry + underlying. **No live producer exists** (Bhavcopy-shaped). Futures are the only order book for the underlying — the index has none.

### `OptionObservation` — EXISTS, fields declared, mostly unpopulated

```
FIELD_OPEN · HIGH · LOW · CLOSE · SETTLEMENT · VOLUME
FIELD_OPEN_INTEREST · CHANGE_IN_OPEN_INTEREST · UNDERLYING_PRICE
FIELD_BID · ASK · BID_QUANTITY · ASK_QUANTITY      ← declared, "Never populated from Bhavcopy"
```

**The schema is already correct.** Full-mode WS supplies bid/ask/sizes; the gap is a producer, not a model.

**Deliberately absent and must stay absent:** IV, Greeks, spread, mid, moneyness. All derived.

### `MarketDepthObservation` — **NEW** (verified: no depth model exists anywhere)

```
instrument_id · exch_feed_time · receive_ts
bid_levels: Tuple[(price, size, orders), ...]     5 levels
ask_levels: Tuple[(price, size, orders), ...]     5 levels
source · quality_flags · raw_payload
```

Maps directly onto the SDK's 32-field `depthvalue`. **Separate model, not fields on `OptionObservation`** — depth arrives on a distinct `DepthUpdate` subscription with its own cadence and its own failure modes.

### `MarketEvent` — EXISTS, reuse

```
event_id · event_type · timestamp · originating_observation_ids
detail · provenance · schema_version
```

Its comments already encode the right discipline: *"the (current) observation's own timestamp — never detection time"* and *"Minimal fact-of-change payload — never a full Observation copy."*

**Extend the type vocabulary, not the shape:** `FEED_CONNECTED` · `FEED_STALE` · `FEED_GAP` · `TICKS_DROPPED` · `UNIVERSE_CHANGED` · `CONTRACT_EXPIRED` · `SESSION_OPEN/CLOSE`.

### Raw-observation ledger

| Model | Status |
|---|---|
| `MarketDataPoint` | ✅ exists, unchanged |
| `FuturesObservation` | ✅ exists, needs producer |
| `OptionObservation` | ✅ exists, needs producer + populated bid/ask |
| `MarketDepthObservation` | ❌ **NEW** |
| `MarketEvent` | ✅ exists, extend vocabulary only |

---

## 3. Derived Feature Models

**Every derived object carries the full contract — no exceptions:**

```
Feature
  name · timeframe · instrument_id · value: float | None
  lineage:     Lineage      source_observation_ids · as_of · calc_version
                            · code_version · config_version
  uncertainty: Uncertainty  state · confidence · limiting_factor
                            · freshness_s · completeness
```

`Lineage` and `Uncertainty` **already exist** (`bujji.epistemics`, Phases 16C/16D). No new provenance or confidence model.

### Ownership

| Derived value | Source observations | Owner | Notes |
|---|---|---|---|
| **spread / spread% / mid** | `OptionObservation.bid/ask` | feature layer | trivial — must never be stored raw |
| **IV** | option price + underlying + expiry + rate | `msi_greeks` (extend to series) | **always a model output** — pricing model, rate assumption, T convention. Storing it raw would be storing a model as market truth. |
| **Greeks** (δ γ θ ν) | same as IV | `msi_greeks` | already carries `available` + `reason` — correct discipline |
| **OI change** | ⚠️ **both** | see below | |
| **premium velocity / acceleration** | option price series | feature layer | needs ≥2 observations → `INSUFFICIENT_HISTORY` until then |
| **liquidity metrics** | depth + spread + volume | feature layer | |
| **volatility metrics** | price series | `market_timeseries.indicators` (canonical) | |
| **VWAP** | price + volume | harvest semantics from `signal.VwapTracker` | already has `is_real`/`using_fallback`/`fallback_reason` |

**OI change deserves care.** `FIELD_CHANGE_IN_OPEN_INTEREST` is **exchange-published** (REST-verified: `oich == oi − prev_oi` exactly) — that is **RAW**. An OI delta Bujji computes between two of its own observations is **DERIVED**. They must not share a field; conflating them would mix exchange truth with a sampling artifact.

### Two rules

1. **Insufficient history → no value.** `INSUFFICIENT_HISTORY`, which self-heals — never a low-confidence number.
2. **`range_dependent` features** (ATR, Bollinger, realised vol) → a GAP **breaks the definition**, so the output is `UNKNOWN`, not merely weakened. Already implemented in `epistemics.compose`.

---

## 4. Historical Reconstruction

> **"What did the NIFTY options market look like at 10:32:15?"**

### Today: **NO**

| Component | Status | Blocker |
|---|:--:|---|
| Underlying | ⚠️ | 30s REST — the instant does not exist |
| **Futures** | ❌ | never captured |
| Option chain (OI) | ✅ | `market_snapshots.jsonl`, verified |
| Option bid/ask | ❌ | declared, never populated |
| **Depth** | ❌ | no model, no capture |
| Volatility (IV) | ❌ | ATM-only, per-cycle, not a series |
| Liquidity | ⚠️ | 1/174 cycles had both ATM CE+PE bid/ask |
| Positioning (OI) | ⚠️ | chain snapshot only |
| Regime | ✅ | persisted per cycle |

### After the canonical schema + capture: **YES, with one caveat**

| Component | Source |
|---|---|
| Underlying | tick series |
| Futures | `FuturesObservation` series |
| Option chain | `OptionObservation` per strike |
| Volatility | **recomputed** from stored price + underlying + expiry, with `calc_version` |
| Liquidity | depth + spread |
| Positioning | OI + exchange-published change |

**The caveat matters:** IV and Greeks are derived, so they need not be *stored* — but they can only be *recomputed* if their raw inputs were captured at sufficient resolution. Capture the inputs and the whole volatility surface is reconstructable at any historical instant. Fail to, and it is gone permanently.

### Permanently unrecoverable without capture

Per-tick bid/ask + sizes · depth · futures · intra-30s price path · exchange-time ordering.

---

## 5. Integration Map

```
RAW OBSERVATION          MarketDataPoint · FuturesObservation
                         OptionObservation · MarketDepthObservation
                         MarketEvent
                         owners: market_observation · options_observation
                                 futures_observation · live_market_events   ✅ EXIST
                              │
                              ▼
FEATURE                  spread · IV · Greeks · premium velocity
                         liquidity · volatility · VWAP
                         owners: msi_greeks · market_timeseries.indicators
                                 + option feature layer                     ⚠️ PARTIAL
                              │
                              ▼
PHENOMENON               premium expansion/compression · IV expansion
                         OI buildup/unwinding · gamma concentration
                         owner: msi_market_phenomena (10 types)             ✅ EXISTS
                              │
                              ▼
STATE                    per-TF regime → ComposedRegimeView
                         + option-flow / positioning / liquidity regime
                         owner: mil_next                                    ✅ UNDERLYING
                                                                            ❌ OPTIONS
                              │
                              ▼  ═══ THE SEAM ═══
                              │
DECISION                 msi_consensus → msi_decision_synthesis
                         → eligibility → trade_intent                       ✅ UNCHANGED
                              │
                              ▼
                         lifecycle → P&L → portfolio → attribution → memory ✅ UNCHANGED
```

### Duplication check

| Concern | Verdict |
|---|---|
| Provenance | ✅ one model (`epistemics.Lineage`); 9 existing shapes adapt |
| Uncertainty | ✅ one model (`epistemics.Uncertainty`); 6 vocabularies adapt |
| Content hashing | ✅ `replay_engine.fingerprint_state` — no 7th |
| Multi-TF fold | ✅ `mil_next` — no second engine |
| Phenomena | ✅ `msi_market_phenomena` — no second |
| Candle TA | ✅ `market_timeseries.indicators` — uncontested |
| **Option feature layer** | ❌ **genuinely new — no existing owner** |
| **Options state** | ❌ **genuinely new — `mil_next` is underlying-only** |

**Only two genuinely new layers.** Everything else adapts.

---

## 6. Summary

### New (3)
`MarketDepthObservation` · option feature layer · options state layer

### Extend (4)
`InstrumentIdentity` consolidation (+ `instrument_type`, `lot_size`, lifecycle) · `MarketEvent` type vocabulary · `msi_greeks` → time series · `FuturesObservation`/`OptionObservation` producers

### Unchanged (8)
`MarketDataPoint` · `market_observation` · `mil_next` · `msi_market_phenomena` · `msi_decision_synthesis` · `epistemics` · `market_timeseries.indicators` · lifecycle→memory

### The two sharpest schema decisions

1. **Contract lifecycle is not optional.** Without `expired_at`/`status`, an expired contract and a dead feed are indistinguishable — and an autonomous desk that cannot tell those apart will eventually act on a stale series.
2. **Exchange-published OI change ≠ computed OI delta.** One is market truth, the other a sampling artifact of Bujji's own cadence. Separate fields, always.

### Recommended next (no Gate 1 dependency)

1. `InstrumentIdentity` consolidation + contract lifecycle contract
2. `MarketDepthObservation` schema
3. Option feature layer contract (spread/IV/Greeks with `Lineage` + `Uncertainty`)
4. `ComposedRegimeView → DomainSignal` seam
5. Verify `market_calendar.py`

---

**No code written. Frozen untouched: TickStore · storage backend · watermark value · ingestion topology · Stack A. Observation packages diff: 0 lines. Regression 5,167 / 0 failed.**
