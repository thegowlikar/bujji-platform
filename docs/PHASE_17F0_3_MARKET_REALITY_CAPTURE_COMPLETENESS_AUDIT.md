# Phase 17F.0.3 — Market Reality Capture Completeness Audit

**Status: AUDIT ONLY. No code. No schema changes applied.**

Purpose: verify Layer 0's design can eventually answer real market
questions — not "do the tests pass," but "is anything being thrown away
right now that a future Layer 2 will need and can never get back." Every
finding below is grounded in a response actually observed from FYERS this
engagement (depth, optionchain, quotes, websocket certification prep,
`run_live_shadow.py` logs), not assumed from documentation.

> The goal is not more data. The goal is preserving enough market reality
> so future intelligence is possible.

---

## 1. Executive Conclusion

**Not yet ready to start production capture — three items are blocking,
none require a schema redesign.**

The taxonomy itself is sound: five observation kinds
(`MARKET_TICK`/`QUOTE`/`MARKET_DEPTH`/`OPTION_CHAIN`/`CANDLE`) plus the
`CAPTURE_EVENT` sibling already cover everything FYERS can provide. This
audit found **no missing observation kind** and recommends **no new Layer
0 taxonomy entries**. What's missing is not schema — it's three
collector-design decisions that, if skipped, would produce silently
sparse or misleading data no schema fix could repair after the fact.

**Blocking, must resolve before collectors write anything permanent:**

1. **NIFTY spot has no order book.** Confirmed live: `NSE:NIFTY50-INDEX`
   returns `bid=0, ask=0, spread=0` structurally — an index is not a
   traded instrument and never carries a real quote. Any liquidity
   question about "NIFTY" must be answered from **NIFTY futures depth**,
   not spot. If this isn't decided now, a future materializer will either
   silently treat spot's zero bid/ask as a real (illiquid) fact — which
   is wrong, it's *absence of the concept*, not *presence of an empty
   one — or nobody notices liquidity data for NIFTY was never captured
   at all.
2. **Depth polling cadence is undefined.** Futures OI, the only place OI
   exists at all (§4), only accumulates history if `depth()` is polled
   repeatedly. No cadence has been chosen. Ship without deciding this and
   OI history for the whole capture period is whatever cadence someone
   picked by accident.
3. **Trade-print / aggressor data does not exist at this broker**, at
   any endpoint audited this session. This isn't a capture gap to fix —
   it's a permanent capability boundary that must be written down now, so
   nobody spends a future phase trying to answer a question this data
   source cannot answer.

**Not blocking, but must be decided before option-chain-dependent
questions are trusted:** option-chain and depth polling frequency (§9),
and whether per-strike option depth is ever attempted (§4 — untested this
session, capability unknown).

**No taxonomy change required.** Two small, defensive additions to the
forbidden-fields list are recommended (§4 addendum, §9).

---

## 2. Price Reality Audit

| Capability | Classification | Evidence |
|---|---|---|
| Tick-by-tick price movement | (2) Missing but required — **pending field census** | The websocket certification (prepared, not yet run) is what determines whether ticks carry enough resolution to reconstruct genuine tick-by-tick movement vs. a coalesced last-value stream |
| Quote changes over time | (1) Captured correctly | `QUOTE` kind + repeated polling; each poll is an immutable Layer 0 fact |
| Bid/ask evolution | (2)/(3) Depends on instrument — see spot/futures split above | Futures: yes, via repeated `MARKET_DEPTH`. Spot: **not applicable**, index has no book (confirmed) |
| Price acceleration | (4) Must NOT enter Layer 0 | Purely computed from stored timestamp+price pairs — a Layer 2/3 derivation |
| Gaps | (1) Captured correctly, by design | `CaptureEvent` (Phase 17F.0.1) records *why* nothing was observed, distinct from the market itself gapping |
| Volatility of movement | (4) Must NOT enter Layer 0 | Same as acceleration — derived, not observed |
| Trade prints | (2) **Missing — broker limitation, not a design gap** | No endpoint audited (`ltp`, `quotes`, `depth`, `optionchain`, `historical`) returns individual trade prints. FYERS's retail API does not expose them. **Document and stop looking for it** — a future phase re-discovering this absence would waste real effort. |
| Executed volume | (1) Partially captured | Cumulative session volume is in `quotes`/`depth` responses (`volume`/`v` fields) and in historical/live candles. Per-trade executed size is not (same limitation as trade prints). |
| Aggressor information (buy/sell-initiated) | (2) **Missing — broker limitation** | Not present anywhere audited. FYERS's retail feed does not classify trade direction. **Any future question of the form "was this buyer-driven or seller-driven" cannot be answered from this data source, ever, regardless of Layer 2/3 sophistication.** This must be stated plainly rather than discovered by a future engineer assuming it's just unbuilt. |
| Last traded quantity (LTQ) | (2) Missing but partially available | Confirmed present in the `depth()` response (`"ltq": 65` observed live). Not confirmed in tick payloads — pending field census. Collector design must extract `ltq` from depth polls regardless of tick-level availability. |

---

## 3. Liquidity Reality Audit

**Target question:** *"Was the breakout supported by real liquidity or
was it a thin move?"*

| Field | Classification | Evidence |
|---|---|---|
| Bid / ask | (1)/(3) | Futures: real, via `depth()`. Spot: **structurally absent** (index). Options: top-of-book only, via `optionchain` (`bid`/`ask` per strike, confirmed live). |
| Bid/ask quantity | (1) for futures, (2) for options | Futures `depth()`: 5 levels, each with `{price, volume, order_count}` — confirmed live. Options: **not available** — `optionchain`'s per-strike rows carry `bid`/`ask` price only, no size field, confirmed by direct inspection of a real captured row this session. |
| Spread | (5) Deferred — **must be computed, never stored** | `spread = ask - bid` is a one-line subtraction, but by this project's own established rule (Phase 17D: "not even `oi_change` unless the broker itself returned that exact field"), it does not belong in Layer 0. Compute it at query/materializer time from the two raw fields already stored. |
| Depth levels | (1) for futures, (2) for options | Futures: 5 real levels via `depth()`. Options: **zero levels available** — only the top-of-book bid/ask `optionchain` provides. Whether FYERS's `depth()` endpoint even accepts an option symbol was **never tested this session** — status unknown, not "unavailable." Flagged for a future, narrowly-scoped certification if per-strike depth is ever needed. |
| Depth changes | (1) — a consequence of repeated capture, not a new field | Each `MARKET_DEPTH` poll is a full, timestamped snapshot. "Change" is two snapshots compared — a Layer 1/2 operation over raw facts already present, not something Layer 0 computes. |
| Liquidity disappearance events | (5) Deferred, correctly | Not a Layer 0 concept — it's a *pattern* across consecutive depth snapshots (bid/ask size drops to zero between polls). As long as snapshots are captured at adequate frequency, this is fully reconstructable later. **No new Layer 0 field needed — only adequate polling cadence (blocking item #2).** |

**Explicit rule, restated for this audit:** no `liquidity_score`,
`liquidity_state`, `thin`/`thick` label, or any qualitative liquidity
field may ever enter Layer 0. Every liquidity question is answered from
raw bid/ask/size/order-count facts, at query time, by something above
Layer 0.

---

## 4. Volume Reality Audit

| Capability | Classification | Evidence |
|---|---|---|
| Tick volume | (2) Missing — pending field census | Same open question as tick-level price resolution |
| Candle volume | (1) Captured correctly | `CandleAggregator` already sums real per-tick volume into `Candle.volume`, `None` (never coerced to 0) when the feed reports none — verified existing, correct behaviour |
| Futures volume | (1) Captured correctly | Present in both `quotes` (`v.volume`) and `depth` (`v` field) responses, confirmed live |
| Option volume | (1) Captured correctly | Present in `optionchain` per-strike rows (confirmed) and in `get_option_candles()`'s historical response, which the broker layer's own docstring states was live-verified to carry genuine, substantial non-zero volume |
| Volume changes over time | (1) — consequence of repeated capture | Same reasoning as depth changes above — no new field, just repeated snapshots |
| Participation vs. liquidity evaporation | (5) Deferred — **answerable, if and only if both channels are captured jointly** | This question needs volume (captured) *and* depth (captured for futures, absent for spot) at comparable timestamps. It is answerable for futures. **It is structurally unanswerable for spot**, because spot has no depth to evaporate — this is the same finding as the blocking spot/liquidity item, restated in volume terms. |

**Addendum — defensive forbidden-field additions:** `velocity` and
`acceleration` are not currently in `FORBIDDEN_PAYLOAD_FIELDS`, though
nothing computes them yet. Recommend adding both, for the same reason
`vwap`/`regime`/`signal` are already there: naming the boundary before
something crosses it is cheaper than removing a field after a
materializer starts depending on it.

---

## 5. Derivatives Reality Audit

### Futures

| Field | Classification | Evidence |
|---|---|---|
| Price | (1) Captured | `quotes`/`depth`, both confirmed live |
| Volume | (1) Captured | `v.volume` in both endpoints |
| OI | (1) Captured, but **single-source** | Confirmed 2026-08-12: **`depth()` is the only endpoint that returns futures OI.** `quotes`/`ltp` and `optionchain`'s underlying row both lack it. This makes blocking item #2 (depth polling cadence) load-bearing for every future OI question about futures, not optional. |
| OI history | (2) Missing but required — a cadence decision, not a schema gap | Layer 0 is append-only, so OI history is automatic *once* depth is polled repeatedly. Today, nothing defines how often. |
| Depth | (1) Captured | 5 levels, confirmed live, including `oi`/`pdoi`/`oi_percent` at the top level of the same response |

### Options

| Field | Classification | Evidence |
|---|---|---|
| Strike / expiry / CE-PE | (1) Captured | `OptionObservation` identity fields, required at the validator level (17E) |
| Bid / ask | (1) Captured, top-of-book only | Confirmed real for live near-ATM contracts; confirmed **zero for a stale/far-OTM contract** on 2026-08-12 — this is a genuine market fact (illiquid, no market maker quoting), not a capture defect, and the validator already treats it correctly (present-but-zero, never conflated with absent) |
| LTP | (1) Captured | `optionchain`, confirmed |
| Volume | (1) Captured | `optionchain` + option historical candles, both confirmed |
| OI | (1) Captured | `optionchain` per-strike `oi`/`prev_oi`/`oich`, confirmed live and internally consistent (`oich == oi - prev_oi` verified) |
| Per-strike depth (size beyond top-of-book) | (2) **Missing — capability genuinely unknown, not confirmed absent** | `optionchain` gives no size. Whether `depth()` accepts an option symbol was never tried this session. This is different from the trade-print gap (confirmed absent everywhere) — this is an **untested capability**, and should be labeled that way rather than assumed unavailable. |

### Can Layer 0 preserve enough to LATER derive OI change / IV / Greeks / skew / premium behaviour?

**Yes for OI change** (two raw OI snapshots, subtracted downstream).
**Yes for premium behaviour** (option LTP/candle history, raw). **Yes for
IV/Greeks/skew, in principle** — every input Black-Scholes-family models
need (spot price, strike, expiry, time-to-expiry, option premium) is
already captured as raw fact across `QUOTE`(spot)/`OPTION_CHAIN`/identity
fields. **What Layer 0 does not and must not do is compute IV or a Greek
itself** — those remain explicitly forbidden (already enforced at the
validator, `FORBIDDEN_PAYLOAD_FIELDS`), and this audit found no attempt
or temptation to add them. The inputs survive; the interpretation does
not happen here.

---

## 6. Time Model Audit

| Requirement | Classification | Evidence |
|---|---|---|
| Exchange event time vs. capture/knowledge time, kept distinct | (1) Captured correctly, structurally | `Layer0Lineage.event_timestamp` / `capture_timestamp` are separate fields; 17E explicitly forbids backfilling one from the other; tested (`test_absent_event_timestamp_is_legal`) |
| Broker feed time | (2) Missing but required — **an extraction step, not a schema gap** | REST `quotes` responses already carry a `"tt"` field (observed live, e.g. `"tt": "1786492800"`) that is plausibly the broker's own feed timestamp. **The schema already has a place for this (`event_timestamp`) — what's missing is a collector-level decision to extract `tt` and populate it, rather than leaving `event_timestamp` null for REST-sourced quotes.** This is an implementation requirement for Step 4+, not a new field. |
| Storage time (distinct from capture time) | (5) Deferred, deliberately | `knowledge_time` is stamped at the moment of arrival (hook/poll), not at write completion. A separate "durability time" was considered and rejected in 17F.0.1 — queue latency is bounded and monitored via completeness/backpressure counters, not a third timestamp per record. No change recommended. |
| Bitemporal queries ("what was known at 10:00" vs. "what happened before 10:00 with today's complete data") | (1) Designed correctly, **unproven in practice** | The `as_of` contract (17F.0) mandates both `event_time` and `knowledge_time` bounds. This is real and tested at the Layer 0 record level. It has not yet been exercised by an actual materializer, because materializers don't exist yet — correctly deferred, not a gap. |

---

## 7. Market Session Reality Audit

| Item | Classification | Reasoning |
|---|---|---|
| Pre-open / market open / close | (4) Must NOT be a Layer 0 observation | These are calendar facts, not something observed *from* the market — they're known in advance from the NSE schedule, not derived from a tick |
| First 15 minutes / lunch / power hour | (4) Must NOT enter Layer 0 | These are *labels on a time window*, i.e., classifications — explicitly Layer 3 interpretation territory, same category as "regime" |
| Expiry day | (4) Must NOT enter Layer 0 as a new fact | Fully derivable from instrument identity (`expiry` field, already captured) + a calendar — no new observation needed |
| Trading holiday calendar | (2) Missing, but **infrastructure, not an observation** | This gap was flagged as far back as the Phase 16 audits and is still genuinely absent from the repository. It belongs as reference data (alongside the instrument master), consumed by Layer 2/3 to *label* time windows — never as a Layer 0 observation kind. **Not blocking collector start**, since Layer 0's job is unaffected by whether a calendar exists elsewhere; flagged here so it isn't lost again. |

**Conclusion for this section:** none of these belong in Layer 0. The
only actionable item is a reference-data gap (holiday calendar),
correctly out of scope for the collector work.

---

## 8. Absence Reality Audit

This is the section most directly tested by work already done this
session, and it holds up:

| Distinction | Status | Evidence |
|---|---|---|
| "No quote received (feed failed)" vs. "quote received with bid=0/ask=0" | (1) **Already correctly distinguished** | The former is a `CaptureEvent` (17F.0.1); the latter is a stored `QUOTE`/`OPTION_CHAIN` observation with the fields present and legitimately zero. Directly regression-tested (`test_zero_bid_ask_is_a_real_fact_not_a_missing_field`) using the actual 2026-08-12 stale-strike finding as the fixture. |
| "No depth" vs. "depth exists but liquidity is empty" | (2) **Partially verified, needs one explicit test** | The validator's `REQUIRED_PAYLOAD_FIELDS` for `MARKET_DEPTH` checks only that `bids`/`asks` **keys are present** — an empty list (`bids: []`) passes validation as a legitimate fact, structurally. This was not exercised by an explicit test. **Recommend adding one before collectors go live**, so the distinction is provably correct, not just structurally plausible. |
| A poll that legitimately returns nothing (e.g., depth requested outside trading hours) | (1) Correctly modeled | This is exactly the shape `RATE_LIMIT_SKIP`/`DISCONNECT` `CaptureEvent`s exist for — a poll attempt that could not produce an observation is a fact about the collector, not a market fact |

**No absence-representation gap found beyond the single missing test
noted above.**

---

## 9. Data Quality / Provenance Audit

| Requirement | Status | Evidence |
|---|---|---|
| Lineage completeness | (1) | Source, access_method, both timestamps, certification status + ref, derived confidence, transformation_history — all present, all tested |
| Certification binding | (1) | Per `(access_method, instrument_type)`, fails closed on missing/mismatched artifacts, verified against real certification files this session |
| Rejected observation handling | (1) | Permanent, attributable, separate store — verified |
| Duplicate handling | (1) | Content-hash identity, idempotent — verified, including across restart |
| Replay determinism | (1) | Two independent replays byte-identical; survives restart — verified |
| Gap handling | (1) | `CaptureEvent`, ordered union stream, excluded from observation-only replay — verified this session (Step 3) |
| Corruption detection | (1) for structural checks; (5) deferred for candle-level OHLC integrity | Timestamp/schema/identity checks run at every write. OHLC-impossibility checks (`high < low`, etc.) are a *candle*-level concept — correctly deferred to materializer output, not applicable to raw ticks/quotes |
| Can every future derived fact trace back to exact raw observations? | **Designed, but unproven** — flagged, not a defect | `source_observation_ids` + `calc_version` lineage is specified (17F.0/17D) but **no materializer exists yet to exercise it.** This is the correct order of operations (design before build), not a gap in this audit's scope — restated here so it isn't mistaken for "done." |

**One genuine polling-frequency risk surfaces here, not elsewhere:**
provenance is only as good as capture frequency. A depth poll every 10
minutes produces technically-complete, honestly-lineaged, utterly
insufficient data for "did liquidity evaporate during this 90-second
breakout." Provenance correctness and capture *adequacy* are different
properties — Layer 0's design nails the first and is silent on the
second, which is exactly blocking item #2.

---

## 10. Future Intelligence Question Test

| Future Question | Required Raw Data | Available? | Missing? |
|---|---|---|---|
| "At NIFTY 24500, before a 100-point breakout, what was different vs. failed breakouts?" (the core question) | Spot price series; **futures** depth/OI (spot itself has none); option chain state; volume | **Mostly yes** — spot price (candles), futures depth/OI, option chain all captured in principle | Depth/chain polling cadence (blocking #2) must be sufficient to resolve a fast breakout window; tick-level granularity pending field census |
| "What happens after NIFTY crosses VWAP with rising participation?" | Price, volume, liquidity, positioning | Price/volume: yes. Liquidity: **futures only** (spot has none — must substitute futures depth as NIFTY's liquidity proxy). Positioning: yes (futures OI + option OI) | Explicit design decision that "NIFTY liquidity" means "NIFTY futures liquidity" — not yet written down anywhere until this audit |
| "When does option premium expansion precede directional moves?" | Option premium history, spread (bid/ask raw), underlying movement, volatility inputs (raw price series, not IV) | Yes, provided option chain is polled at adequate frequency | Polling cadence decision (same root cause as blocking #2) |
| "Are breakouts failing because sellers absorb buying?" | Price, depth, volume, OI | Yes **for futures**; **not answerable for spot** in isolation | Same spot/futures liquidity substitution as above |
| "When does short straddle risk increase?" | Premium behaviour, volatility inputs (raw), liquidity, movement regime (raw series; "regime" label itself is Layer 3) | Premium/price: yes. Liquidity: **top-of-book only for options** — per-strike depth capability is untested, not confirmed absent | Depth-for-options capability check (§5) if this question is ever prioritized; not blocking today since top-of-book is still real signal |

**Pattern across all five:** every question that involves "NIFTY
liquidity" specifically resolves to "NIFTY futures liquidity," because
the index itself structurally cannot have one. This single finding
underlies three of the five rows above and is the audit's most
consequential result.

---

## 11. Missing Capture Requirements (Blocking)

1. **Decide and document that NIFTY spot has no order book; futures
   depth is the liquidity proxy for NIFTY.** No code change — a design
   decision that must be written into the collector spec so nobody polls
   `depth()` on `NSE:NIFTY50-INDEX` expecting a real book, and nobody
   later concludes "NIFTY has zero liquidity" from an index's structural
   zero.
2. **Set an explicit depth-polling cadence** for futures OI/liquidity
   history, before any collector writes permanent data. Whatever value is
   chosen, it must be a deliberate decision, not whatever a first draft
   happened to use.
3. **Document, permanently, that trade prints and aggressor-side
   (buy/sell-initiated) data do not exist at this broker.** This belongs
   in the Layer 0 contract itself (e.g., as a section in
   `docs/PHASE_17D_MARKET_REALITY_DATA_CONTRACT.md` or a new capability
   boundary note), not just in this audit, so it survives past this
   document being superseded by later phases.
4. **Extract broker feed time (`tt`) into `event_timestamp` for
   REST-sourced quotes**, rather than leaving it null. The field exists
   in real captured responses; the schema already has a place for it;
   only the collector-level extraction is missing. (Not applicable to
   websocket ticks — pending the field census for whether an equivalent
   field exists there.)
5. **Add one test** proving `MARKET_DEPTH` with `bids: []`/`asks: []`
   (present, structurally empty) is accepted and distinguishable from a
   missing `MARKET_DEPTH` observation entirely (§8).

## 12. Deferred Items (Correctly Out of Scope Now)

- Per-strike option depth beyond top-of-book — capability untested, not
  needed until a question specifically requires it (§5, §10 row 5).
- Trading holiday/session calendar as reference data — a real gap, but
  infrastructure, not a Layer 0 observation (§7).
- Liquidity-disappearance-event detection, spread computation, OI-change
  computation, participation-vs-evaporation classification — all fully
  answerable later from raw facts already specified, none require a new
  Layer 0 field (§3, §4).
- Provenance chain-to-raw-observation proof — designed, correctly awaits
  materializers to exist before it can be exercised (§9).
- Tick-level field census (event time availability, LTQ, volume) — this
  is precisely what the prepared, unexecuted websocket certification
  script exists to resolve. Not a new requirement; already scheduled.

## 13. Explicit "DO NOT CAPTURE IN LAYER 0" List

Consolidated from every section above, plus the pre-existing 17E list:

- IV, implied volatility (any form)
- Delta, gamma, theta, vega, rho
- VWAP, moving averages, RSI, Bollinger bands
- **Spread** (`ask - bid`) — compute at query time from stored raw bid/ask
- **OI change / OI delta** — compute from two stored raw OI snapshots
- Liquidity score, liquidity state, "thin"/"thick" labels
- **Velocity, acceleration** of price (newly recommended addition)
- Regime classification, session-phase labels (pre-open/lunch/power-hour
  as *tags*, as opposed to the raw timestamp itself)
- Breakout / rejection / acceptance labels (this is Layer 2's
  `interaction_outcome`, per the 17F design — never Layer 0)
- Sentiment, trend, signal, score, classification (any form)
- Aggressor / buy-sell-initiated volume — doubly excluded: forbidden by
  policy **and** unavailable from this broker regardless

## 14. Recommended Layer 0 Observation Vocabulary

**No new kind is required.** The existing five, confirmed sufficient
against every question in §10:

| Kind | Confirmed sufficient for |
|---|---|
| `MARKET_TICK` | High-frequency price capture (websocket) — resolution pending field census |
| `QUOTE` | Point-in-time price/volume across spot, futures, options |
| `MARKET_DEPTH` | Order book + OI — **futures only in practice**; spot structurally inapplicable; options untested |
| `OPTION_CHAIN` | Full strike ladder, OI, top-of-book bid/ask, volume |
| `CANDLE` | Broker-provided historical bars, tagged distinctly from live-aggregated ones |
| `CAPTURE_EVENT` *(sibling, not an observation kind)* | Every failure mode in §2/§7/§8's absence-reality analysis |

The taxonomy passed this audit. The gaps found are entirely in **what
gets polled, how often, and what's explicitly documented as
unavailable** — not in what the schema can represent.

## 15. Schema Changes Required Before Production Capture

**None required.** Two small, defensive additions recommended, both
additive and low-risk:

1. Add `velocity`, `acceleration` to `FORBIDDEN_PAYLOAD_FIELDS` (§4).
2. Add the empty-depth-vs-missing-depth test (§8, §11 item 5) —
   test-only, no schema change.

Everything else identified as "missing" in this audit is a **collector
design decision or a documentation requirement**, not a change to
`bujji/market_reality/`. That is the headline finding: Phase 17E's
taxonomy was built correctly enough that a completeness audit against
real future intelligence questions did not need to reopen it.

---

## 16. Gate Status

| Gate | Status |
|---|---|
| Price / liquidity / volume / derivatives audits | **Complete** — §2–5 |
| Time model / session / absence / provenance audits | **Complete** — §6–9 |
| Future-question test | **Complete** — §10 |
| Blocking items identified | **3** — §11 |
| Deferred items identified | **5 categories** — §12 |
| Forbidden list consolidated | **Complete** — §13 |
| Vocabulary recommendation | **No new kind required** — §14 |
| Schema changes | **None required; 2 defensive additions recommended** — §15 |
| Review | **Pending — awaiting operator decision on §11 items 1–2 before collector work proceeds** |
