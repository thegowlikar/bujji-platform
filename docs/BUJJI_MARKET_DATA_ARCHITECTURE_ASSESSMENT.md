# Bujji — Market-Data Architecture Assessment (pre-implementation)

Audit against the "autonomous quant desk" target. **No code was written for this document.**

---

## 0. The finding that reframes everything

```
94 packages
856 Python files
ShadowSessionRunner imports ZERO observation packages
```

There are **seven** observation/tick subsystems already in the repo:

| Package | Files | Imported by | Status |
|---|---|---|---|
| `market_observation` | 9 | **34 modules** | The de-facto backbone `Observation` model |
| `live_market_events` | 9 | 20 | Event layer |
| `futures_observation` | 9 | 12 | Futures observations |
| `options_observation` | 9 | 8 | Option observations (Bhavcopy-shaped; **bid/ask never populated**) |
| `live_observation` | 9 | 7 | **Tick→window aggregation** (reused by 15Q) |
| `tick` | 2 | **0** | **Dead code** |
| `market_timeseries` | 5 | 0 | New (15Q) |

All follow an identical 9-file shape (`config/engine/journal/models/query/runner/serialization/taxonomy`). This is a **template that was instantiated seven times**.

And critically:

```
ShadowSessionRunner references to any observation package: 0
```

The production runtime polls REST every 30s and touches none of it. **`FyersTickFeed` has never fed a Bujji intelligence path.**

This means the dominant architectural problem is **not** missing capability. It is **integration debt plus duplication debt**. Building a new "market data brain" without resolving this would create the eighth observation subsystem.

---

## 1. Capability audit

| Capability | Exists | Complete | Connected | Reusable | Verdict |
|---|:--:|:--:|:--:|:--:|---|
| Websocket ticks | ✅ | ✅ | ❌ | ✅ | `FyersTickFeed` real, reconnect-safe, **never wired to intelligence** |
| Raw tick persistence | ❌ | — | — | — | **Missing entirely.** No tick DB anywhere |
| Tick aggregation | ✅ | ✅ | ⚠️ | ✅ | `live_observation` correct (late ticks, no fabrication); wired only to 15Q |
| 1m candles | ⚠️ | ❌ | ❌ | ✅ | Interval exists in taxonomy; no store |
| 5m candles | ✅ | ✅ | ❌ | ✅ | 15Q — **synthetic ticks only** |
| 15m / 30m / 1H | ❌ | — | — | — | Not in taxonomy |
| 4H | ❌ | — | — | — | **Ill-defined for NSE** (see §5) |
| Daily / Weekly / Monthly | ❌ | — | — | — | Missing |
| Historical backfill | ⚠️ | ❌ | ⚠️ | ✅ | FYERS `historical` endpoint **live-verified**, used ad-hoc for 30×5m bars only |
| Technical indicators | ⚠️ | ❌ | ❌ | ✅ | 6 indicators (15Q); target needs ~40 |
| Multi-timeframe state | ❌ | — | — | — | **Missing entirely** |
| Option-chain capture | ✅ | ⚠️ | ✅ | ⚠️ | Full ±500 chain in `market_snapshots.jsonl` — **JSONL, not queryable** |
| OI history | ⚠️ | ❌ | ⚠️ | ⚠️ | Fields exist in `options_observation`; no timeseries |
| Greeks history | ⚠️ | ❌ | ⚠️ | ✅ | `msi_greeks` computes per-cycle ATM only; not persisted as series |
| IV surface | ❌ | — | — | — | **Missing.** No skew, no term structure, no percentile |
| Premium behaviour | ✅ | ⚠️ | ✅ | ✅ | Phase 15E — per-cycle, not a timeseries |
| VIX intelligence | ⚠️ | ❌ | ⚠️ | ⚠️ | `get_vix()` exists; no VIX history/features |
| Market structure | ✅ | ⚠️ | ✅ | ✅ | MSSI/PSI real, single-timeframe only |
| Replay | ✅ | ✅ | ✅ | ✅ | Phase 15H — **event replay, not tick replay** |
| Data-quality engine | ⚠️ | ❌ | ⚠️ | ✅ | `execution_reality` has `DATA_QUALITY_*`; not tick-level |
| Research queries | ❌ | — | — | — | **Missing.** No analytical query surface |
| Market memory | ✅ | ✅ | ✅ | ✅ | Regime memory + Outcome Memory (15N) |

### Classification

**1. Existing & reusable** — `FyersTickFeed`, `live_observation` aggregation, `market_observation` model, `instrument_master` (has `resolve_atm`, expiry rows), FYERS `historical` API, `msi_greeks`, replay, EventStore, Outcome Memory.

**2. Existing but disconnected** — *the dominant category.* Websocket feed, all observation packages, `live_shadow_operator`, `live_pipeline_bridge`. None reach `ShadowSessionRunner`.

**3. Existing but insufficient** — indicators (6 vs ~40), single-timeframe structure, per-cycle (not series) Greeks/premium, ad-hoc backfill.

**4. Missing entirely** — raw tick persistence, TF hierarchy above 5m, multi-TF state, IV surface, research query layer, tick-level data quality, holiday calendar, feature lineage/versioning.

**5. Dangerous duplication** — **seven observation subsystems.** `tick/` is dead. `market_timeseries` risks becoming the eighth. **This must be resolved before expanding.**

---

## 2. Direct answer: is Phase 15Q enough?

**No. It is a correct foundation and roughly 5% of the target.**

What holds up: OHLC exactness, clock-aligned deterministic windows, gap honesty (no forward-fill), `Candle`/`FormingCandle` type split, insufficient-history→`None`, immutable store, reuse of `live_observation`.

What is insufficient:

| Dimension | 15Q | Target | Gap |
|---|---|---|---|
| Data source | Synthetic ticks | Live NIFTY ticks | **Not wired** |
| Raw ticks | Discarded after folding | Permanent, replayable | **Missing layer** |
| Timeframes | 5m | 1m→Monthly (9) | 8 missing |
| Instruments | 16 | ~162 dynamic | 10× + rollover |
| Expiries | 1 implicit | 4 with rollover | **No expiry manager** |
| Indicators | 6 | ~40 + provenance | 15% |
| Derivatives | none | IV surface, OI, skew, PCR | **Missing subsystem** |
| Backfill | none | years | **Missing** |
| Research | none | analytical SQL | **Missing** |

**The single most important structural flaw: 15Q stores only candles.** Ticks are folded and dropped. That forecloses microstructure intelligence (spread expansion, liquidity withdrawal, premium acceleration) and makes candles unreproducible — you can never re-derive a different timeframe from source, or fix an aggregation bug retroactively. **Raw ticks must become the authoritative store; candles become a materialized derivative.**

---

## 3. Storage: capacity analysis

**Volume (162 instruments, 09:15–15:30 = 22,500s):**

| Scenario | Ticks/sec/inst | Ticks/day | Bytes/row | Per day | **Per year (250d)** |
|---|---|---|---|---|---|
| Quiet | ~1 | 3.6 M | ~175 | 630 MB | **~160 GB** |
| Typical | ~2 | 7.3 M | ~175 | 1.3 GB | **~320 GB** |
| Volatile | ~5 | 18 M | ~175 | 3.1 GB | **~790 GB** |

Row = instrument_id, exch_ts, recv_ts, ltp, bid, ask, bid_qty, ask_qty, volume, oi, flags, seq ≈ 80 B raw → ~175 B in SQLite with row overhead + PK index.

**Is SQLite enough?**

- **Write throughput: yes, comfortably.** ~320 ticks/sec average vs SQLite's ~50k+/sec batched. Not a bottleneck.
- **Capacity: technically yes** (281 TB limit).
- **Analytical reads: no.** "ATM IV every 5m for 6 months" over a ~160 GB row-store means scanning far more than needed — no column projection, no compression, no predicate pushdown.

**Recommendation — hybrid, behind an abstraction:**

```
Live session   → SQLite WAL   (transactional, crash-safe, recoverable)
Session close  → Parquet/ZSTD (columnar, sorted by instrument+ts)
Research       → DuckDB over Parquet
Candles/state  → SQLite       (small, transactional — 15Q as-is)
```

Parquet+ZSTD on numeric tick data typically compresses **8–20×** → **~15–40 GB/year** instead of 320 GB. DuckDB queries it directly with no server.

**The critical requirement is the interface, not the technology.** Define `TickStore` / `CandleStore` protocols; the intelligence layer must never import `sqlite3` or `duckdb`. Then the backend is swappable without touching intelligence — and the migration to Timescale/ClickHouse later becomes a storage-layer change only.

**Retention:** ticks forever in Parquet (cheap at ~20 GB/yr); SQLite hot store rolls off after archival. Candles materialized (cheap, ~300 K rows/yr) **and** reproducible from ticks — materialization is a cache, ticks are truth.

---

## 4. Universe: is 162 enough? Should we capture the full chain?

**Two different questions with two different answers.**

**For ticks: ~162 is right, and may already be at the ceiling.** Full NIFTY chain across 4 expiries ≈ 1,000–1,400 contracts. Brokers cap symbols per websocket connection (FYERS commonly cited ~200).

> ⚠️ **Must-verify blocker:** I could not confirm the FYERS subscription cap from the code or a live session. This number determines the entire universe design and **must be measured against the real socket before committing.**

**For the full chain: use REST snapshots — which already exist.** `market_snapshots.jsonl` already captures the full ±500 chain each cycle. Ticks for ±10 strikes (microstructure), periodic chain snapshots for breadth (OI distribution, skew, max-pain). That split is honest and reuses built capability.

**Dynamic expansion during volatility:** yes in principle, but only after the cap is known and after `subscription_reason` is recorded. Never silently — every subscription change must be an event.

**ATM movement / expiry transitions — the schema must carry:**

```
instrument_id, underlying, strike, option_type, expiry
valid_from, valid_to           -- subscription validity window
subscription_reason            -- why this instrument entered the universe
atm_distance_at_subscription   -- point-in-time, NOT recomputed
expiry_bucket                  -- CURRENT_WEEK / NEXT_WEEK / FAR_WEEK / MONTHLY
```

Critically, **`expiry_bucket` is a point-in-time label, not an identity.** Today's `CURRENT_WEEK` becomes next week's expired contract. Historical queries must resolve the bucket *as of* the observation timestamp, or every backtest silently mislabels. Store the absolute expiry date as identity; derive the bucket per query.

**Old instruments are never unsubscribed from history** — only from the live universe. `valid_to` is set; rows remain immutable.

---

## 5. Timeframe hierarchy — derivation rules

**Derive everything from 1m, not by chaining.** Chained derivation (1m→5m→15m→1H) accumulates rounding and makes provenance ambiguous. Single-source derivation keeps every bar traceable to the same authority.

| TF | Source | Alignment |
|---|---|---|
| 1m | **Ticks** (authoritative) | Clock |
| 5m / 15m / 30m | 1m | Clock; divides evenly |
| 1H | 1m | ⚠️ 09:15 start → hours are 09:15–10:15, not 09:00 |
| **4H** | 1m | ⚠️ **Does not divide the 6h15m NSE session** |
| Daily | 1m | Session 09:15–15:30 IST |
| Weekly | Daily | ISO week |
| Monthly | Daily | Calendar month |

**4H is the real problem and needs your decision.** NSE trades 6h15m. Options:
1. `09:15–13:15` + `13:15–15:30` (one full, one 2h15m partial) — honest but asymmetric
2. Two half-sessions `09:15–12:22:30` + `12:22:30–15:30` — even but not 4H
3. **Drop 4H** — it is a forex/crypto convention with no NSE meaning

**My recommendation: option 3.** Adding a timeframe whose bars are structurally unequal invites silent misinterpretation for no analytical gain. Use 1H and Daily instead. If you want 4H, option 1 with explicit `is_partial` flagging.

**Holiday calendar does not exist anywhere in the repo** — required before Daily/Weekly/Monthly are trustworthy. Currently unsourced.

---

## 6. Correctness guarantees required

**Preventing future-data leakage:** every read must be as-of scoped —
```
store.recent(instrument, tf, count, as_of=T)   → only bars with window_end <= T
```
Without this, any research query silently sees the future. This is the single easiest way to produce a beautiful, worthless backtest.

**Forming-candle contamination:** 15Q's type split (`FormingCandle` has `last`, not `close`) is the right mechanism — **extend it to all timeframes**, never weaken it.

**Live/replay parity:** enforce structurally — one aggregation function used by both paths, with a safety test asserting no second implementation exists. The Phase 15H precedent (same reducer for live and replay) is exactly right and should be mandated for ticks too.

**Duplicate ticks across reconnect:** dedupe on `(instrument, exchange_ts, sequence)` where available; where the feed publishes no sequence, dedupe on `(instrument, exchange_ts, ltp, bid, ask)` and **flag the record as dedupe-heuristic** rather than pretending it is exact.

**Websocket recovery:** on reconnect, mark a `FEED_GAP` interval for every subscribed instrument between last-tick and resubscribe. Candles spanning a gap must carry `data_quality=GAP` — never silently completed.

**OI timestamping:** OI updates arrive at different cadence from price. Store OI with its **own** observation timestamp, never merged into a price tick's timestamp.

**Reconciling historical API with live ticks:** never mix in one row. Tag `source ∈ {LIVE_TICK, HISTORICAL_API, RECONSTRUCTED, SYNTHETIC_TEST}` on every candle. A safety test must assert `SYNTHETIC_TEST` never lands in a production store.

---

## 7. Recommended phased roadmap

The ordering is driven by **dependency, not ambition**.

**Phase 16A — Consolidation & Decision (no new capability)**
Resolve the seven-subsystem duplication *before* building. Delete dead `tick/`. Decide the canonical observation model. Define `TickStore`/`CandleStore` protocols. Verify the FYERS subscription cap live. **Highest priority — everything else compounds on top of this.**

**Phase 16B — Live tick capture** ⟵ *the real unblocking step*
Wire `FyersTickFeed` → normalization → `TickStore` (SQLite WAL). Dedupe, gap detection, reconnect handling. Record ONE real NIFTY session. **This is the first time real ticks enter Bujji.**

**Phase 16C — Dynamic universe manager**
Instrument master → ATM resolution → 4 expiry buckets × ±10 strikes; validity windows, rollover, subscription events.

**Phase 16D — Multi-timeframe aggregation**
1m from ticks; 5m/15m/30m/1H/D from 1m; W/M from D. Holiday calendar. Provenance + `as_of` queries.

**Phase 16E — Historical backfill**
FYERS `historical` for spot/VIX. Document precisely what options history is *not* available. Tag sources.

**Phase 16F — Feature engine**
~40 indicators, each with definition, min-history, missing-data semantics, version, lineage, tests.

**Phase 16G — Derivatives intelligence**
IV surface, skew, term structure, OI distribution, PCR, gamma concentration, expected move.

**Phase 16H — Multi-timeframe market state**
Per-TF structured state + explicit cross-TF conflict representation (not flattened).

**Phase 16I — Event-driven runtime**
Decompose the 30s loop into data/aggregation/feature/intelligence/position loops.

**Phase 16J — Research layer**
Parquet archival + DuckDB analytical queries.

**Phase 16K — Tick replay**
Deterministic full-day reconstruction from raw ticks.

### Dependency graph

```
16A Consolidation  ─────────────► everything
     │
     ▼
16B Live ticks ──► 16C Universe ──► 16D Multi-TF ──┬──► 16F Features ──┐
     │                                              │                   ├──► 16H Multi-TF State ──► 16I Runtime
     │                                 16E Backfill ┘   16G Derivatives ┘
     │
     └──────────────────────────────► 16J Research ──► 16K Tick replay
```

**16B is the true bottleneck.** Until real ticks land, 16D–16H are all built on synthetic data — the exact trap Phase 15P exposed, where an entire chain of conclusions rested on stale/unreal evidence.

---

## 8. Autonomous-desk readiness

Beyond data, still missing:

| Missing | Why it blocks autonomy |
|---|---|
| Real position lifecycle | Never executed end-to-end (15G–15P) |
| Statistical sufficiency | Only `MIN_SAMPLE_SIZE`; no significance/multiple-comparison discipline |
| Confidence calibration | Nothing measures whether stated confidence matches realised accuracy |
| Execution quality | Fill data exists; nothing judges it |
| Historical similarity search | Only exact-match filtering; no "what resembles now?" |
| Learning loop | Deliberately absent — correct, but a prerequisite for autonomy |
| Risk governance for the new core | `risk_governor` is legacy/disconnected |
| Capital allocation | Not connected |

**Bujji is currently an unusually well-instrumented market observer that has never traded.** The gap to "autonomous desk" is not primarily analytical sophistication — it is (a) real data, (b) one real completed lifecycle, (c) calibration, (d) risk governance.

---

## 9. Answer to the direct question

> **"Is Phase 15Q as currently implemented enough?"**

**No — good foundation, insufficient.** Keep it; extend it. Specifically it must change by: storing raw ticks as authoritative (not folding-and-dropping), expanding 5m→9 timeframes, 16→162 dynamic instruments, adding expiry/ATM rollover with validity windows, adding source/lineage/version to every row, adding `as_of` scoping, and — before any of that — resolving the seven-observation-subsystem duplication rather than becoming the eighth.

**The most important correction is not scope, it is sequence:** do not build layers 16D–16H on synthetic ticks. Get real ticks flowing (16B) first, so every subsequent layer is validated against reality as it is built.
