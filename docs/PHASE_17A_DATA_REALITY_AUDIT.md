# Phase 17A — Data Reality Audit

> **AMENDMENT (2026-08-12, same day):** §2 and §4's original conclusions —
> "futures/options historical data unavailable" — are **RETRACTED**. The
> "Invalid symbol" errors both sections relied on were produced by the FYERS
> MCP connector used for this audit's live queries, not by FYERS itself. Proof:
> querying `NSE:NIFTY26AUGFUT` through the connector returns an echoed symbol
> of `NSE:NIFTY26AUGFUT-INDEX`; querying `NSE:RELIANCE26AUGFUT` returns
> `NSE:RELIANCE26AUGFUT-EQ`. The connector is rewriting every symbol with a
> cash/index suffix (`-INDEX` for names starting `NIFTY`/`BANKNIFTY`, `-EQ`
> otherwise) before sending it to FYERS — a symbol-normalization bug scoped to
> that connector, not to `bujji/broker/fyers.py` (which sends symbols through
> unmodified via the real `fyers-apiv3` SDK) and not to the FYERS API itself.
> **Every corrected finding below replaces the corresponding original passage
> verbatim; nothing in §2/§4 as originally written should be treated as true.**
>
> Corrected finding: *"Futures/options availability is unverified because the
> MCP connector used for this audit cannot correctly represent derivative
> symbols. Verification requires direct FYERS SDK execution through Bujji's
> broker layer (`bujji/broker/fyers.py`), with a refreshed access token,
> executed on the VPS — not through this connector."* See §11 for the prepared
> (not-yet-executed) verification plan.

**Status:** AUDIT ONLY. No code written, no storage built, no architecture changed.
**Method:** Live queries against the real FYERS API (via an authenticated session,
`fy_id: XS26531`) made directly during this audit, cross-checked against
`bujji/broker/fyers.py` and the real public NFO symbol master
(`public.fyers.in/sym_details/NSE_FO.csv`). Every claim below is either a
**LIVE-VERIFIED** result from a real API call made today (2026-08-12), an
existing **CODE-VERIFIED** fact read directly from `fyers.py`, or explicitly
marked **NEEDS FURTHER VERIFICATION**. Per the session's standing lesson —
*absence in imports does not mean absence in repository* — every "missing"
claim below was checked against the actual file, not inferred from call graphs.

---

## 1. NIFTY Spot — Historical Availability

| Resolution | Depth confirmed live | Notes |
|---|---|---|
| Daily (`D`) | **1998-01-01 → today**, LIVE-VERIFIED | API enforces a **366-day max range per request** for D/W/M resolutions (`code -50`, message: *"Date range cannot exceed 366 days for 1D, 1W, and 1M resolutions"*). Full depth requires chunking into ≤366-day windows — this is a real, hard API constraint, not a design choice. |
| 1-minute | Present **2018-06-01**, LIVE-VERIFIED. **Absent** for 2010-01-01, 2015-01-02, 2017-01-02 (all returned `"s": "no_data"`, not an error — the request was valid, the data simply doesn't exist that far back). | Exact cutoff between 2017 and mid-2018 **NEEDS FURTHER VERIFICATION** (not narrowed further to conserve budget) — but the boundary is real and somewhere in that ~18-month window, not a guess. |
| Volume field | Always `0` for `NSE:NIFTY50-INDEX` at every resolution tested. | CODE-VERIFIED consistent with `fyers.py:287-296`'s own comment: the index has no traded volume because it isn't a tradable instrument — only options/futures contracts on it are. This matches the session's earlier `map.json` finding (`index_val` has no volume/OI/bid/ask fields). |
| Timestamps | Unix epoch, confirmed via `epoch_to_ist()` usage in `fyers.py:311` (explicit IST conversion, not host-timezone-dependent). | CODE-VERIFIED — this the same discipline flagged safe in Phase 16D. |
| Gaps | Not exhaustively scanned (would require walking the full 1998–2026 range) — but daily candles for 1998 show a plausible ~250 candles/year cadence with no missing-value placeholders (no `null`/`NaN` OHLC seen in any sample). | NEEDS FURTHER VERIFICATION for exhaustive gap detection — out of scope for this pass. |

**Conclusion:** NIFTY spot daily history is deep (28 years) and immediately usable for level/reaction-statistics work. Intraday (1-min) history is materially shallower — roughly 7-8 years, not 28 — a real constraint on any backtest that wants minute-level price action further back than ~2018.

---

## 2. NIFTY Futures — AMENDED: unverified due to connector defect, not a FYERS rejection

`fyers.py:91-100`'s `_futures_symbol()` builds `NSE:{underlying}{yy}{MON}FUT` (e.g.
`NSE:NIFTY26AUGFUT`) and its own docstring already flags this as *"NOT
live-verified against a real FYERS response this session — no futures quote
call has been made yet. Treat as provisional."*

This audit attempted that verification through the FYERS MCP connector and
originally reported a rejection. That reporting is **retracted**. Root-cause
evidence, gathered after the original write-up:

- Querying `NSE:NIFTY26AUGFUT` through the connector, the response's own echoed
  symbol field came back as `NSE:NIFTY26AUGFUT-INDEX` — the connector appended
  `-INDEX` before the request ever reached FYERS.
- Querying `NSE:RELIANCE26AUGFUT` (a stock future, used as an isolation test)
  came back as `NSE:RELIANCE26AUGFUT-EQ` — same bug, different default suffix.
- Both `-INDEX` and `-EQ` are cash/index-segment suffixes; neither is valid on a
  `FUT` contract. FYERS's `"Invalid symbol"` response is correct behavior given
  the (connector-corrupted) input it actually received.
- The exact, uncorrupted string `NSE:NIFTY26AUGFUT` **is** the real
  `fyers_symbol` for the current-month NIFTY future, confirmed by grepping the
  live public NFO symbol master today: `101126082558072,NIFTY 25 Aug 26
  FUT,...,NSE:NIFTY26AUGFUT,...`.

**`_futures_symbol()`'s "provisional" caveat remains open — this audit did not
resolve it, in either direction.** No valid test of futures data access has yet
been performed against FYERS; every attempt so far has actually tested the MCP
connector's symbol handling. §11 below prepares the correct verification path:
executing `bujji/broker/fyers.py` directly (which does not mangle symbols) with
a refreshed token.

**Continuous-contract / rollover stitching:** No code implementing rollover detection or continuous-contract stitching exists anywhere in the repository (grep for "rollover", "continuous", "stitch" in `bujji/` returns nothing outside this audit's own vocabulary). This remains true independent of the connector-bug finding — it was verified by filesystem inspection, not by the failed API calls.

**Implication for volume intelligence (the reason futures matters at all per the Data Doctrine):** VWAP, volume profile, accumulation/distribution, and OI-price relationships as described in the doctrine remain **unconfirmed either way** — neither shown reachable nor shown blocked. This is now a pending-verification item, not a known gap.

---

## 3. India VIX — Historical Availability

| Resolution | Result |
|---|---|
| Daily | Same 366-day chunking cap as NIFTY spot applies (`code -50` on a 1998–2026 request) — LIVE-VERIFIED, same mechanism, same fix (chunked backfill). India VIX itself has only existed since **2007-11-01** (NSE launch date, a known external fact, not verified via API this pass), so full-history backfill is bounded by that, not by FYERS. |
| 1-minute | Not directly tested this pass — a same-day (2026-08-11) 1-min request was issued but its result exceeded the tool's output-size limit before being read, and was not re-run to conserve budget. NEEDS FURTHER VERIFICATION, though there is no structural reason to expect it to behave differently from NIFTY spot's 1-min data (both are `index_val`-class instruments per the session's known `map.json` schema). |

**Conclusion:** Sufficient for daily VIX percentile/regime calculations back to VIX's actual 2007 launch, once chunked-backfill is built. Intraday VIX depth is unconfirmed but not expected to differ structurally from NIFTY spot's ~2018 cutoff.

---

## 4. Options Historical Data — AMENDED: unverified due to connector defect, not a contradiction

`fyers.py:328-361`'s `get_option_candles()` carries a very specific claim:

> *"VERIFIED LIVE 2026-07-19 ... a real NIFTY ATM CE showed 75/75 candles with real volume (2M-12M range) across a full session, zero zero-volume candles."*

This audit originally reported that re-testing the same code path live today
against `NSE:NIFTY2681829350CE` produced `"Invalid symbol"` and treated this as
a contradiction of the 2026-07-19 claim. **That conclusion is retracted.** The
same connector defect documented in §2 applies here: a direct check of
`NSE:NIFTY2681829350CE` through the connector shows its echoed symbol came back
as `NSE:NIFTY2681829350CE-INDEX` — again a wrongly-appended cash/index suffix on
a contract that has neither an index nor an equity segment. The `"Invalid
symbol"` response was FYERS correctly rejecting connector-corrupted input, not
FYERS rejecting a real option contract.

**No valid test of options historical data has been performed.** The
2026-07-19 in-code claim is neither confirmed nor contradicted by this audit —
it stands as previously written, unverified by today's (failed, connector-side)
attempt. §11 prepares the correct re-verification path.

---

## 5. Data Truth Classification (design only — matches user's mandate)

Two-tier taxonomy, to be stamped on every stored dataset going forward:

```
SOURCE   ∈ {FYERS_HISTORICAL_REST, FYERS_WEBSOCKET_LIVE}
REALITY  ∈ {BROKER_HISTORICAL, LIVE_OBSERVED}
```

- `BROKER_HISTORICAL`: fetched via the `historical` REST endpoint. Third-party
  (FYERS-computed) candles — not something Bujji observed itself. Depth varies
  sharply by resolution and instrument class (§1–§4 above).
- `LIVE_OBSERVED`: captured tick-by-tick from the WebSocket feed as it happens
  (the Gate-1-gated path). Starts empty; grows only from the moment capture begins.

**Non-mixing rule:** any derived value (candle, level, indicator) must carry exactly
one `REALITY` tag inherited from 100% of its inputs. A candle built by resampling
live ticks is `LIVE_OBSERVED`; one fetched from `get_recent_candles()`/
`get_option_candles()` is `BROKER_HISTORICAL`. The two must never be concatenated
into one series without an explicit, visible seam — this reuses the session's
existing `Lineage`/`data_class` contract (`bujji/epistemics/lineage.py`) rather than
inventing a parallel mechanism; `SOURCE`/`REALITY` become two additional fields on
that same dataclass, not a new one.

---

## 6. Storage Principle Audit

No raw-market-data storage exists yet outside `bujji/market_timeseries/` (Phase 15Q),
which stores **derived 5-min candles resampled from live ticks** — itself downstream
of raw data, and already `LIVE_OBSERVED`-only by construction (it has no broker-
historical ingestion path). This is consistent with the doctrine's "immutable raw
data" principle only insofar as nothing currently violates it — there is simply
nothing yet that stores raw ticks or raw broker-historical candles as an
independent, append-only, never-modified layer beneath `market_timeseries`.
**No storage code should be built from this audit alone** — per the task's explicit
instruction — but the eventual raw layer must sit *below* `CandleStore`, not beside
or inside it, so `CandleStore` remains a reproducible derived view.

---

## 7. Assessment Against Required Future Queries

| Doctrine requirement | Can it be answered today? |
|---|---|
| Price memory (multi-year swing highs/lows, S/R zones) | **Yes, immediately buildable** — NIFTY spot daily history (§1) is deep enough (28 years) once chunked-backfill exists. This is the one clearly unblocked path. |
| Volume memory (futures volume, VWAP, volume profile) | **Unverified** — §2's original "blocked" finding is retracted; futures data access has not actually been tested yet (only the broken connector has). Pending §11. |
| OI memory (change in OI over time, per strike) | **Partially known, partially unverified** — `get_option_chain()` gives a confirmed-working live snapshot (verified 2026-07-20); historical OI time series is unverified (not contradicted) pending §11. |
| Volatility memory (VIX percentile/regime) | **Yes, for daily** — §3 gives a clear, chunkable path to 2007. Intraday VIX unconfirmed but non-blocking for percentile/regime work, which is inherently daily-cadence. |
| Similarity memory (fingerprint retrieval) | Not assessed here — explicitly out of scope (Phase 16K already covered this; unaffected by data-reality findings). |

---

## 8. Explicit Non-Scope Reminder

Per the task's instruction, this audit did **not** create any RSI/MACD/level/pattern
database — those are derived views belonging to later phases. This document covers
only the raw-acquisition foundation.

---

## 9. Recommended Acquisition Strategy

**Can start immediately, no Gate 1 dependency:**
- Chunked daily NIFTY spot backfill, 1998→present (§1) — mechanical, ~28 API calls at
  366-day windows.
- Chunked daily India VIX backfill, 2007→present (§3) — same mechanism.
- Both feed directly into 17B (level engine) and 17D (IV percentile) as already
  proposed prior to this audit.

**Blocked only by token / connector bypass — unverified, not confirmed unavailable
(amended; see §11 for the prepared verification plan):**
- NIFTY Futures (§2) — `_futures_symbol()`'s construction was never actually tested
  against FYERS; every attempt so far tested the MCP connector's symbol corruption
  instead. Needs a refreshed access token and direct execution of
  `bujji/broker/fyers.py`'s `get_futures_quote()` plus a new
  futures-historical-candles call, on the VPS, bypassing the connector.
- Options historical data (§4) — does **not** contradict the repo's 2026-07-19
  "LIVE-VERIFIED" `get_option_candles()` claim (that claim was made through the real
  SDK, not the connector). Still needs a fresh re-verification run since three weeks
  have passed, but as a routine re-check, not a contradiction chase.

**Still requires Gate 1 (live tick capture), unchanged from prior analysis:**
- Everything under `LIVE_OBSERVED` — futures-based intraday volume intelligence
  (once/if §11's futures verification succeeds), OI-behavior-over-time at tick
  resolution, and Bujji's own candle generation as an independent cross-check against
  `BROKER_HISTORICAL`.

---

## 10. Architecture Implications

1. **The Data Doctrine's biggest near-term blocker is not a confirmed FYERS-side
   futures/options data-access gap — it is that futures/options access has never
   actually been tested against FYERS.** Every prior attempt (this audit's original
   run, and earlier same-day queries) went through the MCP connector, which corrupts
   derivative symbols before they reach FYERS (see amendment, top of document). The
   real blocker is operational: an expired/unrefreshed token and no direct-SDK
   verification run yet. §11 prepares that run.
2. Spot-price and VIX daily history are the only two data classes confirmed deep
   and reliable today — the acquisition strategy should start there while §11's
   verification is prepared and (once the user refreshes the token) executed.
3. `_futures_symbol()`'s docstring should remain "provisional" / "not live-verified"
   — the earlier plan to mark it "live-verified as currently rejected" is retracted,
   since no valid rejection was ever observed; the string was never actually sent to
   FYERS unmangled. Leave the docstring as-is until §11's real SDK test runs.
4. `get_option_candles()`'s 2026-07-19 "VERIFIED LIVE" docstring claim is **not**
   contradicted by anything found this session — the contradicting evidence was
   itself an artifact of the connector bug, not of the SDK path the original claim
   was made through. No documentation change is needed here. The general lesson
   about stale confidence still holds as a process point: any live-verification
   claim (via `calc_version`/lineage-style pinning, Phase 16D/16C) should record
   *which* execution path (SDK vs. connector) was used, since this session shows
   different paths to the same broker can produce contradictory results for reasons
   that have nothing to do with the underlying data's true availability.

---

## 11. Prepared (Not Executed) Direct-SDK Verification Plan

Per explicit instruction: **this section is a plan and a script, neither of which
has been run.** Execution requires the operator to first refresh the FYERS access
token (PIN entry is the user's/operator's action — not something this audit
performs or requests be done on its behalf). Once refreshed, the script below can be
run on the VPS directly against `bujji/broker/fyers.py` (real `fyers-apiv3` SDK),
bypassing the MCP connector entirely, so its results are not subject to the
connector's symbol-corruption bug.

### 11.1 What this verifies

Six items, exactly as specified:

1. **Token health** — is there currently a valid, non-expired access token, and if
   not, can one be refreshed without operator interaction (i.e. is a refresh token
   present and unexpired)?
2. **NIFTY futures quote** — does `get_futures_quote()` (or a direct `ltp`/`quote`
   call with `_futures_symbol()`'s output) return a real quote for the current-month
   contract (`NSE:NIFTY26AUGFUT`, confirmed to exist in the public symbol master,
   §-independent evidence gathered this session), or does FYERS reject it?
3. **NIFTY futures historical candles** — no such method exists yet in
   `bujji/broker/fyers.py`. The plan below adds a thin, read-only test call using the
   same `historical` REST endpoint pattern as `get_recent_candles()`, with
   `NSE:NIFTY26AUGFUT` and `resolution="D"`, to check whether historical futures
   candles are returned at all (even a short window) before deciding whether a
   permanent `get_futures_candles()` method is worth building.
4. **Option contract quote** — does a live quote request for a real, currently
   listed contract (e.g. `NSE:NIFTY2681829350CE`, confirmed to exist in the public
   symbol master) succeed via the SDK path?
5. **Option historical candles** — re-run `get_option_candles()` (the method whose
   docstring claims 2026-07-19 live verification) against a currently live ATM
   contract, to obtain a *current*, dated re-confirmation rather than relying on a
   three-week-old claim.
6. **OI availability** — for both futures (item 2/3) and options (item 4/5), check
   whether the response payload includes an `oi` field with a plausible non-null
   value, and for `get_option_chain()` specifically, whether the OI-consistency
   check from the 2026-07-20 verification (`oich == oi - prev_oi`) still holds today.

### 11.2 Prepared script

Saved, **not executed**, at
`/private/tmp/claude-501/-Users-gowlikar-Downloads/73d55feb-4927-4f0a-825f-f7a11f3822f2/scratchpad/verify_fo_access.py`
(a copy may be placed on the VPS at `/opt/bujji/app/scripts/verify_fo_access.py` for
the operator to run manually once the token is refreshed). The script:

- Imports `bujji/broker/fyers.py` directly — never touches the MCP connector.
- Step 1 checks token health via the existing `TokenManager` (`can_refresh()` /
  whatever health-check surface it exposes at
  `bujji/broker/fyers_token_manager.py:65`) and **aborts immediately, printing a
  clear message, if no valid token is available** — it does not attempt to prompt
  for or handle a PIN itself.
- Steps 2–6 are read-only GET-style calls only (`get_futures_quote()`, a historical
  candles probe, `get_option_chain()`/quote for the confirmed contract,
  `get_option_candles()`) — no orders, no state mutation, nothing that touches
  `bujji`'s live trading path.
- Each step prints the raw response, an explicit PASS/FAIL/UNCERTAIN verdict, and
  the reason, so results can be pasted directly into a follow-up audit rather than
  re-interpreted from logs.
- The script is not wired into any scheduler, CI job, or runtime path — it exists
  only to be run manually, once, by the operator's explicit go-ahead.

### 11.3 Explicit non-actions

This audit does **not**: request the `FYERS_PIN`, attempt a token refresh, execute
the script above, modify `bujji/broker/fyers.py`, or build any storage/intelligence
component. All of that remains gated on the user reviewing this amendment and
performing the token refresh themselves.

---

## 12. Standing Lesson: The Observation Chain Must Be Checked at Every Layer

This session's error — reporting a FYERS-side capability gap that was actually a
connector-side symbol-corruption bug — happened because the audit checked only
"did the API call succeed," not each layer between source and result. Per the
user's explicit instruction, every future data-availability audit in this project
must separately verify:

1. **Source capability** — does the underlying provider (FYERS) actually support
   the data being requested, in principle?
2. **Transport capability** — does the specific access path used (MCP connector vs.
   direct SDK vs. REST) faithfully carry the request?
3. **Symbol translation** — is the instrument identifier reaching the source
   unmodified, or is some intermediate layer rewriting it (as the MCP connector was
   found to do here)?
4. **Authentication** — is the token/session actually valid at the time of the
   test, and is an auth failure being misread as a data-unavailability failure?
5. **Response validation** — does the raw response's echoed fields (e.g. the
   symbol name FYERS echoes back) match what was intended, so a silent
   transformation upstream can be caught before its downstream error is
   misattributed?

A negative result at any single layer can fabricate a false "data unavailable"
conclusion for the whole chain, as happened in §2/§4's original (now retracted)
text. Future audits should log which layer(s) were actually tested, not just the
end-to-end pass/fail.
