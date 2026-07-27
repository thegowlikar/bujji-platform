# Runtime Execution Service — Architecture Review

**BUJJI Options OS v3 — Engineering Series 41**
**Type: Research & Architecture Specification. No code was written or modified for this sprint.**

## Read This First

This document is the output of a complete, read-only audit of the
existing production execution stack at `/opt/bujji/app/bujji/` —
`broker/`, `execution/`, `trade/`, `core/`, `journal/`, `ops/`,
`app.py`. Every claim below traces to a real file this sprint actually
read. Nothing here was implemented, replaced, or modified. Production
remains exactly as it was before this sprint began, and the
qualification fingerprint (`/opt/bujji/qualification/baselines.json`)
is unchanged.

The governing rule for this entire document: **integrate with
production, never replace, duplicate, or redesign it.**

---

## Deliverable 1 — What Exists, What's Missing, What to Reuse, What to Never Duplicate

### What already exists (and is genuinely production-grade)

BUJJI already has a working, tested, single-position execution stack
for one broker (FYERS), built around one clean seam:

- **`bujji/broker/base.py::Broker`** — an ABC every broker
  implementation (`FyersBroker`, `PaperBroker`, `HybridPaperBroker`)
  conforms to. This is *the* integration seam.
- **`bujji/execution/engine.py::ExecutionEngine`** — the only module
  that talks to a `Broker`. Owns retry-with-backoff, idempotent order
  placement, partial-fill polling, and reconciliation. This is a real,
  working reliability layer, not a stub.
- **`bujji/core/orchestrator.py::Orchestrator._recover()`** — a
  genuine, four-branch crash-recovery routine that reconciles a
  persisted session snapshot against live broker-reported positions on
  every process start, including an orphan-position flattening path.
- **`bujji/core/state_machine.py`** — a small, enforced FSM
  (`WAITING → READY → CONFIRMED → IN_POSITION → EXITING →
  DONE_FOR_DAY`) with a separate, audited `restore()` bypass used only
  by recovery.
- **`bujji/broker/fyers_token_manager.py::FyersTokenManager`** — daily
  access-token refresh via FYERS's refresh-token grant, with an
  atomic, `fsync`-then-`rename` credential persistence write.
- **`bujji/ops/health_monitor.py` / `ops/alerts.py`** — a real
  observability layer: auth-expiry tracking, candle staleness,
  restart-frequency, exception-rate, memory, disk, journal-write
  health, all with named WARNING/CRITICAL thresholds and
  state-transition-only alert dedup.
- **A working paper/hybrid-paper safety mechanism**
  (`broker/guard.py::disable_live_execution`, `broker/hybrid.py`) that
  neuters a broker instance's execution methods at the instance level
  before it is ever wired into anything else.
- **One-way, append-only JSONL journals** for every subsystem
  (`journal/*.py`), each independent and never read by any other
  journal.

### What is missing

- **No circuit breaker or rate limiter anywhere.** Retry-with-backoff
  (`ExecutionEngine._with_retry`) is the *only* resilience primitive.
  A sustained FYERS outage simply exhausts `retry_attempts` and raises
  on every call, with no cooldown/trip state remembered between calls.
- **Idempotency cache does not survive restart.**
  `FyersBroker._cid_to_order_id` is an in-memory dict; the fallback
  (scanning today's `orderbook()` by `orderTag`) is explicitly flagged
  in production code as **unverified against a live FYERS account** —
  no real order has ever been placed to confirm FYERS actually echoes
  `orderTag` back.
- **No multi-position bookkeeping.** `TradeManager` manages exactly
  one open straddle at a time; this is a deliberate current
  limitation, not an oversight, but it constrains what a Runtime
  Execution Service can assume.
- **One known architectural wart, already flagged in production
  comments**: `app.py::_candle_loop()` calls
  `self._exec._broker.get_recent_candles` directly, bypassing
  `ExecutionEngine`'s retry wrapper (tracked internally as audit item
  "N6"). Any Runtime Execution Service work should not deepen this
  wart, and could plausibly be the natural place to finally close it —
  but that decision belongs to a future series, not this one.
- **No standalone health/liveness HTTP endpoint** distinct from the
  existing `DashboardServer` — `ops/health_monitor.py` computes a
  snapshot, but nothing yet exposes it as a service-level heartbeat a
  separate runtime process could poll.

### What must be reused, verbatim

- `Broker` ABC and every existing concrete broker
  (`FyersBroker`/`PaperBroker`/`HybridPaperBroker`).
- `ExecutionEngine`'s retry, idempotency, and reconciliation logic.
- `core/models.py`'s `OrderRequest`/`OrderResult`/`OptionContract`
  shapes — these are the only order-shaped types that exist in
  production; a Runtime Execution Service must construct these, never
  invent parallel ones.
- `core/state_machine.py`'s FSM and its `restore()` recovery bypass.
- The existing journal pattern (one file per subsystem, append-only,
  independent).
- `Orchestrator._recover()`'s four-way reconciliation logic as the
  canonical restart-recovery algorithm.

### What must never be duplicated

- A second retry/backoff implementation. `ExecutionEngine._with_retry`
  is the one and only retry primitive in this codebase; a Runtime
  Execution Service must call through it, not reimplement it alongside
  it.
- A second idempotency key scheme. `OrderRequest.client_order_id` is
  already documented in `core/models.py` as *the* idempotency key.
  Nothing should introduce a second, competing identifier.
- A second broker abstraction. The `Broker` ABC already exists and is
  the seam every future broker (Zerodha, etc.) is meant to implement.
- A second position/order state machine. `core/state_machine.py`'s
  `State` enum is the one lifecycle authority; the Broker Adapter's
  own `ExecutionInstructionSet`/`BrokerExecutionRequest` statuses
  (Series 39/40) are Trading-Brain-side workflow states, not
  broker-side order states, and must not be confused with or merged
  into this one.
- A second recovery routine. `Orchestrator._recover()` already
  reconciles saved state against live broker truth on every restart;
  a Runtime Execution Service integrates with this, it does not stand
  up a competing recovery path.

---

## Deliverable 2 — Architecture Diagram

```
Trading Brain (bujji/trading_brain/, Series 31-39)
        │
        ▼
Broker Adapter (bujji/broker_adapter/, Series 40 — pure translation, no I/O)
        │
        │  BrokerExecutionRequest (abstract operations: PREPARE_PLACE_ORDER, ...)
        ▼
Runtime Execution Service   ← NOT YET BUILT (Series 42+)
        │
        ├── Authentication            -> REUSES broker/fyers_token_manager.py::FyersTokenManager
        ├── Token Refresh             -> REUSES same (daily refresh-token grant)
        ├── Broker Session            -> REUSES broker/factory.py::build_broker() + broker/base.py::Broker
        ├── Order Dispatcher          -> REUSES execution/engine.py::ExecutionEngine.submit_and_confirm()
        ├── Retry Manager             -> REUSES execution/engine.py::ExecutionEngine._with_retry() (do not reimplement)
        ├── Fill Monitor              -> REUSES execution/engine.py::ExecutionEngine._await_fill()
        ├── Position Reconciliation   -> REUSES execution/engine.py::ExecutionEngine.reconcile()
        │                                 + core/orchestrator.py::Orchestrator._recover()'s four-way match
        ├── Startup Recovery          -> REUSES Orchestrator._recover() pattern (adapted, not duplicated)
        ├── Circuit Breaker           -> MISSING IN PRODUCTION -- new, Series 46 (see Gap Analysis)
        ├── Health Monitor            -> REUSES ops/health_monitor.py::HealthMonitor + ops/alerts.py
        └── Audit Journal             -> REUSES journal/ append-only JSONL pattern; new dedicated
                                          runtime_execution_journal.py mirroring the existing convention
        │
        ▼
Broker SDK (fyers-apiv3, via broker/fyers.py — UNCHANGED)
        │
        ▼
Exchange
```

Every box under "Runtime Execution Service" either points at an
existing production module to be reused as-is, or is explicitly
flagged as a genuine gap. Nothing in this diagram proposes a parallel
broker stack.

---

## Deliverable 3 — Component Map

For every existing component, this table records Purpose / Owner /
Dependencies / Inputs / Outputs / Current Maturity / and the
disposition this sprint recommends (Reuse / Replace / Extend / Leave
Untouched).

| Component | Purpose | Owner (file) | Dependencies | Inputs | Outputs | Maturity | Disposition |
|---|---|---|---|---|---|---|---|
| `Broker` ABC | Defines the one seam every broker implements | `broker/base.py` | none | — | — | High — stable contract, documented invariants (idempotency, `AuthenticationError` distinctness, position-shape guarantee) | **Reuse verbatim** |
| `build_broker()` | Constructs the correct broker for `config.broker.name` | `broker/factory.py` | `Broker` subclasses | `AppConfig`, `logger` | `Broker` instance | High — 3 modes (`paper`/`fyers`/`fyers_paper`), well-tested guard-wiring for hybrid mode | **Reuse verbatim** |
| `FyersBroker` | Live FYERS execution/data adapter | `broker/fyers.py` | `fyers-apiv3` SDK, `InstrumentMaster` | `OrderRequest`, symbols | `OrderResult`, candles, quotes | Medium-High — auth/data paths live-certified against a real account; **order placement/status/cancel paths are UNVERIFIED — no live order has ever been placed** | **Reuse; treat order-path verification as a prerequisite, not an assumption, before any live order flows through it** |
| `FyersTokenManager` | Daily access-token refresh via refresh-token grant | `broker/fyers_token_manager.py` | FYERS validate-refresh-token endpoint | stored refresh token, pin | new access/refresh token pair, persisted atomically | High — atomic persistence engineered around `PrivateTmp` non-atomicity; refresh-token itself expires ~15 days and requires human interactive re-login (not automatable) | **Reuse verbatim**; the Runtime Execution Service's "Authentication"/"Token Refresh" boxes are this component, not a new one |
| `FyersTickFeed` | Live WebSocket tick stream | `broker/fyers_ws.py` | `fyers-apiv3` `FyersDataSocket` | app_id:access_token | latest-tick dict | Medium — relies entirely on the SDK's own `reconnect=True`; no custom backoff | **Reuse; note as a gap that reconnect behavior is entirely SDK-owned, unverified under real network partition** |
| `disable_live_execution()` / `HybridPaperBroker` | Guarantees paper mode never touches real capital | `broker/guard.py`, `broker/hybrid.py` | `Broker` | a live broker instance | a neutered/composed broker instance | High — instance-level monkeypatch applied redundantly (factory + `HybridPaperBroker.__init__`) | **Reuse verbatim; this is the safety mechanism any Runtime Execution Service testing must run under** |
| `PaperBroker` | Deterministic in-memory broker simulator with fault injection | `broker/paper.py` | none | `OrderRequest` | `OrderResult` | High — explicit idempotency check by `client_order_id`, fault-injection hooks (`partial_fill_qty`, `raise_on_place_after_record`, `simulate_auth_expiry`) | **Reuse verbatim as the test harness for every Runtime Execution Service behavior before it ever touches `FyersBroker`** |
| `ExecutionEngine` | The only module permitted to call a broker; owns retry, idempotency, fill-polling, reconciliation | `execution/engine.py` | a `Broker`, `AppConfig` | `OrderRequest` | `OrderResult` | High — real, tested reliability layer (`submit_and_confirm`, `_await_fill`, `reconcile`) | **Reuse verbatim; this IS most of the "Order Dispatcher", "Retry Manager", "Fill Monitor", and "Position Reconciliation" boxes in the target diagram** |
| `TradeManager` | Single-position exit-condition evaluation | `trade/manager.py` | a `Position`, candles | candle + premium | `TradeDecision` | High — but explicitly single-position; out of scope for a broker-facing Runtime Execution Service (this is Trading Brain-adjacent decision logic, not execution plumbing) | **Leave untouched; not a dependency of the Runtime Execution Service** |
| `Orchestrator._recover()` | Four-way reconciliation of saved session vs. live broker truth on every restart | `core/orchestrator.py` | `SessionStore`, `ExecutionEngine.reconcile()` | persisted `SessionSnapshot`, live positions | resumed/flattened/clean-slate FSM state | High — genuinely handles the orphan-position case (flattens every live short found, not just the first) | **Reuse the algorithm; the "Startup Recovery" box should call an equivalent routine, not a bespoke one** |
| `core/state_machine.py::State`/`StateMachine` | Order/position lifecycle FSM | `core/state_machine.py` | none | transition requests | current `State` | High — small, enforced, with an explicitly separate audited `restore()` path | **Reuse verbatim as the one lifecycle authority** |
| `ops/health_monitor.py::HealthMonitor` | Computes an `OpsSnapshot` (auth, staleness, restarts, exceptions, memory, disk, journal health, WS connectivity) | `ops/health_monitor.py` | `RuntimeStatus`, process signals | none (observational) | `OpsSnapshot` | High — named WARNING/CRITICAL thresholds throughout | **Reuse and extend with Runtime-Execution-Service-specific signals (broker session age, retry-budget exhaustion) rather than building a parallel monitor** |
| `ops/alerts.py::evaluate()` | Fires alerts on state-worsening transitions | `ops/alerts.py` | `OpsSnapshot`, previous state | current + previous snapshot | alert list | High — dedups against previous cycle, plus always-fire conditions | **Reuse verbatim** |
| Journal pattern (`journal/*.py`) | One append-only JSONL writer per subsystem | `journal/` | none | a subsystem's own frozen record type | appended JSONL line | High — consistent, isolated, one-way | **Reuse the pattern exactly; add one new `runtime_execution_journal.py` following it** |
| Circuit breaker | Trip/cooldown on sustained broker failure | **does not exist** | — | — | — | **None — genuine gap** | **Build new (Series 46); do not retrofit into `ExecutionEngine` without a dedicated design pass** |
| Rate limiter | Outbound call throttling | **does not exist** | — | — | — | **None — genuine gap** | **Build new (Series 46)** |

---

## Deliverable 4 — Gap Analysis

| Capability | Status | Notes |
|---|---|---|
| Broker connection abstraction | **Already Exists** | `Broker` ABC + `build_broker()` factory |
| Authentication | **Already Exists** | `FyersBroker.connect()` + `AuthenticationError` classification |
| Token refresh | **Already Exists** | `FyersTokenManager`, atomic persistence; refresh-token expiry (~15 days) requires human re-login — not automatable, documented as a known operational limit, not a bug to fix |
| Order placement | **Already Exists** | `ExecutionEngine.submit_and_confirm()` → `Broker.place_order()`; **order-path itself is live-unverified on FYERS** |
| Order modification | **Missing** | No `modify_order` on the `Broker` ABC or `ExecutionEngine` today (only place/get/cancel) |
| Order cancellation | **Already Exists** | `Broker.cancel_order()`, used by `ExecutionEngine._safe_cancel()` on fill-timeout |
| Polling | **Already Exists** | `ExecutionEngine._await_fill()` polls `get_order` on a configurable interval/timeout |
| WebSocket events | **Partially Exists** | `FyersTickFeed` exists for market data ticks; there is no order/fill event stream — fills are discovered only by polling, never pushed |
| Reconciliation | **Already Exists** | `ExecutionEngine.reconcile()` + `Orchestrator._recover()`'s four-way match, including orphan-position flattening |
| Persistence | **Already Exists** | `SessionStore` (session snapshot), `TradeJournal` (CSV + SQLite), per-subsystem JSONL journals |
| Restart recovery | **Already Exists** | `Orchestrator._recover()` — genuinely handles resume / already-flat / orphan / clean-slate |
| Circuit breakers | **Missing** | Confirmed absent anywhere in `execution/`, `broker/`, or `ops/` |
| Retries | **Already Exists** | `ExecutionEngine._with_retry()` — linear graduated backoff, auth errors excluded from retry |
| Rate limiting | **Missing** | No outbound throttle anywhere; a `code=429` from FYERS is treated as a generic retryable error, not rate-limit-aware |
| Observability (logs/metrics/health) | **Already Exists** | `ops/health_monitor.py`, `ops/alerts.py`, structured `log_event` calls throughout |
| Journaling / audit trail | **Already Exists** | Per-subsystem append-only JSONL, `TradeJournal` dual CSV+SQLite write |
| Idempotency (order submission) | **Partially Exists** | `client_order_id`-based, correctly designed end-to-end, but the FYERS-side cache is in-memory only (lost on restart) and the orderbook-tag-scan fallback is unverified against a live account |
| Duplicate-submission prevention across restarts | **Partially Exists** | `ExecutionEngine.submit_and_confirm()`'s `_lookup()`-before-place logic is the correct pattern, but depends on the same unverified FYERS orderTag lookup above |
| Multi-position bookkeeping | **Must Not Be Added Here** | `TradeManager` is deliberately single-position; extending this is a Trading Brain / Strategy Selector concern (already scoped out in Series 34's own registry design as future work), not a Runtime Execution Service concern |
| A second broker abstraction | **Must Not Be Added** | `Broker` ABC already exists and is the correct seam |
| A second retry mechanism | **Must Not Be Added** | `ExecutionEngine._with_retry()` already exists |
| A second idempotency key scheme | **Must Not Be Added** | `OrderRequest.client_order_id` is already the documented key |
| Live order placement on FYERS during this or the next several series | **Must Not Be Added** | Explicitly forbidden until the order-path verification gap above is closed under controlled, human-supervised conditions — that decision belongs to whoever owns production risk, not to an Engineering Series |

---

## Deliverable 5 — Integration Points

```
Execution Engine (Trading Brain, Series 39)
    produces: ExecutionInstructionSet
        {status, execution_intent, abstract_actions, required_controls,
         blocking_conditions, confidence, plan_id, ...}
        │
        │  consumed by
        ▼
Broker Adapter (Series 40, bujji/broker_adapter/)
    engine.py::translate(instruction_set, broker="FYERS")
    produces: BrokerExecutionRequest
        {broker, execution_status, translated_actions: [
            {source_action: "AUTHORIZE_EXECUTION", broker_operation: "PREPARE_PLACE_ORDER", ...},
            {source_action: "WAIT_FOR_ADAPTER", broker_operation: None, status: "PENDING", ...},
         ], ...}
        │
        │  ═══ THIS IS THE INTEGRATION BOUNDARY THIS SERIES SPECIFIES ═══
        │  consumed by a NEW component: Runtime Execution Service
        ▼
Runtime Execution Service (NOT YET BUILT — Series 42+)
    responsibility: for each TranslatedAction whose status is
    TRANSLATED and whose broker_operation names a real capability
    (today, only "PREPARE_PLACE_ORDER"), construct the concrete,
    already-existing production type it corresponds to and hand it to
    already-existing production code:

        "PREPARE_PLACE_ORDER"
            → construct core/models.py::OrderRequest
              (contract, side, quantity, client_order_id, ...)
              *** where do strike/expiry/quantity come from? ***
              THIS IS AN OPEN GAP -- see note below.
            → call execution/engine.py::ExecutionEngine.submit_and_confirm(request)
            → receive core/models.py::OrderResult

        "VALIDATE_ORDER_PREREQUISITES" / "VALIDATE_ORDER_CONTROLS"
            → no broker call; pure precondition checks the Runtime
              Execution Service itself performs before dispatching
              (e.g. is a broker session currently CONNECTED?)

        PENDING-status actions (WAIT_FOR_ADAPTER, BLOCK)
            → no broker call, by the Broker Adapter's own design
              (Series 40) -- nothing to integrate here
        │
        ▼
ExecutionEngine.submit_and_confirm() / .reconcile()  (UNCHANGED, Series 39-and-earlier code)
        │
        ▼
Broker (FyersBroker / PaperBroker / HybridPaperBroker)  (UNCHANGED)
        │
        ▼
Exchange
```

### Method boundaries

- **Broker Adapter → Runtime Execution Service**: a plain function
  call passing an immutable `BrokerExecutionRequest` — no shared
  mutable state, matching every prior Trading Brain boundary's own
  discipline.
- **Runtime Execution Service → ExecutionEngine**: must go through
  `ExecutionEngine`'s existing public methods
  (`submit_and_confirm`, `reconcile`, `connect`) only. It must never
  reach past `ExecutionEngine` to call a `Broker` method directly —
  that would recreate the exact "N6" wart already flagged in
  `app.py::_candle_loop()`, not fix it.
- **ExecutionEngine → Broker**: unchanged, exactly as it is today.

### Data contracts

- `BrokerExecutionRequest` / `TranslatedAction` (Broker Adapter,
  Series 40, frozen) is the Runtime Execution Service's only input
  type from upstream.
- `OrderRequest` / `OrderResult` (`core/models.py`, frozen, already in
  production) is the only order-shaped type the Runtime Execution
  Service is permitted to construct or consume — it must never invent
  a competing order type.

### Ownership

- The Trading Brain and Broker Adapter own **what** should happen
  (which abstract action, translated to which named broker
  operation).
- The Runtime Execution Service owns **whether and when** it actually
  happens — session state, retry budget, timing — by orchestrating
  calls into `ExecutionEngine`, which continues to own **how** a
  single call is made reliably.
- `ExecutionEngine` and everything below it is completely unaware the
  Trading Brain, Broker Adapter, or Runtime Execution Service exist.
  This is intentional and must be preserved.

### The open gap this integration point exposes

`BrokerExecutionRequest` names an operation (`PREPARE_PLACE_ORDER`)
but carries no strike, expiry, or quantity — because, as documented
across Series 32-40, no stage in the Trading Brain has ever produced
one. **The Runtime Execution Service cannot construct a real
`OrderRequest` from a `BrokerExecutionRequest` alone.** Closing this
gap (a position-sizing/contract-selection component sitting between
the Trading Brain and the Runtime Execution Service) is out of scope
for Series 41 and is not listed in the Series 42-47 roadmap below —
it is flagged here explicitly so it is not silently assumed away.

---

## Deliverable 6 — State Machine (Runtime Lifecycle)

This is a **new** state machine, for the Runtime Execution Service
itself — distinct from, and layered on top of, the existing
`core/state_machine.py::State` (which governs position lifecycle:
`WAITING → READY → CONFIRMED → IN_POSITION → EXITING →
DONE_FOR_DAY`). The Runtime Execution Service's own states describe
the *session and per-order* lifecycle, one level below that.

```
STARTING
   │
   ▼
AUTHENTICATING  ──(AuthenticationError, non-refreshable)──►  AUTH_FAILED (terminal; human intervention required)
   │  (FyersTokenManager.refresh() on expiry, reuses existing code)
   ▼
CONNECTED
   │  (Broker.connect() succeeded; equivalent to today's Orchestrator.startup()'s first step)
   ▼
RECOVERING  ──(reuses Orchestrator._recover()'s four-way match)──►
   │
   ├─(resumed position found)──► READY (with a known open position)
   ├─(orphan found, flattened)──► READY (flat)
   └─(clean slate)──► READY (flat)
   │
   ▼
READY
   │  (awaiting a BrokerExecutionRequest from the Broker Adapter)
   ▼
SUBMITTING
   │  (ExecutionEngine.submit_and_confirm() in flight; idempotency
   │   lookup-before-place already handles the "was this already
   │   submitted?" question)
   ▼
WAITING
   │  (ExecutionEngine._await_fill() polling loop)
   │
   ├──► PARTIALLY_FILLED ──(poll continues until timeout or full fill)──┐
   │                                                                     │
   ├──► FILLED ◄─────────────────────────────────────────────────────────┘
   │
   └──► TIMED_OUT ──(ExecutionEngine._safe_cancel(), best-effort)──► CANCELLED
   │
   ▼
RECONCILING
   │  (ExecutionEngine.reconcile() against live broker positions --
   │   the same call already used by Orchestrator._recover())
   ▼
COMPLETE
   │
   ▼
READY  (loop back for the next instruction)


Recovery paths (from any non-terminal state, on process restart):
   any state  ──(process crash / restart)──►  STARTING  ──►  RECOVERING
   (exactly Orchestrator._recover()'s existing four-way match:
    resume / already-flat / orphan-flatten / clean-slate)

Recovery paths (mid-session):
   WAITING / PARTIALLY_FILLED  ──(lost websocket / lost poll connectivity)──►
       (no state change -- polling is HTTP request/response, not a
        persistent connection; ExecutionEngine's own retry-with-backoff
        handles a transient network gap without a distinct state)

   CONNECTED  ──(token expires mid-session)──►  AUTHENTICATING
       (re-enter the refresh flow; FyersTokenManager already
        distinguishes refreshable vs. non-refreshable expiry)
```

This lifecycle deliberately sits *above* `ExecutionEngine`, never
inside it — `ExecutionEngine` itself remains stateless per-call
(matching its current design), and the Runtime Execution Service is
the one new place session-level state is tracked.

---

## Deliverable 7 — Failure Model

| Failure | Existing handling today | What the Runtime Execution Service must add |
|---|---|---|
| Authentication failure (invalid/expired/revoked token) | `FyersBroker._raise_if_auth_error()` classifies distinctly; `ExecutionEngine._with_retry()` explicitly does **not** retry `AuthenticationError` | Route to `AUTHENTICATING` state; attempt `FyersTokenManager.refresh()` if `can_refresh`; if refresh-token itself has expired (~15 days), transition to terminal `AUTH_FAILED` and alert a human — this is already documented in production as non-automatable |
| Network failure (transient) | `ExecutionEngine._with_retry()` — linear graduated backoff, `retry_attempts` (default 3) | Reuse as-is; no new logic needed at this layer |
| Broker downtime (sustained) | Retry budget exhausts, raises `ExecutionError` every call, no memory between calls | **New**: circuit breaker (Series 46) — trip after N consecutive `ExecutionError`s, cool down before allowing further attempts, surface a distinct health state rather than hammering a down broker |
| Rejected orders | `OrderResult.status == REJECTED` (mapped from FYERS code `5`, **unverified**) | Surface the rejection reason (`OrderResult.message`) through the runtime journal and health/alert layer; do not auto-retry a broker-side rejection (distinct from a network failure) |
| Partial fills | `ExecutionEngine._await_fill()` returns the true partial result on timeout after a best-effort cancel | Reuse as-is; Runtime Execution Service should log the partial-fill outcome distinctly from a full fill in its own journal |
| Duplicate submissions | `ExecutionEngine.submit_and_confirm()`'s lookup-before-place via `client_order_id` | Reuse as-is; **the residual risk is the unverified FYERS orderTag echo/lookup** — flagged, not solved, by this series |
| Restart recovery | `Orchestrator._recover()`'s four-way match, including multi-leg orphan flattening | The Runtime Execution Service's `RECOVERING` state calls this exact routine — it does not reimplement it |
| Lost websocket | `FyersTickFeed` relies on the SDK's own `reconnect=True`; `HealthMonitor` reports WS connectivity as DEGRADED, not CRITICAL (candle path doesn't depend on it) | No order/fill event stream exists to "lose" — fills are discovered by polling, not pushed, so this failure mode is scoped to market-data ticks only, not order execution |
| Token expiry | `FyersTokenManager` distinguishes refreshable (daily) vs. non-refreshable (refresh-token itself expired, ~15 days) | Same as "Authentication failure" above |
| Rate limiting | FYERS `code=429` currently raises a generic `RuntimeError`, retried like any other transient failure | **New**: rate-limit-aware backoff (Series 46) — a 429 should not consume the same linear retry budget as a random network blip; ideally paired with the rate limiter gap noted in the Component Map |
| Timeouts | `order_timeout_seconds` (default 15.0s) governs fill-polling; best-effort cancel on timeout | Reuse as-is |

---

## Deliverable 8 — Idempotency (Architecture Only, No Implementation)

The idempotency key is, and must remain, `OrderRequest.client_order_id`
— already documented in `core/models.py` for exactly this purpose. The
chain of custody:

1. **Runtime Execution Service** generates `client_order_id`
   deterministically from the `BrokerExecutionRequest`'s own
   `request_id` (itself deterministic, `hashlib.md5`-derived per
   Series 40) — never from `uuid4()` or a random source, so the same
   translated request always yields the same idempotency key even
   across a process restart.
2. **`ExecutionEngine.submit_and_confirm()`** (unchanged) first calls
   `_lookup(cid)` against the broker before ever placing an order. If
   a result already exists for that `client_order_id`, it is adopted,
   not re-submitted.
3. **`FyersBroker`** sends `client_order_id` as FYERS's `orderTag` and
   maintains an in-memory `cid → fyers_order_id` cache for the current
   process's lifetime, falling back to scanning the day's orderbook by
   tag if the process restarted and the cache is empty.
4. **The known residual gap**: step 3's fallback path is unverified
   against a live account. Architecturally, the correct fix is not a
   new idempotency scheme — it is verifying (under controlled,
   human-supervised conditions, explicitly out of scope for this
   series) that FYERS actually echoes `orderTag` in `orderbook()`
   entries, and, if it does not, persisting the `cid → fyers_order_id`
   mapping to disk (mirroring `FyersTokenManager`'s own atomic
   fsync-then-rename pattern) so it survives a restart without relying
   on that echo at all.

No new idempotency mechanism is proposed. The architecture is: reuse
the existing key, reuse the existing lookup-before-place pattern,
close the existing verification gap rather than building around it.

---

## Deliverable 9 — Observability

| Concern | Existing mechanism | Runtime Execution Service's role |
|---|---|---|
| Logs | Structured `log_event(...)` calls throughout `execution/engine.py`, `core/orchestrator.py` | Reuse the same structured logging convention for every new Runtime Execution Service log line |
| Metrics | `ops/health_monitor.py::HealthMonitor.observe()` computes an `OpsSnapshot` (auth-expiry duration, candle staleness, restart frequency, exception rate, memory RSS, disk free %, journal write health, WS connectivity) | Extend `OpsSnapshot` with Runtime-Execution-Service-specific fields (broker session age, current lifecycle state, retry-budget consumption, circuit-breaker trip state once built) rather than a parallel metrics object |
| Health | Same `HealthMonitor`, feeding `RuntimeStatus` | The Runtime Execution Service's own lifecycle state (Deliverable 6) becomes one more input `HealthMonitor` reads |
| Heartbeat | No standalone heartbeat endpoint exists today — `DashboardServer` is the closest analog | **Gap**: a lightweight heartbeat the Runtime Execution Service emits on a fixed interval, consumable by `HealthMonitor`, is worth adding in Series 42 (Runtime Execution Core) rather than deferred |
| Alerts | `ops/alerts.py::evaluate()` — state-worsening-transition dedup, plus always-fire conditions (`auth_expired`, `journal_failure`) | Add Runtime-Execution-Service-specific always-fire conditions (e.g. `AUTH_FAILED` terminal state, circuit breaker tripped) to the same function, not a new alerting path |
| Journaling | One append-only JSONL file per subsystem (`journal/*.py`), never cross-read | Add `journal/runtime_execution_journal.py` following the exact same pattern — one record per lifecycle transition and per order dispatched |
| Audit trail | `TradeJournal`'s dual CSV+SQLite write on trade close; per-subsystem journals for everything upstream | The Runtime Execution Service's own journal is the audit trail for *this* layer; it does not write into `TradeJournal` directly — that remains `TradeManager`'s/`Orchestrator`'s responsibility |

---

## Deliverable 10 — Implementation Plan (Series 42+, Not Implemented This Sprint)

- **Series 42 — Runtime Execution Core.** Build the `STARTING →
  AUTHENTICATING → CONNECTED → RECOVERING → READY` lifecycle
  (Deliverable 6) as a thin state-holding wrapper around existing
  `Broker.connect()` / `Orchestrator._recover()`'s algorithm. Add the
  heartbeat gap noted in Deliverable 9. No order dispatch yet.
- **Series 43 — Authentication & Session Management.** Wire
  `FyersTokenManager` into the `AUTHENTICATING` state fully, including
  the non-refreshable-token terminal path and human-alert integration.
- **Series 44 — Order Dispatcher.** Wire `READY → SUBMITTING → WAITING
  → RECONCILING → COMPLETE` to `ExecutionEngine.submit_and_confirm()`
  / `reconcile()`. This is the series that must resolve — or
  explicitly re-scope — the open gap from Deliverable 5 (where a real
  `OrderRequest`'s strike/expiry/quantity come from), since it cannot
  dispatch a real order without it.
- **Series 45 — Fill Monitor & Reconciliation.** Formalize partial-fill
  and timeout handling on top of `ExecutionEngine._await_fill()`
  (Deliverable 7), plus the Runtime Execution Service's own journal
  records for each outcome.
- **Series 46 — Recovery & High Availability.** Build the two genuine
  gaps identified in this review: a circuit breaker and a rate
  limiter, sitting alongside (never inside) `ExecutionEngine._with_retry()`.
  Also close the idempotency-cache-persistence gap from Deliverable 8.
- **Series 47 — Production Qualification.** A qualification harness
  for the Runtime Execution Service, in the same spirit as Series 38's
  Decision Pipeline Qualification — deterministic replay against
  `PaperBroker`'s fault-injection hooks (`partial_fill_qty`,
  `raise_on_place_after_record`, `simulate_auth_expiry`), never against
  a live FYERS account, before any live-order-path verification is
  even considered.

None of Series 42-47 is implemented by this sprint. This roadmap
exists so future work proceeds in the order that respects what's
already proven (reuse first) and what's genuinely missing (build only
that), never the reverse.

---

## Summary

Production already has a working `Broker` abstraction, a real
retry/idempotency/reconciliation layer (`ExecutionEngine`), a genuine
crash-recovery routine (`Orchestrator._recover()`), and a working
observability/alerting layer. What's missing is narrow and specific: a
circuit breaker, a rate limiter, cross-restart idempotency-cache
persistence, and — most importantly — verification that the FYERS
order-placement path actually behaves as designed, since no live order
has ever been placed through it. The Runtime Execution Service's job
is to sit above `ExecutionEngine` as a new lifecycle/session layer,
reusing everything below it unchanged, and to close exactly those
gaps — nothing more, nothing duplicated, nothing redesigned.
