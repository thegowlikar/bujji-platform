# Runtime Execution Orchestrator Architecture

**BUJJI Options OS v3 — Engineering Series 45, Sprint 1 (v1)**

## Status

Deployed. This is the orchestration bridge between the Trading
Brain's deterministic `OrderRequest` output (Series 44) and
production's existing, mature `ExecutionEngine`, per the Series 41
architecture review's own roadmap. It lives at
`bujji/runtime_execution/` — outside both `bujji/trading_brain/` and
`bujji/broker_adapter/`, its own operational-infrastructure package,
depending only on the frozen Order Construction Service (Series 44).

## Purpose and Philosophy

This module is an orchestrator. It owns workflow — validating
execution readiness and preparing deterministic dispatch sequencing —
and owns nothing else. It owns no broker logic, no retry logic, no
authentication, and no reconciliation. Those remain entirely inside
production's own `Broker` and `ExecutionEngine`, mapped in detail
during the Series 41 audit, and this sprint neither imports, modifies,
nor duplicates any of it.

## The Abstract Interface: A Protocol, Never an Import

`engine.py::ExecutionEngineInterface` is a structural
`typing.Protocol` — an abstract shape (`submit_and_confirm(order_request)`),
not an import of production's concrete
`bujji.execution.engine.ExecutionEngine`. Production's real
`ExecutionEngine` already satisfies this shape without any change on
its part; this module simply never needs to import it to be tested or
to be correct. Whatever object is actually injected as `executor` —
production's own `ExecutionEngine`, or a test stub — performs all real
I/O entirely outside this package's own code and import graph. This
module contains **zero broker SDK imports and zero network calls of
its own**, structurally, not merely by convention.

## Inputs: One Object, Plus an Injected Interface

`engine.py::build_session()` accepts exactly a tuple of already-produced
`OrderRequest`s (Series 44, or `None`) — no other module. Dispatching
(a separate, optional step — see below) additionally accepts an
`ExecutionEngineInterface`-shaped object, injected by the caller.

## The Execution State Machine

Evaluated as three composable functions, each a pure transition:

```
build_session()                    queue_for_dispatch()        dispatch()
────────────────                   ────────────────────        ──────────
(missing input) ──► ABORTED
(empty tuple)   ──► FAILED_VALIDATION
(invalid order) ──► FAILED_VALIDATION
(duplicate id)  ──► FAILED_VALIDATION
(all pass)      ──► READY  ──────►  DISPATCH_PENDING  ──────►  DISPATCHED
                                                          └───►  ABORTED (executor raised)
```

**An honest note on `CREATED` and `VALIDATED`**: both are declared in
the finite state machine per the specification's own list, but
`build_session()` runs synchronously start-to-finish — validation and
dispatch-plan construction happen back-to-back in one call, never as
two separately observable steps. Neither state is ever independently
returned by any function in this module this sprint; they are kept in
the taxonomy for completeness and for a future series that might split
validation and planning into two separately awaitable steps.

## Dispatch Rules: One Order, One Instruction, Atomic

`build_session()` produces exactly one `DispatchInstruction` per
`OrderRequest` — never more, never fewer, never fabricated. If
validation fails for **any** leg, the entire session fails
(`FAILED_VALIDATION`) and the dispatch plan is empty — multi-leg
strategies remain atomic, the same discipline the NIFTY Contract
Builder (Series 42) and Order Construction Service (Series 44) already
established one layer up.

## Validation: The Decision Table

Evaluated top to bottom; first matching rule wins.

| # | Condition | State | Failure Reason |
|---|---|---|---|
| 1 | `order_requests is None` | `ABORTED` | `INSUFFICIENT_DATA` |
| 2 | `order_requests == ()` | `FAILED_VALIDATION` | `EMPTY_SESSION` |
| 3 | Any `OrderRequest` missing a contract, non-positive quantity, unrecognized `order_type`, or empty `client_order_id` | `FAILED_VALIDATION` | `INVALID_ORDER_REQUEST` |
| 4 | Two or more `client_order_id`s collide | `FAILED_VALIDATION` | `DUPLICATE_CLIENT_ORDER_ID` |
| 5 | Defensive: dispatch plan length ≠ order request count | `FAILED_VALIDATION` | `INVALID_DISPATCH_PLAN` |
| — | All checks pass | `READY` | — |

`order_type` recognition reuses the frozen Order Construction
Service's own `ALL_EXECUTION_POLICIES` vocabulary verbatim — never
redefined here. Validation deliberately performs **no broker
connectivity checks** — this module has no way to reach a broker and
would not attempt to even if it could.

## Dispatch: Delegation, Never Execution

`queue_for_dispatch()` and `dispatch()` are separate, optional,
idempotent-on-wrong-state functions — each is a no-op (returns the
session unchanged) unless the session is in the exact state it
expects (`READY` and `DISPATCH_PENDING` respectively). `dispatch()`
calls `executor.submit_and_confirm()` once per instruction, in
sequence; if `executor` raises at any point, the session becomes
`ABORTED`. This module makes **no claim about broker-side state after
a partial sequence** — it performs no reconciliation of its own; that
remains production's `ExecutionEngine.reconcile()`'s job entirely, per
the Series 41 audit.

## Traceability

`execution_trace` accumulates one clause per transition — matching the
specification's own worked example format:
*"OrderRequest x2. Validation PASSED. Dispatch Plan: 25150CE, 25150PE.
READY."* — appended to with *"Queued for dispatch. DISPATCH_PENDING."*
and *"Dispatched to ExecutionEngineInterface. DISPATCHED."* as each
subsequent function runs.

## Determinism

`session_id` is derived via `hashlib.md5` over the ordered
`client_order_id`s of every supplied `OrderRequest`, the state, and
the timestamp — never `uuid4()`. `timestamp` is genuinely
wall-clock-derived by design (an injectable `clock` parameter
defaulting to `datetime.now`), the same pattern used throughout the
Trading Brain. Given the same `OrderRequest`s and the same fixed clock,
`build_session()` always produces a byte-identical `ExecutionSession`.

## Journaling

`bujji/journal/runtime_execution_journal.py::RuntimeExecutionJournal`
— append-only JSONL, own schema (`schema_version` field), independent
of every other journal in the codebase.

## Query API

`runtime_execution/query.py::ExecutionSessionIndex` mirrors the
established `*Index` pattern: `ingest()`, then read-only `latest()`,
`history()`, `find_by_id()`, `find_by_state()`, `summary()`. No
mutation method beyond `ingest`.

## What This Sprint Explicitly Does Not Build

Per the specification's own explicit exclusions, this sprint contains
zero: broker connectivity, order submission logic of its own,
authentication, token refresh, retries, polling, reconciliation,
WebSocket handling, or any modification to production's execution
components.

## A Recommendation for What Comes Next

Before any further series, the specification itself recommends running
an end-to-end replay qualification —
`Market Snapshot → Trading Brain → Contract Builder → Position Sizing
→ Order Construction → Runtime Execution Orchestrator` — against
historical sessions and `PaperBroker` only, satisfying
`ExecutionEngineInterface` with production's own existing paper
broker. Only after that replay passes consistently should a first live
broker interaction be introduced. This sprint does not perform that
replay itself; it is flagged here as the explicit next step, not
silently assumed.

## Isolation Guarantees

- No import of a broker SDK, `bujji.broker`, `bujji.execution`
  (the concrete `ExecutionEngine` class), MIC v2, or any Trading Brain
  module upstream of Order Construction anywhere in this package —
  only the frozen `OrderRequest` model this module consumes, and a
  structural `Protocol` describing (never importing) production's
  execution shape.
- No import of `bujji.core`, `bujji.trade`, `bujji.market`, or
  `bujji.tick` anywhere in this package.
- No authentication token, session credential, retry count, or
  reconciliation field anywhere on `ExecutionSession` or
  `DispatchInstruction`.
- `build_session()`, `queue_for_dispatch()`, and `dispatch()` perform
  no network I/O of their own; every id is `hashlib.md5`-derived,
  never `uuid4()`.
- A dispatch instruction is only ever produced from an already-valid
  `OrderRequest` — never fabricated when validation fails.

**The Runtime Execution Orchestrator's sole responsibility is to
prepare validated, execution-ready sessions and hand them to the
existing production execution infrastructure. All live operational
concerns — authentication, retries, polling, reconciliation, broker
interaction — remain entirely within the already-proven production
`ExecutionEngine` and related runtime components identified during the
Series 41 architecture review.**
