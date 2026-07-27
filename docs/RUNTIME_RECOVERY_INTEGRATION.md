# Runtime Recovery Integration

**BUJJI Options OS v3 — Engineering Series 53, Sprint 1**

## Status

Deployed. The Runtime layer is now restart-aware while remaining
completely subordinate to production. It lives at
`bujji/runtime/recovery_coordinator.py` — a new top-level package,
depending only on the frozen `RuntimeSession` (Series 48),
`BrokerSession` (Series 49), and `ExecutionSession` (Series 45)
models.

## Critical Rule, Restated

Production remains the source of truth. The Runtime reconstructs
itself from Production. Never the reverse. Every design decision below
follows directly from that rule.

## Existing Production Recovery — Studied, Not Modified

`bujji.core.orchestrator.Orchestrator._recover()` was read in full for
this sprint (never edited). It performs, entirely inside itself:

1. **Restart sequence**: `Application.__init__` acquires a
   single-instance file lock, builds the broker via the factory,
   constructs `ExecutionEngine`, and calls `Orchestrator.startup()` →
   `self._exec.connect()` → `self._recover()`.
2. **Broker reconnect**: `ExecutionEngine.connect()` (unchanged, Series
   41/51), including `FyersBroker.connect()`'s own internal token
   refresh path if the stored token had expired.
3. **Position reconciliation**: `SessionStore.load()` (a persisted
   `SessionSnapshot`) is parsed via `_safe_parse_position` (never
   raises — a corrupt snapshot is treated as "no saved position"), then
   cross-matched against `self._exec.reconcile()`'s live broker
   truth, in four cases: resumed (parsed+live match, restoring FSM to
   `EXITING`/`IN_POSITION`), already-flat (parsed but no live position,
   FSM forced to `DONE_FOR_DAY`), orphan (no/corrupt saved state but a
   live short found — every live short is flattened via
   `_find_all_live_shorts`/`_flatten_orphan`, `DONE_FOR_DAY`, marked
   unhealthy), or clean-slate (neither, only the `trades_taken` counter
   carries forward).
4. **Order reconciliation**: handled entirely inside
   `ExecutionEngine.reconcile()` → `Broker.get_open_positions()`,
   already covered above.
5. **Session reconstruction**: `core/state_machine.py::StateMachine.restore()`
   — a separate, explicitly-audited bypass used only by this recovery
   path, distinct from the normal `transition()` method.
6. **Failure handling**: a corrupt saved position never crashes
   startup (parsed as "none"); an `AuthenticationError` during
   `startup()` is caught specifically in `app.py::run()`, sets
   `status.auth_expired=True`, and re-raises (no trading has occurred
   yet).

**None of this is modified, duplicated, or reimplemented by this
sprint.**

## Why This Coordinator Does Not Call `Orchestrator._recover()` Directly

`_recover()` is `async`, private, returns nothing, mutates
`Orchestrator`'s own internal state (`self._fsm`, `self._position`,
`self._trades_taken`, `RuntimeStatus.healthy`), and is deeply coupled
to `SignalEngine`, `TradeManager`, `EventBus`, and
`CapitalManagementEngine` — none of which this coordinator has, or
should have, any relationship with.

Calling it directly from here would require either constructing a full
`Orchestrator` (pulling in everything it depends on, exactly the
"overlapping responsibility" risk the Series 50 review warned against)
or reaching into its private attributes after the fact (the same
private-attribute coupling this project explicitly avoided with
`FyersBroker._cfg` in Series 51).

Instead, `ProductionRecoverySource` is a structural `typing.Protocol`
— the same seam pattern as `ExecutionEngineInterface` (Series 45) and
`AuthenticationProviderInterface` (Series 49) — exposing exactly the
five facts this coordinator needs:
`is_healthy`, `broker_connected`, `has_open_position`,
`position_state`, `recovery_error` (via
`ProductionRecoverySnapshot`). **A future wiring layer**, holding a
real `Orchestrator` instance and having already called its own
`startup()`/`_recover()`, is responsible for reading that instance's
own state and constructing the snapshot — this coordinator never
performs that reading itself, and this sprint does not build that
wiring layer (out of scope; a candidate for a future series).

## Runtime Reconstruction Rules

Evaluated top to bottom.

| # | Condition | Outcome |
|---|---|---|
| 1 | No `ProductionRecoverySource` supplied | `INSUFFICIENT_DATA` |
| 2 | `snapshot.recovery_error` is not `None` | `FAILED`, surfaced verbatim |
| 3 | `position_state` not recognized (not one of `IN_POSITION`/`EXITING`/`CONFIRMED`/`WAITING`/`READY`/`DONE_FOR_DAY`) | `INSUFFICIENT_DATA` — never guessed |
| 4 | `broker_connected` is `False` | `INSUFFICIENT_DATA` |
| — | All checks pass | `RECOVERED` |

### Mapping production state onto EXISTING runtime vocabulary

No new lifecycle state is invented for `RuntimeSession` or
`BrokerSession` — every value used below already existed before this
sprint:

| Production `position_state` | Reconstructed `RuntimeSession.session_state` |
|---|---|
| `IN_POSITION`, `EXITING`, `CONFIRMED` | `ACTIVE` |
| `WAITING`, `READY` | `READY` |
| `DONE_FOR_DAY` | `COMPLETED` |

`BrokerSession` is reconstructed as `authentication_state=AUTHENTICATED`,
`session_state=READY` whenever `broker_connected` is `True` — both
already-existing Series 49 values.

## The One Disclosed Gap: `ExecutionSession` Reconstruction

**`execution_session` is always `None` this sprint**, whether or not a
position exists. Reconstructing a full `ExecutionSession` (Series 45)
would require the *original* `OrderRequest`s and dispatch plan that
produced the recovered position — data
`ProductionRecoverySnapshot` does not expose, and this coordinator
never fabricates. Rather than silently omitting the field or inventing
placeholder order data, `RuntimeRecoveryResult.execution_session_gap_reason`
always names exactly what is missing:

> *"Reconstructing a full ExecutionSession requires the original
> OrderRequests and dispatch plan; ProductionRecoverySnapshot does not
> expose order-level detail, and this coordinator never fabricates
> it."*

This is a genuine, newly-discovered gap, documented explicitly per the
specification's own instruction — not silently addressed. Closing it
properly would require production to expose (or a future series to
build) a durable order-level recovery record; that is out of scope
here.

## Recovery Status: The One New, Disclosed Vocabulary

`recovery_status` (`RECOVERED`, `INSUFFICIENT_DATA`, `FAILED`) is new
— no equivalent top-level "did recovery succeed" concept existed
anywhere in this project before this sprint. It describes this
coordinator's own outcome only; it is never confused with, and never
feeds back into, `RuntimeSession.session_state`,
`BrokerSession.authentication_state`/`.session_state`, or production's
own `core.state_machine.State` — four still-separate state machines,
exactly as the Series 50 review established.

## State Ownership, Preserved

Production continues to own orders, positions, fills, broker truth,
and reconciliation — this coordinator reads a summary of what
production already determined and never recomputes, second-guesses, or
duplicates any of it. Runtime continues to own lifecycle,
orchestration, coordination, and its own deterministic artifacts —
this sprint's only new capability is reconstructing those artifacts
*after* a restart, from production's own truth.

## Failure Handling

If `snapshot.recovery_error` is set, this coordinator surfaces it
verbatim in `failure_reason` and `recovery_trace` — it never retries,
never attempts its own recovery, and never fabricates a `RECOVERED`
result to paper over the failure.

## Traceability

Every `recover()` call's `recovery_trace` records, in order: restart
detected, production recovery invoked, the recovered production
snapshot (`production_snapshot_summary`), the runtime reconstruction
performed (or why it could not be), and the resulting `recovery_status`
— matching the specification's own five required trace elements
exactly.

## Determinism

`recovery_id`, `runtime_session_id`, and `broker_session_id` are each
derived via `hashlib.md5` over a fixed seed and the timestamp — never
`uuid4()`. `timestamp` is genuinely wall-clock-derived by design (an
injectable `clock` parameter defaulting to `datetime.now`), the same
pattern used throughout this project. Given the same
`ProductionRecoverySnapshot` and the same fixed clock, `recover()`
always produces a byte-identical `RuntimeRecoveryResult`.

## What This Sprint Explicitly Does Not Do

Per the specification's own explicit exclusions, this sprint contains
zero: broker-state recovery of its own, order recreation, position
recreation, execution retries, manual authentication, or any
modification to `Orchestrator._recover()`, `ExecutionEngine`,
`FyersBroker`, or `FyersTokenManager`.

## Verification Before Declaring Completion

- **`Orchestrator._recover()` remains completely unchanged.** Confirmed
  — read only, never edited.
- **The coordinator is the only new runtime recovery component
  introduced.** Confirmed — `bujji/runtime/recovery_coordinator.py` is
  the sole new file.
- **Production remains the single source of truth for orders,
  positions, and broker state.** Confirmed — this coordinator reads a
  five-field summary and reconstructs nothing beyond `RuntimeSession`/
  `BrokerSession` state values already defined elsewhere.
- **Runtime recovery performs only reconstruction and coordination.**
  Confirmed by this sprint's own isolation tests (no retry, no
  reconciliation, no broker call, no authentication call anywhere in
  this module).
- **All new tests pass**; see `tests/test_runtime_recovery_integration.py`.
- **Full regression passes**, and **the qualification fingerprint
  remains unchanged** — no deterministic trading logic was altered.
- **The `ExecutionSession` reconstruction gap is documented explicitly**
  above, not silently addressed.

## Isolation Guarantees

- No import of `bujji.core.orchestrator.Orchestrator`, `bujji.broker`,
  `bujji.execution`'s concrete `ExecutionEngine`, a broker SDK, or any
  network library anywhere in this module.
- No modification to any file under `bujji/core/`, `bujji/broker/`, or
  `bujji/execution/`.
- No retry loop, no reconciliation call, no order/position
  construction, and no authentication call anywhere in this module.
- Every id is `hashlib.md5`-derived, never `uuid4()`; no randomness of
  any kind appears anywhere in this file.
- A `RuntimeRecoveryResult` is only ever `RECOVERED` when a real,
  recognized production snapshot was supplied — never fabricated.

**After a restart: production performs recovery, exactly as it always
has. Runtime reconstructs itself from the recovered production state,
using only existing lifecycle vocabulary. No business decision is
recomputed, no broker state is fabricated, and no execution is retried
automatically. The architectural principle that production remains
authoritative is preserved completely.**
