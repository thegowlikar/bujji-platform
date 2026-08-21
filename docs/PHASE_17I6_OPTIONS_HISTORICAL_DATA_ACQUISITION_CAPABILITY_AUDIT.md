# Phase 17I.6 — Options Historical Data Acquisition Capability Audit

**Status: AUDIT ONLY. No code, no ingestion scripts, no storage
models, no architecture changes.** Every FYERS claim below is from a
live probe run today (2026-08-14, pre-market), not carried over from
memory of prior findings — though this audit independently reproduces
and confirms a finding already documented in `bujji/intelligence/volatility_brain.py`'s
own docstring ("FYERS serves no historical data for expired option
contracts... every expired-contract symbol tested returned 'Invalid
symbol provided'"), rather than merely citing it.

---

## 1. Executive Summary

**C — Only future capture is realistic.**

FYERS provides zero mechanism to retrieve historical option chain
snapshots, individual expired option contracts, or historical expiry
reconstructions. This was tested live, not assumed: five real,
plausible strikes for a real, already-passed weekly expiry all
returned `"Invalid symbol provided"`, and the `optionchain` endpoint's
own `timestamp` parameter was proven to be silently ignored (a
past-date timestamp still returns only current/future expiries).
Bujji's own existing options-adjacent code (`options_observation`,
`option_chain_ingestion`) is real and Reality-grade in design, but its
only real historical source (NSE bhavcopy) provides just 4 days on
disk today, EOD-only, no bid/ask/IV/Greeks by the source's own nature —
not a substitute for genuine historical depth. Per this audit's own
final rule: **historical options data cannot be acquired reliably from
FYERS, so permanent forward Options Reality capture should begin
rather than leaving this domain empty.**

---

## 2. Source Inventory

| Source | Historical Depth | Resolution | Fields | Reliability |
|---|---|---|---|---|
| FYERS `historical` endpoint (per expired contract symbol) | **None** — live-tested, 5/5 candidate expired symbols rejected | N/A | N/A | Confirmed unavailable, not assumed |
| FYERS `optionchain` endpoint (with `timestamp` param) | **None** — parameter silently ignored, always returns current chain | N/A | N/A | Confirmed unavailable via live probe |
| FYERS `historical` endpoint (currently-LIVE, unexpired contract) | Whatever the contract's own listed lifetime has been so far (weeks, for a weekly option) | Daily confirmed working; finer resolutions not separately re-tested this pass (inferred available by symmetry with spot/futures/VIX, not independently re-verified for options today) | OHLC + volume, real, confirmed live | High, for the narrow window before a contract expires |
| NSE Bhavcopy (`data/bhavcopy/*.csv`, via `bujji/replay/option_chain_ingestion.py`) | **4 real trading days on disk** (2026-07-27..30); NSE's public archive itself goes back further but nothing beyond these 4 days has been downloaded into this repo | EOD only (structural — bhavcopy is a settlement snapshot) | strike, expiry, option_type, OHLC, settlement, volume, OI, change-in-OI — confirmed present; bid/ask/IV/Greeks confirmed absent from the 34-column source itself | High for what it has; real but shallow |
| `bujji/options_observation/` (Series 73C) | **Zero persisted records** — real, tested code, never actually run to produce a stored corpus | Whatever its bhavcopy input provides (EOD) | Same field set as bhavcopy, correctly modeled | Code is high-reliability; data is absent |

---

## 3. FYERS Capability Findings

### Historical option chain data — tested live, today

- **Does FYERS expose historical option chain snapshots?** No — confirmed
  by passing a real past-date epoch timestamp
  (`2026-08-11T15:00:00`) to the `optionchain` action; the response
  still returned only current/future expiries (`18-08-2026` onward),
  proving the `timestamp` parameter has no historical effect on this
  endpoint, at least as this codebase calls it.
- **Does FYERS expose historical option contracts individually?** No —
  tested against 5 real strikes (24300/24350/24400/24450/24500) for
  the 2026-08-11 weekly expiry (a real Tuesday expiry that had already
  passed by the time of this test), each via the `historical` action.
  **All 5 returned `{"code": -300, "message": "Invalid symbol provided", "s": "error"}`.**
  This is the SAME failure mode already documented independently in
  `volatility_brain.py`'s own module docstring for a different
  session's test — this audit reproduces that finding live, on a
  different date, with different strikes, and gets the identical
  result.
- **Is there an endpoint for expired option contracts?** None found —
  not in `bujji/broker/fyers.py`'s existing method set
  (`get_quote`, `get_option_chain_raw`, `get_option_chain`, `_call("historical", ...)`,
  `_call("optionchain", ...)` — all checked directly), and the live
  probes above show the two most plausible candidates (per-symbol
  historical, chain-with-timestamp) both fail.
- **Can expired strikes be retrieved?** No — same evidence as above.
- **Can historical expiry chains be reconstructed?** No, not from
  FYERS. The only real path to a past expiry's chain is NSE's own
  bhavcopy (§2), a completely separate, non-FYERS source.

### Endpoint availability / auth / limits (confirmed, from this and prior sessions' live use)

- **Authentication**: same FYERS broker session used throughout this
  project (`FyersBroker.connect()`), no separate auth for options
  endpoints.
- **Rate limits**: not separately stress-tested for options in this
  pass; no rate-limit error encountered across the ~7 live calls made
  in this audit.
- **Maximum date range per request (historical, live/unexpired
  contract)**: not re-verified at scale for options specifically this
  pass — the working 30-day probe succeeded; whether the same 366-day
  cap already measured for equity/futures/VIX (17H.1) applies
  identically to options is **inferred by symmetry (same underlying
  `historical` action), not independently re-measured here** — stated
  honestly as an inference, not a verified fact, per this audit's own
  discipline against assuming.
- **Resolution limits**: daily confirmed working live for an unexpired
  contract; 5-minute/other resolutions not re-tested this pass for
  options specifically (out of scope given the headline finding — none
  of this matters once a contract expires, and expiry is what defines
  "historical" here).

---

## 4. Existing Bujji Options Assets

| Module | Purpose | Current Maturity | Reality Compatibility | Future Role |
|---|---|---|---|---|
| `bujji/options_observation/` (Series 73C) | Wraps MOC to record literal option facts (strike/expiry/type/OHLC/OI/etc.) | High — 521-line test file, 49/49 passing, correct identity/value design, zero contamination (verified in 17I.5) | Model is compatible; storage/certification layer is not (no `CertificationGate`, no persistent store) | The natural home for forward-capture Options Reality, once wired to a store + certification |
| `bujji/replay/option_chain_ingestion.py` + `historical_session.py` | Parses NSE bhavcopy into `HistoricalSessionRecord` | Medium — real, working, disclosed-gaps parser; only 4 real days on disk | Different model shape (`HistoricalSessionRecord`, not `HistoricalObservation`); not certification-gated | A possible EOD backfill source if NSE's archive is ever pulled further back, but shallow and bid/ask-less regardless |
| `bujji/market_perception/option_chain_adapter.py` | Live, per-cycle option chain construction for Shadow Campaign | High (in active use) | Not applicable — Intelligence-tier, live-cycle-only, no historical persistence | Stays Intelligence-tier; not a Reality source |
| `bujji/intelligence/greeks_brain.py`, `greeks_adapter.py` | Live Black-Scholes Greeks | High (validated, in active use) | Not applicable — explicitly Intelligence-tier, computes from live premiums, never claims to be Reality | Stays Intelligence-tier |
| `data_certification/fyers_option_chain_certification.json` | Certifies live option chain/quote access for one contract | Certified, real | Reality-grade as a certification artifact, proves ACCESS not DATA | Would be the template for a forward-capture access_method certification |

---

## 5. Reality Layer Compatibility Assessment

**Can options data fit the existing architecture? Mostly yes, for the
identity/value model — no, for storage and certification, as already
established precisely in `PHASE_17I5_OPTIONS_HISTORICAL_REALITY_INVENTORY_AUDIT.md`
§4, reconfirmed here rather than re-derived:**

| Component | Compatible? |
|---|---|
| `RawObservation` (Layer 0, live capture) | ✅ Structurally compatible — `KIND_OPTION_CHAIN` already exists in `market_reality.taxonomy` (`REQUIRED_PAYLOAD_FIELDS[KIND_OPTION_CHAIN] = ("strikes",)`), unused today but present, same pattern as `KIND_MARKET_DEPTH` before 17I.2 activated it |
| `HistoricalObservation` / `HistoricalObservationStore` | ⚠️ The `Observation`-wrapping identity/value pattern `options_observation` already uses is compatible in SHAPE, but nothing today constructs a `HistoricalObservation` (as opposed to `options_observation`'s own `OptionObservation`) for an option contract — would need a real ingestion script mirroring 17H.4's pattern, not a redesign |
| `CertificationGate` | ⚠️ `INSTRUMENT_TYPE_TO_CERT_KEY` already has an `INSTRUMENT_OPTION → "NIFTY_OPTION_CE"` entry (confirmed present in `market_reality/certification.py`, used by the existing live option chain certification) — the gate itself needs no redesign, only a real certification run for whatever access_method a forward-capture script would use (a new, distinct value, per this project's own recurring collision-avoidance discipline) |
| `MarketRealitySnapshot` | Not directly relevant — that model is deliberately spot/futures/VIX-only by design (17H.5); options would need its own parallel snapshot concept if ever built, not an extension of this one |

**No real incompatibility requiring a redesign was found.** The gap is
entirely "not yet built," not "doesn't fit" — consistent with 17I.5's
own conclusion, reconfirmed by this pass's live FYERS testing.

---

## 6. Decision Recommendation

### Option B — Start permanent capture (selected, per this audit's own final rule)

Historical acquisition (Option A) is not realistic — proven live,
not assumed. A hybrid (Option C) has no meaningful historical leg to
combine with, given FYERS's confirmed zero expired-contract access and
the shallow, EOD-only, bid/ask-less bhavcopy alternative. Per this
audit's explicit final rule — *"If historical options data cannot be
acquired reliably, recommend starting permanent Options Reality
capture immediately"* — **Option B is the recommendation.**

```
capture frequency:  not decided here (a real design question — this
                     audit does not size it; 17I.2's depth poller used
                     60s as a first-pass, disclosed value for a
                     comparable live-capture problem, a plausible
                     reference point, not a decision made here)
fields:              strike, expiry, option_type, underlying, OHLC/LTP,
                      volume, OI, change_in_OI, bid, ask, bid_qty,
                      ask_qty (whichever of these the live optionchain/
                      quote endpoints actually return -- not
                      independently re-verified field-by-field in this
                      pass; `fyers_option_chain_certification.json`
                      already confirms OI and quote/historical access
                      for at least one contract)
instruments:          NIFTY (matching the rest of this project's scope)
storage requirements: not designed here -- 17I.5 §4 already identified
                      the two concrete missing pieces (persistent
                      store, CertificationGate wiring) without
                      prescribing their shape
```

This audit **recommends starting the capability**, per its own final
rule, but does **not** design, size, or implement it — that remains
separate, future, explicitly-scoped work, consistent with every prior
phase in this thread.

---

## 7. Explicit Non-Goals

Confirmed, none of the following were computed, designed, or
implemented anywhere in this audit:

- No Greeks
- No IV
- No PCR
- No max pain
- No option-selling or any strategy logic
- No strategy signals
- No Memory/Understanding artifact created from options data

Every live probe performed in this audit read a real FYERS response
and reported it as-is (symbol validity, chain contents, timestamp
parameter behavior) — nothing was interpreted, scored, or classified
beyond "available" / "not available" / "confirmed by direct test."
