# Phase 17A.5 — Market Data Source Certification

**Status as of this document's writing: NO CERTIFICATION HAS BEEN RUN.** This
phase defines the certification framework and prepares (but does not execute)
the certification script. Every artifact under `data_certification/` is
currently a `PENDING` template, not a result.

## Doctrine

> Data availability is not proven by code existence. Data availability is
> proven only by successful observation through the complete production path.

A method existing in `bujji/broker/fyers.py`, a docstring claiming
"LIVE-VERIFIED," or a successful call through *some* access path (e.g. the MCP
connector) is not certification. Certification requires the full chain below
to be exercised, end to end, through the path Bujji's own runtime actually
uses in production:

```
REAL MARKET
   ↓
BROKER SOURCE        (FYERS)
   ↓
TRANSPORT             (which client: MCP connector vs. direct fyers-apiv3 SDK)
   ↓
SYMBOL / CONTRACT RESOLUTION   (was the requested symbol what the broker received?)
   ↓
AUTHENTICATION         (was the token actually valid at test time?)
   ↓
API RESPONSE
   ↓
VALIDATION             (does the response's own echoed fields match the request?)
   ↓
STORAGE READINESS       (is the shape usable by RawTickStore/CandleStore as designed?)
```

This gate exists because of a concrete, documented failure: the Phase 17A
audit's original conclusion — "FYERS does not provide futures/options
data" — was false. The actual truth, discovered only by isolating a second,
unrelated instrument and inspecting the response's echoed symbol field, was
"the MCP connector corrupted derivative symbols before they reached FYERS."
See `docs/PHASE_17A_DATA_REALITY_AUDIT.md`'s amendment for the full account.
A test that only checks "did the call return 200 / did it raise" cannot catch
this class of error — it requires checking the *symbol translation* and
*response validation* links specifically.

## Certification Record Schema

Each certified (or attempted) data source produces one JSON file under
`data_certification/`:

```json
{
  "timestamp": "ISO-8601 UTC, when the test was actually run",
  "broker": "fyers",
  "access_method": "direct_sdk_fyers_broker_py | mcp_connector | ...",
  "instrument": "NIFTY_SPOT | NIFTY_FUTURES | NIFTY_OPTION_CE | INDIA_VIX | ...",
  "symbol_requested": "the exact symbol string sent",
  "symbol_returned": "the exact symbol string echoed back by the broker response, or null if not applicable/observable",
  "quote_status": "OK | NO_DATA | ERROR | SYMBOL_MISMATCH | NOT_TESTED_SEPARATELY | UNTESTED",
  "historical_status": "OK | NO_DATA | ERROR | UNTESTED",
  "volume_available": true | false | null,
  "oi_available": true | false | null,
  "timestamp_valid": true | false | null,
  "validation_result": "CERTIFIED_AVAILABLE | NOT_CERTIFIED | PENDING",
  "limitations": ["free-text notes — caveats, partial results, what wasn't tested"]
}
```

`symbol_requested` vs. `symbol_returned` mismatch is treated as an automatic
`NOT_CERTIFIED`, regardless of whether the call itself "succeeded" — this is
the specific check that would have caught the MCP connector bug immediately,
instead of it surfacing as a false "unavailable" conclusion three weeks later.

## Certification Tests Required

| # | Instrument | Verify | Expected status |
|---|---|---|---|
| 1 | NIFTY Spot | current quote, daily historical, intraday historical, OHLC consistency, timestamp correctness | AVAILABLE (already partially certified via direct-SDK calls documented in `bujji/broker/fyers.py`'s own verified-live comments; §17A.5 re-runs it as the harness's control case) |
| 2 | India VIX | historical availability, daily candles, timestamp correctness | AVAILABLE |
| 3 | NIFTY Futures | quote (LTP, volume, OI, bid/ask if available), daily historical, intraday historical, continuous-futures support, OI history, symbol-integrity check (`NSE:NIFTYxxxxxFUT` in, same derivative contract out — no silent `-EQ`/`-INDEX`) | BLOCKED UNTIL TOKEN REFRESH — target after refresh: AVAILABLE or NOT_CERTIFIED with a concrete reason, never "unknown" |
| 4 | Options (e.g. NIFTY weekly ATM CE/PE) | identity (underlying/expiry/strike/type), OHLC, volume, OI, bid/ask, candle history, OI history | BLOCKED UNTIL TOKEN REFRESH — same target |

## Architecture Rule

**Certified data enters the Market Reality Database. Non-certified data
cannot feed MSI, MIC, strategy engines, memory, or decision synthesis.**

Concretely: any future Phase 17B (Market Reality Database) ingestion pipeline
must check `validation_result == "CERTIFIED_AVAILABLE"` for a given
instrument/access-method pair before that pipeline is allowed to write to
immutable storage from it. A `PENDING` or `NOT_CERTIFIED` source is not wired
into storage, even provisionally, even for "just testing the schema" — Phase
17A's own root-cause was exactly this kind of provisional trust turning into a
false conclusion once written down.

## Certification Artifacts (current status: all PENDING)

- `data_certification/fyers_nifty_spot_certification.json`
- `data_certification/fyers_nifty_future_certification.json`
- `data_certification/fyers_option_chain_certification.json`

Each currently contains `"validation_result": "PENDING"` and
`"limitations": ["Certification script not yet executed — see scripts/verify_fo_access.py and §Verification Script below."]`
placeholders. They will be overwritten in place by `scripts/verify_fo_access.py`
once the operator authorizes and runs it (the script writes real values to
these exact three filenames, plus prints a JSON summary to stdout).

## Verification Script Review (`scripts/verify_fo_access.py`)

**A full 9-point static audit and the exact operator run procedure now live
in `docs/PHASE_17A5_CERTIFICATION_RUNBOOK.md`** — including three real gaps
found and fixed in a second review pass (symbol-transformation detection on
the futures leg, timestamp/OHLC integrity validation, and a third
`PARTIAL_CERTIFICATION` outcome state). The summary below is the original,
first-pass review; treat the runbook as authoritative for current script
behavior.

Reviewed and revised this session against the actual `bujji/broker/fyers.py`
and `bujji/core/config.py` source (not assumed) before being treated as ready
to hand to the operator:

**Corrections made during this review:**
- The private `_call()` action name for historical candle requests is
  `"historical"`, not `"history"` — the script's initial draft used the wrong
  action name; fixed to match `get_recent_candles()`/`get_option_candles()`'s
  actual usage (`fyers.py:282-364`).
- Token health has no separate `TokenManager.can_refresh()` entrypoint
  reachable the way the first draft assumed (`FyersTokenManager` takes
  five positional constructor args, not a `BrokerConfig` object, and is
  normally constructed internally by `FyersBroker.__init__`). The script now
  uses `FyersBroker.connect()` directly — which already validates the token
  via a `profile` call and performs the SDK's built-in refresh_token-based
  renewal internally if `refresh_token`/`app_secret`/`pin` are configured —
  as the token-health check, rather than duplicating that logic.
- Config construction now goes through `AppConfig.load("config/config.yaml")`,
  the app's real env-overlay path (`bujji/core/config.py:201-224`, which reads
  `FYERS_APP_ID`/`FYERS_ACCESS_TOKEN`/`FYERS_APP_SECRET`/
  `FYERS_REFRESH_TOKEN`/`FYERS_PIN`/`FYERS_CREDENTIALS_FILE` from the
  environment), instead of a bare `BrokerConfig(name="fyers")` construction
  that would leave every secret field `None`.
- `OptionContract` is now constructed with all six required fields
  (`symbol`, `underlying`, `strike`, `option_type`, `expiry`, `lot_size`)
  matching the real dataclass in `bujji/core/models.py`, using
  `OptionType.CE` from `bujji/core/enums.py` rather than a raw string.

**Confirmed properties (checklist per your instruction):**
- **Correct broker layer usage** — calls go through `FyersBroker` methods
  (`get_futures_quote`, `get_option_candles`, `get_option_chain`,
  `get_recent_candles`) and the same private `_call()`/action-name pattern
  those methods use internally for the one probe (futures historical
  candles) that has no dedicated method yet. Never touches
  `mcp__fyers__*` connector tools.
- **No hidden assumptions** — every place the script relies on something not
  independently confirmed this session (exact `AppConfig.load()` path in a
  given deployment, whether `config/config.yaml` is the right file) is
  flagged inline as a comment for the operator to check before running,
  rather than silently guessed.
- **Read-only behaviour** — every broker call used is a GET-style
  quote/history/chain read. `place_order`, `cancel_order`, and any other
  mutating method are never imported or referenced.
- **No order capability** — confirmed by inspection: the script's only
  imports from `bujji.broker.fyers` are the class itself; it never calls
  `place_order`/`cancel_order`/`modify_order`.
- **No mutation** — the only writes the script performs are its own
  certification JSON files under `data_certification/`; it does not modify
  `bujji/broker/fyers.py`, any config file, or any trading/position state.

**Still NOT executed.** Per your instruction, this review improves the script
but does not run it. Execution remains gated on the operator refreshing the
FYERS token and giving explicit go-ahead.

## Gate Status

| Gate | Status |
|---|---|
| NIFTY Spot certified | **Not yet run this phase** (strong existing evidence from live-verified docstrings in `fyers.py`, but no formal certification artifact exists until the script runs once, as the harness's own control case) |
| India VIX certified | Not yet run |
| NIFTY Futures certified | **Blocked** — awaiting token refresh |
| Options certified | **Blocked** — awaiting token refresh |
| Phase 17B (Market Reality Database design) | **Not started** — gated on the above |

No indicator, strategy, or intelligence-layer work has been started under
this phase. Only this document and the three `PENDING` certification
templates, plus the reviewed-and-corrected (still unexecuted)
`scripts/verify_fo_access.py`, exist as of this writing.
