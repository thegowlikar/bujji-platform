# Phase 17H.2.0.2 — Historical Reality Source Evaluation

**Status: AUDIT ONLY. No code. No schema changes. No provider
implementation. No adapters created.**

Compares NSE Bhavcopy against FYERS's historical REST capability, using
only what is verified in this codebase's real code and Gate B's real
captures. Every claim is tagged **VERIFIED** (confirmed against code this
session, with a live-verification note already in that code, or a real
Gate B artifact), **AVAILABLE** (a real code path exists but has never
been exercised live / has no live-verification note), or **UNKNOWN**
(genuinely not established either way) — per the explicit constraint to
separate these three states rather than blur them.

---

## Part 0 — A finding that reframes the question before answering it

**This project already has a standing policy decision
(`docs/PHASE_17G_BACKFILL_DEPTH_DECISION.md`) that assumes FYERS, not
Bhavcopy, is the historical source for spot/futures/VIX** (3-year daily,
6-month 5m/15m targets) — made **before** this audit, and **without** a
dedicated futures-historical verification existing anywhere in the code.
Concretely: `FyersBroker` has a live-verified spot-historical method
(`get_recent_candles`) and a live-verified per-option-contract historical
method (`get_option_candles`) — **but no futures-historical method
exists at all**, verified or otherwise (§1.1). The 17G backfill decision's
"3 years daily for futures" target currently has **zero code path to
even attempt it**, live-verified or not. This audit surfaces that gap
rather than assuming the prior decision already accounted for it.

---

## Part 1 — NIFTY Futures

### 1.1 Availability of historical futures candles

**UNKNOWN — no method exists to check.** Grep confirms `_futures_symbol()`
(the futures symbol constructor) is used in exactly one place in
`bujji/broker/fyers.py`: `get_futures_quote()` (a **live quote** method,
not historical). Neither `get_recent_candles()` (uses `_index_symbol`,
i.e. spot) nor `get_option_candles()` (uses a specific option contract's
symbol) has ever been called with a futures symbol. **No one has ever
asked FYERS's `historical` endpoint for a futures candle in this
codebase's history.** This is a genuine unknown, not a "probably works"
assumption — the same `historical` action likely accepts a futures
symbol (per FYERS's general API shape), but this has never been tried,
so nothing about it — earliest date, contract continuity, whether
`cont_flag=1` produces a stitched continuous series or errors on an
expiring instrument — is verified.

### 1.2 Earliest available date (FYERS)

**UNKNOWN for futures** (§1.1). For spot/options, no method has ever
requested more than a handful of days back — `get_recent_candles`/
`get_option_candles` both compute `lookback_days = max(5, (count // 75) +
3)`, i.e. a window sized to the caller's requested `count`, never a
multi-year request. **The 17G backfill doc's own "does not resolve"
section already states this precisely**: *"This engagement confirmed
`historical` works with 30-day windows... whether 3 years of daily
NIFTY/VIX history is actually retrievable in practice... is an
implementation detail... not resolved here."* Restated as still true,
now specifically for futures too, where even the 30-day claim has never
been tested.

### 1.3 Earliest available date (Bhavcopy)

**VERIFIED, structurally, though not date-bounded in code.** Bhavcopy is
an externally-sourced daily file per trading date (`REAL_BHAVCOPY =
"/tmp/m1/BhavCopy_NSE_FO_0_0_0_20260525_F_0000.csv"` — one file, one
date, in the filename itself). NSE publishes bhavcopy files going back
years on its own public archive — this is common, external knowledge
about NSE's own data retention, **not something this codebase's code
verifies or depends on**; this project's own bhavcopy ingestion path has
only ever been exercised against the one real file referenced above.
**How far back Bhavcopy could go is bounded by NSE's own archive and
whoever acquires the files, not by anything in this codebase.**

### 1.4 OHLC fields

- **Bhavcopy — VERIFIED, full.** `runner.py`'s row mapping reads
  `OpnPric`/`HghPric`/`LwPric`/`ClsPric`/`SttlmPric` directly into
  `build_option_observation()` (the same builder used for options;
  `futures_observation/runner.py` mirrors this for futures rows per its
  own docstring cross-reference — not re-read in full this session, but
  its existence and mirroring relationship is confirmed by
  `options_observation/runner.py`'s own docstring: *"Mirrors
  `bujji/futures_observation/engine.py` exactly"*).
- **FYERS — VERIFIED for spot and per-option-contract, UNKNOWN for
  futures.** `get_recent_candles`/`get_option_candles` both parse
  `[epoch, open, high, low, close, volume]` — a real, live-verified
  6-field row shape (2026-07-19 audit note on `get_option_candles`:
  *"real NIFTY ATM CE showed 75/75 candles with real volume... zero
  zero-volume candles"*). Whether a futures symbol returns the same shape
  is untested (§1.1).

### 1.5 Volume

- **Bhavcopy — VERIFIED**: `TtlTradgVol` column, real per-day total.
- **FYERS — VERIFIED for spot/options** (6th candle field, confirmed
  live and genuinely non-zero for options specifically — the same audit
  note explicitly contrasts this with the **index** quote's `volume=0`/
  `atp=0` artifact, which the `historical` endpoint does NOT share:
  *"The HISTORICAL endpoint... returns genuine per-candle volume."*
  **UNKNOWN for futures** (§1.1).

### 1.6 Open interest availability

- **Bhavcopy — VERIFIED, real EOD OI.** `OpnIntrst`/`ChngInOpnIntrst`
  columns, read directly. This is Bhavcopy's single strongest advantage
  over FYERS for OI specifically.
- **FYERS — VERIFIED ABSENT from historical candles, structurally,
  permanently.** The `[epoch, o, h, l, c, volume]` candle row has **no
  OI field, ever** — confirmed by the row-parsing code itself (6 fixed
  positions, nothing beyond `volume`). OI from FYERS is **only** ever
  available via `depth()`/`optionchain()`'s live snapshot calls (already
  established, 17F.0.4/17G), which have **no historical form at all**.
  **This is the single most consequential, already-decided fact in the
  entire comparison**: FYERS cannot supply historical OI for anything,
  ever, at any resolution, full stop. The 17G backfill decision already
  states this for futures/options ("Futures OI history: Not backfilled.
  Captured only going forward... no backfill can ever change this") —
  restated here as directly relevant to this evaluation's outcome, not a
  new finding.

### 1.7 Timestamp/session semantics

- **Bhavcopy — VERIFIED, one row per day, EOD only.** `timestamp =
  f"{trading_date}T{market_close_time}"` where `market_close_time =
  "15:30:00"` — every Bhavcopy-sourced observation carries the identical
  intraday timestamp, `15:30:00`, regardless of what actually happened
  during the session. **No intraday granularity exists in Bhavcopy at
  all** — this project's Bhavcopy path has never had daily-only
  ambiguity; it is a structural, permanent limitation of the source
  itself, not an ingestion gap.
- **FYERS — VERIFIED for spot/options at whatever resolution is
  requested** (`resolution=str(minutes)`, e.g. `"5"` for 5-minute bars,
  real per-candle epoch timestamps, converted via `epoch_to_ist` with an
  explicit note about IST-vs-host-timezone correctness). Genuine intraday
  granularity, unlike Bhavcopy.

### 1.8 Contract continuity / expiry handling, and rollover implications

**UNKNOWN for both sources, in different ways — the least-understood
part of this entire evaluation, stated plainly rather than guessed:**

- **Bhavcopy**: each file's `FinInstrmNm`/contract identity is per-specific-
  expiry (verified: `options_observation`'s contract key is
  `(strike, expiry, option_type)`, and the mirrored futures package
  presumably keys by `(underlying, expiry)` per its own docstring
  reference). **Stitching a continuous futures series across rollovers
  from Bhavcopy would be entirely this project's own responsibility** —
  no continuity logic exists in `futures_observation`/`options_observation`
  today; each contract's series is independent, with a real, unadjusted
  price gap at every rollover boundary if naively concatenated.
- **FYERS**: `cont_flag="1"` is passed on every existing `historical`
  call (both `get_recent_candles` and `get_option_candles`) — **but its
  exact semantic effect is UNVERIFIED and not explained anywhere in this
  codebase.** No comment, docstring, or live-verification note states
  what `cont_flag=1` does. It is plausible this is FYERS's own
  "continuous contract" (broker-side rollover-adjusted series) flag —
  common in other brokers' APIs — but this is an inference from the
  parameter's name, not a verified fact, and **must not be treated as
  confirmed** per this document's own stated discipline. It is also used
  on the **spot index** call, where "continuity across expiry" has no
  meaning at all (an index never expires) — suggesting `cont_flag` may
  simply be a required-but-mostly-inert default parameter for this
  endpoint rather than a meaningful rollover-stitching switch. **This
  needs a dedicated live check before either source's rollover behavior
  can be trusted for futures**, and that check has never been performed.

### 1.9 Ability to reconstruct futures basis, OI behaviour, price+OI relationships

- **Futures basis**: needs futures close + spot close for the same
  window. **Bhavcopy**: both available, daily only (`UndrlygPric` even
  gives underlying price directly on the options side; the futures side
  presumably has its own close column). **FYERS**: spot close is
  live-verified (`get_recent_candles`); futures close is UNKNOWN (§1.1).
  **Verdict: Bhavcopy can reconstruct daily basis today; FYERS cannot,
  until futures-historical is verified.**
- **OI behaviour (change over time)**: requires OI at two+ points in
  time. **Bhavcopy**: yes, daily, real (`OpnIntrst`/`ChngInOpnIntrst`
  per day, going back as far as the acquired files reach). **FYERS**:
  **no historical OI exists at any resolution** (§1.6) — this is not
  reconstructable from FYERS at all, for any historical date, ever.
  **Verdict: Bhavcopy is the ONLY source capable of historical OI
  behaviour reconstruction.**
- **Price + OI joint observations over history**: follows directly from
  the above two — **only Bhavcopy can supply this**, daily granularity,
  for as far back as real files are acquired.

---

## Part 2 — Options

### 2.1 Availability of historical option contracts

- **Bhavcopy — VERIFIED.** Every option row in a bhavcopy file becomes
  one `OptionObservation`, real, already proven in this project's own
  test suite (`REAL_BHAVCOPY`, exercised by
  `test_options_os_runner.py`/`test_trading_brain_runtime.py`).
- **FYERS — VERIFIED, per-specific-contract, at real intraday
  resolution.** `get_option_candles(contract, minutes, count)` is
  live-verified (2026-07-19) with genuine non-zero volume. **This is a
  real capability Bhavcopy does not have at all: FYERS can give
  intraday option candles; Bhavcopy is EOD-only, permanently.**

### 2.2 OHLC availability

Both sources: **VERIFIED available.** Bhavcopy (daily), FYERS
(per-contract, at any requested intraday resolution, live-verified).

### 2.3 LTP availability

- **Bhavcopy**: no separate "LTP" concept — `ClsPric` (close) is the
  closest analog, EOD only.
- **FYERS**: `ltp` is the live/near-real-time value (Gate B, confirmed
  present on every real `optionchain` row); historically, the candle's
  own `close` field is the equivalent per-bar value. Genuinely richer —
  FYERS can answer "what was the LTP at 10:35am on a given recent day,"
  Bhavcopy fundamentally cannot (it only ever has one EOD value per day).

### 2.4 OI availability

- **Bhavcopy — VERIFIED, real, daily.**
- **FYERS historical candles — VERIFIED ABSENT** (§1.6 — the same 6-field
  candle shape, no OI column, applies identically to options as to
  futures/spot). **FYERS's live `optionchain`/`depth` calls DO carry OI**
  (Gate B, confirmed extensively) — but that is a **live snapshot**, not
  a historical series; asking "what was NIFTY 24100 CE's OI three weeks
  ago" is answerable from Bhavcopy and **not answerable from FYERS at
  all**, live or historical.

### 2.5 Strike/expiry metadata

- **Bhavcopy**: `FinInstrmNm`/contract-key parsing already handles this
  (`_contract_key(row)`, existing, tested).
- **FYERS historical**: the requesting caller already knows the strike/
  expiry (it's encoded in the `contract.symbol` being requested) — the
  candle response itself carries no separate strike/expiry metadata
  (same 6-field shape). Not a gap, just a different responsibility split
  — the caller supplies identity, the response supplies price series.

### 2.6 Timestamp quality

- **Bhavcopy**: one fixed EOD timestamp per day (§1.7) — structurally
  the weakest of the two for anything require intraday timing.
  **UNKNOWN whether Bhavcopy's own trading_date carries any timezone
  ambiguity** — not checked this session, and not previously flagged
  as an issue anywhere in the read code, so presumed fine but not
  independently reverified here.
- **FYERS historical**: real per-candle epoch timestamps, explicit IST
  conversion already verified correct (`epoch_to_ist`, with the D2 code
  comment explaining exactly why naive `datetime.fromtimestamp` would be
  wrong). **FYERS's live `optionchain` endpoint, by contrast, has ZERO
  timestamp fields at all** (Gate B, §1 of `FYERS_REALITY_PAYLOAD_
  CONTRACT.md`) — an important asymmetry: FYERS's *historical* candle
  timestamps are good; FYERS's *live chain snapshot* timestamps are
  entirely absent. These are two different FYERS endpoints with two
  different, opposite timestamp-quality profiles — must not be
  conflated when reasoning about "FYERS's" timestamp quality generally.

### 2.7 Whether historical option-chain reconstruction is possible

**Different answer per source, and this is the sharpest divergence in
the whole comparison:**

- **Bhavcopy: yes, trivially — a full historical chain snapshot for any
  past date is exactly what one bhavcopy file already contains** (every
  strike, every expiry, one EOD row each, for that date). This is
  Bhavcopy's single greatest structural advantage.
- **FYERS: no, confirmed impossible, already decided** (17G backfill
  doc, restated here as directly load-bearing for this evaluation, not
  re-derived): *"full historical chain snapshots (every strike, every
  moment) are not a broker-provided historical product — they only exist
  from the moment a collector starts polling `optionchain()` live."*
  `get_option_candles()` can reconstruct ONE contract's intraday price
  history, but never a full multi-strike chain as it looked at a past
  moment — those are genuinely different capabilities, and this
  document is careful not to let the real per-contract capability (§2.1)
  be mistaken for full chain reconstruction, which remains impossible
  from FYERS.

---

## Part 3 — Market Intelligence suitability

| Future capability | Better served by | Why |
|---|---|---|
| Market regime detection | **Bhavcopy**, for anything requiring OI as an input | Regime work using price+OI jointly (e.g. distinguishing a trending day with OI buildup from one without) needs historical OI, which only Bhavcopy has |
| Futures positioning | **Bhavcopy**, exclusively, for history | FYERS has zero historical OI at any resolution (§1.6/§1.9) — futures positioning history is structurally impossible from FYERS alone |
| OI interpretation | **Bhavcopy** for history; **FYERS** for live/current | Same OI asymmetry — FYERS is the only source for CURRENT OI (via live `depth()`/`optionchain()`), Bhavcopy is the only source for PAST OI. Neither alone is sufficient; both are needed for different time horizons |
| Premium behaviour | **FYERS**, for intraday; **Bhavcopy**, for long-run daily | FYERS's `get_option_candles` gives real intraday premium movement (verified, non-zero volume); Bhavcopy gives only one EOD premium point per day, but reaches further back |
| Volatility memory | **FYERS**, for realized vol at fine resolution (needs a real intraday series, which `market_timeseries.indicators.realised_volatility()` already consumes); **Bhavcopy** only supports daily-resolution realized vol | FYERS's intraday candles are the only source rich enough for anything finer than daily volatility |
| Liquidity understanding | **FYERS only, and only LIVE**, never historical from either source | Neither source has a historical bid/ask/depth ladder — Bhavcopy structurally never had bid/ask columns at all (confirmed, `bid=None, ask=None` always in the ingestion code); FYERS's depth ladder is live-only, no historical form (17F.0.4/17G, restated) |
| Historical pattern memory (multi-year structure) | **Bhavcopy**, for as far back as files are acquired; **FYERS, UNKNOWN depth** (§1.2) | Bhavcopy's depth is bounded only by file acquisition, not by any broker API limit; FYERS's real retrievable depth for even 3 years of daily data has never been tested |

---

## Part 4 — Architecture questions

### 1. Is Bhavcopy currently a historical dependency out of necessity, or legacy design?

**Both, genuinely, for different reasons — not a single clean answer:**

- **Necessity, for OI history**: FYERS structurally cannot supply
  historical OI at all (§1.6/§1.9/§2.4/§2.7), permanently, not a gap
  that improves with more engineering. Any historical OI-dependent
  capability has no alternative to Bhavcopy, ever, with this broker.
- **Necessity, for full historical option-chain snapshots**: same
  reasoning — FYERS confirmed incapable (§2.7), already decided in 17G.
- **Legacy/convenience, for price-only historical needs** (OHLC, no OI):
  `ReplayChainProvider`'s Bhavcopy dependency for `MarketDataProvider`
  (feeding the Trading Brain's Phase-1 chain/spot) is **not** a necessity
  in the same sense — it is Phase-1's chosen implementation, explicitly
  named as a placeholder in `bujji_options_os_runner.py`'s own docstring
  ("Phase-1 ships exactly one concrete provider... No live broker...
  Category B/C future work"). For price-only historical needs, FYERS's
  live-verified `get_option_candles`/`get_recent_candles` are real
  alternatives *once futures-historical is verified* (§1.1's open gap).

### 2. Can FYERS historical data replace Bhavcopy for some layers?

**Yes, for specific, bounded layers — not as a wholesale replacement:**

- **Yes**: intraday option/spot price history (premium behaviour,
  short-horizon realized volatility) — FYERS is live-verified and richer
  (real intraday resolution vs. Bhavcopy's one EOD point).
- **Yes, conditionally on §1.1's gap being closed**: intraday/daily
  futures price history, once a futures-historical call is actually
  tested.
- **No, never**: anything requiring historical OI (positioning, OI-based
  regime signals) or historical full-chain reconstruction — FYERS is
  structurally incapable, permanently, confirmed twice now (this
  document and the pre-existing 17G decision).

### 3. Should Bujji support multiple historical source profiles?

**Yes — and this project already has the exact mechanism for it,
decided one prompt ago and not yet built:** Decision 1 of
`PHASE_17H2_0_LIVE_PROVIDER_CONTRACT_DECISIONS.md` (`ObservationSourceProfile`,
keyed by source, generalizing the existing `MANDATORY_OPTIONS_
OBSERVATION_FIELDS`/`KNOWN_UNAVAILABLE_FROM_BHAVCOPY` pattern) is
directly applicable here without modification to that decision — a third
profile, `fyers_historical`, would declare `KNOWN_UNAVAILABLE =
(OPEN_INTEREST, CHANGE_IN_OPEN_INTEREST, BID, ASK, BID_QUANTITY,
ASK_QUANTITY)` (no OI, no bid/ask, ever, from a candle) alongside
Bhavcopy's and the live-REST-chain's own profiles. **No new
architectural concept is needed — this is the same decision already
made, applied to a third source.**

### 4. Primary / validation / fallback source

**Recommended, not decided (this is a design-only audit; the framing
below is a recommendation for a future explicit decision, not itself
that decision):**

- **Primary historical source for OI, positioning, full-chain
  reconstruction: Bhavcopy.** No alternative exists; this is not a
  preference, it is the only source capable of it at all.
- **Primary historical source for intraday price/premium/volatility
  (once §1.1's futures gap is closed): FYERS.** Richer resolution, real
  intraday granularity Bhavcopy structurally cannot provide.
- **Validation source, for daily closes specifically:** each source
  could cross-check the other on days both exist for (Bhavcopy's daily
  close vs. FYERS's daily-resolution candle close) — a genuine,
  low-cost consistency check, not decided or scoped further here.
- **Fallback source:** not clearly applicable in either direction — the
  two sources are not substitutes for each other's unique capabilities
  (OI vs. intraday resolution), so "fallback" in the usual sense (source
  B if source A fails) applies only within a capability both genuinely
  share (daily OHLC), not across the board.

---

## Part 5 — Capability gap summary (all rows re-derived from Parts 1–3, not restated blindly)

| Capability | Bhavcopy | FYERS Historical | Winner |
|---|---|---|---|
| Historical futures OHLC | VERIFIED | **UNKNOWN — never attempted** | Bhavcopy (verified beats unknown) |
| Historical futures OI | VERIFIED | **Structurally impossible, permanently** | Bhavcopy, exclusively |
| Historical option OHLC (EOD) | VERIFIED | VERIFIED (richer — intraday too) | FYERS (superset) |
| Historical option OI | VERIFIED | **Structurally impossible, permanently** | Bhavcopy, exclusively |
| Historical option bid/ask | Structurally absent, permanently | Structurally absent from candles, permanently | Neither |
| Full historical option-chain snapshot | VERIFIED (trivial, per-file) | **Structurally impossible, permanently** (already decided, 17G) | Bhavcopy, exclusively |
| Intraday granularity | Structurally absent, permanently (EOD only) | VERIFIED, any requested resolution | FYERS, exclusively |
| Multi-year depth | Bounded by file acquisition only | **UNKNOWN, never tested past ~30 days** | Bhavcopy (verified capability beats untested claim) |
| Contract rollover/continuity handling | UNKNOWN (no logic exists; would need building) | **UNKNOWN** (`cont_flag` semantics unverified) | Neither — open gap on both sides |

---

## Recommendation

**Bujji's historical memory should be built on BOTH sources, with
Bhavcopy as the irreplaceable foundation for anything OI- or
chain-structure-related, and FYERS historical as a genuine, additive
enhancement for intraday price/premium/volatility depth that Bhavcopy
cannot provide — not a replacement decision, because the two sources
are not substitutes for each other's core strengths.**

This is not a compromise reached for lack of a cleaner answer — it
follows directly from Part 5's summary table: every OI/chain-structure
row is a Bhavcopy exclusivity (permanent, structural, not an engineering
gap to close), and every intraday-resolution row is a FYERS advantage
(also structural, in the other direction). A single-source decision
would either permanently forfeit historical OI (choosing FYERS) or
permanently forfeit intraday resolution (choosing Bhavcopy alone) —
neither is acceptable given Bujji's stated future intelligence
requirements (Part 3), which need both.

**Before any implementation, two real gaps found by this audit need
closing, neither authorized here:**

1. **§1.1 — verify FYERS futures-historical actually works at all**
   (symbol format, `cont_flag` behavior, real earliest date) — currently
   a complete unknown, not merely under-verified, and blocks half of
   Part 4 item 2's "yes, conditionally" answer.
2. **§1.8 — determine `cont_flag`'s real semantic effect**, live, on an
   instrument that genuinely has expiries (a future, not the spot index
   or a single option contract) — needed before any FYERS-sourced
   futures series could be trusted across a rollover boundary.

Both are natural candidates for a future Gate-B-style discovery script
(read-only, market-hours-gated, same discipline as
`discover_depth_response_shape.py`) — not authorized or scoped by this
document.
