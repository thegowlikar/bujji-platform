# Phase 17I.1 — Live Futures Microstructure Reality Capture: Audit & Design

**Status: AUDIT + DESIGN ONLY.** No order-flow analysis, no OI
interpretation, no signals. This phase closes Gap #2 from
`PHASE_17I0_MARKET_REALITY_INVENTORY_AND_MEMORY_BOUNDARY_AUDIT.md` in
design, and identifies exactly what remains before that gap can be
closed in code.

---

## 1. What microstructure facts can FYERS provide?

**Live-verified, not guessed** — `data_certification/fyers_depth_discovery_20260813.json`
captured two real polls of `FyersBroker.get_depth()` for both the
active NIFTY futures contract and a live option contract, 5 seconds
apart. Every field below is from that real payload.

| Field | Type | Present | Notes |
|---|---|---|---|
| `bids` | array of `{price, volume, ord}` | Yes | 5 levels, real order-book depth. |
| `ask` | array of `{price, volume, ord}` | Yes | 5 levels. **Singular key name `ask`, not `asks`** — see §4, a real naming mismatch against Layer 0's own vocabulary. |
| `oi` | int | Yes | Real open interest, e.g. 12,587,900. |
| `pdoi` | int | Yes | Previous-day OI. |
| `oiflag` | bool | Yes | |
| `oipercent` | float | Yes | OI change percentage. |
| `totalbuyqty` / `totalsellqty` | int | Yes | Aggregate order quantities across the full book, not just the 5 visible levels. |
| `ltp` / `ltq` / `ltt` | float/int/int | Yes | Last traded price/quantity/time (epoch). |
| `o`/`h`/`l`/`c`/`ch`/`chp` | float | Yes | Day OHLC + change — redundant with, not a replacement for, the candle/quote paths already captured elsewhere. |
| `v` | int | Yes | Day cumulative volume. |
| `atp` | float | Yes | Average traded price. |
| `tick_Size`, `lower_ckt`, `upper_ckt`, `expiry` | mixed | Yes | Contract metadata, static within a session. |

**Fields NOT present anywhere in this payload:** true multi-level
order-book beyond 5 levels, per-order/per-participant identifiers,
timestamped depth-change deltas (the response looks like a full
snapshot each poll, not an incremental feed — see below).

### Update frequency / reliability / quirks (live-observed)

- Two polls 5 seconds apart both returned `row_present=True` with
  identical key sets and identical list lengths (5 bids, 5 asks each) —
  **weak evidence this is a full-snapshot response each call**, not an
  incremental delta feed. The discovery script itself flags this as
  "weak evidence," correctly declining to assert it as certain from
  only 2 samples.
- `oi`/`pdoi`/`oiflag`/`oipercent` were byte-identical across both
  polls (OI updates far slower than price) — expected, not a bug.
- Real price/quantity movement occurred between the two polls (`ltp`
  24416.2 → 24426, bid/ask ladders shifted) — confirms the endpoint is
  live, not cached/stale.
- No rate-limit or error was hit across the 2-poll discovery run; the
  60-second cadence chosen in Phase 17F.1.2 (`POLL_INTERVAL_SECONDS`)
  has not itself been stress-tested against a real rate limit in this
  audit — carried over as an existing, not re-verified, decision.

---

## 2. What does Bujji currently capture?

**Nothing, today.** `scripts/run_futures_depth_poller.py` (Phase
17F.1.2) exists and is wired to write via the standard
`build_raw_observation(kind="MARKET_DEPTH", ...)` → `RawObservationStore.append()`
path, but it has never gone live:

- `FIELD_MAPPING_VERIFIED = False` — the script's own gate refuses
  `--live` until a human confirms the real field names against a live
  response and updates `_normalize_depth_payload()`. **This audit is
  that confirmation** (§1) — the field names are now real and
  documented, but the code has not yet been updated to match them.
- `_current_expiry_hint()` unconditionally raises `NotImplementedError`
  — an explicit, deliberate TODO, not a bug: the script's author left
  the expiry-identity field unresolved rather than guess it, per this
  project's standing discipline against inventing identity fields.
- Running it today in DISCOVERY mode (the only mode that currently
  works) logs the raw shape and writes nothing to any store — exactly
  what produced the `fyers_depth_discovery_20260813.json` artifact this
  audit reads from.

So: **microstructure facts are accessible and partially wired, but zero
rows exist in any Reality store.**

---

## 3. What is missing?

Three concrete, code-level gaps, all inside the already-written poller
script — not architectural gaps:

1. **`_normalize_depth_payload()` is unimplemented** — needs to map the
   real raw keys (`bids`, `ask`, `oi`, `pdoi`, `oiflag`, `oipercent`,
   `totalbuyqty`, `totalsellqty`, `ltp`, `ltq`, `ltt`, `tick_Size`,
   `lower_ckt`, `upper_ckt`, `expiry`) onto Layer 0's `MARKET_DEPTH`
   payload shape. Layer 0's own `REQUIRED_PAYLOAD_FIELDS[KIND_MARKET_DEPTH]`
   is `("bids", "asks")` (plural) — the raw FYERS key is singular
   `ask`. This is a pure rename, not a fabrication (§4) — but it is
   real, unverified-until-now work.
2. **`_current_expiry_hint()` needs real wiring** to
   `bujji.broker.fyers`'s existing expiry-resolution logic (the same
   one `InstrumentMaster.resolve_nearest_future()` already uses for
   historical futures ingestion, Phase 17H.6/17H.9) — not re-derived,
   not guessed.
3. **A genuine certification collision, found by this audit, not
   present in the script's own docstring**: the poller currently sets
   `ACCESS_METHOD = "direct_sdk_fyers_broker_py"` — the exact same
   value already `CERTIFIED_AVAILABLE` for live futures **quotes**
   (`fyers_nifty_future_certification.json`, 2026-08-12).
   `CertificationGate.status_for()` is keyed by `(instrument_type→cert_key,
   access_method)` **only** — it has no concept of observation `kind`.
   Flipping `FIELD_MAPPING_VERIFIED` today, as-is, would make
   `gate.status_for("direct_sdk_fyers_broker_py", INSTRUMENT_FUTURE)`
   return `CERTIFIED_AVAILABLE` for depth writes that were **never
   themselves certified** — the exact collision class already found
   and fixed three times in this project (17G.0 REST-vs-websocket,
   17H.3 live-vs-historical, 17H.9 daily-vs-intraday). This is the
   single most important finding of this audit.

---

## 4. Where should the missing facts live?

### Can microstructure fit into existing Reality architecture? — Yes, exactly as-is.

- `KIND_MARKET_DEPTH` already exists in `market_reality.taxonomy`
  (defined since Phase 17E, `REQUIRED_PAYLOAD_FIELDS[KIND_MARKET_DEPTH]
  = ("bids", "asks")`).
- `build_raw_observation(kind="MARKET_DEPTH", ...)` already exists and
  is already called by the (unfinished) poller — same identity
  minting, same MOC observation machinery every other Reality fact
  uses (`market_observation.engine.build_observation()`).
- `RawObservationStore.append()` already exists and is already wired
  in — same append-only JSONL, same event-sourced discipline as
  QUOTE/CANDLE observations.
- **No new model, no new store, no parallel observation identity
  system is needed.** The architecture was already correctly designed
  for this in Phase 17E/17F.1.2 — the only work remaining is finishing
  three concrete pieces of unfinished code (§3), not building new
  architecture.

### `RawObservation` vs `MarketRealitySnapshot` vs `HistoricalObservation`

- **`RawObservation` is the correct home** — microstructure facts are
  live, high-frequency, tick-like captures, structurally identical in
  role to the existing QUOTE observations already flowing through this
  exact path.
- **Not `MarketRealitySnapshot`**: that model is deliberately
  daily-granularity (one row per trading day, OHLC-shaped) — forcing a
  5-level order book into it would either lose the depth structure or
  require inventing a new field shape on a model designed for
  something else. Out of scope, not touched.
- **Not `HistoricalObservation`**: that store is for backfilled,
  chunked, certified-after-the-fact historical data. Microstructure
  is inherently live-only (FYERS's depth endpoint has no historical
  equivalent verified anywhere in this codebase) — there is nothing to
  backfill.

---

## 5. Storage design review

| Property | Assessment |
|---|---|
| Identity uniqueness | Already handled: `build_observation()`'s content hash includes `(observation_type, instrument, exchange, segment, timestamp, resolution, source, schema_version)` — a `MARKET_DEPTH` kind's `KIND_TO_MOC_TYPE`/`KIND_TO_RESOLUTION` mapping already gives it a distinct observation_type/resolution from `QUOTE`, so two same-instant depth vs. quote observations cannot collide. Not re-verified live in this audit (no code was flipped live), but the mechanism is the same one already empirically proven for daily-vs-intraday in Phase 17H.9. |
| Duplicate handling | `RawObservationStore.append()`'s existing event-sourced JSONL append semantics apply unchanged — no new logic needed. |
| Conflict handling | Same store, same discipline — not re-designed here. |
| Restart survival | Append-only JSONL survives restarts identically to every other Layer 0 writer already in production (QUOTE capture, 17I.7 campaign). |
| Storage growth | At 60-second cadence, market hours (~6h15m/session post-15:40-close change), one instrument: ~375 observations/session, ~93,750/year. Modest — orders of magnitude smaller than the 5-min intraday historical backfill (§Historical, 17H.9: ~170k rows/instrument total, not per year). No retention concern at this cadence. |
| Retention | Not addressed by this audit — Layer 0's raw JSONL is currently a session-scoped/rolling capture buffer (confirmed in 17I.0, 456 rows = today's session only), not a long-horizon archive; the same retention question already exists for QUOTE observations and is unchanged by adding MARKET_DEPTH. Out of this phase's scope to solve. |

---

## 6. Certification review

**A new, distinct `access_method` is required — confirmed necessary by
this audit, not assumed.**

Current state (`data_certification/`):

| access_method | Instrument | Status |
|---|---|---|
| `direct_sdk_fyers_broker_py` | NIFTY_FUTURES (live quote) | CERTIFIED_AVAILABLE (2026-08-12) |
| `direct_sdk_fyers_historical_rest` | NIFTY_FUTURES (historical daily) | CERTIFIED_AVAILABLE |
| `direct_sdk_fyers_historical_intraday_rest` | NIFTY_FUTURES (historical 5-min) | CERTIFIED_AVAILABLE |

**Decision: `access_method = "direct_sdk_fyers_broker_py_depth"`** — a
fourth, distinct value. Reusing `direct_sdk_fyers_broker_py` (as the
poller currently does) would let depth writes silently inherit the
live-quote certification's `CERTIFIED_AVAILABLE` status without depth
itself ever being certified — precisely the bug this audit exists to
catch before it ships.

A dedicated certification script (not yet built) would be needed:
`certify_fyers_futures_depth_access.py`, mirroring the existing
certification scripts' structure — live probe of `get_depth()`,
integrity checks on the returned `bids`/`ask` ladders (price
ordering, no negative/zero prices, `bids` best price ≤ `ask` best
price), a dated artifact (`fyers_nifty_future_depth_certification_<date>.json`),
fail-closed on anything else. **Not built in this audit-only phase.**

`CertificationGate.status_for()` keying (`cert_key, access_method`)
requires no code change — it already correctly separates any two
distinct access_method values; the new value is what closes the gap,
not a gate modification.

---

## 7. Reality contract — minimum required representation

Reusing `RawObservation`'s existing shape (identity / quality /
provenance / value), no new dataclass:

```
identity:
  observation_id       (minted by build_observation(), unchanged mechanism)
  observation_type      -> derived from KIND_MARKET_DEPTH's existing KIND_TO_MOC_TYPE mapping
  instrument             = the resolved futures contract symbol (e.g. "NSE:NIFTY26AUGFUT")
  exchange = "NSE", segment = "NSE_FO"
  timestamp              = capture time (event time unavailable -- FYERS depth has no per-tick timestamp of its own beyond ltt, which is the last TRADE time, not the depth snapshot time)
  resolution              -> KIND_TO_RESOLUTION[KIND_MARKET_DEPTH] (existing mapping, unchanged)
  source = "fyers"

market facts (payload):
  last_price            <- raw "ltp"
  open_interest          <- raw "oi"
  prior_day_open_interest <- raw "pdoi"          (real fact, not derived)
  oi_change_percent       <- raw "oipercent"      (a raw field FROM the source, not computed by Bujji -- still a fact, not an interpretation)

depth facts (payload):
  bids: [{price, volume, order_count}, ...]   <- raw "bids", renamed field-for-field, unchanged values
  asks: [{price, volume, order_count}, ...]   <- raw "ask", renamed key only (ask -> asks, matching Layer 0's existing KIND_MARKET_DEPTH vocabulary)
  total_buy_quantity     <- raw "totalbuyqty"
  total_sell_quantity    <- raw "totalsellqty"

provenance:
  source = "fyers"
  access_method = "direct_sdk_fyers_broker_py_depth"   (NEW, distinct -- see §6)
  certification_status / certification_ref  <- from CertificationGate, unchanged mechanism
  raw_artifact_ref / transformation_history  <- unchanged Layer 0 lineage fields
```

**Explicitly NOT stored** (per the phase's own restriction, and
consistent with every other Reality-tier decision in this project):
bid/ask imbalance, order-flow score, liquidity score, pressure,
sentiment, any derived signal. `total_buy_quantity`/`total_sell_quantity`
are stored as-is because FYERS itself reports them as raw aggregate
facts — Bujji computes nothing to produce them, unlike e.g. an
imbalance ratio (`buy/sell` or `(buy-sell)/(buy+sell)`), which WOULD be
a Bujji-computed derived signal and must NOT be stored here.

---

## 8. Boundary classification

| Layer | Example | Where it lives |
|---|---|---|
| Reality | "NIFTY futures OI was 12,587,900 at 09:22:04 IST." "Best bid was 24,416.1 for 195 lots." "The ask ladder contained these 5 levels." | `RawObservation`, `kind=MARKET_DEPTH` — this phase's scope. |
| Memory | "Similar depth/OI conditions (OI within X of Y, book skew Z) occurred before." | Not built. Would live in a future `MarketRealityTimeline`-style query layer, same boundary already drawn in Phase 17H.6/17I.0 — retrieval-across-time, no computation, still Memory not Reality. |
| Understanding | "Aggressive buying pressure." "Accumulation." "Distribution." "Book imbalance regime." | Not built. Requires computing a ratio/score FROM the raw bid/ask/OI facts — explicitly forbidden in this phase. |
| Intelligence | "Probability of a breakout given this depth pattern." "Select this strategy." | Not built. Already exists as a separate, mature subsystem (Shadow Campaign) for other Reality inputs; would extend there, not here, if ever pursued. |

---

## 9. Does this complete the Reality layer?

**No single implementation was made in this audit — nothing has been
"completed" in the sense of shipped code.** But the audit itself
answers the architectural question definitively: **yes, once the three
concrete gaps in §3 are closed (normalize function, expiry wiring, new
access_method + certification script), live futures microstructure
Reality capture is architecturally complete** — no new model, no new
store, no new identity system is required. This closes the one
Reality-tier deficiency Phase 17I.0 identified, using entirely existing
architecture.

**What remains, not done here, listed but not built (per this phase's
own "audit first, no implementation" instruction):**
1. Implement `_normalize_depth_payload()` using the real field mapping
   in §1/§7.
2. Wire `_current_expiry_hint()` to real expiry resolution.
3. Mint the new `direct_sdk_fyers_broker_py_depth` access_method and
   build its certification script.
4. Flip `FIELD_MAPPING_VERIFIED = True` only after 1–3 are done and a
   human has reviewed the change, per the script's own existing
   discipline.
5. Run a live certification, then a live bounded capture, then decide
   retention — all live-execution steps, none performed in this
   audit-only phase.

No indicator, OI interpretation, liquidity metric, regime detection,
or signal was designed or built. No strategy or execution code was
touched. No duplicate observation identity system was proposed.
