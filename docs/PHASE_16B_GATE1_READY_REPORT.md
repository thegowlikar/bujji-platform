# Phase 16B — Gate 1 READY REPORT (Track A)

**Measurement-quality corrections only. No TickStore, no fabric, no candle storage, no storage/watermark/topology decision.**

---

## 1. Status

# GATE 1 HARNESS: **READY**

`scripts/gate1/gate1_measure_v3.py` — all 4 defects and 8 measurement gaps from the readiness review are corrected and verified.

Previous versions retained unmodified (`gate1_measure.py`, `gate1_measure_v2.py`) as an audit trail. Nothing deleted.

---

## 2. Corrections applied

| ID | Defect / gap | Fix | Verified |
|---|---|---|---|
| **D1** | `Metrics` mutated from callback thread, no lock (v1 had one, v2 lost it) | `threading.Lock` guarding every mutation and the summary read | ✅ |
| **D2** | Unbounded `_seen` duplicate set → OOM / swap risk | Bounded FIFO window, `DUP_WINDOW = 250_000`, reported in output | ✅ |
| **D3** | Depth phase measured depth **+** 411 symbols | `unsubscribe(SymbolUpdate)` + 10s settle before depth phase | ✅ |
| **D4** | `MAX_DISK_BYTES` declared but never enforced; dead imports | Enforced in `guard()`; `signal`/`sys` removed | ✅ |
| **G1** | **Out-of-order counted but magnitude never measured** | `LATENESS_MAGNITUDE_ms` full distribution + per instrument kind | ✅ |
| **G2** | 5-min samples only — cannot size storage | `--sustained-minutes` full-universe phase + per-minute msg/byte buckets | ✅ |
| **G3** | No CPU/memory — ingestion topology undecidable | `/proc`-based RSS, threads, utime/stime per phase (no psutil dependency) | ✅ |
| **G4** | Subscription failure indistinguishable from illiquidity | Ack capture + correlation → `SUBSCRIPTION_FAILURE` / `NO_UPDATE` / `LOW_LIQUIDITY` / `UNKNOWN` | ✅ |
| **G5** | Raw corpus never re-parsed — replay premise unproven | Offline re-parse validator → `corpus_self_sufficient` | ✅ |
| **G6** | Burst recovery time not measured | 0.5s queue-depth sampler + drain-episode timing | ✅ |
| **G7** | No ceiling probe beyond locked universe | `--ceiling-probe`, runs **last** so it cannot contaminate locked results | ✅ |
| **G8** | Thread identity unknown — D1's severity unmeasurable | `threading.get_ident()` per message → `callback_thread_ids` | ✅ |

---

## 3. Functional proof (synthetic, no live feed)

Fed a controlled sequence through the real `Metrics` class — five in-order ticks, one arriving **2.5s behind** the max exchange time seen, one exact duplicate:

```
G1 LATENESS_MAGNITUDE_ms : {n:2, min:2500.0, max:4000.0, p50:4000.0,
                            p99:4000.0, p999:4000.0, mean:3250.0}
G1 by kind               : {'OPTION': {...}}
out-of-order count       : 2
duplicates               : 1
field envelope present   : {symbol:7, ltp:7, exch_feed_time:7, bid_price:7}
field envelope NULL      : {ask_price:7}
silent symbols           : {'NO_UPDATE': 1}
G8 thread ids            : {1: 7}
G3 proc                  : rss=14323712 threads=1
negatives preserved      : 0 of 7
```

The 2.5s-late tick is recorded as exactly `2500.0ms`. **This is the watermark input that did not exist before, and without which Gate 1 would have produced volumes of data and still left the watermark a guess.**

Field envelope correctly separates *present* from *explicitly NULL* — the distinction that tells us whether FYERS omits a field or sends it empty.

---

## 4. Safety re-validation

```
forbidden imports : NONE
broker methods    : ['close_connection', 'subscribe', 'unsubscribe']
imports           : __future__, argparse, collections, datetime,
                    fyers_apiv3.FyersWebsocket, json, os, pathlib,
                    queue, shutil, statistics, threading, time
credential handling : env-only; token never logged
bounded runtime   : MAX_RUNTIME_S = 8h   |  disk: MIN_FREE 2GB + MAX_DISK_BYTES 20GB
deterministic     : sort_keys=True
```

No import of execution, trading_brain, position_lifecycle, msi_*, risk, capital, outcome_memory, portfolio, broker, signal, or trade. **Read-only with respect to trading, AST-verified.**

`unsubscribe` is newly present — required by D3, and read-only.

---

## 5. Revised run plan

```bash
set -a && . ./.env.fyers && set +a          # after FYERS_PIN + token refresh

STRIKES_EACH_SIDE=25 python scripts/gate1/build_universe.py <REAL_SPOT>

python scripts/gate1/gate1_measure_v3.py \
    --universe /tmp/gate1_universe.json --out /opt/bujji/gate1 \
    --phase-seconds 300 --sustained-minutes 60 \
    --mode both --depth --reconnect-test --stall-test-minutes 30
```

**Shape:** ramp (6 steps × 2 modes) → **sustained 60m full universe** → depth (isolated) → reconnect → 30m stall watch.
**Runtime ≈ 4h.** Fits one session with the recommended 10:00 IST start.

---

## 6. Gate 2 sufficiency — now complete

| Gate 2 decision | Before | After |
|---|---|---|
| TickStore design | ✅ | ✅ |
| Storage tier | ❌ | ✅ (G2 sustained + per-minute) |
| Ingestion topology | ❌ | ✅ (G3 CPU/RSS headroom) |
| Backpressure architecture | ⚠️ | ✅ (G6 drain timing) |
| **Watermark / lateness** | ❌ | ✅ (**G1 magnitude distribution**) |
| Candle aggregation | ⚠️ | ✅ (`interarrival_by_kind`) |
| Universe scalability | ⚠️ | ✅ (G4 acks, G7 probe) |
| Feed reliability | ✅ | ✅ |
| Replay corpus design | ❌ | ✅ (G5 self-sufficiency proof) |

**All nine Gate 2 decisions are now answerable from the planned measurement.**

---

## 7. Remaining blockers — unchanged, both external

1. `FYERS_PIN` absent → token refresh blocked (expired 107h; refresh token likely still valid)
2. Next valid NSE session, **non-expiry**, ~4h window from 10:00 IST

**No architectural decision was made. Nothing was assumed. Frozen items remain frozen:** Market Data Fabric, TickStore, storage selection, universe, watermark, ingestion topology.
