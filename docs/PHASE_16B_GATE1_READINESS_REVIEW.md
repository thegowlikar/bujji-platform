# Phase 16B — Gate 1 Readiness Review

**Review only. No code written, modified, or deleted. Architecture Lock intact.**

---

## 1. Harness readiness assessment

**Verdict: NOT READY TO RUN AS-IS.** Four defects found by reviewing my own harness against its own claims. All four are in `scripts/gate1/gate1_measure_v2.py`.

### D1 — Metrics mutated from the callback thread with no lock ⚠️ **HIGH**

```
grep 'lock|Lock' gate1_measure_v2.py  →  (no lock in Metrics)
```

`Metrics.observe()` mutates `Counter`s, lists and dicts directly from the websocket callback. **Harness v1 had a lock; I removed it in v2.** If the SDK dispatches callbacks from more than one thread, counters undercount silently and lists can corrupt.

Worse: **the measurement would be wrong in a way that looks plausible.** A silently undercounted tick rate is exactly the class of error Gate 1 exists to prevent.

Compounding this: **the harness never records which thread delivered each message**, so it cannot even establish whether the single-threaded assumption holds.

### D2 — Unbounded duplicate-detection set ⚠️ **HIGH**

```python
self._seen: set = set()      # never bounded
self._seen.add(sig)
```

At an unknown-but-possibly-high full-mode rate over a 2h20m run, this set grows without limit. At ~5M messages it is multiple GB of tuples. **The harness could OOM mid-run, or trigger swapping that inflates the very latency it is measuring.**

### D3 — Depth experiment is contaminated ⚠️ **MEDIUM**

```python
sock.subscribe(symbols=sub, data_type="DepthUpdate")   # SymbolUpdate never unsubscribed
```

The depth phase subscribes `DepthUpdate` while `SymbolUpdate` remains active for all 411 symbols. The measured result is **depth + symbol traffic combined**, not depth's incremental cost. The stated deliverable ("additional messages/sec, additional bytes/sec") cannot be computed from it.

### D4 — Dead constant and unused imports ⚠️ **LOW**

`MAX_DISK_BYTES` is defined and never referenced (the guard uses a separate 2 GB literal). `signal` and `sys` are imported and unused. Cosmetic, but `MAX_DISK_BYTES` implies a bound that is not actually enforced as written.

### What is correct

Raw-corpus-before-parse ordering · negative latency preserved with `negative_count` · clock-skew floor separated from transport jitter · bounded runtime · disk guard · deterministic output · `harness_was_bottleneck` self-diagnosis · AST-verified read-only · direct SDK binding (bypassing the lossy wrapper).

---

## 2. Gate 2 decision sufficiency

| Gate 2 decision | Evidence needed | Harness provides | Sufficient? |
|---|---|---|---|
| **TickStore design** | Field envelope, bytes/msg, write rate | ✅ envelope, ✅ sizes, ✅ rates | ✅ **YES** |
| **Storage tier** | Bytes per *full session*; intraday variation | ⚠️ 5-min samples only | ❌ **NO — G2** |
| **Ingestion topology** | Whether one process keeps up, with headroom | ⚠️ drops/queue only, **no CPU/mem** | ❌ **NO — G3** |
| **Backpressure architecture** | Queue depth, drops, drain rate, burst recovery | ✅ depth/drops, ⚠️ no recovery time | ⚠️ **PARTIAL — G6** |
| **Watermark / lateness policy** | Distribution of **lateness magnitude** | ❌ **count only, no magnitude** | ❌ **NO — G1 (critical)** |
| **Candle aggregation** | Max silence per instrument class | ⚠️ aggregate max only | ⚠️ **PARTIAL** |
| **Universe scalability** | Per-symbol subscription confirmation; ceiling | ⚠️ inferred from ticks; stops at 411 | ⚠️ **PARTIAL — G4, G7** |
| **Feed reliability model** | Reconnect, stall, error taxonomy | ✅ all three | ✅ **YES** |
| **Replay corpus design** | Proof corpus alone reproduces parsed view | ❌ never re-parsed offline | ❌ **NO — G5** |

**Four of nine decisions cannot be made from the current plan.**

---

## 3. Missing measurements

### G1 — Out-of-order **magnitude** ❗ **CRITICAL**

The harness counts `exch_nonmonotonic` but never records *how late* a late tick was.

**The watermark is the single thing Gate 1 exists to determine, and it cannot be set from a count.** A watermark needs the distribution of lateness in milliseconds — p50/p95/p99/p99.9/max of `(previous_max_exch_time − this_exch_time)` for every out-of-order arrival.

Without it, Gate 2 would have to guess the watermark — the exact failure this whole gate was designed to prevent.

### G2 — No sustained full-universe session

Ramp phases are 5 minutes each. Extrapolating 5 minutes × 411 instruments to a full trading day ignores the open spike, midday lull and closing surge. **Storage tier chosen on that basis would be a guess wearing a measurement's clothes.**

Needs: one continuous run at the full 411 for a substantial part of a session, with per-minute rate/byte buckets so intraday shape is visible.

### G3 — No CPU / memory measurement

"Zero drops" at 95% CPU means single-process ingestion is **not** viable — but the harness cannot tell the difference between comfortable and saturated. Ingestion topology is undecidable without headroom data.

Needs: process CPU%, RSS, and thread count sampled per phase.

### G4 — Subscription acks not correlated per symbol

The harness counts message `type`s but never maps acks to symbols, so `SUBSCRIPTION_FAILURE` cannot be separated from genuine illiquidity — the harness admits this in its own note. For a 411-instrument universe this matters: a silently failed subscription looks identical to an untraded strike.

Needs: capture subscription-response messages and correlate to the requested symbol set.

### G5 — Raw corpus never re-parsed offline

Replay depends on the raw corpus being sufficient to reconstruct everything. The harness parses in-flight and writes raw separately, but **never proves the corpus alone reproduces the same metrics.**

Needs: a post-run offline re-parse of `raw_*.jsonl` producing identical summary statistics. Cheap, and it validates the replay premise before anything is built on it.

### G6 — Burst recovery time not measured

The spec asked for "recovery after burst." Queue depth max is captured; time-to-drain after a burst is not.

Needs: sample queue depth on a timer and record drain duration after each peak.

### G7 — No ceiling probe beyond 411

Ramp stops at the locked universe. That proves 411 works but never finds the practical ceiling — relevant to whether full-chain capture (~1,851, 37% of the SDK's 5,000) is ever viable.

Needs: an optional final ramp step beyond 411 **after** all locked-universe phases complete, so it cannot contaminate the primary result.

### G8 — Callback thread identity not recorded

Directly determines whether D1's missing lock matters. One field (`threading.get_ident()`) per message settles it empirically instead of by assumption.

---

## 4. Updated measurement plan

**Harness changes required before the run** (reported, not implemented):

| Item | Change |
|---|---|
| D1 | Add a lock around `Metrics.observe`, or a single-consumer design |
| D2 | Bound `_seen` (rolling window or periodic clear per phase) |
| D3 | Unsubscribe `SymbolUpdate` before the depth phase, or run depth in an isolated connection |
| D4 | Remove dead constant/imports, or wire `MAX_DISK_BYTES` into the guard |
| G1 | Record lateness magnitude distribution for every out-of-order arrival |
| G2 | Add a sustained full-universe phase with per-minute buckets |
| G3 | Sample CPU%, RSS, thread count per phase |
| G4 | Capture and correlate subscription acks per symbol |
| G5 | Add offline re-parse validator for the raw corpus |
| G6 | Timer-sampled queue depth + drain-time after peaks |
| G7 | Optional post-locked-universe ceiling probe |
| G8 | Record `threading.get_ident()` per message |

**Revised run shape:** ramp (as now) → **sustained full-universe phase** → depth (isolated) → reconnect → stall watch → optional ceiling probe → offline corpus re-parse.

Estimated runtime grows from ~2h20m to roughly a **full session**, which is appropriate: G2 requires it.

---

## 5. Architecture Lock — intact

| Locked decision | Status |
|---|---|
| Universe 411 (±25 × 4 expiries + spot + futures + VIX) | ✅ unchanged; built, zero missing strikes |
| Incremental harvest Stack A → Stack B | ✅ untouched |
| Canonical replay owner `replay_engine` | ✅ untouched |
| Gate 1 as next operational gate | ✅ unchanged |
| Watermark data-driven only | ✅ **reinforced** — G1 shows the current plan could not have supplied it |

**No architectural decision was reopened, weakened, or reinterpreted.** Every finding here is about measurement adequacy, not architecture.

Nothing was built: no TickStore, no candle/indicator/multi-TF/derivatives engine, no changes to ShadowSessionRunner, strategy selection, risk, execution, lifecycle, or memory.

---

## 6. Exact next action

**Before Gate 1 can execute, in order:**

1. **You:** add `FYERS_PIN` to `.env.fyers` and refresh the access token (expired 107h; refresh token likely still valid — written 2026-08-06, ~15-day life).
2. **You:** confirm a **non-expiry** trading day and authorise a near-full-session run (G2 needs the duration).
3. **Me, on your instruction:** apply the twelve harness corrections above (D1–D4, G1–G8). Measurement-harness only, in `scripts/gate1/`, outside `bujji/`.
4. **Me:** re-run pre-run validation (AST safety, credential handling, bounded runtime/disk, determinism).
5. **Me:** rebuild the universe with the **real spot** at run time — the current 24250 ATM is a placeholder and is not acceptable for the real run.
6. **Me:** execute Gate 1 and report observations before interpretation.

**The single most important correction is G1.** Without lateness magnitude, Gate 1 would return a large volume of data and still leave the watermark a guess — which would defeat its entire purpose.

---

**Review stops here. No implementation. Awaiting instruction to apply the harness corrections.**
