# Production Execution Adapter

**BUJJI Options OS v3 — Engineering Series 52, Sprint 1**

## Status

Deployed. This closes the second (and, per the Series 50 review, last)
concrete integration seam: `ExecutionEngineInterface` (Series 45) was
already designed correctly, but no concrete implementation of it
existed. This sprint adds exactly one.

## What This Adapter Reuses, Unchanged

`bujji.execution.engine.ExecutionEngine.submit_and_confirm()` is the
single entry point this adapter calls. Reading it (and confirmed
again for this sprint) shows it already owns, entirely internally:

- **Retry-with-backoff**, via `ExecutionEngine._with_retry()`.
- **Idempotent placement**, via a lookup-before-place check against
  `client_order_id`.
- **Fill polling**, via `ExecutionEngine._await_fill()`, with a
  best-effort cancel on timeout.
- **Reconciliation**, via the separate `ExecutionEngine.reconcile()`
  method (this adapter does not call it — reconciliation is not part
  of a single order's submission, and this sprint never invokes it).

**None of this is duplicated here.** The adapter calls
`submit_and_confirm()` exactly once per invocation and does nothing
else with production's retry/idempotency/polling machinery.

## The Type Translation Problem

Production's `bujji.core.models.OrderRequest` (used by `ExecutionEngine`)
and this project's own `bujji.trading_brain.order_construction.models.OrderRequest`
(Series 44, "runtime OrderRequest" below) are **different types with
different field shapes**, despite the similar name:

| Field | Production `OrderRequest` | Runtime `OrderRequest` (Series 44) |
|---|---|---|
| Contract | `OptionContract(symbol, underlying, strike, option_type, expiry, lot_size)` | `NiftyOptionContract(contract_symbol, underlying, strike, option_type, expiry, side, ...)` — **no `lot_size`** |
| Side | `Side` enum | plain string, same values (`BUY`/`SELL`) |
| Quantity | `int` (lots × lot_size) | `int` (same concept, same value) |
| Price | `limit_price: Optional[float]` | **no price field at all** — only `execution_policy` (a name: `MARKET`/`LIMIT`/`STOP`/`STOP_LIMIT`) |
| Idempotency key | `client_order_id` | `client_order_id` — **identical field name and semantics**, direct passthrough |

`_translate_order_request()` performs this translation. Two disclosed,
necessary decisions:

1. **`lot_size` is supplied explicitly by the adapter's own caller**
   (the constructor's `lot_size` parameter) — the same
   `LotSpecification.lot_size` value already used by the Position
   Sizing Engine (Series 43). `NiftyOptionContract` never carried this
   value, and this adapter never fabricates or infers one; it is
   passed in, exactly as `broker_identity` was in Series 51's
   `ProductionAuthenticationAdapter`.
2. **`limit_price` is always `None` in this v1 adapter.** Order
   Construction (Series 44) never persists a numeric price onto the
   final runtime `OrderRequest` — only the finite `execution_policy`
   name (`MARKET`/`LIMIT`/`STOP`/`STOP_LIMIT`) survives. A price-bearing
   policy therefore cannot yet carry a real price through to
   production via this adapter. Only `MARKET` orders are fully
   supported end-to-end this sprint — a disclosed v1 limitation, not a
   silent gap, and a natural candidate for a future series to close
   (by extending Order Construction to persist a real price, not by
   working around it here).

## The Result Translation Problem — Why `ExecutionResult` Is New

The specification calls for "no new result vocabulary," expecting an
existing "runtime model" to be reused, the way Series 51 reused
`AuthenticationOutcome` (a type Series 49 already defined). But no
such type exists: Series 45's `ExecutionEngineInterface.submit_and_confirm()`
was declared with a generic `Any` return type, and
`runtime_execution.engine.dispatch()` never inspects or stores what it
returns — only whether it raises. There is genuinely nothing to reuse.

`ExecutionResult` (this module's own frozen dataclass) is therefore a
disclosed, necessary addition — not a silent scope expansion. Its
`status` field is copied **verbatim** from production's own
`bujji.core.enums.OrderStatus` values (`PENDING`, `FILLED`, `PARTIAL`,
`REJECTED`, `CANCELLED`, `UNKNOWN`) whenever a real `OrderResult` was
actually produced — never invented. The **one** exception: when
`ExecutionEngine.submit_and_confirm()` raises `ExecutionError` (its own
documented behavior on a **zero** fill after retries are exhausted),
there is no real `OrderResult` to report a status from at all — this
adapter uses the literal string `"FAILED"`, a translation-layer-only
marker that is never a `bujji.core.enums.OrderStatus` member, and
documents this distinction explicitly rather than blending it into
production's own vocabulary.

## Translation Rules

```
Runtime OrderRequest
        │
        ▼  _translate_order_request() -- pure field mapping
Production OrderRequest
        │
        ▼  ExecutionEngine.submit_and_confirm()  (unchanged)
        │
   ┌────┴────┐
   │         │
success   ExecutionError raised
   │         │
   ▼         ▼
OrderResult   ExecutionResult(status="FAILED", message=str(exc))
   │
   ▼
ExecutionResult(status=result.status.value, ...)
```

Any exception other than `ExecutionError` is **also** translated into
a `status="FAILED"` `ExecutionResult` — never suppressed, never
re-raised past the adapter, and always recorded verbatim in the trace.

## Traceability

Every `submit_and_confirm()` call records, in `self.last_trace`: the
runtime request (by `client_order_id`), the production method invoked,
the production response (the real `OrderResult` summary, or the exact
exception), and the resulting `ExecutionResult` — matching the
specification's own requirement that every invocation record all four.

## The Sync/Async Bridge — The Same Disclosed v1 Limitation as Series 51

`ExecutionEngineInterface.submit_and_confirm()` is declared as a plain
synchronous method; `ExecutionEngine.submit_and_confirm()` (production)
is `async def`. `_run_async()` bridges the two via `asyncio.run()`,
raising explicitly (rather than nesting event loops) if called from
within an already-running loop — the identical, disclosed limitation
documented in Series 51's `ProductionAuthenticationAdapter`.

## What This Sprint Explicitly Does Not Do

Per the specification's own explicit exclusions, this adapter contains
zero: retry logic, reconciliation logic, fill polling of its own,
idempotency logic of its own, or broker-specific business logic beyond
one field-mapping translation and one outcome translation.

## Verification Before Declaring Completion

- **Only one new adapter file was added.** Confirmed:
  `bujji/integration/execution_adapter.py`.
- **No production execution files were modified.** Confirmed:
  `bujji/execution/engine.py` and `bujji/core/models.py`/`enums.py`
  were read, never edited.
- **Existing retry, reconciliation, idempotency, and polling remain
  entirely inside `ExecutionEngine`.** Confirmed by inspection and by
  this sprint's own isolation tests (no retry loop, no polling loop, no
  reconciliation call anywhere in this adapter's source).
- **All tests pass**; see `tests/test_production_execution_adapter.py`.
- **The qualification fingerprint for the deterministic pipeline
  (Series 31-51) remains unchanged** — this sprint adds an integration
  layer only, touching no business logic.

## Isolation Guarantees

- No import of the FYERS SDK, `bujji.broker.fyers`, or any network
  library anywhere in this module.
- No modification to any file under `bujji/execution/`, `bujji/core/`,
  or `bujji/broker/`.
- No retry loop, no polling loop, no reconciliation call, and no
  idempotency check of this adapter's own anywhere in this module —
  `submit_and_confirm()` is called exactly once per invocation.
- Every production error is translated into a `status="FAILED"`
  `ExecutionResult` — never swallowed, never re-raised past the
  adapter, never turned into a fabricated fill.

**With this sprint complete, both Protocol seams identified during the
Series 50 Runtime Integration Review — `AuthenticationProviderInterface`
and `ExecutionEngineInterface` — are now backed by real production
adapters. From here, remaining work shifts to operational resilience:
recovery integration, health monitoring integration, circuit breaker,
rate limiter, and production qualification — not additional
integration layers.**
