# Phase 17A.5 — Certification Runbook

**Status: no certification run has occurred.** This is the operator procedure
to follow the one time it is authorized to run. Nothing in this document
authorizes execution by itself — see "Data Certification Gate" doctrine below.

> Never build intelligence on uncertified perception. Bujji's first
> responsibility is not to trade. Bujji's first responsibility is to know
> what is true.

---

## Part 1 — Static Audit of the Certification Process (completed this session)

Before any execution is even considered, `docs/PHASE_17A5_DATA_CERTIFICATION.md`,
`scripts/verify_fo_access.py`, and `data_certification/*.json` were re-audited
against the current, real source of `bujji/broker/fyers.py` and
`bujji/core/config.py` (not assumed from memory of the prior write-up).

| # | Check | Finding |
|---|---|---|
| 1 | Does the script truly remain READ ONLY? | **Yes.** Every call is a quote/history/chain read (`get_futures_quote`-equivalent `_call("ltp", ...)`, `_call("historical", ...)`, `_call("quotes", ...)`, `get_option_candles()`, `get_option_chain()`, `get_recent_candles()`, plus `connect()`'s own `profile` check). No mutating broker call is imported or reachable. |
| 2 | Can it accidentally place orders? | **No.** `place_order`/`cancel_order`/`modify_order` are never imported from `bujji.broker.fyers`, and the script has no code path that constructs an `OrderRequest`. |
| 3 | Can it accidentally mutate broker state? | **No trading-state mutation.** The script's only writes are its own three JSON files under `data_certification/` — no config file, no `bujji/broker/fyers.py`, no position/order state is touched. `cfg.name = "fyers"` (if needed) is an in-memory-only object mutation on a config instance the script itself constructed, never persisted. |
| 4 | Are all API paths using the real FYERS SDK layer? | **Yes.** All imports are from `bujji.broker.fyers`/`bujji.core.*`. The MCP connector (`mcp__fyers__*`) is never imported or called — this is the entire point of the script, given the connector's proven symbol-corruption bug. |
| 5 | Are symbol transformations detected? | **Fixed this session.** The original draft's futures leg used the `get_futures_quote()` wrapper, which only returns a populated result when the broker's echoed symbol already matches the request — a mismatch silently degraded to an indistinguishable "no data," which is exactly the failure mode that hid the original MCP connector bug. The script now calls the raw `_call("ltp", ...)` action directly for futures (matching the pattern already used for options), so `symbol_returned` is always the broker's actual echoed value, and a mismatch is classified `SYMBOL_MISMATCH` → automatic `NOT_CERTIFIED`, never silently absorbed into `NO_DATA`. |
| 6 | Are timestamps validated? | **Fixed this session.** The original draft hardcoded `timestamp_valid: None` everywhere without ever computing it. A `_validate_candles()` helper now checks, on every returned candle set: no future timestamps, no duplicate timestamps, ascending order. Result feeds directly into the validation_result decision (a failed integrity check forces `NOT_CERTIFIED`). |
| 7 | Are empty responses treated as failures? | **Yes, and distinguished from errors.** `"s": "no_data"` (a valid, empty FYERS response) is classified `NO_DATA`, separate from `"s": "error"` → `ERROR`, and separate from a `200`-shaped-but-empty candle list. `NO_DATA` never silently reads as `OK`. |
| 8 | Are partial data responses explicitly marked? | **Fixed this session.** The original draft only had a binary `CERTIFIED_AVAILABLE` / `NOT_CERTIFIED` outcome. A third state, `PARTIAL_CERTIFICATION`, is now implemented via `_classify()` — used when the instrument is reachable (quote or historical succeeded, symbol matched, no integrity failure) but incomplete (e.g. OI/volume/bid/ask missing, or only one of quote/historical succeeded, or candle history too short to confirm multi-date coverage). The classifier always returns exactly one of the three states — never `None`/`"UNKNOWN"`. |
| 9 | Are assumptions documented? | **Yes.** Inline comments flag: the `AppConfig.load("config/config.yaml")` path assumption (verify it matches this deployment before running), and that `FUTURES_SYMBOL`/`OPTION_SYMBOL` are point-in-time correct as of the Phase 17A session and must be re-confirmed against the current near-month/near-expiry contract before running (both roll over — futures monthly, options weekly). |

**Conclusion: three real gaps were found and fixed in this session's static
audit** (items 5, 6, 8) before this script is handed to the operator as
"ready." No execution occurred to find these — this was a pure code read
against the real `bujji/broker/fyers.py` source, applying the same
observation-chain discipline (§12 of `PHASE_17A_DATA_REALITY_AUDIT.md`) the
prior connector-bug incident established.

---

## Part 2 — Certification Run Procedure

### Before run

Operator must confirm, in order:

- [ ] FYERS access token has been refreshed (interactive login, or the
      built-in `refresh_token` renewal if `FYERS_APP_SECRET`/
      `FYERS_REFRESH_TOKEN`/`FYERS_PIN` are configured — `connect()` will
      attempt this automatically, but a refresh_token itself expires after
      ~15 days and cannot self-renew past that).
- [ ] A market session is available (NSE trading hours) for the quote legs —
      historical-candle legs do not strictly require this, but a quote
      returning `NO_DATA` outside market hours should not be misread as a
      broker-side failure; note the run's wall-clock time against NSE hours
      when interpreting results.
- [ ] The correct environment/config is loaded — confirm
      `config/config.yaml` (or whatever path this deployment actually uses)
      is the one `AppConfig.load()` will read, and that `FYERS_APP_ID`/
      `FYERS_ACCESS_TOKEN` (and refresh triplet, if used) are set in the
      shell environment the script runs under.
- [ ] `FUTURES_SYMBOL`/`OPTION_SYMBOL` constants at the top of
      `scripts/verify_fo_access.py` are re-checked against the current FYERS
      NSE_FO symbol master (`public.fyers.in/sym_details/NSE_FO.csv`) — stale
      contract symbols will read as `NO_DATA`/expired, not as a genuine
      capability gap.
- [ ] Confirmed: **no order permissions are required or used** — this run
      only needs quote/history/profile read scopes on the FYERS token.

### During run

The script itself captures, per instrument, into each JSON artifact:

- `symbol_requested` — the exact string sent
- `symbol_returned` — the broker's own echoed symbol (not the request
  string echoed back by the script)
- the raw API response is not persisted verbatim in the artifact (to keep
  it small and stable), but is fully visible in the script's stdout JSON
  summary printed at run time — **capture and save that stdout output**
  (e.g. `python -m scripts.verify_fo_access | tee data_certification/run_log_<timestamp>.txt`)
  as the durable record of the raw responses, latency is not currently
  instrumented by the script (see Known Limitations below)
- `timestamp` — wall-clock UTC at artifact-write time
- which fields were available (`volume_available`, `oi_available`, and for
  options, bid/ask presence noted in `limitations`) vs. missing

**Known limitation:** the script does not currently measure or record
per-call latency. If latency needs to be part of the certification record,
add wall-clock timing around each `_call()`/broker-method invocation before
running — flagged here rather than silently omitted.

### After run

Each of the three artifacts under `data_certification/` will contain exactly
one of:

- `CERTIFIED_AVAILABLE`
- `NOT_CERTIFIED`
- `PARTIAL_CERTIFICATION`

in its `validation_result` field, with `limitations` populated explaining
why, per `_classify()`'s logic (see Part 3 below for the exact acceptance
rule). No ambiguous/blank status is possible — if the script crashes before
writing an artifact, that artifact is left in its prior `PENDING` state,
which itself is a legitimate "not run" signal, distinct from all three
outcomes above.

Only a `CERTIFIED_AVAILABLE` result permits that instrument/access-method
pair to be wired into any future Market Reality Database ingestion path
(Phase 17B), per the architecture rule in
`docs/PHASE_17A5_DATA_CERTIFICATION.md`. `PARTIAL_CERTIFICATION` sources may
inform design discussion but must not be treated as ingestion-ready until
the specific gap noted in `limitations` is closed and re-certified.

---

## Part 3 — Minimum Acceptance Criteria (implemented in `_classify()`)

### Identity
Confirmed via `symbol_requested == symbol_returned` (exact string match on
the broker's own echoed symbol). Any mismatch is an automatic
`NOT_CERTIFIED`, independent of every other signal — this is the specific
check the original MCP-connector incident lacked.

### Market fields
- **Spot:** OHLC + timestamp only (no volume/OI requirement — index has
  neither; a `False` value in those fields is "not applicable," not "gap").
- **Futures:** OHLC (from historical), volume, OI, timestamp. Missing
  volume or OI on the quote leg is recorded as a `limitations` entry and
  forces at minimum `PARTIAL_CERTIFICATION`.
- **Options:** OHLC, volume, OI, bid, ask, timestamp. Same partial-credit
  logic — any one of these missing/zero forces `PARTIAL_CERTIFICATION`
  rather than a full pass.

### Historical
The script requests a 30-day window (futures/spot) or a `count=75` /
1-minute window (options, matching `get_option_candles()`'s existing usage
elsewhere in the codebase) specifically so more than a single date is
present. Fewer than 2 candles returned is flagged in `limitations` and
prevents `CERTIFIED_AVAILABLE`. Ordering and duplicate checks (see next
section) additionally confirm the "no gaps / correct ordering" requirement
to the extent a single run can — a full gap-scan across the entire available
history is out of scope for this script and remains future work if deeper
backfill validation is needed.

### Integrity
`_validate_candles()` checks, on every returned candle set:
- no future timestamps (candle time > run time)
- no duplicate timestamps
- ascending order
- no impossible OHLC (`high < low`, `high`/`low` violating open/close
  bounds, or any non-positive price)

A failed integrity check is a hard `NOT_CERTIFIED`, same severity as a
symbol mismatch — bad data must never be classified as available just
because the call itself "succeeded," per the Data Doctrine's Data Quality
Layer requirement.

---

## Part 4 — Execution

**Not performed.** Per your explicit instruction, Step 4 ("after token
refresh only, run `scripts/verify_fo_access.py`") is not executed as part of
this document. This runbook exists so that when the operator does refresh
the token and give the go-ahead, the run follows a fixed, pre-agreed
procedure rather than an improvised one — and so the resulting artifacts are
interpreted against acceptance criteria that were fixed *before* seeing any
data, not adjusted after the fact to fit whatever the data turned out to
show.
