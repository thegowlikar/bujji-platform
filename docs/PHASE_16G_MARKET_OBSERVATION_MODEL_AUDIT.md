# Phase 16G — Market Observation Model Audit

**Audit only. No code written. Regression unchanged at 5,167 / 0 failed.**

---

## 0. THE HEADLINE

> **`MarketDataPoint` cannot represent an option contract.**

```python
class MarketDataPoint:
    event_time: datetime
    price: float
    volume: Optional[float] = None
    symbol: str = "NIFTY"
    sequence_no: Optional[int] = None
```

No strike. No expiry. No option type. No lot size. No bid/ask. No OI.

`mil_next` — the multi-timeframe intelligence layer Phase 16F identified as the reuse target — **can only reason about the underlying**. It is structurally an index/price-series engine, not an options engine.

**This is not a defect in `mil_next`.** Its docstrings are explicit that it is a "laboratory" over price points. The defect would be assuming it is the whole observation model for an options desk.

**Consequence:** the Market Reality Bridge has *two* destinations, not one:

```
FYERS full tick ──┬──► MarketDataPoint ──► mil_next ──► regime/direction    (UNDERLYING)
                  │
                  └──► OptionObservation ─► option-chain intelligence       (OPTIONS)
                                             ↑ partially exists, gaps below
```

Phase 16F's conclusion still holds — but it described the **underlying** half only.

---

## 1. Underlying market representation

| Instrument | Model | Identity fields | Status |
|---|---|---|---|
| **NIFTY Spot** | `MarketDataPoint` / `Observation` | symbol, timestamp | ✅ sufficient |
| **NIFTY Futures** | `FuturesObservation` | + `expiry`, `underlying`, `instrument_symbol` | ✅ sufficient |
| **India VIX** | `MarketDataPoint` / `Observation` | symbol | ✅ sufficient (it is an index) |
| **Option contract** | `OptionObservation` | + `strike`, `expiry`, `option_type`, `underlying` | ✅ **identity sufficient** |

`OptionObservation`'s docstring shows the right design already:

> *"`strike`, `expiry`, `option_type`, and `underlying` are options-domain identity concepts not present in MOC's generic `ObservationIdentity`, so they are threaded through explicitly at construction time and stored alongside the wrapped Observation, **never re-derived by string-parsing `instrument`**."*

Never string-parsing the symbol is exactly right — symbol formats change, identity must not.

### Missing from the canonical models

| Concept | Where it exists | Gap |
|---|---|---|
| **lot_size** | `instrument_master.OptionRow` only | ❌ not on any *observation* — required for exposure/margin at observation time |
| **instrument_type** enum | implied by which wrapper class is used | ⚠️ no single discriminator across spot/fut/opt/index |
| **contract lifecycle** | `first_seen`/`last_seen`/`expired_at`/settlement | ❌ **absent entirely** |
| **absolute expiry as identity** | `expiry: str` (string) | ⚠️ works, but no expiry-bucket derivation rule |

**Contract lifecycle is the significant gap.** Nothing records when a contract came into existence, when it expired, or its settlement price. At expiry, a series simply stops — indistinguishable from a feed failure.

---

## 2. Option market intelligence — field-by-field

| Field | Available today | Class | Notes |
|---|---|---|---|
| LTP / close | ✅ `FIELD_CLOSE` | **RAW** | |
| bid / ask | ⚠️ **field exists, never populated** | **RAW** | `options_observation` declares `FIELD_BID`/`FIELD_ASK` with the comment *"Never populated from Bhavcopy"*. Full-mode WS supplies them. |
| bid/ask quantity | ⚠️ same | **RAW** | `bid_size`/`ask_size` in full mode |
| **bid/ask depth (5 levels)** | ❌ | **RAW** | `DepthUpdate` exists; no model |
| traded volume | ✅ `FIELD_VOLUME` | **RAW** | |
| **OI** | ✅ `FIELD_OPEN_INTEREST` + `FIELD_CHANGE_IN_OPEN_INTEREST` | **RAW** | REST-verified exact (`oich == oi − prev_oi`) |
| underlying price | ✅ `FIELD_UNDERLYING_PRICE` | **RAW** | correctly stored *with* the option row |
| spread / spread % | ❌ | **DERIVED** | trivially from bid/ask — must not be stored as raw |
| **IV** | ❌ **not an observation field** | **DERIVED** | lives only in `msi_greeks.GreeksLegAssessment`, per-cycle, ATM-only, never persisted as a series |
| **Greeks** (δ γ θ ν) | ⚠️ computed, not stored | **DERIVED** | `GreeksLegAssessment` has `available: bool` + `reason` — correct anti-fabrication discipline already |
| premium change / velocity | ❌ | **DERIVED** | |
| premium expansion | ❌ | **PHENOMENON** | `msi_market_phenomena` has the vocabulary |
| option-flow bias | ❌ | **STATE** | |

### Ownership map

```
RAW OBSERVATION   ltp · bid · ask · bid_size · ask_size · volume · OI ·
                  change_in_OI · underlying_price · depth levels
                  ← what the exchange actually published

DERIVED FEATURE   spread · spread% · mid · IV · delta/gamma/theta/vega ·
                  premium change/velocity · moneyness · intrinsic/extrinsic
                  ← + calc_version + inputs used

PHENOMENON        premium expansion/compression · IV expansion ·
                  OI buildup/unwinding · gamma concentration

STATE             option-flow bias · positioning regime · liquidity regime
```

**IV is the sharpest case.** It is *always* derived — it requires a pricing model, a rate assumption and a time-to-expiry convention. Storing IV as an observation would be storing a model output as market truth. `msi_greeks` already gets this right by carrying `available`/`reason` and refusing to emit values when inputs are missing.

---

## 3. Observation vs Feature separation

**Verdict: the separation is architecturally sound and currently respected.**

| Evidence | |
|---|---|
| `market_observation.ObservationValue` | domain-neutral, closed set of variants — no derived semantics |
| `options_observation` fields | all exchange-published quantities; **no IV, no Greeks, no spread** |
| `GreeksLegAssessment` | lives in `msi_greeks` (analysis), not in any observation package |
| `msi_market_phenomena` | phenomena are a separate layer entirely |

**No derived intelligence is currently stored as raw truth.** The `provenance` + `schema_version` discipline (171 files) reinforces it.

**The one risk to guard:** when the full-fidelity feed adapter lands, `bid`/`ask` become populated for the first time. Spread, mid and IV must **not** be added to `OptionObservation` alongside them — they belong to the feature layer with `calc_version` attached. This is precisely the moment the boundary is most likely to be violated for convenience.

---

## 4. Historical reconstruction — "what did the market look like at 10:32:15?"

| Component | Reconstructable | Blocker |
|---|:--:|---|
| Underlying price | ⚠️ **coarse** | 30s REST snapshots only; no tick history |
| Futures | ❌ | not captured at all |
| India VIX | ⚠️ coarse | per-cycle only |
| **Option chain (OI)** | ✅ | `market_snapshots.jsonl` — real, verified |
| Option bid/ask | ❌ | never populated |
| Option depth | ❌ | never captured |
| Per-strike volume | ⚠️ | chain snapshot only |
| **IV** | ❌ | never persisted; ATM-only, per-cycle |
| **Greeks** | ❌ | per-cycle in intelligence corpus, not a series |
| Liquidity | ⚠️ | 1/174 cycles had both ATM CE+PE bid/ask |
| Regime | ✅ | persisted per cycle |
| **Exact 10:32:15** | ❌ | **30s granularity — the instant does not exist** |

**Answer: no.** Bujji can approximately reconstruct 10:32:00 or 10:32:30, with OI and regime but without bid/ask, IV, Greeks, depth, or futures.

### Permanently unrecoverable without capture

1. Per-tick bid/ask and sizes
2. Market depth
3. Futures entirely
4. IV/Greeks *as observed inputs* (recomputable **only** if the underlying + option prices at that instant were captured)
5. Intra-30s price path
6. Exchange-time ordering

Point 4 is worth stating carefully: IV and Greeks are derived, so they need not be *stored* — but they can only ever be *recomputed* if their raw inputs (option price, underlying, timestamp) were captured at sufficient resolution. **Today they cannot be.**

---

## 5. Minimum Market Reality Bridge

### MUST HAVE before Shadow Trading

| # | Component | Status | Why |
|---|---|:--:|---|
| 1 | Full-fidelity feed adapter (23 fields) | ❌ | bid/ask/OI/volume otherwise discarded |
| 2 | Observation store (append-only) | ❌ | backend = Gate 1 |
| 3 | `MarketDataPoint` projection (underlying) | ❌ | trivial — feeds mil_next |
| 4 | **Option observation path** | ⚠️ | `OptionObservation` exists; needs the live-tick producer |
| 5 | **lot_size on observations** | ❌ | required for exposure at observation time |
| 6 | Backpressure / drop accounting | ❌ | silent loss otherwise |
| 7 | Feed health → `SourceHealth` | ⚠️ | model exists, no producer |
| 8 | Calendar verification | ⚠️ | unverified template |
| 9 | `ComposedRegimeView → DomainSignal` seam | ❌ | design question |

### MUST HAVE before Real Money

| # | Component | Why |
|---|---|---|
| 10 | **Contract lifecycle** (first_seen/expired_at/settlement) | expiry must be distinguishable from feed failure |
| 11 | **Risk layer (L10)** | absent from Stack B entirely |
| 12 | **Kill switch** | 0 files |
| 13 | Position / P&L / margin reconciliation | broker-vs-Bujji divergence must be an incident |
| 14 | Market depth capture | execution quality unassessable without it |
| 15 | Futures capture | basis, and the only order book for the underlying |
| 16 | IV/Greeks as versioned features | with `calc_version` + stored inputs |
| 17 | Tick lateness watermark | Gate 1 |

### RESEARCH ENHANCEMENT (not blocking)

CandleStore + timeframe ladder · ~34 indicators · IV surface / skew / term structure · OI analytics (PCR, migration, concentration) · historical backfill · portfolio correlation · experiment tracking.

---

## 6. Verdict

**The observation model is sufficient for the *underlying*, and incomplete for *options*.**

For an **Autonomous NIFTY Options Quant Desk**, that gap is central rather than peripheral:

| Layer | Assessment |
|---|---|
| Instrument identity | ✅ **sound** — options/futures identity modelled correctly, never string-parsed |
| Observation vs derived separation | ✅ **sound** — no derived value stored as raw truth |
| Raw option microstructure | ❌ bid/ask declared but never populated; depth absent |
| Contract lifecycle | ❌ absent — expiry indistinguishable from failure |
| lot_size at observation time | ❌ only in instrument master |
| IV / Greeks as series | ❌ per-cycle ATM only |
| Historical reconstruction | ❌ 30s granularity, no order book |
| **mil_next for options** | ❌ **structurally underlying-only** |

**The single most important correction to the Fabric plan:** Phase 16F concluded "the bridge is one third of what was assumed." That is true **for the underlying path**. The options path — which is what makes this an options desk — still needs a producer, lot_size, contract lifecycle, and depth.

**Recommended next, in order, none requiring Gate 1:**
1. Design the **option observation producer** contract (live tick → `OptionObservation`)
2. Design **contract lifecycle** (first_seen / expired_at / settlement_price)
3. Decide where **lot_size** attaches to observations
4. Verify `market_calendar.py`
5. Design the `ComposedRegimeView → DomainSignal` seam

---

**No code written. Frozen untouched: TickStore · storage backend · watermark value · ingestion topology · Stack A. Regression 5,167 / 0 failed.**
