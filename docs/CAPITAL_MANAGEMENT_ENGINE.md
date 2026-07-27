# Capital Management Engine (CME)

Platform subsystem. Strategy-independent. Every present and future strategy
must call `CapitalManagementEngine.approve_trade()` instead of calculating a
quantity directly — no strategy module multiplies `lots * lot_size` anymore.

## 1. Architecture

```
bujji/capital/
    exceptions.py    -- CapitalUnverifiedError, MarginCalculatorUnavailableError,
                         BrokerCapitalQueryError, CapitalRejectedError
    models.py         -- CapitalSnapshot, MarginRequirement, SizingDecision,
                         CapitalStatus (SAFE/WARNING/BLOCKED/UNVERIFIED)
    broker_adapter.py -- translates a Broker's raw funds/margin dicts into
                         the typed models above; the ONLY place that decides
                         whether a broker response can be trusted
    engine.py         -- CapitalManagementEngine.approve_trade() -- the
                         sizing algorithm itself
    health.py         -- publishes a SizingDecision to the dashboard
```

Strategy code never talks to a broker for funds/margin directly:

```
Strategy (Orchestrator._enter)
    -> CapitalManagementEngine.approve_trade(ce_contract, pe_contract)
        -> Broker.get_funds()
        -> Broker.get_order_margin(ce_contract, pe_contract)
    -> SizingDecision (approved_lots, quantity, status, reason, full report)
```

## 2. Sequence diagram (text)

```
Orchestrator._enter()
  │
  ├─ resolve ce_contract, pe_contract  (unchanged -- ATM/expiry resolution)
  │
  ├─► CapitalManagementEngine.approve_trade(ce_contract, pe_contract)
  │     │
  │     ├─► Broker.get_funds()  ──────────────► raises? ─► BLOCKED (funds_query_failed)
  │     │                                        returns None/partial?
  │     │                                        ─► BLOCKED (capital_unverified)
  │     │
  │     ├─► Broker.get_order_margin(ce, pe) ──► raises? ─► BLOCKED (margin_query_failed)
  │     │                                        returns None?
  │     │                                        ─► BLOCKED (margin_unverified /
  │     │                                             LIVE CERTIFICATION REQUIRED)
  │     │
  │     ├─ usable_margin = available_margin * safety_buffer
  │     ├─ maximum_safe_lots = floor(usable_margin / margin_per_lot)
  │     ├─ approved_lots = min(configured_max_lots, maximum_safe_lots)
  │     │
  │     └─► SizingDecision(status, approved_lots, quantity, full report)
  │
  ├─ publish_to_dashboard(status, decision)   -- always, whether approved or not
  ├─ log_event("capital_health", ...) + render() to logs   -- always
  │
  ├─ if not decision.approved:
  │     raise CapitalRejectedError(decision.reason)
  │     └─► caught by Orchestrator._handle_pre_position's except clause
  │           -> FSM rolls back to READY, status.healthy=False, clearly logged
  │           -> NO ORDER WAS EVER PLACED
  │
  └─ else: requested_qty = decision.quantity
        -> proceeds to place CE/PE SELL orders exactly as before
```

## 3. Decision flow / sizing algorithm

```
usable_margin       = available_margin * safety_buffer
maximum_safe_lots   = floor(usable_margin / margin_required_per_lot)
approved_lots       = min(configured_max_lots, max(0, maximum_safe_lots))
quantity            = approved_lots * lot_size

if approved_lots <= 0:  status = BLOCKED
elif utilization >= warning_utilization (default 0.85): status = WARNING
else: status = SAFE

If funds or margin cannot be verified at all: status = UNVERIFIED,
approved_lots = 0, unconditionally -- never computed from a guess.
```

## 4. Failure modes (every one fails toward BLOCKED/UNVERIFIED, never a guess)

| Failure | Detection | Result |
|---|---|---|
| Broker funds call raises (timeout, disconnect) | `BrokerCapitalQueryError` | BLOCKED, `funds_query_failed` |
| Broker funds call returns `None`/partial (missing `account_equity` or `available_margin`) | `CapitalUnverifiedError` | BLOCKED, `capital_unverified` |
| Broker margin call raises | `BrokerCapitalQueryError` | BLOCKED, `margin_query_failed` |
| Broker margin call returns `None` (no margin-calculator available) | handled in `engine.py` | BLOCKED, `margin_unverified` — **LIVE CERTIFICATION REQUIRED** territory |
| `maximum_safe_lots` computes to 0 | normal algorithm path | BLOCKED, `insufficient_margin` |
| Margin requirement changes intraday | N/A — sizing happens **once**, at entry only; never recomputed mid-trade | not applicable — matches "freeze trade identity" invariant |
| Exchange lot-size change | flows through `ce_contract.lot_size` (whatever the resolved contract reports) | reflected automatically in `quantity` |
| Partial fills | unchanged — existing C4 handling sizes off actual fill, independent of the CME | unaffected |

## 5. Recovery behaviour

The entry-time `SizingDecision` is attached to `Position.capital_decision` and
round-trips through `position_codec.py` exactly like every other position
field — a restart resumes with the original approved-lots decision intact,
never re-running sizing against (possibly different) current-day capital.
This matches the "freeze trade identity forever" invariant: sizing, like
strike/expiry/premium, is decided once at entry and never revisited for the
life of the trade.

## 6. Configuration guide

`config.yaml`:
```yaml
risk:
  lots: 1                    # Ceiling -- CME may approve FEWER, never more.
  margin_safety_buffer: 0.90 # Use at most 90% of available margin.
```

`CapitalManagementEngine(broker, logger, safety_buffer=0.90, configured_max_lots=1, warning_utilization=0.85)`
— constructed automatically by `Orchestrator.__init__` from `config.risk.*`
if not injected explicitly (same idiom as `event_bus`).

## 7. Operations guide

- **Reading the dashboard's "Capital Management" section:** shows the most
  recent pre-trade sizing decision — status (SAFE/WARNING/BLOCKED/
  UNVERIFIED), account equity, available margin, margin required per lot
  (and whether it's broker-verified or unverified), safety buffer, maximum
  safe lots, configured ceiling, approved lots, capital utilization,
  remaining margin.
- **If entry is BLOCKED with `entry_blocked_capital`:** check
  `status.health_detail` for the exact reason. The Signal Engine will not
  retry that day regardless (its one-shot latch fires independent of entry
  success) — this is existing, unchanged behavior, not new to the CME.
- **If status is UNVERIFIED:** this means the broker's funds or margin
  response could not be trusted (missing field, broker error). No trade
  will be attempted until this is resolved. In `fyers`/`fyers_paper` mode,
  margin will ALWAYS report unverified until a real margin-calculator
  endpoint is verified against a live FYERS account (see Section 8).

## 8. Broker Adapter Certification Status

| Broker | `get_funds()` | `get_order_margin()` |
|---|---|---|
| `PaperBroker` | Synthetic, fully configurable (test/dev) | Synthetic, fully configurable (test/dev) |
| `ReplayBroker` | Synthetic, schedule-driven for deterministic replay | Synthetic, schedule-driven for deterministic replay |
| `FyersBroker` | ✅ **LIVE-CERTIFIED 2026-07-19** | ✅ **LIVE-CERTIFIED 2026-07-19** |
| `HybridPaperBroker` (`fyers_paper`) | Delegates to the live leg — same certification as `FyersBroker` | Delegates to the live leg — same certification as `FyersBroker` |

**`capital_policy: CERTIFIED`, `margin_provider_certified: true` are now
enabled in `config.yaml`.** `approve_trade()` will use the real, broker-
verified margin figure from `POST https://api.fyers.in/api/v2/span_margin`.

### span_margin Live Certification (2026-07-19)

Certified against a real account, using the existing `FyersBroker.connect()`
path with credentials already configured server-side (never printed/exposed
in chat or logs). Full evidence in `docs/AUDIT_LOG.md` Pass 8. Summary:

**Endpoint:** `POST https://api.fyers.in/api/v2/span_margin`
**Auth header:** `Authorization: "{app_id}:{access_token}"` — confirmed
(HTTP 200 valid / HTTP 401 `code=-17` invalid).

**Request** (real captured payload, 2-leg NIFTY 24350 short straddle):
```json
{"data": [
  {"symbol": "NSE:NIFTY2672124350CE", "qty": 65, "side": -1, "type": 2,
   "productType": "INTRADAY", "limitPrice": 0, "stopLoss": 0},
  {"symbol": "NSE:NIFTY2672124350PE", "qty": 65, "side": -1, "type": 2,
   "productType": "INTRADAY", "limitPrice": 0, "stopLoss": 0}
]}
```

**Response** (real captured, verbatim) — note the margin figures are
nested under `"data"`, which is DIFFERENT from what community forum posts
suggested (a real bug in the pre-certification implementation, now fixed):
```json
{"code": 200, "message": "", "s": "ok", "latency": "",
 "data": {"span": 143177, "expo": 0, "total": 143177, "benefit": 142048.25},
 "individual_info": {
   "101126072157354": {"ltp_info": 115.3, "span": 142048.25, "expo": 0, "total": 142048.25},
   "101126072157355": {"ltp_info": 132.55, "span": 143177, "expo": 0, "total": 143177}
 }}
```

**Multi-leg hedging benefit — confirmed real:** single CE leg alone =
₹142,048.25; CE+PE combined = ₹143,177 total (barely more, not additive) —
the exchange's SPAN benefit for a hedged straddle is genuinely applied, not
merely two legs priced independently.

**Error responses (captured, now regression-tested):**
| Scenario | HTTP | Response |
|---|---|---|
| Invalid symbol | 400 | `{"s":"error","code":-310,"message":"Please provide valid symbols"}` |
| Malformed payload (missing required fields) | 400 | `{"s":"error","code":-50,"message":"Invalid input"}` |
| Invalid/expired token | 401 | `{"s":"error","code":-17,"message":"Could not authenticate the user"}` |

**Repeatability:** two identical back-to-back calls returned byte-identical
responses — deterministic.

**funds() title mapping — corrected after live capture** (the real titles
differ from the pre-certification guess):
| Real title | Maps to |
|---|---|
| `"Total Balance"` | `account_equity` |
| `"Available Balance"` | `available_funds` / `available_margin` |
| `"Clear Balance"` (NOT "Clear Cash" as previously guessed) | `cash_balance` |
| `"Utilized Amount"` | `used_margin` |
| `"Collaterals"` (newly mapped) | `collateral` |

**Regression fixtures:** `tests/test_fyers_span_margin_certified.py` — every
captured response above is now a permanent test fixture; a future SDK or
endpoint change that breaks parsing will be caught immediately.

**Separately discovered during certification (unrelated to margin):**
`FyersTokenManager`'s automatic refresh_token flow — previously verified
working — is now rejected: `code=-16, message="Refresh token API is
currently disabled to comply with SEBI regulations."` This is a new
regulatory restriction, not a code defect. See `docs/FYERS_TOKEN_LIFECYCLE.md`.
Automatic daily token renewal is currently non-functional; the interactive
login flow must be run manually each trading day.
