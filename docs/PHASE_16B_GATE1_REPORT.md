# Phase 16B — Gate 1: Live Feed Capability Measurement

# GATE 1 VERDICT: **BLOCKED — NOT RUN**

Not PASS, not CONDITIONAL, not FAIL. **The measurement did not execute**, so there is no feed evidence to grade. Reporting the observation first, per instruction.

---

## 1. Blockers (both external, neither architectural)

```
Wall clock (VPS)  : Tue 2026-08-11 17:14 IST
NSE session       : 09:15–15:30 IST  →  CLOSED (1h44m ago)
Access token      : EXPIRED 107.24 hours ago  (~4.5 days)
FYERS_PIN         : NOT configured in .env.fyers
TokenManager      : can_refresh() → False  (requires pin)
```

The refresh token was written 2026-08-06 and FYERS refresh tokens last ~15 days, so it is very likely still valid. Adding `FYERS_PIN` to `.env.fyers` should restore the access token without an interactive browser login.

**I did not attempt authentication and did not handle credentials.** That step is yours.

**No architectural decision was reopened.** The lock holds.

---

## 2. What WAS completed (no auth required)

### 2.1 Universe — ±25, built from the real symbol master

`public.fyers.in/sym_details/NSE_FO.csv` is public, so the approved universe was constructed and validated against real listed contracts:

```
NIFTY contracts in symbol master : 4,012
Future expiries available        : 18

SELECTED EXPIRY BUCKETS:
  2026-08-11  WEEKLY   dte=0    (expiry day)
  2026-08-18  WEEKLY   dte=7
  2026-08-25  MONTHLY  dte=14
  2026-09-01  WEEKLY   dte=21

ATM grid (50)  : 24250   [proxy — real spot required at run time]

UNIVERSE
  spot + VIX            :   2
  per expiry (51×CE/PE) : 102
  4 expiries            : 408
  ─────────────────────────────
  TOTAL                 : 410   (+1 NIFTY futures = 411) ✓
```

**Zero missing strikes** across all four expiries at ATM±25. Every symbol is a real, listed contract. Written to `/tmp/gate1_universe.json` (73.0 KB).

This matches the approved 411 exactly. **The universe was not silently reduced.**

### 2.2 Harness v2 — built to the full expanded spec

`scripts/gate1/gate1_measure_v2.py`. Binds `data_ws.FyersDataSocket` **directly**, because `FyersTickFeed` keeps only `symbol`+`ltp` and discards the other 21 full-mode fields — measuring through it would characterise the wrapper, not the feed.

| Spec requirement | Implementation |
|---|---|
| Raw evidence first | Verbatim payload → append-only JSONL **before** parsing |
| Ramp 10→50→100→200→300→411 | `RAMP = [10, 50, 100, 200, 300, 411]` |
| Lite / full / full+depth | `--mode both`, `--depth` (separate cost experiment) |
| Field envelope | Present **and** null counts per field, per instrument kind |
| Rates | msgs/s, msgs/s/instrument, bytes/s, burst max/p95/median, quiet seconds |
| Latency | `exch→recv` and `recv→processing`, both full distributions |
| **Negative latency** | **Preserved exactly**; `negative_count` reported. Never clamped. |
| **Clock skew vs latency** | Separated: minimum observed offset = floor estimate (`clock_offset_floor_ms`); jitter above floor reported as transport. Never merged. |
| Percentiles | min, p50, p90, p95, p99, **p99.9**, max, mean |
| Duplicates | Exact-duplicate detection on `(symbol, ltp, exch_feed_time, last_traded_time)` |
| Monotonicity | Exchange-time and receive-time violations counted separately |
| Backpressure | Queue depth, max depth, offered/written/dropped, drop %, write latency |
| **Drops** | First-class result. `no_silent_drops` validation flag. |
| Silent symbols | Classified `NO_UPDATE` / `LOW_LIQUIDITY`, with an explicit note that `SUBSCRIPTION_FAILURE` needs reconnect/ack evidence — **not inferred** |
| Reconnect | Disconnect-detect latency, time-to-first-tick, symbols restored/missing, connect counts |
| Silent-stall | `--stall-test-minutes` with a 30s-resolution timeline |
| Bounded | `MAX_RUNTIME_S = 6h`, disk guard at 2 GB free |
| Deterministic | `sort_keys=True` JSON output |
| Self-diagnosis | `harness_was_bottleneck` flag → rates reported as a lower bound if tripped |

### 2.3 Pre-run validation — all pass

```
forbidden refs      : NONE
imports             : __future__, argparse, collections, datetime,
                      fyers_apiv3.FyersWebsocket, json, os, pathlib,
                      queue, shutil, signal, statistics, sys, threading, time
broker methods used : ['close_connection', 'subscribe']
credential handling : env-only = True | token never logged = True
bounded runtime     : True | bounded disk: True
deterministic output: True
```

No import of execution, trading_brain, position_lifecycle, msi_*, risk, capital, outcome_memory, portfolio, PaperBroker, signal, or trade. **Read-only with respect to trading, verified by AST.**

---

## 3. KNOWN vs UNKNOWN

### Now KNOWN (established without the live feed)

| Fact | Source |
|---|---|
| Lite mode delivers exactly 3 fields: `ltp, symbol, type` | `map.json` `lite_val` |
| Full mode (options/futures) declares **23** fields incl. `bid_price, ask_price, bid_size, ask_size, vol_traded_today, OI, exch_feed_time, last_traded_time, prev_close_price`, circuit limits | `map.json` `data_val` |
| Full mode (INDEX) declares **8** fields — no bid/ask/OI/volume | `map.json` `index_val` |
| Depth declares **32** fields — 5 levels × price/size/order | `map.json` `depthvalue` |
| SDK symbol cap = **5,000/channel**, auto-chunked, multi-channel | `data_ws.py:218` |
| `FyersTickFeed` discards 21 of 23 full-mode fields | `fyers_ws.py:312-321` |
| NIFTY lot size = **65** | Symbol master |
| 411-instrument universe is fully constructible; zero missing strikes | Symbol master |
| Full chain (4 expiries) ≈ 1,851 instruments = 37% of SDK cap | Derived |
| REST option chain supplies `oi`/`prev_oi`/`oich`, exact | Live-verified 2026-07-20 |

### Still UNKNOWN — requires the live run

| # | Unknown | Blocks |
|---|---|---|
| 1 | **Actual full-mode tick rate** per instrument class | Storage tier, ingestion topology |
| 2 | Whether live payloads match `map.json` (extra/missing fields) | Tick contract |
| 3 | `exch_feed_time`→`receive_ts` distribution | **Watermark bound** |
| 4 | Clock-skew magnitude and sign | Latency interpretation |
| 5 | Out-of-order arrival frequency | Lateness policy |
| 6 | Duplicate rate, especially post-reconnect | Dedup design |
| 7 | Server-side symbol cap vs SDK 5,000 | Universe viability |
| 8 | Queue/drop behaviour at 411 in full mode | Ingestion topology |
| 9 | Reconnect resubscribe fidelity | Recovery design |
| 10 | Silent-stall reproducibility | Health gating |
| 11 | Depth incremental cost; whether it shares the cap | Depth tiering |
| 12 | Whether a sequence number exists on the wire | Ordering/dedup |
| 13 | Real bytes/message | Storage sizing |

**Every Gate 2 design input remains UNKNOWN.** This is exactly why the watermark, storage tier and ingestion topology were deferred — none can be chosen on present evidence.

---

## 4. Deliverables status

| # | Deliverable | Status |
|---|---|---|
| 1 | Raw measurement corpus | ⛔ requires run |
| 2 | Machine-readable summary | ⛔ requires run (schema implemented) |
| 3 | Human-readable report | ✅ **this document** |
| 4 | Per-mode/per-universe statistics | ⛔ requires run (implemented) |
| 5 | Feed capability matrix | ⚠️ **partial** — declared schema known; live-verified pending |
| 6 | Backpressure/drop report | ⛔ requires run (implemented) |
| 7 | Latency/clock-skew report | ⛔ requires run (implemented, separated) |
| 8 | Reconnect report | ⛔ requires run (implemented) |
| 9 | Silent-symbol report | ⛔ requires run (implemented) |
| 10 | Depth-cost report | ⛔ requires run (implemented) |

---

## 5. To run

```bash
# 1. Add FYERS_PIN to .env.fyers and refresh the access token   (YOUR STEP)
# 2. Verify the token decodes as VALID, then:

cd /opt/bujji/app
set -a && . ./.env.fyers && set +a

# rebuild with the REAL spot at run time (ATM proxy is not acceptable for a real run)
STRIKES_EACH_SIDE=25 /opt/bujji/.venv/bin/python scripts/gate1/build_universe.py <SPOT>

/opt/bujji/.venv/bin/python scripts/gate1/gate1_measure_v2.py \
    --universe /tmp/gate1_universe.json \
    --out /opt/bujji/gate1 \
    --phase-seconds 300 \
    --mode both --depth --reconnect-test --stall-test-minutes 30
```

**Runtime ≈ 2h20m** (6 ramps × 2 modes × 5min + depth + reconnect + 30min stall).
**Recommended window: 10:00–13:00 IST on a non-expiry day.** Measuring only on 0DTE would over-size the storage architecture; 2026-08-11 was itself an expiry.

**Add `--stall-test-minutes 30` deliberately** — the 2026-07-29 incident (7.5h silence with `is_connected=True`, `reconnect_count=0`) is precisely what this test exists to make measurable.

---

## 6. Verdict

# GATE 1: **BLOCKED — NOT RUN**

No feed characteristics are reported, because none were measured. Nothing has been inferred, estimated, or fabricated.

**Ready:** universe (411, real contracts, zero gaps) · harness v2 (full spec) · pre-run validation (all pass) · safety (AST-verified read-only).

**Blocked on:** token refresh (yours) · one non-expiry trading session.

**Gate 1 remains the next operational step. 16C is not started and must not begin until the thirteen unknowns above are measured.**

---

**No production code written, deleted, merged, or refactored. Harness lives in `scripts/gate1/`, outside `bujji/`. Working tree uncommitted at `b148e39`.**
