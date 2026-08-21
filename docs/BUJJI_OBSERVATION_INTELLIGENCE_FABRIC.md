# Bujji — Market Observation & Intelligence Fabric
## Final Architecture Re-Baseline

**Audit only. No code modified, nothing deleted.**

---

## 1. Target architecture

```
                         LIVE MARKET (NSE)
                                │
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                       ▼
   WS SymbolUpdate         WS DepthUpdate           REST (chain/history)
        │                       │                       │
        └───────────────────────┴───────────────────────┘
                                ▼
                    ╔═══════════════════════╗
                    ║   FEED ADAPTER LAYER  ║  normalize · stamp · dedupe · quality-tag
                    ╚═══════════════════════╝
                                ▼
                    ╔═══════════════════════╗
                    ║ CANONICAL OBSERVATION ║  ← market_observation (EXISTS, 31 importers)
                    ╚═══════════════════════╝
                                ▼
         ┌──────────────────────┴──────────────────────┐
         ▼                      ▼                      ▼
   ╔═══════════╗        ╔═══════════════╗      ╔══════════════╗
   ║ TICKSTORE ║        ║ SNAPSHOTSTORE ║      ║ UNIVERSE LOG ║
   ║AUTHORITATIVE║      ║ (REST chain)  ║      ║  (events)    ║
   ╚═══════════╝        ╚═══════════════╝      ╚══════════════╝
         │ ← the ONLY source of truth
         ▼
   ╔═══════════════════╗
   ║  CANDLE ENGINE    ║  1m from ticks; 5/15/30/1H/D from 1m; W/M from D
   ╚═══════════════════╝
         │
         ▼  CANDLESTORE (materialized cache — always re-derivable)
         │
    ┌────┴─────────────────────┐
    ▼                          ▼
╔══════════════╗      ╔═══════════════════╗
║   FEATURES   ║      ║  MICROSTRUCTURE   ║  spread, imbalance, velocity
║ (timeframed) ║      ║   (tick-native)   ║
╚══════════════╝      ╚═══════════════════╝
    └────┬─────────────────────┘
         ▼
   ╔══════════════════╗
   ║   PHENOMENA      ║  ← msi_market_phenomena (EXISTS, 10 types)
   ╚══════════════════╝
         ▼
   ╔══════════════════════════════╗
   ║ MULTI-TIMEFRAME MARKET STATE ║  conflict-preserving, never flattened
   ╚══════════════════════════════╝
         ▼
   ╔══════════════════════════════╗
   ║  MSI INTELLIGENCE (EXISTS)   ║  consensus → synthesis → eligibility → intent
   ╚══════════════════════════════╝
         ▼
   POSITION LIFECYCLE → MANAGEMENT → P&L → ATTRIBUTION → MEMORY   (15G–15N, EXISTS)
```

**The fabric is the bottom half.** The top half already exists and is proven.

---

## 2. Authoritative vs derived — the central classification

| Layer | Class | Mutability | Re-derivable? |
|---|---|---|---|
| Raw tick (as received) | **AUTHORITATIVE** | Append-only, never rewritten | No — must be captured or lost forever |
| REST chain snapshot | **AUTHORITATIVE** (own timeline) | Append-only | No |
| Universe change event | **AUTHORITATIVE** | Append-only | No |
| 1m candle | Derived | Immutable after watermark | Yes, from ticks |
| 5m…Monthly | Derived | Immutable after watermark | Yes, from 1m |
| Features | Derived | Versioned | Yes, given `calc_version` |
| Phenomena | Derived | Versioned | Yes |
| Market state | Derived | Versioned | Yes |
| Decisions | **AUTHORITATIVE** (a real event occurred) | Append-only | No |

**Rule: if it cannot be recomputed, it must be captured. If it can be recomputed, it must carry the version that computed it.**

Everything currently in `shadow_sessions/*.jsonl` is derived-and-discarded — the authoritative substrate underneath it was never kept.

---

## 3. Capability ledger

### Exists and reusable — no rewrite

| Capability | Where | Note |
|---|---|---|
| Canonical Observation model | `market_observation` | 31 importers, de-facto standard |
| Tick→window aggregation | `live_observation` | Late-tick correct, no fabrication |
| Websocket feed + reconnect + watchdog | `broker/fyers_ws` | Survived 2 prod incidents |
| Instrument master, ATM resolve, expiry rows | `broker/instrument_master` | |
| REST option chain (OI verified exact) | `broker/fyers` | `oich == oi - prev_oi` proven |
| REST historical candles | `broker/fyers` | `[epoch,o,h,l,c,v]` verified |
| IST clock + drift guard | `core/clock` | `now_ist`, `from_epoch`, `ClockGuard` |
| Data-quality vocabulary | `execution_reality` | `DATA_QUALITY_*` |
| Phenomena layer | `msi_market_phenomena` | 10 phenomenon types |
| Greeks | `msi_greeks` | Per-cycle |
| Full MSI intelligence chain | 25 `msi_*` packages | Phase 15P proved state-reachable |
| Lifecycle / P&L / attribution / memory | 15G–15N | Replay- and recovery-proven |
| Candle semantics (OHLC, gaps, forming/closed) | `market_timeseries` (15Q) | ~70% survives |

### Missing entirely

| Missing | Consequence |
|---|---|
| **TickStore** | No authoritative substrate exists |
| **Feed adapter (full mode → Observation)** | Feed delivers 23 fields; nothing consumes them |
| **Universe manager** | No ATM/expiry rollover, no validity windows |
| **Multi-timeframe engine** | Only 5m exists |
| **NSE holiday calendar** | Daily/Weekly/Monthly untrustworthy |
| **Uncertainty algebra** | 20 packages, no composition rules (§10) |
| **`as_of` query discipline** | Look-ahead is currently possible |
| **Feature versioning/lineage** | History becomes incomparable on formula change |
| **Backpressure/drop accounting** | Silent tick loss possible (§13) |
| **Derivatives series** (IV surface, OI history, skew) | Snapshot-only today |
| **Research query layer** | No analytical surface |

---

## 4. Raw tick requirements

**Resolved statically from `fyers_apiv3/FyersWebsocket/map.json`:**

| Mode | Fields | Verdict |
|---|--:|---|
| `lite_val` | 3 | `ltp, symbol, type` — current default, **insufficient** |
| `data_val` (options/futures, FULL) | **23** | **Everything required** |
| `index_val` (INDEX, FULL) | 8 | No bid/ask/OI/volume — structurally impossible |
| `depthvalue` (`DepthUpdate`) | 32 | 5 levels × price/size/order |

Full-mode option payload: `ltp, vol_traded_today, last_traded_time, exch_feed_time, bid_size, ask_size, bid_price, ask_price, last_traded_qty, tot_buy_qty, tot_sell_qty, avg_trade_price, OI, low_price, high_price, Yhigh, Ylow, lower_ckt, upper_ckt, open_price, prev_close_price, type, symbol`

**Two consequences:**
1. `litemode=False` is required. One flag.
2. **NIFTY index can never supply an order book.** For underlying bid/ask/volume/OI, subscribe **NIFTY futures**. Market-structure fact.

**Capacity is not a constraint:** `symbol_limit = 5000` per channel, auto-chunked, multi-channel. A 162-instrument universe is 3% of cap; the full chain across 4 expiries (~1,400) fits.

### Canonical Tick shape

```
Tick
  instrument_id, exch_feed_time, receive_ts, ingest_seq
  ltp
  bid_price, ask_price, bid_size, ask_size          Optional → NULL = NOT_AVAILABLE
  vol_traded_today, OI, last_traded_qty, last_traded_time
  tot_buy_qty, tot_sell_qty, avg_trade_price
  prev_close_price, open_price, high_price, low_price
  lower_ckt, upper_ckt, Yhigh, Ylow
  feed_mode (LITE|FULL), connection_id, source
  raw_payload                                        verbatim, for provenance
  quality_flags
```

---

## 5. Universe management

```
UniverseChangeEvent            ← APPEND-ONLY, authoritative
  ts, instrument_id, action (SUBSCRIBE|UNSUBSCRIBE)
  reason (ATM_ROLL|EXPIRY_ROLL|VOL_EXPANSION|STARTUP)
  atm_at_time, atm_distance_at_subscription    ← FROZEN at write
  spot_at_time, valid_from, valid_to
```

**Two invariants:**
- `expiry_date` is identity. `CURRENT_WEEK` is a **query-time projection** from `(expiry_date, as_of)`. Storing the label mislabels every historical query after rollover.
- `atm_distance_at_subscription` is frozen. Recomputing it would mutate history as spot moves.

**Instruments are never removed from history** — only from the live universe (`valid_to` set).

---

## 6. Time semantics

| Concept | Authority |
|---|---|
| Ordering / windowing | **`exch_feed_time`** (exchange truth) |
| Latency measurement | `receive_ts − exch_feed_time` |
| Tie-break within same exchange ts | `ingest_seq` (monotonic per connection) |
| Session boundary | 09:15–15:30 IST, from holiday calendar |
| Window alignment | Clock-aligned, session-anchored (1H = 09:15–10:15, **not** 09:00) |
| Timezone | `core/clock.IST` — already correct |

**4H is excluded.** A 6h15m session cannot yield equal 4H bars; you would get one full bar and one 2h15m stub. It is an FX/crypto convention with no NSE meaning.

**Unresolved and needing a decision (§13.2):** the late-tick watermark.

---

## 7. Data quality & provenance

Every stored row carries:

```
source ∈ {LIVE_TICK, BROKER_HISTORICAL, EXCHANGE_HISTORICAL, DERIVED, RECONSTRUCTED, SYNTHETIC_TEST}
quality ∈ {OK, GAP, STALE, DUPLICATE, OUT_OF_ORDER, CROSSED, SUSPECT, PARTIAL}
completeness, freshness, connection_id, calc_version (derived rows)
```

**Feed health must be a first-class published state**, not an inference:

```
FEED_HEALTHY | FEED_DEGRADED | FEED_STALE | FEED_DISCONNECTED | FEED_RECOVERING
```

with evidence: last-tick-per-instrument, expected vs actual interval, watchdog state, reconnect count, symbols silently stale.

**`is_connected == True` must never by itself mean healthy.** The 2026-07-29 incident (7.5 h silence, `is_connected=True`, `reconnect_count=0`) is the proof. `CONNECTED + NO_TICKS = DATA_STALE` is a distinct failure class, and it must propagate into intelligence so Bujji cannot make a high-confidence decision on stale data.

---

## 8. Storage abstraction

```
TickStore
  append(ticks) -> AppendResult(written, deduped, dropped)
  range(instrument, t0, t1, *, as_of) -> Iterable[Tick]
  latest(instrument, *, as_of) -> Optional[Tick]
  gaps(instrument, t0, t1) -> Iterable[Gap]

CandleStore / SnapshotStore / FeatureStore / MarketStateStore
  ... all as_of-scoped
```

**Three binding rules:**
1. `as_of` is a **required keyword** on every read — look-ahead becomes a `TypeError`, not a review comment.
2. Intelligence imports protocols only. A safety test asserts no intelligence module imports `sqlite3`/`duckdb`.
3. `AppendResult.dropped > 0` must raise or alarm — **never silently lose a tick**.

**Backend:** SQLite/WAL initially (precedent: `journal/`, `mil_next/`). Parquet/DuckDB archival **only after measurement justifies it** — the storage spread is 10× (§14 Gate 1).

---

## 9. Multi-timeframe reconstruction

**One engine. Derive from 1m, never chain.**

```
TICKS ──► 1m ──┬──► 5m ──► 15m ──► 30m       (all computed FROM 1m, not from each other)
               ├──► 1H
               └──► Daily ──► Weekly ──► Monthly
```

Chaining (1m→5m→15m→1H) accumulates rounding and makes provenance ambiguous. Single-source derivation keeps `derived_from` unambiguous and every bar traceable to one authority.

Every candle carries `source_tick_range` (or `source_candle_range`) — answering "exactly which observations produced this bar?"

---

## 10. Feature / phenomenon / state hierarchy

```
FEATURE      scalar, one instrument × timeframe × timestamp
             e.g. EMA20 = 24513.2  (window=20, sufficiency=OK, calc_version=v3)
                ▼
PHENOMENON   named market behaviour, evidence-backed        ← msi_market_phenomena EXISTS
             e.g. VOLATILITY_COMPRESSION (from ATR percentile + BB width)
                ▼
STATE        coherent per-timeframe description
             e.g. 15m: {trend=UP, vol=COMPRESSED, structure=BALANCE, confidence=MODERATE}
                ▼
MULTI-TF STATE   cross-timeframe, CONFLICT-PRESERVING
             e.g. {D: bullish, 1H: neutral, 15m: bearish, conflict=TF_DIVERGENCE}
```

**Conflict must be represented, not averaged.** "Daily bullish, 15m bearish" is the single most valuable signal in the system; collapsing it to a scalar destroys it.

`msi_market_phenomena` already implements the phenomenon layer (10 types: trend expansion/failure, range compression, volatility expansion/compression, momentum persistence/exhaustion, mean reversion, gap continuation/failure). **Reuse it; feed it better inputs.**

---

## 11. Uncertainty propagation — the largest overlooked gap

**Finding: 20 packages define their own confidence constants.** The level names are mostly consistent (`HIGH/MODERATE/LOW/NONE/UNKNOWN`, with one stray `MEDIUM`), but there are at least six *parallel scales*:

```
CONFIDENCE_*      HIGH/MODERATE/LOW/NONE/UNKNOWN     (msi family)
STATUS_*          KNOWN/PARTIAL/UNKNOWN              (15M portfolio)
PNL_*             COMPLETE/PARTIAL/UNKNOWN           (15K)
QUALITY_*         EXCELLENT/GOOD/FAIR/POOR           (decision synthesis)
STRENGTH_*        STRONG/MODERATE/WEAK/UNKNOWN       (15J attribution)
DATA_QUALITY_*    (execution_reality)
epistemic         KNOWN/UNKNOWN/NOT_APPLICABLE/NOT_AVAILABLE  (15N)
```

**Nothing defines composition.** There is no rule for: *what confidence does a feature have when computed from a candle whose `data_quality = GAP`?* Today the answer is "whatever that package decides locally," which means **uncertainty does not propagate — it resets at every layer boundary.**

**Required: one uncertainty algebra.**

```
Uncertainty = (level, basis, limiting_factor)

Composition rules:
  MIN-rule       derived confidence ≤ min(input confidences)
  UNKNOWN-absorb any UNKNOWN input → UNKNOWN output (never averaged away)
  SUFFICIENCY    insufficient history → no value at all, not a low-confidence value
  GAP-taint      any GAP in the source window → caps derived confidence at LOW
  limiting_factor names WHICH input capped it   ← explainability
```

This is the mechanism that makes "why did you believe this?" answerable, and it is the difference between a system that degrades honestly and one that launders uncertainty into false precision as it climbs the stack.

---

## 12. Replay requirements — and a duplication finding

**Five replay subsystems exist:**

| Package | Files | Role |
|---|--:|---|
| `replay` | 10 | Corpus builder, historical session, validator |
| `mic_replay` | 7 | Publication record/replay, compatibility validator |
| `replay_engine` | 4 | Phase 15H formal replay |
| `msi_counterfactual_replay` | 9 | Counterfactual |
| `qualification` | 8 | Historical/replay runner, statistics |

**Replay parity cannot be claimed while five implementations exist.** This is the most serious duplication in the repository — more so than the observation packages, which turned out to be genuine specialisations.

**Requirement:** one canonical replay path (`replay_engine`, 15H, is the proven one). Others become adapters or are scoped to their specific purpose. **Not a merge to perform now — a decision to make before building on top.**

### Replay contract

```
TickStore.range(instrument, t0, T, as_of=T)
   ↓ CandleEngine     (same pure function as live)
   ↓ FeatureEngine    (calc_version pinned)
   ↓ Phenomena → State → MSI → Decision
```

Four guarantees: single implementation for live+replay; `as_of` on every read; `calc_version` pinned and recorded; forming candles type-excluded.

**Honest caveat (§13.6):** "same code path" is achievable only for the *pure core*. Anything touching IO, timeouts, retries or rate limits cannot be identical. The pure/impure boundary must be drawn explicitly, and parity asserted only across the pure core.

---

## 13. Challenge — what we have overlooked

### 13.1 Phase 15Q has a real design flaw I shipped

`CandleAggregator` closes a window **only when the next tick arrives**. For an illiquid far-OTM strike, a window can stay open for hours, and its candle is emitted with a wildly late wall-clock time. At session end `flush()` rescues it, but intraday the series is wrong for exactly the instruments where liquidity intelligence matters most.

**Fix required:** time-driven window closure (a clock tick or watermark), not tick-triggered. This must be corrected when 15Q is folded into the fabric.

### 13.2 Late-tick vs immutability is unresolved

`live_observation` correctly preserves late ticks. But if a tick arrives *after* its 1m candle closed:
- Revise the candle → immutability breaks, and every derived feature is now stale
- Ignore it → ticks and candles permanently disagree

**Neither is currently chosen.** Required: an explicit **watermark policy** — a bounded lateness window (e.g. 2s) after which a candle seals; late-beyond-watermark ticks are stored (never dropped) and flagged `OUT_OF_ORDER`, with the candle carrying `late_excluded_count`. Sealed candles get a `revision` field for the rare corrected case.

### 13.3 Backpressure and silent tick loss

The websocket callback is synchronous. At 162 instruments in full mode, if persistence blocks, ticks are dropped **inside the SDK with no signal.** This is the same failure class as the 2026-07-29 silence: the system would believe it is recording while it is not.

**Required:** bounded queue between callback and persistence, explicit drop counter, and drops treated as a data-quality incident — never silent.

### 13.4 The universe choice permanently bounds future research

If you store only ATM±10, you can **never** retroactively research a strategy needing ATM±15. Unlike a bug, this is unrecoverable — the data was never captured.

Given `symbol_limit = 5000`, the cost of a wider capture is far lower than I previously assumed. **Recommendation: capture wider than current strategy needs** (ATM±15 or ±20), because storage is cheap and un-captured history is infinitely expensive. This should be decided *before* 16B, since the measurement should test the wider universe.

### 13.5 Expiry settlement / instrument death

An option contract ceases to exist at expiry. Unaddressed:
- Does the tick series simply end, or is there a terminal settlement record?
- How does a query spanning expiry behave?
- Weekly→monthly transition when both settle the same day?

**Required:** an explicit `InstrumentLifecycle` (first_seen, last_seen, expired_at, settlement_price) so a gap at expiry is distinguishable from a feed failure. Currently those two would look identical.

### 13.6 "Same code, live and replay" is not literally achievable

Live code has timeouts, retries, rate limiting, reconnects. Replay has none. Parity can only be claimed for the **pure core** (aggregation, features, phenomena, state, decision logic). The IO shell is necessarily different.

**Required:** draw the pure/impure line explicitly, and make the parity test assert only across the pure core — otherwise the guarantee is rhetorical.

### 13.7 Feature versioning makes history incomparable

If EMA's implementation changes, previously stored feature rows are no longer comparable to new ones. Without `calc_version` on every row and a rule that features are **never** recomputed in place, any longitudinal study silently mixes two definitions.

### 13.8 Full mode's cost is still unknown

The repo docstring notes full mode fires on nearly every broadcast (it compares every field, including a timestamp that changes constantly). The rate could be **10× lite mode**. This is exactly what Gate 1 must measure — and it may change the universe decision.

### 13.9 Five replay subsystems (§12)

### 13.10 No uncertainty algebra (§11)

### 13.11 Stack A / Stack B is still undecided

Two complete applications. Stack A (deprecated ORB-VWAP) owns the only working websocket integration and 45+ test files. The decision cannot be deferred much longer — the fabric will need Stack A's hard-won feed-reliability patterns.

---

## 14. Integration boundaries with the existing stack

**The fabric replaces nothing above the state layer.**

| Boundary | Contract |
|---|---|
| Fabric → MSI | `MultiTimeframeState` + `Phenomena` replace the current 5 single-TF `DomainSignal`s fed to `msi_decision_synthesis`. **`synthesize()` itself is unchanged** — 15P proved it correct. |
| Fabric → Position Intelligence | Thesis monitoring reads features/state via `as_of`, not raw ticks |
| Fabric → Position Management | Unchanged; consumes thesis evaluations |
| Fabric → P&L (15K) | Unchanged; `pnl.py` remains the sole economic authority |
| Fabric → PaperBroker bridge (15L) | Unchanged; still the only execution surface |
| Fabric → Portfolio (15M) | Unchanged |
| Fabric → Outcome Memory (15N) | Memory may additionally store the `MarketState` at decision time — **read-only, no feedback loop** |
| Fabric → Replay (15H) | 15H becomes the canonical replay driver over `TickStore` |

**Explicitly preserved:** no outcome→decision feedback; PaperBroker remains the execution boundary; no live order path.

---

## 15. Dependency-ordered roadmap with validation gates

```
16A  Consolidation decisions ──────────────────────────────┐
      (Stack A/B · canonical replay · uncertainty algebra)  │
                         ▼                                  │
16B  FEED MEASUREMENT ═══ GATE 1 ═══════════════════════════┤ ← REAL MARKET REQUIRED
      full-mode rate · symbol cap · reconnect · depth cost   │
                         ▼                                  │
16C  Feed adapter + TickStore + backpressure + health       │
                         ▼                                  │
      ═══ GATE 2: one full session captured, zero silent drops ═══ ← REAL MARKET
                         ▼                                  │
16D  Universe manager (ATM/expiry rollover, lifecycle)      │
16E  Multi-TF engine (watermark policy, holiday calendar)   │
16F  Historical backfill ←── can run in PARALLEL from here ─┘
                         ▼
      ═══ GATE 3: replay of a captured session reproduces it exactly ═══
                         ▼
16G  Feature engine (versioned, lineage, uncertainty algebra)
16H  Derivatives intelligence (IV surface, OI series, skew)
                         ▼
      ═══ GATE 4: features stable across replay; uncertainty composes ═══
                         ▼
16I  Multi-TF market state (conflict-preserving)
16J  MSI integration — swap DomainSignal source
                         ▼
      ═══ GATE 5: shadow session end-to-end on fabric data ═══ ← REAL MARKET
                         ▼
16K  Research layer + tick replay + validation
```

### The gates

| Gate | Requires real market | Blocks | Pass criterion |
|---|:--:|---|---|
| **1** Feed measurement | **YES** | 16C–16E | Rate, cap, reconnect, depth cost measured. Storage decidable. |
| **2** Capture integrity | **YES** | 16D+ | One full session, zero silent drops, gaps explained |
| **3** Replay fidelity | No | 16G+ | Replay reproduces captured session byte-identically |
| **4** Feature stability | No | 16I+ | Deterministic across replay; uncertainty composes correctly |
| **5** End-to-end shadow | **YES** | 16K | Full chain on fabric data, one unattended session |

**Three gates need a live market.** Gate 1 is the immediate blocker and needs one trading session.

### Decisions needed before 16B

1. **Stack A vs Stack B** — recommend harvest-and-retire-incrementally
2. **Universe width** — recommend ATM±15/±20, not ±10 (§13.4); un-captured history is unrecoverable and capacity is not the constraint
3. **Canonical replay owner** — recommend `replay_engine` (15H)
4. **Watermark lateness bound** — needs Gate 1 latency data to choose

---

## 16. Verdict

**The architecture is sound and reachable by consolidation. No foundation requires rewriting.**

What changed across this audit, honestly:
- Two of my own earlier claims were wrong (`tick/` "dead"; ~200 symbol cap) and are corrected
- The observation packages are **specialisations, not duplicates** — no merges needed
- The **replay** packages *are* genuine duplication — five implementations
- The feed delivers **everything required**; it was a one-flag question, not an architecture question
- Capacity is a non-issue (5,000 vs 162)
- The largest genuinely-overlooked gaps are **uncertainty propagation** (§11), **the 15Q window-close flaw** (§13.1), **the late-tick watermark** (§13.2), and **silent backpressure loss** (§13.3)

**The binding constraint is no longer architectural. It is a single missing measurement** — full-mode tick rate — and a handful of decisions that only you can make.

**Nothing started. Awaiting approval on the four decisions above and on building the Gate 1 harness.**
