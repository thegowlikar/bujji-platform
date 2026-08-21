# Phase 17F.0.4 — Instrument Capability & Broker Reality Contract

**Status: CONTRACT DOCUMENT. No code. No existing file modified.**

This freezes every discovery from the Market Reality Capture
Completeness Audit (17F.0.3) into a permanent, binding reference. Nothing
here is new investigation — every claim below cites where it was already
verified live against FYERS this engagement. This document's only job is
to make those findings impossible to forget, misapply, or rediscover.

> Absence of data capability must be represented as a known limitation,
> not rediscovered as a bug later.

---

## Section 1 — Instrument Capability Registry

Answers: *for this instrument type, which market realities exist at all?*
Four states only — no fifth "probably fine" option:

- **AVAILABLE** — verified live, this engagement
- **UNAVAILABLE** — attempted or reasoned from verified evidence; does
  not exist at this broker
- **NOT_APPLICABLE** — the concept does not apply to this instrument
  type, regardless of broker (an index has no order book by definition,
  not by broker limitation)
- **UNKNOWN_PENDING_CERTIFICATION** — genuinely untested; must not be
  assumed either way until certified

### NIFTY INDEX (`NSE:NIFTY50-INDEX`)

| Capability | Status | Evidence |
|---|---|---|
| Price (LTP) | AVAILABLE | Confirmed live via `quotes`/`historical`, repeatedly, across this engagement |
| Quote (bid/ask) | **NOT_APPLICABLE** | Confirmed live: `bid=0, ask=0, spread=0, volume=0, atp=0` structurally, every observation. An index is not a traded instrument — it never carries a real two-sided quote, at any broker. |
| Depth | NOT_APPLICABLE | Consequence of no quote — there is no book to have depth in |
| Volume | NOT_APPLICABLE | The index print itself carries no real traded volume (the underlying constituents' volumes are a different, unrelated question) |
| OI | NOT_APPLICABLE | An index has no open interest — OI belongs to the derivative contracts written on it, not the index itself |

### NIFTY FUTURES (near-month contract, e.g. `NSE:NIFTY26AUGFUT`)

| Capability | Status | Evidence |
|---|---|---|
| Price | AVAILABLE | Confirmed live, `quotes` and `depth` |
| Quote (bid/ask) | AVAILABLE | Confirmed live via both `quotes` and `depth` |
| Depth | AVAILABLE | Confirmed live: 5 levels, each `{price, volume, order_count}` |
| Volume | AVAILABLE | Confirmed live, `quotes.v.volume` and `depth.v` |
| OI | AVAILABLE, **single-source** | Confirmed 2026-08-12: **`depth()` is the only endpoint that returns futures OI.** Absent from `quotes`/`ltp` and from `optionchain`'s underlying row (which carries `fp`, no `oi`). Any OI capability claim for futures is void unless the access path is `depth()` specifically. |

### NIFTY OPTIONS (per contract: strike + expiry + CE/PE)

| Capability | Status | Evidence |
|---|---|---|
| Price (LTP) | AVAILABLE | Confirmed live via `optionchain` and option historical candles |
| Quote (bid/ask), top-of-book | AVAILABLE, **conditional on liquidity** | Confirmed live for a real near-ATM contract (`bid=217.1, ask=217.45`); confirmed **legitimately zero** for a stale/far-OTM contract on the same day — this is real market fact (no market maker quoting), not a capture defect. Availability is a property of the specific contract's liquidity, not of the instrument type in general. |
| Depth (beyond top-of-book) | **UNKNOWN_PENDING_CERTIFICATION** | `optionchain` returns no size field at any strike (confirmed by direct inspection of real captured rows). Whether `depth()` accepts an option symbol was never attempted this engagement. This is a genuinely untested capability — not confirmed absent, and must not be assumed available. |
| Volume | AVAILABLE | Confirmed live, `optionchain` per-strike and option historical candles (broker layer's own docstring records live-verified genuine non-zero volume) |
| OI | AVAILABLE | Confirmed live, `optionchain` (`oi`/`prev_oi`/`oich`), internally consistent (`oich == oi - prev_oi` verified exactly) |

---

## Section 2 — Broker Reality Contract

### Observable from FYERS (verified, this engagement)

- LTP (spot, futures, options)
- Quote: bid, ask (futures full; options top-of-book, liquidity-dependent)
- Bid/ask **size** and **order count** — futures only, via `depth()`
- Futures OI — via `depth()` only
- Option OI — via `optionchain`
- Volume — spot(session cumulative), futures, options
- Full 5-level order book — futures only, via `depth()`
- Broker feed timestamp (`tt` field) — present in real captured REST
  `quotes` responses
- Historical OHLC candles — spot, futures, options, via `historical`

### Permanently unavailable from this source

| Item | Why it cannot be reconstructed later |
|---|---|
| **Trade prints** (individual executed trades) | Not present in any endpoint audited (`ltp`, `quotes`, `depth`, `optionchain`, `historical`). This is the raw atomic fact everything else is aggregated from — if it was never captured, no downstream computation, however sophisticated, can recover it. There is no proxy: volume totals and OHLC bars are aggregates that destroy the very information (individual trade size/time/side) a print would carry. |
| **Aggressor side** (was this trade buyer- or seller-initiated) | Not present anywhere. This is a property of the individual trade print, which does not exist (see above) — it is not merely unlabeled, its prerequisite data was never available to label. Even a full order-book replay cannot reliably infer this without the actual matched-trade record from the exchange, which FYERS's retail feed does not expose. |
| **Buyer-initiated volume** (aggregate) | Derived from aggressor-tagged trade prints, which do not exist. Cannot be approximated from OHLCV alone without a heuristic (e.g. tick rule) — and this contract does not permit heuristic reconstruction to masquerade as observed fact (§8). |
| **Seller-initiated volume** (aggregate) | Same reasoning as buyer-initiated volume. |

**This list is permanent, not a snapshot of current effort.** A future
phase must not re-investigate whether FYERS "secretly" provides these —
every endpoint this account has access to was checked. If FYERS's API
changes in the future, that is a new certification event, not a
correction to this contract.

---

## Section 3 — Data Semantics Rules

### Rule 1 — Bid/Ask Zero

**Case A: structural absence.**
```
instrument_type = INDEX
bid = 0, ask = 0, spread = 0   (every observation, always)
```
Meaning: **this instrument does not provide an order book.** This is not
a market condition — it is a property of the instrument type itself and
never changes. Must never be interpreted as "no one is quoting right
now."

**Case B: genuine illiquidity.**
```
instrument_type = OPTION (or FUTURE)
bid = 0, ask = 0   (this observation; other observations of the same
                     contract, or nearby strikes, show real values)
```
Meaning: **liquidity disappeared for this specific contract at this
specific moment** — a real, meaningful market fact (confirmed live,
2026-08-12, a far-OTM contract with `lp=0.05`). This is exactly the kind
of observation Layer 0 exists to preserve.

**The distinguishing test is not the zero value — it is the instrument
type.** An INDEX's zero is Case A, always. A FUTURE or OPTION's zero is
Case B, and is real information.

### Rule 2 — Empty Depth vs. Unavailable Depth

```
depth EXISTS: bids = [], asks = []
```
Meaning: a `MARKET_DEPTH` observation was successfully captured, and the
book was, at that instant, genuinely empty on one or both sides. This is
a stored fact — the validator's `REQUIRED_PAYLOAD_FIELDS` for
`MARKET_DEPTH` checks only that the `bids`/`asks` **keys are present**,
not that they are non-empty, so this passes as a legitimate observation.

```
depth UNAVAILABLE: no MARKET_DEPTH record for this window at all
```
Meaning: either the collector never polled, or the poll failed. This is
represented by a `CaptureEvent` (`RATE_LIMIT_SKIP`, `DISCONNECT`, etc.),
never by a fabricated empty-depth record. **A missing observation and an
observation of emptiness are different facts and must never be
conflated** — conflating them would make a feed outage indistinguishable
from a real liquidity vacuum.

---

## Section 4 — Liquidity Ontology

**Liquidity is not a property of "the market." It is a property of one
specific, named instrument's order book, at one specific moment.**

| Statement | Status |
|---|---|
| "NIFTY liquidity" | **Malformed.** NIFTY (the index) has no order book (§1). This phrase has no referent. |
| "NIFTY Futures order-book liquidity" | **Well-formed.** Refers to a real, observable book (§1, AVAILABLE). |
| "NIFTY Options order-book liquidity" (top-of-book) | **Well-formed, conditionally.** Refers to real bid/ask size — but only after per-strike depth is certified (currently `UNKNOWN_PENDING_CERTIFICATION`, §1); until then, only top-of-book price presence/absence is well-formed, not size or depth. |

**Binding rule:** any future materializer, memory record, or intelligence
layer that references "NIFTY liquidity" without naming a specific
derivative instrument is malformed by this contract and must be rejected
in review. **Index → liquidity inference is forbidden.** Only
FUTURES (now) and OPTIONS (after the pending depth certification, for
anything beyond top-of-book) may produce liquidity observations.

---

## Section 5 — Timestamp Contract

Three times, one deliberately not persisted per-record:

| Time | Definition | Status |
|---|---|---|
| **`event_time`** | When the market event actually happened, per the source | Frozen field, `Layer0Lineage.event_timestamp` — optional, never backfilled from another time |
| **`knowledge_time`** | When Bujji's capture process received it | Frozen field, `Layer0Lineage.capture_timestamp` — always present |
| **`storage_time`** | When the record was durably written | **Deliberately not a separate field.** `knowledge_time` is stamped at arrival (hook/poll), not at write completion — a decision made explicitly in 17F.0.1. Write latency is bounded and observed via queue/backpressure counters, not a third timestamp on every record. |

### Sources, mapped to `event_time`

- **FYERS `tt` field** — confirmed present in real captured REST `quotes`
  responses (e.g., `"tt": "1786492800"`). This is the broker's own feed
  timestamp and is the correct source for `event_time` on REST-sourced
  observations. **Not yet extracted by any collector** — the field
  exists in the schema and in the broker's response; only the
  extraction step is outstanding (a collector implementation item, not a
  contract gap).
- **Websocket exchange feed time** — existence and field name **unknown,
  pending the websocket field census** (the certification run, not yet
  executed). Must not be assumed present.
- **Bujji receive timestamp** — always available; this is
  `knowledge_time`, stamped by the collector at the moment of arrival,
  never inferred or backfilled.
- **Persistence timestamp** — not modeled as a distinct field (see
  `storage_time` above).

### Bitemporal query contract

A query for *"what did Bujji know at 10:00"* must bound **both** times to
10:00:
- `event_time ≤ 10:00` — only events that had happened
- `knowledge_time ≤ 10:00` — only observations already received

An observation with `event_time = 09:58` but `knowledge_time = 10:05`
(a late-arriving or delayed record) **must be excluded**, even though its
event time qualifies — it was not knowable at 10:00. Including it would
be look-ahead, silently making historical memory appear more complete
than Bujji actually was at the time. This is the entire reason the two
times are kept structurally distinct rather than collapsed into one.

---

## Section 6 — Observation Vocabulary Review

Current Layer 0 vocabulary, checked against every finding in the
completeness audit (17F.0.3) and every capability in §1:

| Kind | Confirmed sufficient? | Basis |
|---|---|---|
| `MARKET_TICK` | Yes | Covers websocket-sourced high-frequency price capture regardless of instrument type |
| `QUOTE` | Yes | Covers point-in-time price/bid/ask across spot (price only, per §1), futures, options |
| `MARKET_DEPTH` | Yes | Covers order book + OI; applicability varies by instrument (§1) but no new kind is needed to express that — `instrument_type` already carries it |
| `OPTION_CHAIN` | Yes | Covers the full strike ladder, OI, top-of-book, volume |
| `CANDLE` | Yes | Covers broker-provided historical bars, tagged distinctly from live-aggregated ones |
| `CAPTURE_EVENT` (sibling, not an observation kind) | Yes | Covers every absence/failure mode in §3 |

**Conclusion: the current vocabulary is sufficient. No new observation
type is required.** Every gap surfaced by this contract (§1's
`UNKNOWN_PENDING_CERTIFICATION` entries, §2's permanently-unavailable
list) is a gap in **what FYERS provides or what has been certified**, not
a gap in **what Layer 0 can represent**. Adding a kind would not fix a
missing capability — it would only give a non-existent capability a name.

---

## Section 7 — Future Intelligence Answerability Test

| Question | Required Raw Reality | Available? | Limitation |
|---|---|---|---|
| "Was breakout supported by liquidity?" | Futures depth, futures volume, price | **Yes** | Must be asked about NIFTY **Futures**, never "NIFTY" (§4). Answer quality depends on depth-polling cadence (still undecided — an operational item, not a contract gap). |
| "Was option premium expansion unusual?" | Option price, spread (computed from raw bid/ask), volume, OI | **Yes** | Spread must be computed at query time from stored raw bid/ask, never stored itself (§8). Quality depends on chain-polling cadence. |
| "Was buying aggressive?" | Trade prints, aggressor side | **UNANSWERABLE** | Permanent broker limitation (§2). No amount of future engineering recovers this — the atomic fact was never observable. Any future intelligence layer must refuse this question outright, not approximate an answer from OHLCV. |
| "When does short straddle risk increase?" | Premium behaviour (raw), underlying movement (raw), volatility inputs (raw price series — **not** IV itself, computed later at Layer 3+) | **Yes, for the raw inputs** | The question's ultimate answer requires interpretation (a "risk increase" judgment) that Layer 0 will never produce — Layer 0's job ends at preserving the raw premium/price/OI series this judgment would be computed from. |

---

## Section 8 — Explicit "DO NOT CAPTURE IN LAYER 0" (Permanent)

Layer 0 stores reality, not interpretation. This list is binding on every
future materializer and collector:

- Any indicator (moving averages, RSI, Bollinger bands, or equivalent)
- VWAP
- Implied volatility (IV), in any form
- Greeks: delta, gamma, theta, vega, rho
- Market regime classification
- Trend state / trend labels
- Liquidity score, liquidity state, or any "thin"/"thick" qualitative tag
- Sentiment (any form)
- Signals (buy/sell/hold or equivalent)
- Predictions (of any kind, over any horizon)
- Classifications (breakout/rejection/acceptance, session-phase labels
  such as "power hour," or any other tag applied to a raw fact)
- **Spread** (`ask − bid`) — a one-line computation, still forbidden;
  compute from stored raw bid/ask at query time
- **OI change / OI delta** — compute from two stored raw OI snapshots
- **Velocity, acceleration** of price — computed from stored
  timestamp+price pairs
- Any field name matching this list's intent, even if not verbatim above

**Reason, stated once for permanence:** every item above is reproducible
*from* raw facts that Layer 0 does store. None of them are raw facts
themselves. The moment one is stored as if it were, Layer 0 stops being
a record of reality and starts being a record of somebody's opinion about
reality — and that opinion becomes indistinguishable from observed fact
to everything built on top of it.

---

## Section 9 — Collector Readiness Decision

**Not yet ready to implement 17F.0.4's listed components.** This
contract resolves the *documentation and decision* blockers from the
completeness audit; three **operational** blockers remain, none of which
this document can close on its own:

| Component | Ready? | Blocker |
|---|---|---|
| Websocket hook (17F.0.2 design) | **Design ready**, implementation blocked | Concurrency design is complete and proof-argued (17F.0.2); implementation should proceed only after the item below, since the hook's *value* depends on it |
| Tick collector | Blocked | **Websocket certification has not been run.** Symbol integrity, timestamp availability, and field census are all still unknown for the tick path — writing tick observations before this exists would mean capturing data the certification gate itself would reject once run, or worse, capturing it uncertified. |
| Quote collector | **Ready** | REST path already `CERTIFIED_AVAILABLE` for spot; `tt`-extraction (§5) is a small, well-scoped implementation task, not a blocker |
| Depth collector | Blocked | **Polling cadence undecided.** Futures OI/liquidity history is entirely a function of this cadence (§1, §7) — shipping without deciding it means the capture period's OI history quality is accidental, not designed. |
| Candle provenance (17F.0 lineage fields) | **Ready** | No dependency on certification or cadence — purely additive schema work, independently correct regardless of what feeds it |

**What this contract itself resolves, effective immediately:**
- The NIFTY-liquidity-means-futures-liquidity decision (§4) — no longer
  open.
- The trade-print/aggressor-side permanent limitation (§2) — now written
  down, not merely known informally.
- The bid/ask-zero and empty-depth semantics (§3) — now binding rules,
  not implicit understanding.

**What remains genuinely open, requiring operator action, not more
documentation:**
1. Run the websocket certification during NSE market hours.
2. Decide the depth-polling cadence (a number, not a design).
3. Extract `tt` into `event_time` for REST-sourced quotes (small,
   well-scoped implementation task, safe to do anytime).

---

## Section 10 — Recommended Next Sequence

```
17F.0.4  Contract                                    ← this document, complete
   ↓
17F.0.1  Websocket certification                     ← operator action, market hours, not yet run
   ↓
17F.0.2  Tick hook                                    ← design complete (prior doc); implement once certification exists, per Part 8's rollback-safe design
   ↓
17F.0.3  Collector (tick / quote / depth)             ← quote collector can start in parallel, once tt-extraction is added; depth collector waits on cadence decision; tick collector waits on certification
   ↓
17F.1    Candle materialization                        ← waits on none of the above at the schema level; candle provenance fields are independently buildable now
```

**Note on ordering flexibility:** candle provenance (17F.0's lineage
fields) and the quote collector's `tt`-extraction do not depend on the
websocket certification and may proceed in parallel with it, since
neither writes or depends on tick data. The strict serial order above
applies specifically to the **tick** capture path, where certification is
a hard gate (per Phase 17E's write-gate design, applied without
exception).

---

## Gate Status

| Gate | Status |
|---|---|
| Instrument capability registry (§1) | **Complete** |
| Broker reality contract (§2) | **Complete** |
| Data semantics rules (§3) | **Complete** |
| Liquidity ontology (§4) | **Complete** |
| Timestamp contract (§5) | **Complete** |
| Vocabulary review (§6) | **Complete — no new type required** |
| Answerability test (§7) | **Complete** |
| Forbidden list (§8) | **Complete, permanent** |
| Collector readiness (§9) | **Not ready — 3 operational blockers, 0 contract blockers** |
| Recommended sequence (§10) | **Complete** |
| Review | **Pending — awaiting operator action on §9's three items** |
