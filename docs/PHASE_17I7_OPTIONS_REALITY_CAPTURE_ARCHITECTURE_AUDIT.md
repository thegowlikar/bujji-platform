# Phase 17I.7 — Options Reality Capture Architecture Audit

**Status: AUDIT ONLY. No code, no ingestion, no storage.** Answers the
seven questions using real, live-verified FYERS capability (two new
live probes run today, 2026-08-14) plus direct re-inspection of
existing code — not assumed from 17I.5/17I.6's own summaries.

---

## 1. What option data fields can be legally and reliably captured?

**Two distinct, real, live-verified FYERS endpoints exist, with
genuinely different field sets — this matters directly for §5's
frequency question.**

### `optionchain` action (`FyersBroker.get_option_chain_raw()`) — live-tested today, full real response

One call returns the **entire chain** (all strikes near ATM at once —
11 rows for `strike_count=2`). Real fields, confirmed from an actual
response:

```
ask, bid            -- top-of-book only, single level, no ladder
ltp, ltpch, ltpchp
oi, oich, oichp, prev_oi
volume
strike_price, option_type, symbol, exchange, ex_symbol, fyToken
```

**No OHLC. No 5-level depth. No IV. No Greeks.** Confirmed absent from
the real response, not assumed.

### `depth` action (`FyersBroker.get_depth()`) — already live-verified in Phase 17I.1, for BOTH futures and an option symbol

Re-confirmed by re-reading `data_certification/fyers_depth_discovery_20260813.json`'s
own `"option"` section (a real, dated capture against
`NSE:NIFTY2681824100CE`, not a new probe needed): full OHLC, 5-level
`bids`/`ask` ladders (`{price, volume, ord}` each), `oi`, `pdoi`,
`oiflag`, `oipercent`, `totalbuyqty`, `totalsellqty`, `ltq`, `ltt`,
`tick_Size`, `lower_ckt`/`upper_ckt`. **This is a strictly richer field
set than `optionchain`, but per-symbol only — one call per contract,
not one call per chain.**

### Summary table

| Field | `optionchain` (whole chain, 1 call) | `depth` (per-symbol, N calls for N strikes) |
|---|---|---|
| LTP | ✅ | ✅ |
| OHLC | ❌ | ✅ |
| Volume | ✅ | ✅ (cumulative) |
| OI / prior-day OI | ✅ (oi, prev_oi) | ✅ (oi, pdoi) |
| Bid/Ask | ✅ (top-of-book, single level) | ✅ (5-level ladder) |
| Depth (multi-level) | ❌ | ✅ |
| Greeks | ❌ (never, anywhere in this codebase's live testing) | ❌ |
| IV | ❌ (never, anywhere in this codebase's live testing) | ❌ |

**Neither endpoint provides IV or Greeks anywhere.** Confirmed across
every live probe run in 17I.6 and this phase — never present in a raw
FYERS response. Computing either would be a derived value, correctly
out of Reality scope per this project's standing `FORBIDDEN_PAYLOAD_FIELDS`
rule (already includes `iv`, `delta`, `gamma`, `theta`, `vega`, `rho` —
confirmed present in `market_reality/taxonomy.py`, unchanged since
17E).

## 2. Reality-tier facts vs. future Memory/Understanding/Intelligence

| Reality (this phase's scope) | NOT Reality |
|---|---|
| strike, expiry, option_type, underlying, exchange symbol | PCR, max pain |
| LTP, OHLC (where available), volume | IV rank/percentile |
| OI, prior-day OI, OI change | IV itself (not source-provided — would be Bujji-computed) |
| bid, ask, bid/ask quantity, 5-level depth ladder (where available) | Greeks (delta/gamma/theta/vega/rho) |
| capture timestamp, certification lineage | "bullish"/"bearish" positioning labels, OI-buildup narratives (long buildup/short covering) |

The dividing line is identical to the one already enforced everywhere
else in this project: a Reality fact is something FYERS's response
itself contains, verbatim; anything requiring arithmetic, a model
(Black-Scholes), or a classification judgment belongs to a later tier.

## 3. Audit of existing option-related code

Re-confirmed by direct re-read (not carried over from 17I.5/17I.6's
summaries):

| Component | Reusable? | Incomplete? | Duplicated? | Violates Reality architecture? |
|---|---|---|---|---|
| `bujji/options_observation/` (Series 73C) | **Yes** — identity/value model already wraps MOC's `build_observation()` correctly, same discipline as `futures_observation` | **Yes** — no `CertificationGate` wiring, no persistent store; only ever fed from bhavcopy (EOD), never from live `optionchain`/`depth` | No — the ONLY module of its kind at this identity/value fidelity | **No** — zero contamination confirmed in 17I.5 §5 |
| `bujji/replay/option_chain_ingestion.py` (`HistoricalSessionRecord`) | Partially — real, disciplined bhavcopy parser, but a DIFFERENT model shape than `HistoricalObservation` | Yes — 4 real days on disk, no certification gate | **Yes, in effect** — this and `options_observation` are two independent paths that could both consume the same bhavcopy row into two different models; nothing currently reconciles them | No contamination found, but the duplication itself (two live, uncoordinated observation shapes for the same real-world fact) is worth naming plainly |
| `KIND_OPTION_CHAIN` (`market_reality.taxonomy`) | **Yes** — already declared, `REQUIRED_PAYLOAD_FIELDS[KIND_OPTION_CHAIN] = ("strikes",)`, exactly the same "declared but unused" state `KIND_MARKET_DEPTH` was in before 17I.2 activated it | Yes — no `build_raw_observation(kind="OPTION_CHAIN", ...)` caller exists anywhere in the codebase | No | No |
| `CertificationGate` support | **Yes** — `INSTRUMENT_OPTION` already has a cert-key mapping (`"NIFTY_OPTION_CE"`) and `REQUIRED_IDENTITY_FIELDS[INSTRUMENT_OPTION] = ("expiry", "strike", "option_type")`, both re-confirmed live this pass, unmodified since 17E | N/A — this piece is actually complete, just unused | No | No |
| `data_certification/fyers_option_chain_certification.json` | **Yes** — a real, dated `CERTIFIED_AVAILABLE` artifact already exists for `direct_sdk_fyers_broker_py`/`NIFTY_OPTION_CE`, from live quote+historical probes on one contract | Certifies ACCESS to one contract, not a capture pipeline | No | No |

**Nothing found violates Reality architecture.** The overall picture:
most of the certification/identity/taxonomy scaffolding needed already
exists and is unused (exactly the `KIND_MARKET_DEPTH`-before-17I.2
pattern); the two real gaps are (a) no store, and (b) two independent,
uncoordinated observation models for the same bhavcopy fact
(`options_observation.OptionObservation` vs.
`replay.HistoricalSessionRecord`) that would both need reconciling
before either becomes the certified path.

## 4. Contract identity model requirements

Per `REQUIRED_IDENTITY_FIELDS[INSTRUMENT_OPTION]` (already enforced by
the existing validator, confirmed live this pass) plus the futures
continuous-identity precedent (17H.3 Part 2.4), a complete identity
model needs:

- **underlying**: the index/stock name (`NIFTY`) — stable, never
  rolls.
- **expiry**: the real listed expiry date — already a mandatory
  identity field.
- **strike**: already a mandatory identity field.
- **option_type** (CE/PE): already a mandatory identity field,
  constrained to `taxonomy.ALL_OPTION_TYPES`.
- **exchange symbol** (the literal, rollover-prone contract symbol,
  e.g. `NSE:NIFTY2681824350CE`): per the SAME rule already binding for
  futures (17H.3 Part 2.4) — **this must be preserved as lineage
  (`source_symbol`), never used as the stored identity itself.** Unlike
  futures, however, an option contract has no natural "continuous"
  identity analog (`NIFTY_FUT_CONTINUOUS`'s stitching concept doesn't
  apply — a specific strike+expiry+type combination genuinely stops
  existing at expiry, it doesn't roll into a new one). **The correct
  identity is the tuple (underlying, expiry, strike, option_type)
  itself** — already exactly what `REQUIRED_IDENTITY_FIELDS` +
  `options_observation`'s own identity design (§3) already encode.
  No new identity concept is needed, only using what already exists.
- **Rollover lifecycle**: not "rollover" in the futures sense (a
  strike+expiry+type contract does not roll — it expires and a
  DIFFERENT contract with different identity fields begins trading).
  What DOES need explicit handling: **a strike that was OTM early in
  its life and becomes ATM/ITM later (or vice versa) is still the SAME
  contract identity throughout** — the identity tuple never changes
  based on moneyness, only based on the four fixed fields above. This
  is already correctly how `options_observation` models it (confirmed
  §3) — no design change needed.

## 5. Minimum capture frequency — evidence-based comparison, no choice made

| Option | Real evidence for/against |
|---|---|
| EOD snapshots | Already what bhavcopy provides (17I.5/17I.6) — zero marginal engineering cost, but structurally cannot support any intraday Understanding/Memory work later (17G.A's whole point was that end-of-session snapshots lose path information — directly demonstrated as a real, measured problem for spot/futures/VIX in 17J.2-17J.4's validation work) |
| 5-minute snapshots | Matches the cadence already proven to work cleanly for spot/futures/VIX (17H.9's 5-min ingestion, whose 100-day chunking and window-fit reasoning would apply identically here if ever built) — no NEW capability needed, same proven pattern, just a new instrument class |
| Tick snapshots | No live-tick option feed has been certified or tested anywhere in this codebase (`get_quote`/`get_depth`/`optionchain` are all poll-based REST calls in every test performed across 17I.1–17I.7, never a websocket tick stream for options specifically) — untested, not proven available |
| Full option-chain snapshots (all strikes, one call) | `optionchain` action proven live (§1) to return the whole chain in ONE call — dramatically cheaper than N per-symbol `depth` calls for N strikes, at the cost of the narrower field set (no OHLC, no ladder) |

**No frequency is chosen here, per the instruction.** The real,
evidence-grounded trade-off this audit surfaces: `optionchain` gives
breadth cheaply (whole chain, narrow fields); `depth` gives richness
expensively (one call per strike, full ladder+OHLC). A future capture
design would need to pick one, mix both, or poll `optionchain`
frequently and `depth` more sparingly for a subset of strikes — a real
design decision for a future, separate phase.

## 6. FYERS limitations — consolidated, all live-verified across 17I.6 and this phase

- **Historical option availability**: zero for expired contracts —
  live-tested twice now (17I.6: 5/5 strikes rejected;
  `optionchain`'s `timestamp` parameter confirmed silently ignored).
- **Option chain API behavior**: `optionchain` returns only the
  CURRENT live chain; no historical mode exists, confirmed by direct
  parameter testing, not documentation-reading.
- **Websocket constraints**: not tested for options specifically in
  any phase to date — genuinely unknown, not assumed either way.
- **Rate limits**: no rate-limit error encountered across all live
  probes run in 17I.6/17I.7 combined (~9 calls); not stress-tested at
  volume.
- **Expired contract access**: confirmed fully unavailable, both by
  symbol (`historical` action) and by chain (`optionchain` with a past
  timestamp) — the two most plausible access paths, both tested, both
  closed.

## 7. Future boundary — Reality / Memory / Intelligence

Restating the definitions given, mapped concretely to what this audit
found:

- **Reality** ("what was actually present"): the field table in §1/§2
  — strike, expiry, type, LTP, OHLC (where available), volume, OI, and
  bid/ask/depth (where available), each a literal FYERS-reported value
  with lineage back to a real, certified capture.
- **Memory** ("when have we seen something similar before"): NOT
  addressed by this audit — would consume Options Reality once it
  exists, the same way `RealityMemoryEvent`/`RealityMemoryCatalog`
  (17J.1) already consume spot/futures/VIX Reality. No options-specific
  Memory work is scoped here.
  
- **Intelligence** ("what does it mean"): IV, Greeks, PCR, max pain,
  positioning bias — all already correctly isolated in this project's
  existing Intelligence-tier modules (`greeks_brain`,
  `msi_participant_positioning`, confirmed in 17I.5 §5 to have zero
  leakage into the Reality-grade `options_observation` package).

This audit adds no new boundary decision — it confirms the existing
one, already enforced structurally by `FORBIDDEN_PAYLOAD_FIELDS`, holds
for options exactly as it does for spot/futures/VIX/depth.

---

## Summary

No architectural redesign is required to eventually capture Options
Reality. Most of the scaffolding (`KIND_OPTION_CHAIN`,
`CertificationGate`'s option cert-key, the correct identity field
requirements) already exists, declared but unused — the same maturity
state `MARKET_DEPTH` was in before 17I.2. The two real, concrete gaps
are: no persistent store, and two independent, uncoordinated
observation models (`options_observation` vs.
`replay.HistoricalSessionRecord`) that would need reconciling before
either becomes the certified path. Field coverage is real but bounded
— two genuinely different endpoints (`optionchain` for breadth,
`depth` for richness), neither ever providing IV or Greeks. No
implementation, frequency choice, or store design is made in this
audit, per its own restriction.
