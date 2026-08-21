# Phase 16A + 16B — Consolidation Audit & Feed Capability Measurement

**Audit only. No code written, nothing deleted, nothing merged.**

---

## 0. Corrections to my previous assessment

Two of my earlier claims were wrong. Both are corrected here with evidence.

### Correction 1 — `tick/` is NOT dead

My "zero importers" figure counted only absolute `bujji.<pkg>` imports and **missed relative imports entirely.**

```
bujji/app.py:48:  from .tick.engine import TickEngine
bujji/app.py:49:  from .tick.health import HealthEngine
tests/: 45 files reference it
```

`tick/` is imported by an application entry point and covered by 45 test files. **It was never a deletion candidate.** Your instruction not to infer "dead" from import counts was correct and my methodology was not.

Corrected counts for every package I previously called zero-consumer:

| Package | abs | rel | tests | Corrected status |
|---|--:|--:|--:|---|
| `trade` | 0 | **5** | **155** | **LIVE — heavily used** |
| `signal` | 0 | **4** | **50** | **LIVE** |
| `tick` | 0 | **1** | **45** | **LIVE** |
| `integration` | 0 | **1** | **45** | **LIVE** |
| `runtime_safety` | 0 | **5** | 3 | **LIVE** |
| `execution_integration` | 0 | 0 | 14 | Test-only |
| `mil_next` | 0 | 0 | 8 | Test-only |
| `shadow_validation` | 0 | 0 | 8 | Test-only |
| `outcome_memory` | 1 | 0 | 8 | Phase 15N — pending integration |
| `shadow_observatory` | 0 | 0 | 6 | Test-only |
| `portfolio_intelligence` | 0 | 0 | 3 | Phase 15M — pending integration |
| `shadow_trade_construction` | 0 | 0 | 3 | Phase 14 — pending integration |
| `shadow_lifecycle` | 0 | 0 | 3 | Test-only |
| `replay_engine` | 0 | 0 | 2 | Phase 15H — pending integration |
| `market_timeseries` | 0 | 0 | 2 | Phase 15Q — pending integration |
| `context_window` | 0 | 0 | 2 | Test-only |
| `intelligence_report` | 0 | 0 | 2 | Test-only |
| `msi_evidence_packet` | 0 | 0 | 2 | Test-only |
| `msi_engineering_evidence_board` | 0 | 0 | 2 | Test-only |
| `msi_position_recomposition` | 0 | 0 | 1 | Test-only |

**Nothing is safe to delete.** Everything has at minimum test coverage. `tick/`, `trade/`, `signal/`, `integration/`, `runtime_safety/` are live application code.

### Correction 2 — my storage estimate was built on a false premise

I projected ~320 GB/year. That assumed a tick rate and payload size that the SDK evidence now lets me compute properly (§16B). The rate still requires live measurement, but the payload question is now **settled**.

---

## 1. THE structural finding: two parallel application stacks

```
bujji/app.py                        bujji_options_os_runner.py
  ├── FyersTickFeed  ← WIRED          ├── ShadowSessionRunner
  ├── TickEngine                      ├── intelligence_cycle_recorder
  ├── HealthEngine                    ├── msi_* family (25 packages)
  ├── SignalEngine                    ├── REST polling @ 30s
  ├── TradeManager                    └── NO tick feed
  ├── ExecutionEngine
  ├── CapitalManagementEngine
  ├── Orchestrator / EventBus
  ├── TradeJournal
  └── DashboardServer
```

`bujji_options_os_runner.py` documents this explicitly at line 10:

> `bujji/app.py` (the unrelated, **deprecated legacy ORB-VWAP entrypoint**).

So the repository contains **two complete trading applications**:
- **Stack A** — legacy ORB-VWAP intraday strategy. Deprecated. **Already consumes the websocket.**
- **Stack B** — the Options OS / MSI intelligence stack. Current. **REST-only.**

**This reframes the entire problem.** My earlier statement "`FyersTickFeed` has never fed a Bujji intelligence path" was correct for Stack B but incomplete: the feed *is* wired — into the deprecated stack.

Critically, `TickEngine` is **not a recorder**. It subscribes to `POSITION_OPENED`/`POSITION_CLOSED` and runs `_check_once(symbol)` — it is a **position-exit monitor** that watches an open position's symbol for stop/target triggers. It persists nothing.

**Confirmed: no tick persistence exists anywhere in the repository.**

---

## 2. 16B — Feed capability, RESOLVED STATICALLY

The authoritative wire schema is `fyers_apiv3/FyersWebsocket/map.json`. This answers the field question **without a live session.**

### lite_val — 3 fields (current default, `litemode=True`)
```
ltp, symbol, type
```

### data_val — 23 fields (FULL mode, scrips: options & futures)
```
ltp, vol_traded_today, last_traded_time, exch_feed_time,
bid_size, ask_size, bid_price, ask_price,
last_traded_qty, tot_buy_qty, tot_sell_qty, avg_trade_price,
OI, low_price, high_price, Yhigh, Ylow,
lower_ckt, upper_ckt, open_price, prev_close_price, type, symbol
```

### index_val — 8 fields (FULL mode, INDEX)
```
ltp, prev_close_price, exch_feed_time,
high_price, low_price, open_price, type, symbol
```

### depthvalue — 32 fields (separate `DepthUpdate` subscription)
```
bid_price1..5, ask_price1..5, bid_size1..5, ask_size1..5,
bid_order1..5, ask_order1..5, type, symbol
```

### What this means

| Scope requirement | Verdict |
|---|---|
| bid / ask | ✅ **AVAILABLE** (options, full mode) |
| bid_qty / ask_qty | ✅ **AVAILABLE** (`bid_size`/`ask_size`) |
| volume | ✅ **AVAILABLE** (`vol_traded_today`) |
| **open interest** | ✅ **AVAILABLE** (`OI`) |
| exchange timestamp | ✅ **AVAILABLE** (`exch_feed_time`) |
| last traded time | ✅ **AVAILABLE** |
| previous close | ✅ **AVAILABLE** |
| market depth | ✅ **AVAILABLE** — 5 levels + order counts |
| circuit limits, 52wk H/L | ✅ Bonus fields |
| **NIFTY spot bid/ask/OI/volume** | ❌ **STRUCTURALLY UNAVAILABLE** — index is not traded |

**Your §4 requirement is fully satisfiable for options and futures.** It requires exactly one change: `litemode=False`.

**One architectural consequence:** the NIFTY *index* can never supply an order book. For underlying bid/ask/volume/OI you must subscribe **NIFTY futures**. The index gives price only. This is a market-structure fact, not a broker limitation.

### Symbol capacity — also resolved

```python
self.symbol_limit = 5000                     # data_ws.py:218
LIMIT_EXCEED_MSG_5000                        # explicit error code
symbol_chunks = [...]                        # automatic chunking
scrips_per_channel[self.channel_num]         # multi-channel support
```

**5,000 symbols per channel**, with automatic chunking and multi-channel support.

I previously flagged "~200 symbols" as a probable hard blocker. **That was wrong.** Your proposed 162-instrument universe is ~3% of the SDK cap. Even the **entire NIFTY option chain across 4 expiries (~1,400 contracts) fits comfortably.**

This inverts the §9 feasibility question: full tick capture across a wide universe is **not capacity-blocked at the SDK level.** The remaining constraints are throughput and server-side enforcement, both of which still need live measurement.

### Storage — now computable, one unknown remains

| Item | Value |
|---|---|
| Full-mode option tick | 23 fields, mostly int32 → ~90–100 B raw |
| SQLite row + PK index | ~180–220 B |
| Index tick | 8 fields → ~40 B raw, ~90 B stored |
| **Ticks/sec/instrument** | ⚠️ **STILL UNKNOWN — requires live measurement** |

At 162 instruments × 22,500 s:

| Rate assumption | Ticks/day | Storage/day | **Per year** |
|---|--:|--:|--:|
| 0.5/s | 1.8 M | ~380 MB | ~95 GB |
| 2/s | 7.3 M | ~1.5 GB | ~380 GB |
| 5/s | 18 M | ~3.8 GB | ~950 GB |

Spread is **10×**. This single number determines whether SQLite alone suffices or Parquet archival is mandatory. **It is the one genuinely blocking measurement.**

---

## 3. 16A deliverables

### Canonical ownership proposal — one owner per concept

| Concept | Canonical owner | Basis |
|---|---|---|
| Instrument identity | `broker/instrument_master.py` | Already has `resolve_atm`, expiry rows |
| Observation schema | **`market_observation`** | 31 importers — de-facto standard |
| Data-quality vocabulary | `execution_reality` | Already defines `DATA_QUALITY_*` |
| Feed adapter | `broker/fyers_ws.py` | Only websocket implementation |
| Tick aggregation | **`live_observation`** | Correct late-tick / no-fabrication semantics |
| Raw tick persistence | **NEW — `TickStore` protocol** | Does not exist anywhere |
| Candle persistence | `market_timeseries` (reworked) | 15Q, behind protocol |
| Option-chain snapshots | `options_observation` (adapter) | Bhavcopy-shaped; wrap, don't extend |
| Feature calculation | `market_timeseries.indicators` → new Feature Engine | 15Q seed |
| Market state | `market_state` / `market_state_builder` | Existing |
| Replay | `replay_engine` (15H) | Proven |
| Position identity | `position_lifecycle` (15G) | Proven |
| Outcome memory | `outcome_memory` (15N) | Proven |

**Adapters may exist; canonical models must not be duplicated.** `market_observation` owns identity/value/quality/provenance; everything else adapts to it.

### Duplication analysis — revised

The seven "observation subsystems" are **less duplicative than they appeared.** They are domain-specialised adapters over one shared `Observation` model:

| Package | Real role | Duplicate? |
|---|---|---|
| `market_observation` | Base model | **Canonical** |
| `live_observation` | Tick→window aggregation | No — unique capability |
| `options_observation` | Bhavcopy option rows | No — distinct source |
| `futures_observation` | Futures rows | No — distinct source |
| `live_market_events` | Event envelope | Partial overlap — evaluate |
| `execution_reality` | Quote quality | No — unique |
| `market_timeseries` | Candle store (15Q) | **At risk** — must be folded in |

**Revised judgement: this is specialisation, not duplication.** The real debt is that none of it is connected to Stack B, not that it is redundant. **No merges are recommended.**

### Retirement candidates

**None.** Every package has test coverage or live imports. `tick/` is explicitly retained — it is Stack A application code.

The correct framing is **Stack A deprecation**, not package deletion — and that is a separate decision requiring your approval, not something to infer from an import graph.

---

## 4. Storage protocol proposal

```
TickStore
  append(ticks) -> int
  range(instrument, t0, t1, as_of) -> Iterable[Tick]
  latest(instrument, as_of) -> Optional[Tick]
  gaps(instrument, t0, t1) -> Iterable[Gap]

CandleStore
  write(candle) / write_many(candles)
  recent(instrument, timeframe, count, as_of) -> List[Candle]
  range(instrument, timeframe, t0, t1, as_of) -> List[Candle]

OptionSnapshotStore / FeatureStore / MarketStateStore
  ... analogous, all as_of-scoped
```

**Three binding rules:**
1. `as_of` is a **required** parameter on every read — a caller cannot forget it, so look-ahead becomes a type error rather than a review comment.
2. Intelligence imports protocols only — never `sqlite3`, never `duckdb`.
3. Every row carries `source ∈ {LIVE_TICK, BROKER_HISTORICAL, EXCHANGE_HISTORICAL, DERIVED, RECONSTRUCTED, SYNTHETIC_TEST}`, with a safety test asserting `SYNTHETIC_TEST` never lands in a production store.

---

## 5. What still needs live verification

Reduced from six items to **four**:

| # | Item | Why live |
|---|---|---|
| 1 | **Tick rate per instrument class, full mode** | 10× storage spread; blocks storage decision |
| 2 | Server-side symbol cap vs SDK's 5,000 | SDK limit ≠ server enforcement |
| 3 | Reconnect/resubscribe under real disconnect | Known-defective SDK path; 2 prod incidents |
| 4 | Whether `DepthUpdate` counts against the same cap | Determines depth feasibility |

Items previously listed as blockers and **now resolved statically**: full-mode field set, OI availability, bid/ask availability, symbol-limit order of magnitude.

**16B's live measurement is now a narrow, well-specified experiment** rather than open-ended discovery.

### Measurement harness spec (to build, not yet run)

- Subscribe progressively: 10 → 25 → 50 → 100 → 162 → 500 symbols
- Both `litemode=True` and `False`
- Instrument classes: index, futures, ATM CE/PE, ±5, ±10, far OTM, all 4 expiries
- Record **every raw payload verbatim** + receive timestamp + connection id
- Metrics: ticks/min/instrument, peak & median ticks/s, field-presence histogram, gap intervals
- Fault injection: force disconnect, observe resubscribe; hold 30 min to detect silent stall
- Output: field-availability matrix, rate table, capacity ceiling, storage projection

**One session, ~1 trading day.**

---

## 6. Final question — can consolidation get us there?

> **Can the current repository evolve into the autonomous Quant Desk through consolidation and integration, or does the accumulated debt require a controlled rewrite?**

### Answer: **Consolidation and integration are sufficient. No rewrite of any existing foundation is required.**

Evidence:

1. **The observation packages are not redundant** — they are specialised adapters over one canonical model (`market_observation`, 31 importers). Nothing needs merging.
2. **Tick aggregation is correct today** — `live_observation` handles late ticks and refuses to fabricate empty candles. Verified in Phase 15Q.
3. **The feed already delivers everything required** — 23 fields including OI, bid/ask, sizes, volume. One flag change.
4. **Capacity is not a constraint** — 5,000-symbol SDK cap vs a 162-instrument target.
5. **The intelligence stack is sound** — Phases 15G–15P are replay-proven, recovery-proven, safety-tested. Phase 15P specifically proved `msi_decision_synthesis` reaches every documented state correctly.
6. **The only genuinely missing component is a `TickStore`** — a new capability, not a replacement for one.

### What must be built new (not rewritten)

| Component | Size |
|---|---|
| `TickStore` + protocols | New, small |
| Feed adapter (full mode → canonical Observation) | New, small |
| Multi-timeframe engine | Extends 15Q |
| Universe manager | New |
| Feature engine | Extends 15Q's 6 indicators |
| Derivatives intelligence | New |
| NSE holiday calendar | New (still absent) |

### The one real architectural decision you must make

**Stack A vs Stack B.** The repository maintains two complete applications. Stack A is documented as deprecated but is live code with 45+ test files and owns the only working websocket integration.

Three options:
- **(a) Formally retire Stack A** — extract `FyersTickFeed` usage patterns into the new spine, freeze the rest.
- **(b) Keep both** — accept permanent dual maintenance.
- **(c) Harvest and retire incrementally** — build the spine in Stack B, port Stack A's proven feed handling, retire Stack A only once the spine is proven.

**I recommend (c).** Stack A's tick handling has survived real production incidents; that hard-won reliability knowledge should be ported, not discarded. But this is your decision and I have not acted on it.

### Verdict

**The architecture is strong enough to begin building the Market Data Spine.** The debt is real but it is **integration debt, not design debt** — and my two corrections in §0 show the debt is meaningfully *smaller* than my previous assessment claimed.

**Recommended immediate next step:** build and run the 16B measurement harness (one trading session, four questions). That single experiment unblocks storage architecture, universe sizing, and the tiering decision simultaneously.

**Not started. Awaiting approval on: (1) the Stack A/B decision, (2) proceeding to build the 16B harness.**
