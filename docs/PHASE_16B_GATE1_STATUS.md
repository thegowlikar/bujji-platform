# Phase 16B — Gate 1 Status

**Gate 1 is BUILT and SAFETY-VERIFIED but NOT YET RUN.** Two hard blockers, both external to the code. No 16C implementation started.

---

## 1. Why the live run did not happen

| Blocker | Evidence | Resolution |
|---|---|---|
| **Market closed** | Now `2026-08-11 16:32 IST`; NSE closes 15:30 | Next window: **Wed 2026-08-12, 09:15–15:30 IST** |
| **Access token expired** | JWT `exp` decoded: expired **106.6 hours ago** (~4.4 days) | Needs refresh — see below |
| **Auto-refresh unavailable** | `FYERS_PIN` absent from `.env.fyers` (only APP_ID, APP_SECRET, ACCESS_TOKEN, REFRESH_TOKEN). `TokenManager.can_refresh()` requires `pin` | **You must supply it** |

The refresh token was written `2026-08-06` and FYERS refresh tokens are valid ~15 days, so it is very likely still good. Adding `FYERS_PIN=...` to `.env.fyers` should restore the access token without an interactive browser login.

**I have not attempted to authenticate and will not enter credentials.** That is yours to do.

---

## 2. What WAS completed — with real data, no auth required

`instrument_master` downloads a **public** CSV (`public.fyers.in/sym_details/NSE_FO.csv`), so the universe was built and validated against real contracts.

```
symbol master rows for NIFTY : 4,012
future expiries available    : 18

SELECTED EXPIRY BUCKETS (4):
  2026-08-11  WEEKLY   dte=0     462 contracts, 231 strikes
  2026-08-18  WEEKLY   dte=7     462 contracts, 231 strikes
  2026-08-25  MONTHLY  dte=14    488 contracts, 244 strikes
  2026-09-01  WEEKLY   dte=21    462 contracts, 231 strikes

ATM (50-grid): 24250        [proxy — real spot needed at run time]

=== UNIVERSE ===
  spot + VIX  :   2
  per expiry  :  82  (41 strikes x CE/PE)
  TOTAL       : 330   (+1 NIFTY futures = 331)
```

Written to `/tmp/gate1_universe.json` (58.7 KB). Every one of the 330 option symbols is a **real, listed contract** — zero missing strikes across all four expiries at ATM±20.

---

## 3. Three findings from the offline work

### 3.1 `FyersTickFeed` discards 21 of the 23 full-mode fields

```python
# bujji/broker/fyers_ws.py:312-321
def on_message(msg: dict) -> None:
    symbol, ltp = msg.get("symbol"), msg.get("ltp")
    if symbol is None or ltp is None:
        return
    self._ltp[symbol] = float(ltp)
    self._last_tick_at[symbol] = time.time()
```

**Even with `litemode=False`, the existing wrapper keeps only `symbol` and `ltp`.** `bid_price`, `ask_price`, `bid_size`, `ask_size`, `OI`, `vol_traded_today`, `exch_feed_time` — all silently dropped.

This is a **direct 16C requirement**: the fabric cannot consume `FyersTickFeed` as-is. It needs either a new full-fidelity adapter or an additive pass-through hook on the wrapper. Had this not been caught, 16C would have wired the fabric to a feed that throws away exactly the fields the whole re-baseline was about.

It is also why the harness binds `data_ws.FyersDataSocket` **directly** — Gate 1 must measure the feed's true envelope, not the wrapper's filtering.

### 3.2 Real NIFTY lot size is 65, not 75

The symbol master reports `lot=65` for every NIFTY expiry. My Phase 15Q test fixtures used `75`.

Not a production bug — `lot_size` flows from the instrument master at runtime, and `EntrySnapshot.lot_size` captures whatever is real. But every 15Q/15K test expectation computed against 75 is arithmetically fine yet **economically unrepresentative**. Worth correcting when 15Q is folded into the fabric.

### 3.3 Today was an expiry day (dte=0)

`2026-08-11` was itself a weekly expiry. Relevant to scheduling: a 0DTE session is the **highest** tick-rate scenario, not a representative baseline. See §5.

---

## 4. What was built

```
scripts/gate1/build_universe.py   universe from real symbol master (no auth)
scripts/gate1/gate1_measure.py    the Gate 1 harness
```

**Safety-verified by AST:**
```
forbidden refs: NONE
broker calls used: ['subscribe', 'close_connection']
```
No imports from execution, trading_brain, position_lifecycle, msi_*, or risk. Read-only market data. No order path.

**Harness design, honouring the re-baseline constraints:**

| Constraint | Implementation |
|---|---|
| Raw ticks are immutable authoritative evidence | Verbatim payload → append-only JSONL, written **before** any derivation |
| Callback must never block | Bounded queue (200k) + writer thread |
| Drops are never silent | Explicit counter, max queue depth, loud terminal warning |
| Latency measured, not assumed | `recv_ts − exch_feed_time`, p50/p95/p99/max |
| Ordering never assumed | Inter-arrival tracked per symbol; no sort applied |

**Measures:** full field envelope (present *and* null counts per field), msgs/sec, bytes/msg, distinct symbols seen, **silent symbols** (subscribed but never ticked), latency percentiles, inter-arrival gaps, connect/close/error counts, queue depth, actual raw file bytes.

**Ramp:** 10 → 25 → 50 → 100 → 200 → 331, in both lite and full mode, plus optional `DepthUpdate` and reconnect tests.

---

## 5. To run Gate 1 (tomorrow)

```bash
# 1. Add FYERS_PIN to .env.fyers, then refresh the token (your step)
# 2. Verify the token is valid, then:

cd /opt/bujji/app
set -a && . ./.env.fyers && set +a

# real spot at open, for a correct ATM
/opt/bujji/.venv/bin/python scripts/gate1/build_universe.py 24500

/opt/bujji/.venv/bin/python scripts/gate1/gate1_measure.py \
    --universe /tmp/gate1_universe.json \
    --out /opt/bujji/gate1 \
    --phase-seconds 300 --mode both --depth --reconnect-test
```

Full run ≈ 70 minutes. **Recommended window: 10:00–13:00 IST** — avoids the open/close volatility spikes, giving a representative baseline rather than a peak.

**Recommendation on the date:** run on a **non-expiry day** (Wed 12th or Thu 13th) for the baseline, then optionally repeat on an expiry day to capture the peak. Measuring only on 0DTE would over-size the storage architecture.

---

## 6. Revised 16C plan — conditioned on the measurement

16C's shape genuinely depends on one number, so here is the decision tree rather than a guess:

| Measured full-mode rate | Storage/year (331 inst.) | 16C storage decision |
|---|---|---|
| **< 1 msg/s/inst** | < 60 GB | SQLite/WAL only. No archival tier. |
| **1–3 msg/s/inst** | 60–200 GB | SQLite live + **nightly Parquet archival**. |
| **> 3 msg/s/inst** | > 200 GB | Parquet-first; SQLite for the hot day only. |

| Measured outcome | 16C consequence |
|---|---|
| Queue drops > 0 at 331 symbols | Ingestion must be multi-process or batched-commit before anything else |
| Silent symbols > 0 | Per-symbol staleness detection becomes a **launch requirement**, not a later feature |
| Latency p99 > 2s | Watermark ≥ p99; late-tick policy must allow revision |
| Latency p99 < 500ms | Tight watermark (~1s); candles seal fast, immutability is cheap |
| Reconnect fails to resubscribe | Resubscription state machine is 16C's **highest-risk** item |
| Depth counts against the 5,000 cap | Depth becomes a Tier-2 opt-in, not default |

**16C scope regardless of outcome** (fixed):
1. Full-fidelity feed adapter — the 23-field envelope → canonical Observation (§3.1 makes this mandatory)
2. `TickStore` protocol + SQLite backend, `as_of` required on every read
3. Bounded queue + drop accounting, wired to feed health
4. Feed health states (`HEALTHY/DEGRADED/STALE/DISCONNECTED/RECOVERING`) with per-symbol staleness
5. Raw-payload preservation alongside normalized fields

**Explicitly deferred past 16C:** candle engine changes, universe manager, multi-timeframe, features. 16C ends when one full session is captured with zero silent drops — that is Gate 2.

---

## 7. Status

| Item | State |
|---|---|
| Universe construction | ✅ **Done, real data, 330 + futures** |
| Gate 1 harness | ✅ **Built, compiles, safety-verified** |
| Gate 1 measurement | ⛔ **Blocked** — market closed + token expired |
| 16C implementation | ⏸ **Not started** (as instructed) |
| Existing stacks | ✅ Untouched — nothing deleted or merged |
| Working tree | Uncommitted, `b148e39` |

**Two things needed from you:** add `FYERS_PIN` to `.env.fyers` (or refresh the token yourself), and confirm the run date/window.
