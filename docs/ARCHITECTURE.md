> **DEPRECATED — superseded by `ARCHITECTURE.md` at the repository root.**
>
> This file described the VWAP Premium Straddle Seller generation and declared
> itself authoritative. It is retained for historical reference and is no
> longer maintained. Two architecture documents each claiming authority is the
> condition the root file exists to end.
>
> For the current safety contract, ownership of truth, state machine and
> acceptance criteria, read `ARCHITECTURE.md`.

# BUJJI — Architecture Reference (VWAP Premium Straddle Seller)

This is the authoritative, current-strategy module reference. Several older
docs in this directory (`CHAOS_TESTING_PLAN.md`, `FYERS_TRANSPORT_READINESS.md`,
`PAPER_CAMPAIGN_RUNBOOK.md`, `PAPER_TRADING_LIVE_DATA.md`,
`TIER1_CAPITAL_PROTECTION.md`) still describe the earlier ORB-VWAP breakout
strategy this system replaced — treat their strategy-specific sections as
historical background, not current behavior. This document and
`OPERATIONS_RUNBOOK.md`/`AUDIT_LOG.md` describe the system as it exists today.

## Strategy summary

One trade per day. At 09:20, sell the ATM CE and ATM PE at the nearest weekly
expiry (a short straddle). Track the combined premium (CE LTP + PE LTP)
against its own volume-weighted running-average VWAP. Exit on a single
5-minute candle close above that VWAP (changed 2026-07-19 from the
original two-consecutive-closes rule, per explicit operator request -- see
docs/AUDIT_LOG.md), a configured MTM loss, a fast tick-driven emergency
stop, or the 15:05 hard exit — whichever comes first. No re-entry once flat
for the day.

## Module reference

### `bujji/signal/engine.py` — Signal Engine
- **Purpose:** decide *when* to enter. Nothing else.
- **Inputs:** one completed candle per call (`on_candle`).
- **Outputs:** a `Signal` — `ENTER_STRADDLE` exactly once per day (first
  candle at/after `trading_start`, before `hard_exit`), `NO_TRADE` otherwise.
- **Dependencies:** `AppConfig.timing` only.
- **Failure modes:** none observed — pure, stateless-per-call logic gated by
  one boolean latch (`_signalled`).
- **Invariant:** at most one `ENTER_STRADDLE` signal per process lifetime
  (resets only on a fresh `SignalEngine` instance, i.e. process restart).
- **Replay behavior:** identical to live — the same instance drives both.

### `bujji/trade/manager.py` — Trade Manager
- **Purpose:** own the open position's Premium VWAP and decide *when to
  exit* once entered.
- **Inputs:** `open_position(position, entry_candle)` once; `reassess(candle,
  combined_premium)` every subsequent candle.
- **Outputs:** `TradeDecision` (HOLD/EXIT) with a full `DecisionTrace`.
- **Dependencies:** `PremiumVwapTracker` (signal/indicators.py),
  `AppConfig.risk`/`timing`.
- **Failure modes:** none observed in the decision logic itself; a
  contract-resolution failure at entry never reaches here (caught upstream,
  see Orchestrator).
- **Invariants:** `_premium_vwap`/`_consecutive_above` are reset exactly once
  per `open_position()` call; `PremiumVwapTracker` never receives anything
  but the combined (CE+PE) premium (verified in the Sep 2026 ATM/Premium
  audit — see AUDIT_LOG.md).
- **Replay behavior:** deterministic — same candle sequence + same combined
  premium sequence always produces the same VWAP trajectory and exit point.

### `bujji/core/orchestrator.py` — Orchestrator
- **Purpose:** the only module that wires Signal Engine + Trade Manager +
  Execution Engine together via the FSM. Owns entry, holding, exit, recovery,
  journaling, and dashboard status updates.
- **Inputs:** `on_candle()` (every 5 min), `startup()` (once), `square_off()`
  (Tick Engine / EOD), `end_of_day()` (wall-clock backstop).
- **Outputs:** state transitions, `POSITION_OPENED`/`POSITION_CLOSED`/
  `DECISION_MADE` events, journal records, session snapshots.
- **Dependencies:** everything else — this is the composition root for one
  trading day's logic.
- **Failure modes documented and handled:**
  - Auth failure at any broker call → `AuthenticationError`, never retried,
    surfaced distinctly (`status.auth_expired`).
  - Execution failure (rejected/unfilled order) → `ExecutionError`, FSM rolls
    back to `READY`.
  - **Contract-resolution failure** (missing strike/expiry, e.g.
    `LookupError`) → now (fixed) also rolls back to `READY` instead of
    wedging the FSM at `CONFIRMED` forever (see AUDIT_LOG.md, fix #3).
  - **One-leg entry failure** (CE fills, PE's order fails) → now (fixed)
    immediately buys back the filled CE leg same-cycle instead of leaving it
    naked until the next restart (see AUDIT_LOG.md, fix #1).
- **Invariants:** `resolve_atm_contract`/`InstrumentMaster.resolve_atm`/
  `Broker.atm_strike` are called from exactly one place in the entire
  codebase — inside `_enter()` — proven by exhaustive repository search (see
  the Trade Identity audit in AUDIT_LOG.md). No other code path ever
  re-resolves a contract.
- **Replay behavior:** identical — `ReplayEngine` drives the SAME
  Orchestrator instance/code path over historical candles via `ReplayBroker`.

### `bujji/execution/engine.py` — Execution Engine
- **Purpose:** the only module that talks to a broker. Idempotent order
  placement, partial-fill handling, retry/backoff, reconciliation.
- **Invariants (capital protection contract):**
  - **C3 (no duplicates):** an order is placed at most once per
    `client_order_id` — a lookup precedes every placement; on an ambiguous
    error, it verifies before ever re-placing.
  - **C4 (partial fills):** the caller always sizes the position off the
    truthful `filled_quantity`, never the requested quantity.
- **Failure modes:** `AuthenticationError` never retried; any other
  exception retried up to `retry_attempts` with exponential backoff, then
  raised as `ExecutionError`.
- **NOT covered by this engine:** `resolve_atm_contract` is called directly
  against the broker (`self._exec._broker.resolve_atm_contract`), bypassing
  this retry/backoff wrapper entirely — a known, documented architectural
  gap (see AUDIT_LOG.md, Important Issue).

### `bujji/tick/engine.py` — Tick Engine
- **Purpose:** a faster, supplementary risk check (1s cadence) on top of the
  5-min candle-driven one — never a replacement for it.
- **Inputs:** `POSITION_OPENED`/`POSITION_CLOSED` events; live tick feed.
- **Invariant (fixed this session):** subscribes to and requires **both**
  `pos.ce_contract`/`pos.pe_contract` symbols before computing MTM — never a
  single leg's LTP mistaken for the combined premium.
- **Exit path:** calls `Orchestrator.square_off()` — the exact same hardened
  exit machinery used everywhere else, never a separate mechanism.
- **Failure modes:** no feed → no monitoring (never fabricates data); one leg
  not yet ticked → skips the check for that cycle (candle backstop covers
  the gap).

### `bujji/tick/health.py` — Health Engine
- **Purpose:** purely observational connectivity/staleness monitoring for
  the dashboard. Never makes or influences a trading decision (verified by a
  dedicated test asserting it never calls `square_off`/`on_candle`/etc.).
- **Invariant (fixed this session):** reports the *staler* of the CE/PE
  legs' tick ages, not just one.

### `bujji/broker/instrument_master.py` — Instrument Master
- **Purpose:** resolve a real ATM CE/PE contract from FYERS's public NFO
  symbol-master CSV. No hardcoded symbols, ever.
- **Invariant (fixed this session):** strike rounding now goes through the
  single canonical `Broker.atm_strike()` formula (deterministic round-half-
  up) instead of an independently reimplemented, banker's-rounding-affected
  duplicate.
- **Failure modes:** `LookupError` on missing underlying/expiry/contract.
  Cache TTL 24h; a stale cache (network failure) could theoretically select
  an already-expired contract via the 24h grace window — **documented risk,
  not fixed** (see AUDIT_LOG.md, Important Issue — needs a design decision).

### `bujji/core/position_codec.py` — Position Serialization
- **Purpose:** the single source of truth for turning a live `Position` into
  a JSON-safe dict and back, for crash recovery.
- **Invariant (fixed this session):** a snapshot with exactly one of
  `ce_contract`/`pe_contract` present (the other missing/corrupted) is now
  rejected as `PositionSchemaError` — previously it silently resumed as a
  half-populated position, corrupting MTM/VWAP and orphaning the missing leg
  on exit.
- **Failure modes:** any schema mismatch normalizes to one exception type
  (`PositionSchemaError`), never a raw `KeyError`/`TypeError`/etc. propagating
  out of startup.

### `bujji/core/session_state.py` — Session Persistence
- **Purpose:** atomic (`fsync` + `os.replace`) JSON snapshot of FSM
  state/position/idempotency keys, survives any crash mid-write.
- **Invariant:** a snapshot from a previous trading day is discarded
  (`is_today()` check) — never silently resumed into a new day.

### `bujji/core/orchestrator.py::_recover` — Recovery
- **Every combination handled:** parsed+live → resume; parsed+flat →
  finalize; none+live → orphan-flatten (now flattens **every** leg found,
  fixed in an earlier pass, with unique per-leg client-order-ids to avoid
  idempotent-submit collisions); none+flat → clean slate.

### `bujji/journal/journal.py` — Trade Journal
- **Purpose:** permanent CSV + SQLite record of every closed trade.
- **Fields added this session:** `trade_id`, `ce_symbol`, `pe_symbol`,
  `expiry` — the permanent record previously stored only a derived
  `atm_strike` int, losing the actual traded contract identity once the
  live session snapshot reset the next day.
- **Failure mode (fixed this session):** CSV append and SQLite insert are
  two independent writes, not one transaction. `record()` never raises (a
  raised exception here would abort the exit path's remaining cleanup with
  the broker already flat — a worse outcome) — but any partial-write
  mismatch is now logged at CRITICAL instead of silently diverging.
- **Schema migration:** `_init_db()` now `ALTER TABLE ADD COLUMN`s any
  missing field on top of an existing database — never a destructive
  rebuild; historical rows always survive.

### `bujji/replay/engine.py` + `bujji/replay/__main__.py` — Replay
- **Purpose:** feed historical candles through the exact same live decision
  stack (Orchestrator/TradeManager/SignalEngine/ExecutionEngine), swapping
  only the broker for a deterministic `ReplayBroker`.
- **Invariant (fixed this session):** replay is now isolated under
  `--workdir` (default `data/replay/`) by default — it can no longer read or
  write the live process's session snapshot/journal/database unless
  `--use-live-paths` is explicitly passed. Previously the default invocation
  silently reused the live paths.

### `bujji/dashboard/server.py` — Dashboard
- **Purpose:** read-only HTTP status page + JSON API; never mutates trading
  state.
- **Invariant (fixed this session):** the "Premium VWAP Health" section
  (formerly "Market Data Health") now reports the strategy's ACTUAL live
  indicator (`PremiumVwapQuality` — is the VWAP seeded/ready, what's its
  current value) — previously it displayed a permanently-empty dummy
  spot-VWAP tracker, showing a false "TRADING DISABLED" red banner all day
  regardless of actual trading state.

## What this document does NOT cover

- **LIVE CERTIFICATION REQUIRED:** FYERS's real fill-response field names
  (`filledQty`, `tradedPrice`, order-status codes, `orderTag` echo in
  `orderbook()`) are marked UNVERIFIED in `broker/fyers.py`'s own comments —
  no amount of code review or paper trading (even `fyers_paper` mode) can
  close this; it requires one real placed order. See AUDIT_LOG.md.
