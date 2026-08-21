# FYERS Reality Payload Contract

**Status: OBSERVED FACT ONLY. No code. No schema changes made by this
document — it records what Gate B (2026-08-13, 09:19–09:24 IST) actually
observed, as the canonical reference for every future materializer,
collector, and provider design.**

Every field name below was read directly from a real, dated artifact in
`data_certification/`. Nothing here is inferred, normalized, or renamed
from what FYERS actually returned. Where this contract states a field is
missing, that means: checked, absent — not "not yet checked."

---

## 1. REST `optionchain` — `fyers_option_chain_discovery_20260813.json`

### Observed fields (identical on every CE and PE row, 22 real rows)

```
ask, bid, fyToken, ltp, ltpch, ltpchp, oi, oich, oichp,
option_type, prev_oi, strike_price, symbol, volume
```

### Underlying/index row (`strike_price: -1`) — a DIFFERENT field set

```
description, ex_symbol, exchange, fp, fpch, fpchp, ltp, ltpch,
ltpchp, fyToken, symbol
```

`fp`/`fpch`/`fpchp` = futures price and its change, present alongside
`ltp` (spot) on this one row only. Real, observed basis available
directly from this single response: `ltp: 24345.9` (spot) vs.
`fp: 24429.1` (futures), same capture, same instant.

### Missing fields (checked, confirmed absent — every row, both sides)

- `open`, `high`, `low`, `close`, `settlement` — **no OHLC of any kind.**
  `ltp` is the only price point.
- **No timestamp field whatsoever** — not `exch_feed_time`, not `ltt`,
  not any variant. Zero timestamp-shaped keys on 22 strike rows or the
  underlying row.
- No implied volatility field (`iv`, `impliedVolatility`, or similar).
- No bid/ask size or depth on this endpoint (`bid`/`ask` here are
  single scalar prices, not ladders — see §3 for the *separate* `depth`
  endpoint's real ladder).

### CE vs PE difference

**None.** Field sets are byte-identical between CE and PE rows.

### Timestamp semantics

**None available from this endpoint.** A materializer built on
`optionchain` alone has only capture/knowledge time (when Bujji polled),
never a real FYERS-reported event time for the row.

### Snapshot behavior

Not tested by this endpoint's discovery run (single capture, no repeat
poll performed against `optionchain` the way `depth` was polled twice).
**Unknown**, not assumed either way.

---

## 2. REST `depth` — `fyers_depth_discovery_20260813.json`

### Observed fields (byte-identical set for futures AND the option leg)

```
ask, atp, bids, c, ch, chp, expiry, h, l, lower_ckt, ltp, ltq,
ltt, o, oi, oiflag, oipercent, pdoi, tick_Size, totalbuyqty,
totalsellqty, upper_ckt, v
```

**Futures vs. option: no payload difference.** Same 23 top-level keys
for both instrument types, confirmed by direct comparison of the two
captures.

### The one field-name correction this discovery exists to make

**The ask-side ladder field is `ask` (singular).** It is **not** `asks`.
`bids` is plural; `ask` is not. This asymmetry was unknown before this
run and cannot be guessed correctly — this is the concrete reason
discovery-before-schema-design is the standing rule.

### Ladder shape (5 levels observed on both `bids` and `ask`, both instruments)

```
{ "price": <float>, "volume": <int>, "ord": <int> }
```

`ord` = number of distinct orders resting at that price level. This is a
**real field neither `market_reality.taxonomy.REQUIRED_PAYLOAD_FIELDS`
nor `bujji.options_observation.models.OptionObservation` currently
names.** Not an assumption to add it — a fact to record for whoever
designs the depth materializer's payload mapping next.

Real example (futures, level 0, poll 1):
```json
{"ord": 1, "price": 24428.5, "volume": 65}
```

### OI fields (present on both futures and option, all four together)

```
oi        -- current open interest
pdoi      -- previous-day open interest
oipercent -- percent change (oi vs pdoi)
oiflag    -- boolean
```

Real example (futures): `oi: 12587900, pdoi: 12562800, oipercent: 0.2,
oiflag: true`. `pdoi` stayed exactly constant across both same-session
polls (`12562800` both times) — consistent with "previous trading day's
close," not a rolling/intraday quantity.

### Timestamp semantics

`ltt` — epoch seconds, "last traded time." **Real and advancing.**
Futures: `1786593123` (poll 1) → `1786593128` (poll 2), a genuine 5-second
gap, matching the actual poll interval exactly. This is a usable
event-time source for this endpoint.

### Snapshot behavior — real evidence of a FULL snapshot each call

Two polls, 5 seconds apart, futures leg:

| Field | Poll 1 | Poll 2 |
|---|---|---|
| `ltt` | 1786593123 | 1786593128 |
| `ltp` | 24416.2 | 24426 |
| `oi` | 12587900 | 12587900 (unchanged) |
| `bids[0]` | `{price: 24416.1, volume: 195, ord: 3}` | `{price: 24416.6, volume: 130, ord: 2}` |
| `ask[0]` | (24428.5, 65) | `{price: 24426.4, volume: 390, ord: 3}` |

Every price-moving field changed between polls; no key went missing on
the second call (`keys_only_in_first`/`keys_only_in_second` both empty,
per the discovery script's own structural comparison). This is positive
evidence the response is a full, freshly-computed order-book snapshot on
every call — not a cached response and not a partial/incremental delta
requiring accumulation across polls. **Still evidence from two data
points in one session, not proof across all conditions** (market open
vs. close, illiquid strikes, network retries) — stated at this strength,
no stronger.

### Missing fields (checked, confirmed absent)

- No implied volatility.
- No Greeks of any kind.
- No trade-level prints, no aggressor-side marker anywhere in this
  payload (`bids`/`ask` are resting orders, `ltq`/`ltp`/`ltt` describe
  the last trade's quantity/price/time, but never who initiated it).

---

## 3. Websocket, full mode — `fyers_websocket_certification_20260813.json`

Spot only (`NSE:NIFTY50-INDEX`). 291 real ticks observed over 120 real
seconds.

### Observed fields (present on every one of the 291 ticks)

```
ch, chp, exch_feed_time, high_price, low_price, ltp,
open_price, prev_close_price, symbol, type
```

### Missing fields (checked, confirmed absent from every tick)

```
ask_price, ask_size, bid_price, bid_size,
last_traded_qty, last_traded_time, oi, vol_traded_today
```

For a **spot index** subscription, this is consistent with the
pre-existing structural finding (17F.0.4 Instrument Capability Registry:
an index has no order book, no OI, no traded volume in the derivatives
sense) — the websocket confirms the same structural absence REST already
established for spot, rather than contradicting it. **This finding is
scoped to spot only** — futures/option websocket ticks were not tested
(explicit scope decision, unchanged).

### Timestamp semantics

`exch_feed_time` — present, valid, on all 291 ticks. A real
exchange-side event timestamp, distinct from arrival/capture time.

### Price scaling (full mode vs. certified REST reference)

`CONSISTENT` on two samples taken 110 seconds apart (ratio 0.999895 and
1.000004). Full-mode `ltp` is not subject to the power-of-ten scaling
defect this check exists to catch.

### Snapshot behavior

N/A — this is a push feed, not a poll/snapshot endpoint. 291 ticks in
120s = 2.425 ticks/sec, genuine continuous delivery, not a stalled feed.

### Explicitly NOT certified by this artifact

**Reconnect behavior.** `certify_websocket_access.py` runs with
`reconnect=False` — a single, clean observation window. No claim about
reconnect timing, subscription survival across a reconnect, or recovery
behavior may be made from this artifact. Restated here because it is the
single most likely fact to be misquoted from this evidence later.

**What production actually receives.** `FyersTickFeed` (the only real
websocket client in this codebase, per the 17F.6.2 audit) constructs its
socket with `litemode=True`. This certification ran `litemode=False`.
Every field listed as "present" above is reachable in production **only
if** the wrapper is switched to full mode — a decision this document does
not make.

---

## 4. REST `ltp`/`historical` (VIX) — `fyers_india_vix_certification_20260813.json`

`quote_status: OK`, `historical_status: OK`, symbol echo confirmed
(`NSE:INDIAVIX-INDEX` requested and returned), `timestamp_valid: true`.
`volume_available`/`oi_available` both `null` — structurally
NOT_APPLICABLE for an index, not evaluated as a gap. No new field
discovery beyond the existing REST `ltp`/`historical` shape already
established in earlier certifications (spot, futures).

---

## 5. Unsupported assumptions — explicitly named so they are never made silently

The following are things a designer might reasonably *assume* about
FYERS's payloads. **None of them are supported by anything observed in
Gate B.** Each must be independently verified before being relied on:

- **That the option-chain endpoint will ever carry OHLC.** Observed:
  it does not, on 22 real rows across two option types. Assume it never
  will unless a future capture proves otherwise.
- **That `ask`/`asks` naming is consistent across FYERS endpoints.**
  Observed: the `depth` endpoint uses `ask` (singular) paired with
  `bids` (plural) — an inconsistent pluralization that must not be
  extrapolated to any other endpoint without checking that endpoint too.
- **That depth snapshot behavior holds under load, illiquidity, or
  market-close conditions.** Observed: full-snapshot evidence from ONE
  session, ONE pair of polls, 5 seconds apart, on a liquid near-month
  future and one active strike. Do not assume this generalizes to a
  far-OTM strike or a stressed market.
- **That websocket full-mode field presence holds for futures/options.**
  Observed: spot only. A futures or option websocket subscription has
  never been certified and may carry a different field set entirely
  (plausibly including `oi`, which spot structurally cannot).
- **That `oich`/`oichp` (option chain's pre-computed OI change) and a
  future materializer's own computed `oi_change` will always agree.**
  Both are real, both are observed, but they come from different
  computations (FYERS's server-side delta vs. a difference of two
  independently-polled `oi` values) and have never been cross-checked
  against each other over time.
- **That any of this is stable across FYERS API versions or over time.**
  Every fact in this document is dated 2026-08-13. A materializer or
  provider design that treats this as permanent, rather than the
  most-recent-verified-truth, has silently reintroduced the assumption
  this whole discovery discipline exists to remove.
