# Broker Adapter Architecture

**BUJJI Options OS v3 — Engineering Series 40, Sprint 1 (FYERS v1)**

## Status

Deployed. This is the first production-facing integration layer, per
`TRADING_BRAIN_CONSTITUTION.md`. It lives at `bujji/broker_adapter/`
— **outside** `bujji/trading_brain/`, deliberately: this module is not
part of the Trading Brain and is the only module in the whole codebase
permitted to know a broker's name.

```
Trading Brain -> ExecutionInstructionSet -> Broker Adapter -> Broker SDK -> Exchange
```

Nothing above the adapter may know anything about a broker. Nothing
below the adapter may make a trading decision. This sprint builds only
the adapter itself.

## Purpose and Philosophy

The Broker Adapter translates a broker-neutral `ExecutionInstructionSet`
(Series 39) into a broker-specific description of what operations it
implies. It never places an order, never authenticates, never
connects to FYERS or Zerodha servers, and never changes a strategy,
rejects a risk verdict, resizes capital, reinterprets intent, or
predicts a fill. It translates; it does not execute.

## Why This Adapter Reuses Production Naming, Not Production Code

Before writing this sprint, the existing production broker stack
(`bujji/broker/base.py::Broker`, an ABC every real broker — `FyersBroker`,
`PaperBroker`, `HybridPaperBroker` — subclasses; `bujji/core/models.py`'s
`OrderRequest`/`OrderResult`/`OptionContract`) was inspected, per this
sprint's own explicit instruction to plug into existing production
abstractions rather than duplicate them.

This adapter deliberately does **not** import `bujji.broker` or
`bujji.core.models` directly, for two reasons:

1. **`Broker` has real capability this sprint forbids.** Its abstract
   methods include `connect()` and `place_order()` — importing or
   subclassing it would pull in the FYERS SDK dependency chain
   (`fyers_apiv3`) and the actual ability to place an order, both
   explicitly forbidden here.
2. **`OrderRequest` needs data that does not exist yet.** It requires
   an `OptionContract` (strike, expiry) and a `quantity` — no stage in
   the entire Trading Brain, from the Evidence Interpreter through the
   Execution Engine, ever produces a strike, an expiry, or a contract
   quantity. Constructing a real `OrderRequest` here would mean
   fabricating fields, which every module in this project has refused
   to do since MIC v2's first sprint.

Instead, `engine.py::FYERS_ACTION_MAP` reuses only the production
`Broker` ABC's own **method-name vocabulary** (`connect`, `place_order`)
as the naming convention for its abstract-action-to-broker-operation
translation table. A future Runtime Execution Service that wires this
adapter's output to `bujji.broker.fyers.FyersBroker` needs no renaming
— the operation names already correspond 1:1 to the ABC's own methods.
This is the reuse the sprint asked for, applied at the naming/contract
level rather than the code-import level, which is the only level
consistent with this sprint's own prohibitions.

## Input: Exactly One ExecutionInstructionSet

`engine.py::translate()` accepts exactly one `ExecutionInstructionSet`
(or `None`) and a `broker` name — never MIC v2 intelligence, a Market
State Assessment, a Strategy Decision, a Risk Assessment, or a Capital
Decision. Those decisions are already complete; this adapter never
re-derives or inspects them.

## Multi-Broker Design

`ADAPTER_REGISTRY: Dict[str, Dict[str, str]]` maps a broker name to its
own action-translation table. This sprint registers exactly one broker,
`FYERS`, via `FYERS_ACTION_MAP`. Adding Zerodha (or any future broker)
means adding one new entry — `engine.py::translate()`'s own logic never
changes. `taxonomy.BROKER_ZERODHA` is declared now precisely so that
future registration uses an existing, reviewed constant rather than a
freshly-typed string prone to typos; it has no entry in
`ADAPTER_REGISTRY` yet and is therefore unreachable through `translate()`
until a future series adds one.

## The Translation Table (FYERS v1)

| Abstract Action | Broker Operation | Translation Status |
|---|---|---|
| `VALIDATE_PLAN` | `VALIDATE_ORDER_PREREQUISITES` | `TRANSLATED` |
| `VALIDATE_CONTROLS` | `VALIDATE_ORDER_CONTROLS` | `TRANSLATED` |
| `AUTHORIZE_EXECUTION` | `PREPARE_PLACE_ORDER` | `TRANSLATED` |
| `WAIT_FOR_ADAPTER` | *(none)* | `PENDING` — makes no broker call by design; hands off to a future Runtime Execution Service |
| `BLOCK` | *(none)* | `PENDING` — an honest relay of an upstream block, not a broker call |
| anything else (e.g. `EXECUTE`, `COMPLETE`) | *(none)* | `FAILED` / `UNKNOWN_ACTION` — declared in the Execution Engine's own taxonomy but never actually produced by it |

This exactly matches the specification's own worked examples:
`AUTHORIZE_EXECUTION` → *"Prepare Order Request"*, and
`WAIT_FOR_ADAPTER` → *"No broker call... Pending Translation"*.

## The Decision Table

Evaluated top to bottom; first matching rule wins.

| # | Condition | `execution_status` |
|---|---|---|
| 1 | `broker` not registered | `FAILED` / `UNSUPPORTED_BROKER` |
| 2 | No `ExecutionInstructionSet`, or its own `status == UNKNOWN` | `UNKNOWN` / `INVALID_REQUEST` |
| 3 | Defensive: instruction set has no `instruction_set_id` | `FAILED` / `MISSING_FIELD` |
| 4 | `instruction_set.status == BLOCKED` | `BLOCKED` (each action translated, none as a real operation) |
| 5 | `instruction_set.status == READY_FOR_ADAPTER`, every action translates | `TRANSLATED` |
| 6 | Some actions translate, some don't (structurally rare — see the table above) | `PARTIALLY_TRANSLATED` |
| 7 | No action translates | `FAILED` |

Rule 3 is the same defensive discipline every prior sprint has applied
to its own inputs: this adapter never trusts an upstream object it
cannot correlate back to its source.

## Failure Handling: Explicit, Never Silent

Every translation failure names one of five finite reasons —
`UNKNOWN_ACTION`, `UNSUPPORTED_BROKER`, `INVALID_REQUEST`,
`MISSING_FIELD`, `ADAPTER_CONFIGURATION_ERROR` — recorded on both the
per-action `TranslatedAction.failure_reason` and the request-level
`failure_reasons` tuple. `ADAPTER_CONFIGURATION_ERROR` is declared for
taxonomy completeness but never produced this sprint: this adapter
holds no broker credential, endpoint, or environment setting to
misconfigure (`config.py` is deliberately minimal) — that belongs to
whatever future series adds real adapter configuration for the Runtime
Execution Service.

## Translation Trace: Always Explainable

`translation_trace` states the source instruction set's own status,
the broker, every translated action paired with its resulting
operation (or status), and the final execution status — built
entirely from data already present on the input and this function's
own computed verdict.

## Determinism

`request_id` is derived via `hashlib.md5` over the source instruction
set's id (or `"NONE"`), the broker, the execution status, and the
timestamp — never `uuid4()`. `timestamp` is genuinely wall-clock-derived
by design (an injectable `clock` parameter defaulting to
`datetime.now`), the same pattern used throughout the Trading Brain.
Given the same `ExecutionInstructionSet`, the same broker, and the same
fixed clock, `translate()` always produces a byte-identical
`BrokerExecutionRequest`.

## Journaling

`bujji/journal/broker_adapter_journal.py::BrokerAdapterJournal` —
append-only JSONL, own schema (`schema_version` field), independent of
every other journal in the codebase.

## Query API

`broker_adapter/query.py::BrokerExecutionRequestIndex` mirrors the
established `*Index` pattern: `ingest()`, then read-only `latest()`,
`history()`, `find_by_id()`, `find_by_broker()`, `find_by_status()`,
`summary()`. No mutation method beyond `ingest`.

## Runtime Separation

This sprint builds **only** the pure-translation Broker Adapter. A
second, entirely separate component — the **Runtime Execution
Service**, not built this sprint — will eventually own authentication,
session management, token refresh, retries, rate limiting, order
submission, order status polling, fill reconciliation, network
recovery, circuit breakers, and observability:

```
Runtime Execution Service -> Broker Adapter -> Broker SDK -> Exchange
```

Keeping those concerns separate is what keeps this adapter
deterministic and fully unit-testable without a network, a broker
account, or a mock server.

## What This Sprint Explicitly Does Not Build

Per the specification's own explicit exclusions, this sprint contains
zero: FYERS/Zerodha server connections, authentication, token refresh,
order submission, position/order polling, WebSocket handling, retries,
fill management, or position management.

## Isolation Guarantees

- No import of `bujji.broker`, `bujji.core.models`, `bujji.execution`,
  `bujji.trade`, the FYERS SDK, or any REST/HTTP client library
  anywhere in this package.
- No import of MIC v2, or any Trading Brain module upstream of the
  Execution Engine, anywhere in this package — only the frozen
  `ExecutionInstructionSet` model this module consumes.
- No order id, strike, expiry, quantity, client order id, account
  balance, or authentication token field anywhere on
  `BrokerExecutionRequest` or `TranslatedAction`.
- `translate()` is a pure function apart from its injectable clock;
  every id is `hashlib.md5`-derived, never `uuid4()`, and no
  randomness, retry logic, or network call of any kind appears
  anywhere in this package.

**The Broker Adapter translates a broker-neutral `ExecutionInstructionSet`
into a broker-specific description of the operations it implies —
never a broker call. This is the last module the Trading Brain's own
decisions pass through before a future Runtime Execution Service
actually acts on them.**
