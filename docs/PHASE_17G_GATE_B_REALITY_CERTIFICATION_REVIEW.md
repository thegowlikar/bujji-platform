# Phase 17G.0 — Gate B Reality Certification Review

**Status: DESIGN ONLY. No code. No schema. No strategy. No intelligence models.**

This document does two things: (1) audits the four Gate B scripts, the
existing certification artifacts, and `CertificationGate` exactly as they
exist on disk today — what each proves, what it does not, and one real
defect the audit found; (2) defines the evidence-classification framework
that will be filled in with real values once the scripts are actually run.
**No script has been executed yet as of this document** — every artifact
referenced below except the three pre-existing REST certifications is
still `NOT YET RUN`.

---

## Part 1 — Script-by-script audit (verified against the real code, not assumed)

### 1.1 `scripts/certify_vix_access.py`

**Proves:** whether `NSE:INDIAVIX-INDEX` is reachable via `ltp`/`historical`
over `direct_sdk_fyers_broker_py`, with symbol-echo verification and
candle-integrity checks (no future timestamps, no duplicates, ascending
order, no impossible OHLC).

**Does NOT prove:** anything about VIX over websocket (this script only
uses REST). Anything about VIX depth/volume/OI — these are structurally
`NOT_APPLICABLE` for an index per the Instrument Capability Registry
(17F.0.4), and the script correctly records them as such, not as gaps.

**Artifact:** `data_certification/fyers_india_vix_certification_YYYYMMDD.json`
(date-stamped as of the 17F.7.1-adjacent fix this session). Instrument key:
`INDIA_VIX`. Access method: `direct_sdk_fyers_broker_py`.

**Lineage/timestamp correctness:** the artifact's own `timestamp` field is
the real capture moment (`_now_ist()`, read once, never re-derived), and
the filename's date suffix is parsed from that same field — so filename
and content timestamp cannot diverge. Correct.

### 1.2 `scripts/certify_websocket_access.py`

**Proves, if run to CERTIFIED_AVAILABLE:** the FYERS websocket transport
connects and authenticates for `NSE:NIFTY50-INDEX` specifically; symbol
echo is correct (the exact MCP-incident-driven check); ticks are received
during a real 120-second window; which raw tick fields are present/null
across that window (`FIELDS_OF_INTEREST`: `ltp`, `vol_traded_today`,
`last_traded_qty`, `last_traded_time`, `exch_feed_time`, `bid_price`,
`ask_price`, `bid_size`, `ask_size`, `prev_close_price`, `open_price`,
`high_price`, `low_price`, `oi`); whether an exchange/event timestamp
field exists at all (`exch_feed_time`/`last_traded_time`) and whether
observed values are valid (not future-dated); and — the script's most
consequential check — whether **full-mode (`litemode=False`) `ltp`
scaling agrees with the already-certified REST reference**, via two
timed samples compared as a ratio (catching an order-of-magnitude/
power-of-ten mismatch, not just inequality).

**Does NOT prove, and this session's own claim in an earlier turn was
imprecise about item (b) — corrected here:**
(a) anything about futures or option websocket ticks — **spot only**, by
explicit scope decision (`SYMBOL = "NSE:NIFTY50-INDEX"`, comment: "matching
the operator's session-one scope decision").
(b) **reconnect behavior — this script does NOT test it.**
`data_ws.FyersDataSocket(..., reconnect=False, ...)` is explicit: "A
single clean observation window." An earlier summary in this session
listed "reconnect behaviour?" as one of the questions this script
answers — that was inaccurate; it is not tested here, and remains a gap
this document records honestly rather than repeats.
(c) what **production** actually receives. The script deliberately runs
`litemode=False`; `FyersTickFeed` (the only real websocket client in the
codebase, per 17F.6.2's audit) constructs its socket with `litemode=True`.
A `CERTIFIED_AVAILABLE` result here certifies the **transport's
capability in full mode**, not what the currently-deprecated production
wrapper is actually configured to receive. The script's own
`limitations` list states this explicitly on every run, regardless of
outcome — this is disclosed, not hidden.
(d) depth over websocket — not attempted; this is a tick-only
`SymbolUpdate` subscription.

**Artifact:** `data_certification/fyers_websocket_certification_YYYYMMDD.json`.
Instrument key: `NIFTY_SPOT`. Access method: `fyers_websocket`.

**Lineage/timestamp correctness:** correct, same discipline as 1.1 —
`timestamp` is the real `started_at`, filename derives from it.

### 1.3 `scripts/discover_depth_response_shape.py`

**Proves, once run:** the real, raw, unmodified shape of a FYERS
`depth` response for a futures symbol and (if `--option-symbol` is
supplied) one option contract — every key actually present, an example
value per key, and a weak (explicitly labeled non-proof) signal toward
whether the response is a full snapshot or an incremental delta, via two
timed polls 5 seconds apart compared by key-set and list-length.

**Does NOT prove:** any interpretation of the shape — the script's own
design (Phase 17F.5) is deliberately silent on what `bids`/`asks` should
be named or shaped; it reports what FYERS actually sent. It also does
not prove behavior across a full trading session (two polls, 5 seconds
apart, is a point sample, not a session-long characterization) — the
snapshot-vs-incremental signal is explicitly labeled "evidence, not
proof" in its own output.

**Artifact:** `data_certification/fyers_depth_discovery_YYYYMMDD.json`.
Not certification-shaped (no `instrument`/`validation_result` top-level
keys matching `CertificationGate`'s expected shape) — **this artifact is
not read by `CertificationGate` at all**, by design; it is a discovery
record for a human to read, not a machine-readable certification. Worth
stating explicitly since it lives in the same directory as the
certification artifacts.

### 1.4 `scripts/discover_option_chain_premium_fields.py`

**Proves, once run:** the real, raw shape of a FYERS `optionchain`
response across a real multi-strike chain — every raw key seen across
every row, a CE-vs-PE key comparison (in case the two sides carry
different fields), and explicitly, separately, which fields
`OptionObservation` needs (`open`/`high`/`low`/`close`/`settlement`/
`volume`/`open_interest`/`change_in_open_interest`/`underlying_price`)
without claiming any mapping between the two lists.

**Does NOT prove:** a mapping from FYERS's raw field names to
`OptionObservation`'s field names — the report explicitly states this is
a human decision to make from the raw keys, not something the script
concludes. Does not prove chain behavior across time (single point
sample, same limitation as 1.3).

**Artifact:** `data_certification/fyers_option_chain_discovery_YYYYMMDD.json`.
Same non-certification shape as 1.3 — not read by `CertificationGate`.

### 1.5 Existing certification artifacts (pre-dating this session's Gate B work)

| Artifact | Instrument | Access method | Result | Dated |
|---|---|---|---|---|
| `fyers_nifty_spot_certification.json` | `NIFTY_SPOT` | `direct_sdk_fyers_broker_py` | `CERTIFIED_AVAILABLE` | 2026-08-12 |
| `fyers_nifty_future_certification.json` | `NIFTY_FUTURES` | `direct_sdk_fyers_broker_py` | `CERTIFIED_AVAILABLE` | 2026-08-12 |
| `fyers_option_chain_certification.json` | `NIFTY_OPTION_CE` | `direct_sdk_fyers_broker_py` | `CERTIFIED_AVAILABLE` | 2026-08-12 |

These are **not date-stamped in their filenames** (predate the 17F.7.1
convention). Confirmed still structurally valid against `CertificationGate`
(`status_for()` reads `instrument`/`access_method`/`validation_result`/
`timestamp` — all present in each). No lineage defect found in these
three files themselves.

---

## Part 2 — A genuine defect found during this audit — FIXED

**`CertificationGate._load()` indexes artifacts by `instrument` alone,
not by `(instrument, access_method)`:**

```python
instrument_key = record.get("instrument")
...
by_instrument[instrument_key] = record
```

Every artifact glob-matched in `data_certification/*.json` is folded into
one dict keyed only by its `instrument` string. When two artifacts share
an `instrument` value but differ in `access_method` — which is exactly
what Gate B is about to produce — **only the alphabetically-last-sorted
filename survives in the gate's in-memory index; the other becomes
invisible to every subsequent `status_for()` call, even though its file
still exists on disk.**

**This is not hypothetical for tomorrow's run.** The existing
`fyers_nifty_spot_certification.json` (`instrument: "NIFTY_SPOT"`,
`access_method: "direct_sdk_fyers_broker_py"`) and the about-to-be-created
`fyers_websocket_certification_YYYYMMDD.json` (`instrument: "NIFTY_SPOT"`,
`access_method: "fyers_websocket"`) **collide on the same instrument key.**
Sorted alphabetically, `fyers_w...` sorts after `fyers_n...`, so the
websocket artifact will win the collision. The practical consequence: any
code calling `status_for("direct_sdk_fyers_broker_py", INSTRUMENT_SPOT)`
after the websocket cert is written will read the (now-shadowing)
websocket record, find `artifact_access_method ("fyers_websocket") !=
access_method ("direct_sdk_fyers_broker_py")`, and return
`CERTIFICATION_MISSING` — **the previously-working REST spot
certification would become invisible to the gate, fail-closed, purely as
a side effect of Gate B evidence collection.**

**Why this is not fixed in this document:** per this phase's explicit
constraint, code is only touched if a defect blocks evidence collection
itself. This defect does not block any script from running or writing its
artifact — both files will be written correctly, independently, with
correct content. It only corrupts the **gate's own read-time
interpretation** of two simultaneously-valid certifications for the same
instrument. This is a real, load-bearing finding for the decision tree in
Part 5, not an implementation task authorized here.

**Fixed, same session, on explicit authorization.**
`bujji/market_reality/certification.py`'s `_load()` now indexes by
`(instrument, access_method)` instead of `instrument` alone; `status_for()`
looks up that exact pair directly rather than fetching by instrument and
then comparing access_method after the fact (which is what allowed the
collision to matter — the wrong record could already be sitting in the
slot by the time the comparison ran). Additive, no artifact format change,
no data loss — every existing artifact already carries `access_method`.

Verified: 3 new tests in `tests/test_market_reality_store.py`
(`test_two_access_methods_for_the_same_instrument_do_not_collide` —
constructs the EXACT scenario above, a REST NIFTY_SPOT cert plus a
websocket NIFTY_SPOT cert, and proves both remain independently
`CERTIFIED_AVAILABLE`; `test_a_third_unrelated_access_method_for_a_
certified_instrument_is_missing`; a white-box check that the index key
itself now includes `access_method`). Full regression: 5,395 passed, 0
failed (up from 5,392). Existing tests
(`test_index_and_spot_are_distinct_certification_subjects`,
`test_index_is_certified_once_a_real_vix_artifact_exists`, and the rest
of `test_market_reality_store.py`/`test_market_reality_validator.py`/
`test_market_reality_safety.py`) all still pass unmodified.

**Consequence for tomorrow's Gate B run:** the REST `NIFTY_SPOT`
certification and the about-to-be-produced websocket `NIFTY_SPOT`
certification will now coexist correctly in the gate's index — writing
the websocket artifact will no longer silently break the existing,
working REST-path Layer 0 spot certification. Part 4, condition 2 of this
document ("The Part 2 defect... is resolved") is now satisfied
unconditionally, ahead of Gate B execution rather than as a precondition
discovered mid-run.

---

## Part 3 — Evidence classification framework

Four states, applied per capability. **All values below are placeholders
pending live execution** — this section defines the framework Part 6 will
be filled in with after Gate B actually runs.

- **CERTIFIED** — directly observed from a real FYERS raw response,
  captured in a dated artifact, reproducible.
- **UNKNOWN** — not observed yet; a script exists and is prepared, or no
  script exists yet, but no live evidence has been captured either way.
- **UNAVAILABLE** — verified impossible from this broker/API, established
  by a prior real investigation (not assumed) — e.g. the Instrument
  Capability Registry's NOT_APPLICABLE findings, or FYERS's structural
  absence of trade prints/aggressor side (no endpoint of any kind exposes
  this — confirmed by the FYERS API surface audited across this entire
  engagement, not merely "not tried yet").
- **NEEDS CAPTURE-FORWARD** — possible in principle, but only meaningful
  once continuous collection begins and accumulates; a single point-in-time
  discovery run cannot establish it (e.g. OI *history*, once OI itself is
  certified as observable per-poll).

### 3.1 Market Data

**Updated 2026-08-13 09:19-09:24 IST with real Gate B execution results.**
Every row below now cites a real, dated artifact from today's run instead
of a pre-execution placeholder.

| Capability | Classification | Basis |
|---|---|---|
| NIFTY spot LTP | **CERTIFIED** | `fyers_nifty_spot_certification.json`, 2026-08-12, `quote_status: OK`; reconfirmed live via websocket 2026-08-13 (291 ticks, `ltp` present on every one) |
| NIFTY futures LTP | **CERTIFIED** | `fyers_nifty_future_certification.json`, 2026-08-12; reconfirmed live via `fyers_depth_discovery_20260813.json` (`ltp: 24416.2` -> `24426` across two 5s-apart polls, genuinely moving) |
| Futures OI | **CERTIFIED** | `fyers_depth_discovery_20260813.json`, futures row: `oi: 12587900`, `pdoi: 12562800`, `oipercent: 0.2`, `oiflag: true` — real, dated, from `depth()` |
| Futures previous OI (`pdoi`) | **CERTIFIED** | Same artifact — `pdoi` is a REAL top-level field on the futures depth row, not merely seen in a test fixture. `pdoi` stayed constant across both same-session polls (`12562800` both times), consistent with "previous day's close OI," distinct from `oi` which is intraday-current |
| Futures depth (bid/ask ladder) | **CERTIFIED** | Same artifact — 5-level real ladder on both `bids` and `ask` (see field-name note below), each level `{price, volume, ord}`. `bids[0]` genuinely changed between the two 5s-apart polls (price 24416.1->24416.6, volume 195->130) — real, live order-book movement, not a cached/stale response |
| Option chain structure (strikes, CE/PE, symbols) | **CERTIFIED** | `fyers_option_chain_certification.json`, 2026-08-12; reconfirmed live 2026-08-13 — `fyers_option_chain_discovery_20260813.json` captured 22 real strike rows across CE and PE |
| Option premium fields (LTP on a chain row) | **CERTIFIED for LTP only; UNAVAILABLE for OHLC/settlement on the chain endpoint** | `fyers_option_chain_discovery_20260813.json`: every row carries `ltp`/`ltpch`/`ltpchp` (last price + change + change%) but **no `open`/`high`/`low`/`close`/`settlement` field of any kind, on any of the 22 rows or the underlying row**. This is the exact gap 17F.7 flagged, now confirmed empirically rather than assumed: the `optionchain` endpoint is not a candle source |
| Option OI (chain endpoint) | **CERTIFIED** | Same artifact: `oi`, `oich`, `oichp`, `prev_oi` present on every strike row (both CE and PE identically) |
| Option depth (single contract) | **CERTIFIED** | `fyers_depth_discovery_20260813.json`, option leg (`NSE:NIFTY2681824100CE`) — same 5-level `bids`/`ask` ladder shape as futures, real OI/pdoi/oipercent present |
| India VIX | **CERTIFIED** | `fyers_india_vix_certification_20260813.json` — `CERTIFIED_AVAILABLE`, quote+historical both OK, symbol echo confirmed |
| Websocket ticks (spot, full mode) | **PARTIAL** (see Part 6.3) | `fyers_websocket_certification_20260813.json` — `PARTIAL_CERTIFICATION`. Real ticks (291 in 120s) with `ltp`/`exch_feed_time`/OHLC-of-day fields present on every tick, but `bid_price`/`ask_price`/`bid_size`/`ask_size`/`oi`/`vol_traded_today`/`last_traded_qty`/`last_traded_time` absent from **every** observed tick |
| Websocket ticks (futures/options) | **UNKNOWN**, not even prepared | No script targets this — spot-only scope decision stands, unchanged by today's run |
| Option chain event/exchange timestamp | **UNAVAILABLE from the `optionchain` endpoint** | `fyers_option_chain_discovery_20260813.json` — **zero** timestamp-shaped keys on any of the 22 strike rows or the underlying row. A materializer consuming this endpoint has only `knowledge_time` (capture time), never a real `event_time` from this source |
| Depth response event timestamp | **CERTIFIED** | `fyers_depth_discovery_20260813.json` — `ltt` (epoch seconds) present on both futures and option rows, real and advancing between polls (`1786593123` -> `1786593128`, a genuine 5-second gap matching the real poll interval) |

### 3.2 Market Structure

| Capability | Classification | Basis |
|---|---|---|
| OHLC candles | **CERTIFIED** (materializer level) | `materializer.py`, 66 tests, full regression green — but zero real rows exist (17F.5 Part 0 finding, unchanged) |
| Multi-timeframe candles | **UNAVAILABLE at 15m/hourly/daily**, CERTIFIED-capable at 1m/5m | `aggregator._INTERVAL_SECONDS` only supports `ONE_MINUTE`/`FIVE_MINUTE` (17F.7 Part 4 finding) — this is a code-capability gap, not a broker gap; broker `historical` resolution has not been tried at other intervals |
| Futures basis | **CERTIFIED** (materializer level), **NEEDS CAPTURE-FORWARD** for real values | `futures_stats_materializer.py` computes it correctly from two Candles; needs real futures AND spot candle data to ever populate |
| Volatility measures (realized) | **CERTIFIED** (materializer level) | `indicators.realised_volatility()`, reused unmodified; needs real candle history to populate |
| Volatility measures (implied) | **UNAVAILABLE at Layer 0 by design**; **UNAVAILABLE from FYERS in the endpoints checked** | `iv` is a banned Layer 0 field regardless. Now also empirically checked: neither `fyers_option_chain_discovery_20260813.json`'s 22 real chain rows nor `fyers_depth_discovery_20260813.json`'s futures/option depth rows carry any IV-shaped key (`iv`, `impliedVolatility`, `impVol`, or similar) — FYERS does not appear to compute/expose IV on these two endpoints. Not exhaustively ruled out for every FYERS endpoint, but the two most likely candidates both came back empty |
| Option OI changes | **CERTIFIED** (materializer level AND broker level) | `FuturesStatistics.oi_change` logic proven in tests; the broker side is now also directly observed -- `fyers_option_chain_discovery_20260813.json`'s `oich`/`oichp` fields are FYERS's OWN pre-computed OI-change values (e.g. strike 24100 CE: `oich: 23595`, `oichp: 7.35`), which is richer than expected -- the materializer's own computed `oi_change` can be cross-checked against FYERS's own reported delta as a consistency check once real observations accumulate |

### 3.3 Broker Reality Boundaries — must remain explicitly impossible

| Capability | Classification | Basis |
|---|---|---|
| Trade prints | **UNAVAILABLE** | No FYERS endpoint audited across this entire engagement (REST or websocket) exposes individual executed trades — only aggregated LTP/OHLC/volume |
| Aggressor side (buy/sell-initiated) | **UNAVAILABLE** | Same — no endpoint carries this; explicitly named as a permanent limitation in the 17F.5 audit (Part 7.1) |
| True order flow | **UNAVAILABLE** | Follows directly from the above two — order flow analysis requires trade-level aggressor data this broker does not expose at any tier |
| Dealer positioning | **UNAVAILABLE as direct observation**, proxy-only | Only OI + price joint observations exist; any "dealer positioning" read is an interpretation layered on top of certified measurements, never a direct broker fact — must never be presented as observed |

**These four rows are not expected to change after Gate B executes.**
Gate B interrogates what FYERS's *data* endpoints return; none of them are
order-flow or trade-tape products. Re-running Gate B cannot make these
CERTIFIED — only a genuinely different broker/data product could, and
none is in scope.

---

## Part 4 — What "sufficient evidence" means (defined now, evaluated after execution)

Evidence is **sufficient to proceed to Market Understanding architecture**
only if, after Gate B executes:

1. Every row in §3.1 that is prepared-but-`UNKNOWN` resolves to either
   `CERTIFIED` or a documented `UNAVAILABLE`/`NEEDS CAPTURE-FORWARD` — no
   row is allowed to remain `UNKNOWN` by default; a script failing to run
   (auth, market hours, transport error) is itself a finding to report,
   not a silent gap.
2. ~~The Part 2 defect... is resolved~~ **DONE, ahead of execution** —
   fixed and tested same session (see Part 2). Gate B may now proceed
   without risk of the websocket certification silently shadowing the
   existing REST spot certification.
3. Depth/option-chain field mappings from §3.1 are pinned to real,
   observed field names before any `MARKET_DEPTH`/`OptionObservation`
   payload construction is authorized — guessing remains forbidden
   regardless of how much other evidence is collected.

---

## Part 5 — Post-certification decision tree

```
Gate B executes (all four scripts run, artifacts written)
        |
        v
Read every artifact's validation_result / raw capture
        |
        +--> Evidence CERTIFIED sufficient (Part 4's 3 conditions met)
        |         |
        |         v
        |    Proceed to Phase 17G Market Understanding Architecture
        |    (still design-only at that point -- no code authorized
        |    by evidence sufficiency alone)
        |
        +--> Evidence reveals a MISSING or DIFFERENTLY-SHAPED field
        |    (e.g. depth ladder key names, option premium field names,
        |    OI-vs-pdoi naming)
        |         |
        |         v
        |    Update REALITY CONTRACTS ONLY:
        |      - FyersBroker method return shapes, if a raw-passthrough
        |        method needs a sibling normalized method
        |      - MARKET_DEPTH / OptionObservation payload field mapping
        |        (still no NEW Layer 0 fields -- taxonomy.
        |        REQUIRED_PAYLOAD_FIELDS is closed; a mapping decision is
        |        "which raw key fills the existing bids/asks slot," never
        |        "add a new slot")
        |      - Certification key granularity (Part 1.3/17F.5's earlier
        |        finding: certification is per access_method+instrument_
        |        type, not per observation kind -- may need revisiting
        |        once real depth/option shapes are known)
        |    Then RE-RUN the relevant discovery script to confirm the
        |    updated contract against a fresh capture -- never proceed on
        |    an assumed fix.
        |
        +--> Capability requires HISTORICAL ACCUMULATION to mean anything
        |    (OI history, basis time series, realized vol, any "change
        |    over time" measurement)
        |         |
        |         v
        |    Mark NEEDS CAPTURE-FORWARD explicitly (§3 already does this
        |    for every such row). Do NOT treat a single point-in-time
        |    discovery capture as if it satisfies this -- a materializer
        |    proven correct in tests is not the same claim as "real
        |    values exist."
        |
        +--> A script fails to run at all (auth failure, market-hours
             abort, transport error)
                  |
                  v
             Report the failure PLAINLY as its own finding. Do not
             substitute a guess, a prior day's stale result, or a
             "probably fine" assumption for a capability that was never
             actually observed this run.
```

---

## Part 6 — Live execution results

**Executed 2026-08-13, 09:19-09:24 IST, NSE market hours. All four scripts
run in the runbook's specified order. All four succeeded (exit 0). No
failures to classify.** One operator-side error occurred and was corrected
before any script logic ran: the first VIX attempt used the system
`python3` instead of `/opt/bujji/.venv/bin/python3` and failed with
`ModuleNotFoundError: No module named 'fyers_apiv3'` — an environment
mistake, not a script or broker defect, immediately corrected and re-run
successfully. Not logged as a Gate B failure since no script code executed.

### 6.1 VIX certification — `fyers_india_vix_certification_20260813.json`

`CERTIFIED_AVAILABLE`. Symbol requested/returned both
`NSE:INDIAVIX-INDEX` (echo confirmed). `quote_status: OK`,
`historical_status: OK`, `timestamp_valid: true`. `volume_available`/
`oi_available` both `null` — correctly recorded as not-applicable for an
index, not as a gap.

### 6.2 Option chain premium discovery — `fyers_option_chain_discovery_20260813.json`

**Raw field names observed** (identical set on every CE and PE row, 22
strike rows total plus 1 underlying row):
```
ask, bid, fyToken, ltp, ltpch, ltpchp, oi, oich, oichp,
option_type, prev_oi, strike_price, symbol, volume
```

**Futures vs option chain payload difference:** N/A at this endpoint —
this capture is options-only. The underlying/index row (`strike_price:
-1`) carries a DIFFERENT field set than the strike rows:
`description`, `ex_symbol`, `exchange`, `fp`, `fpch`, `fpchp`, `ltp`,
`ltpch`, `ltpchp`, `fyToken`, `symbol` — notably `fp`/`fpch`/`fpchp`
("futures price" and its change), distinct from `ltp` (spot). Observed
live: `ltp: 24345.9` (spot) vs `fp: 24429.1` (futures) on the same
underlying row — a real, non-zero basis visible directly in this one
response, incidentally.

**CE vs PE payload difference:** **none.** `keys_only_in_ce` and
`keys_only_in_pe` are both empty lists — CE and PE rows are structurally
identical.

**Timestamp semantics:** **no timestamp field of any kind** on any row
(strike or underlying). Confirmed by exhaustive key listing, not
assumed. A materializer built on this endpoint alone has only capture
time (`knowledge_time`), never a real broker-reported `event_time`.

**Option premium availability:** `ltp` (last traded price) only.
**No `open`/`high`/`low`/`close`/`settlement`** on any row — this
endpoint cannot supply the OHLC fields `OptionObservation` was designed
to carry (17F.7's predicted gap, now confirmed rather than assumed).

**Real symbol extracted for step 4 (not constructed):**
`NSE:NIFTY2681824100CE`, read directly from a real row:
`{'symbol': 'NSE:NIFTY2681824100CE', 'strike_price': 24100,
'option_type': 'CE', 'ltp': 319.8, 'oi': 344630, ...}`.

### 6.3 Websocket certification — `fyers_websocket_certification_20260813.json`

`PARTIAL_CERTIFICATION` (not a clean pass — real, disclosed gaps).

**Websocket behavior evidence:** connected, symbol echo confirmed
(`NSE:NIFTY50-INDEX` requested and returned), **291 real ticks in 120
seconds** (2.425 ticks/sec) — this is genuine, active tick delivery, not
a stalled or empty feed.

**Timestamp semantics:** `exch_feed_time` present and valid on all 291
ticks (`event_timestamp_available: true`, `timestamp_valid: true`) — this
is a real exchange-side event timestamp, distinct from arrival/capture
time, and it is available.

**Price scaling (the script's own most consequential check):**
`CONSISTENT` on both samples (ratio 0.999895 and 1.000004 against the
certified REST reference) — `migration_permitted: true`. Full-mode
(`litemode=False`) `ltp` is NOT subject to the power-of-ten scaling bug
this check exists to catch.

**Fields present on every tick:** `ch`, `chp`, `exch_feed_time`,
`high_price`, `low_price`, `ltp`, `open_price`, `prev_close_price`,
`symbol`, `type`.

**Fields absent from every tick:** `ask_price`, `ask_size`, `bid_price`,
`bid_size`, `last_traded_qty`, `last_traded_time`, `oi`,
`vol_traded_today` — for a SPOT INDEX subscription. Consistent with the
Instrument Capability Registry's structural NOT_APPLICABLE finding for
spot depth/OI/volume (17F.0.4) — the websocket confirms the same
structural absence REST already established, rather than contradicting
it.

**Reconnect behavior: NOT TESTED.** Restated per the explicit instruction
for this run — `certify_websocket_access.py` constructs its socket with
`reconnect=False`, a single clean observation window. This artifact says
nothing about reconnect behavior and must never be cited as if it does.

**What this does NOT certify about production:** `FyersTickFeed` (the
only real websocket client in the codebase, 17F.6.2) runs `litemode=True`;
this certification ran `litemode=False`. Every field listed as "present"
above is only reachable in production if the wrapper is switched to full
mode — disclosed in the artifact's own `limitations` list, not omitted.

### 6.4 Depth discovery — `fyers_depth_discovery_20260813.json`

**Raw field names observed** (identical set on futures AND the option
contract — see futures-vs-option comparison below):
```
ask, atp, bids, c, ch, chp, expiry, h, l, lower_ckt, ltp, ltq,
ltt, o, oi, oiflag, oipercent, pdoi, tick_Size, totalbuyqty,
totalsellqty, upper_ckt, v
```

**Bid/ask ladder availability and shape — CERTIFIED, real field name
found:** the ask-side field is **`ask` (singular), not `asks`** — a real
finding neither guessed nor assumed. Both `bids` and `ask` are lists of
5 levels, each level shaped `{price, volume, ord}` (`ord` = number of
orders at that level, itself a new, real field neither
`market_reality.taxonomy.REQUIRED_PAYLOAD_FIELDS` nor
`OptionObservation` currently names). Real example (futures, level 0):
`{'ord': 1, 'price': 24428.5, 'volume': 65}`.

**OI availability — CERTIFIED, real field names found:** `oi` (current),
`pdoi` (previous-day OI), `oipercent` (percent change), `oiflag`
(boolean) — all four present on both futures and the option leg. Real
example (futures): `oi: 12587900, pdoi: 12562800, oipercent: 0.2,
oiflag: True`.

**Futures vs option payload difference: NONE.** Both `futures_top_level_keys`
and `option_top_level_keys` in the structural report are byte-identical
lists. The `depth` endpoint returns the same shape for a futures contract
and an option contract — a real, useful simplification for a future
materializer (one field-mapping, not two).

**Snapshot vs incremental — real evidence toward FULL SNAPSHOT, not
incremental delta.** Comparing the two polls (5 seconds apart) for
futures: `ltt` advanced `1786593123 -> 1786593128` (a real 5-second gap,
matching the actual poll interval exactly); `ltp` moved `24416.2 ->
24426` (real price action); `bids[0]` genuinely changed content (price
`24416.1 -> 24416.6`, volume `195 -> 130`) rather than staying static or
losing keys. No keys were missing on the second poll
(`keys_only_in_first`/`keys_only_in_second` both empty for both
instruments). This is stronger evidence than the script's own
conservative "weak evidence" framing anticipated — a genuinely changing,
fully-keyed response on every poll is the signature of a full live
snapshot each call, not a cached response or a partial delta. Per the
script's own epistemic discipline, this is still evidence, not
absolute proof (only two data points, 5 seconds apart, one session) —
but it is real, positive evidence, not a null result.

**Timestamp semantics:** `ltt` (epoch seconds, last-traded-time) present
and advancing on both futures and option rows — a real, usable
event-time source for this endpoint, distinct from the missing timestamp
on the option-chain endpoint (6.2).

**Real option symbol used (not constructed):** `NSE:NIFTY2681824100CE`,
extracted from step 2's real capture (6.2), matching this document's own
"do not infer or construct symbols" instruction.

### 6.5 Summary of new CERTIFIED capabilities (all backed by a dated 2026-08-13 artifact)

- NIFTY spot LTP (reconfirmed, websocket)
- NIFTY futures LTP, OI, previous OI, full depth ladder
- India VIX (quote + historical)
- Option chain structure (22 real strikes, CE+PE)
- Option chain LTP + OI + OI-change (FYERS's own pre-computed delta)
- Option depth (single contract, same shape as futures)
- Depth-endpoint event timestamp (`ltt`)
- Websocket spot connectivity, tick delivery, price-scaling consistency,
  event timestamp (`exch_feed_time`)

### 6.6 Summary of new UNAVAILABLE findings (empirically confirmed, not assumed)

- Option chain endpoint: no OHLC/settlement fields at all (LTP only)
- Option chain endpoint: no timestamp field at all
- Websocket spot ticks: no bid/ask/size/OI/volume/last-traded-qty/
  last-traded-time (consistent with the pre-existing structural
  NOT_APPLICABLE finding for spot, now confirmed on the websocket path
  too)
- Implied volatility: absent from both the option chain and depth
  endpoints (the two most likely candidates)
- Reconnect behavior: genuinely not tested by any script that has run
  (restated, not a new finding, but now definitively true of the actual
  executed evidence rather than a design-time prediction)

---

## Part 7 — Architecture rule preserved

```
Reality → Memory → Understanding → Intelligence → Strategy
```

Gate B exists to prove **Reality** — specifically, whether the *inputs*
this whole system depends on are real, and in what shape. Nothing in this
document, and nothing authorized by it, does any of the following:

- No demand/supply zone design (explicitly out of scope per 17F.5 Part 5
  — zones remain "at least two layers away")
- No regime model design (17F.5 Part 6 — regime classification needs raw
  inputs this phase is still establishing)
- No strategy logic
- No AI reasoning / intelligence synthesis
- No execution-layer changes

**This document itself makes zero schema changes, zero field additions,
and zero assumptions about what FYERS returns beyond what is already
captured in the three pre-existing, dated 2026-08-12 certification
artifacts.** Every `UNKNOWN` in Part 3 stays `UNKNOWN` until a real
artifact says otherwise.
