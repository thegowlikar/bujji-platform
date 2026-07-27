# Order Construction Service Architecture

**BUJJI Options OS v3 — Engineering Series 44, Sprint 1 (v1)**

## Status

Deployed. This is the **last purely business-logic component** in the
Trading Brain pipeline, per `TRADING_BRAIN_CONSTITUTION.md`. It lives
at `bujji/trading_brain/order_construction/`, depending only on the
frozen Position Sizing Engine (Series 43). Everything after this
series is operational infrastructure — Runtime Execution, session
management, retries, reconciliation, broker connectivity, circuit
breakers, production qualification — built on top of the deterministic
artifacts this module produces.

## Purpose and Philosophy

Business logic ends here. Operational execution begins afterwards.
This module converts an already-validated `PositionPlan` (Series 43)
into a complete, broker-neutral order description — one `OrderRequest`
per contract leg. It never places an order, authenticates, refreshes a
token, opens a socket, retries, reconciles, polls a broker, or monitors
a fill.

## Inputs: Three Objects, Nothing Else

`engine.py::construct_orders()` accepts exactly a `PositionPlan`
(Series 43), an `ExecutionPolicy`, and a `TradingConfiguration` — no
broker, no Runtime Execution Service, no `ExecutionEngine`, no FYERS,
no Zerodha.

`ExecutionPolicy` names one of four finite policies (`MARKET`,
`LIMIT`, `STOP`, `STOP_LIMIT`) plus an optional `limit_price`/
`stop_price` for the two price-bearing policies — no adaptive
policies, no smart routing, no optimization.

`TradingConfiguration` carries the finite `product` (`MIS`/`NRML`) and
`validity` (`DAY`/`IOC`/`FOK`) — both configuration-driven, never
inferred — plus the identifying metadata (`session_id`,
`pipeline_version`, `qualification_fingerprint`) that becomes every
resulting order's immutable `OrderTags`.

## One Contract, One Order

Construction is strictly 1:1 — every contract leg in
`PositionPlan.contracts` becomes exactly one `OrderRequest`, carrying
that plan's own uniform `quantity_per_leg` (Series 43 already enforced
identical sizing across every leg of a strategy; this module never
recomputes or overrides it):

| Strategy | Legs | OrderRequests |
|---|---|---|
| Premium VWAP Straddle | 2 | 2 |
| Iron Fly | 4 | 4 |
| Calendar Spread | 2 | 2 |

## Construction Is Atomic

Every validation check runs before any `OrderRequest` is built. If any
check fails, the entire construction fails
(`CONSTRUCTION_STATUS_FAILED`) and zero requests are returned — never
a partial, fabricated order set missing a leg. This is the same
financial-safety discipline the NIFTY Contract Builder (Series 42)
established for multi-leg contract construction, applied here one
layer further downstream.

## The Decision Table

Evaluated top to bottom; first matching rule wins.

| # | Condition | Failure Reason |
|---|---|---|
| 1 | Any of the three inputs entirely missing | `INSUFFICIENT_DATA` |
| 2 | `PositionPlan.validation != "PASSED"`, or its contract set is empty | `EMPTY_POSITION_PLAN` |
| 3 | `ExecutionPolicy.policy` not one of the four recognized values | `INVALID_EXECUTION_POLICY` |
| 4 | `TradingConfiguration.product` not one of the two recognized values | `INVALID_PRODUCT` |
| 5 | `TradingConfiguration.validity` not one of the three recognized values | `INVALID_VALIDITY` |
| 6 | `PositionPlan.quantity_per_leg <= 0` | `ZERO_QUANTITY` |
| 7 | A generated `client_order_id` collides with one already produced this construction (defensive; structurally unreachable given distinct contract ids) | `DUPLICATE_REQUEST` |
| — | All checks pass | `CONSTRUCTED` |

## Client Order ID: Deterministic, Never Random

Every `client_order_id` is derived via `hashlib.md5` over the source
`PositionPlan`'s own id, the leg's `contract_id`, the execution
policy, and the construction timestamp — never `uuid4()`, never a
counter, never randomness of any kind. The same inputs, replayed with
the same clock, always produce the same `client_order_id` for the same
leg — collision-resistant, immutable once produced, and fully
traceable back to the exact `PositionPlan` and contract that generated
it.

## Tags: Immutable Metadata, Never Broker-Specific

`OrderTags` (`strategy_id`, `session_id`, `pipeline_version`,
`qualification_fingerprint`) is a dedicated frozen dataclass, not a
mutable dict — every field is either carried forward from the source
contract (`strategy_id`) or from `TradingConfiguration` (the rest).
No broker-specific field (an order id, a symbol token format, an
exchange segment code) belongs here; those are the Broker Adapter's
concern (Series 40), not this module's.

## Creation Trace: Always Explainable

Every `OrderRequest.creation_trace` states the source plan id, the
contract's strike/type, the side, the quantity, the policy, and the
product — matching the specification's own worked example format
exactly: *"Position Plan ... Contract 25150CE. Side SELL. Quantity 75.
Policy MARKET. Product MIS. Request Created."*

## Determinism

`request_id` and `construction_id` are each derived via `hashlib.md5`
over already-deterministic upstream values and the timestamp — never
`uuid4()`. `timestamp` is genuinely wall-clock-derived by design (an
injectable `clock` parameter defaulting to `datetime.now`), the same
pattern used throughout the Trading Brain. Given the same three inputs
and the same fixed clock, `construct_orders()` always produces a
byte-identical `OrderConstructionResult`.

## Journaling

`bujji/journal/order_construction_journal.py::OrderConstructionJournal`
— append-only JSONL, own schema (`schema_version` field), independent
of every other journal in the codebase.

## Query API

`order_construction/query.py::OrderConstructionIndex` mirrors the
established `*Index` pattern: `ingest()`, then read-only `latest()`,
`history()`, `find_by_id()`, `find_by_status()`,
`find_by_position_plan()`, `summary()`. No mutation method beyond
`ingest`.

## What This Sprint Explicitly Does Not Build

Per the specification's own explicit exclusions, this sprint contains
zero: broker connectivity, order submission, token refresh, broker
queries, margin calculation, position polling, retries, reconciliation,
socket handling, or any modification to a decision made upstream.

## Isolation Guarantees

- No import of a broker, the Runtime Execution Service, an
  `ExecutionEngine`, a FYERS SDK, or a Zerodha SDK anywhere in this
  package — only the frozen `PositionPlan` model this module consumes.
- No import of `bujji.core`, `bujji.execution`, `bujji.trade`,
  `bujji.broker`, `bujji.market`, or `bujji.tick` anywhere in this
  package.
- No authentication token, session handle, retry count, or broker
  connection field anywhere on `OrderRequest` or `OrderConstructionResult`.
- `construct_orders()` is a pure function apart from its injectable
  clock; every id is `hashlib.md5`-derived, never `uuid4()`, and no
  randomness of any kind appears anywhere in this package.
- An `OrderRequest` is only ever produced from an already-`PASSED`
  `PositionPlan` — never fabricated when validation fails.

**The Order Construction Service is the final business-logic component
in the Trading Brain pipeline. After this series, every trading
decision has been transformed into a complete, validated,
execution-ready order description. Everything remaining — Runtime
Execution, session management, retries, reconciliation, broker
connectivity, circuit breakers, and production qualification — is
purely operational infrastructure built on top of these deterministic
artifacts.**
