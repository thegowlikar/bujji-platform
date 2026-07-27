# Runtime Circuit Breaker

**BUJJI Options OS v3 — Engineering Series 56**

## Status

Deployed. `bujji/production_runtime/circuit_breaker.py` adds
`RuntimeCircuitBreaker` and `CircuitDecision` — a single, deterministic
gate that turns a Series 55 `RuntimeHealthSnapshot` into exactly one
decision: permit or deny new work. The Circuit Breaker observes. It
authorizes. It never repairs.

## Why this module does not touch `runtime.py`

The specification requires every new pipeline execution to pass
"Runtime → Health Aggregator → Circuit Breaker → Accept/Reject" before
running. The most direct way to enforce that would be to edit Series
54's `run_read_only()`/`run_shadow()` to call the breaker internally —
but Series 54 is frozen, and this sprint's own rule is not to modify a
previous series absent a discovered integration defect (there is
none here).

Instead, the gate lives entirely in this new module as
`authorize_new_work()`, plus two thin guarded wrappers,
`guarded_run_read_only()` and `guarded_run_shadow()`, that call the
breaker first and only delegate to the existing, unmodified Series 54
functions when the decision is `CLOSED`. When the circuit is not
`CLOSED`, the wrapper returns `(decision, None)` — `run_read_only()`/
`run_shadow()` is never even called, so no stage of the decision
pipeline, and certainly no dispatch, executes.

## Circuit states

Four values, no more: `CLOSED` (runtime may accept new work), `OPEN`
(runtime must reject all new work), `HALF_OPEN` (reserved for a future
operational recovery workflow — classifiable by this vocabulary, but
this sprint never produces or transitions into it), `INSUFFICIENT_DATA`
(not enough evidence to decide safely).

## Decision rule — one deterministic table lookup, no heuristics

| `RuntimeHealthSnapshot.overall` | `CircuitDecision.state` | `permit_new_work` |
|---|---|---|
| `HEALTHY` | `CLOSED` | `True` |
| `DEGRADED` | `OPEN` | `False` |
| `UNHEALTHY` | `OPEN` | `False` |
| `INSUFFICIENT_DATA` | `INSUFFICIENT_DATA` | `False` |
| `UNKNOWN` | `INSUFFICIENT_DATA` | `False` |

The `UNKNOWN → INSUFFICIENT_DATA` row is not one of the specification's
four worked examples; it is a direct, disclosed extension of the same
conservative principle ("not enough evidence exists to decide safely"
applies to `UNKNOWN` exactly as it applies to `INSUFFICIENT_DATA`,
since Series 55's `UNKNOWN` only ever arises when the aggregator was
given no evidence at all). `evaluate()` is a pure dictionary lookup —
no scoring, no probability, no branching on anything other than
`overall`.

## Traceability

Every `CircuitDecision` carries: `state`, `permit_new_work`, the exact
`health_snapshot` it was derived from (never a copy or summary — full
traceability back to Series 55's evidence), `evidence_used` (a
one-line reference to `health_snapshot.overall` and its own
timestamp), a human-readable `reason` naming the health state and
(when supplied) the current `runtime_mode`/`qualification_mode`, and
its own `timestamp` from an injectable clock.

## Read-only guarantee

`RuntimeCircuitBreaker.evaluate()` never calls `connect()`,
`authenticate()`, `dispatch()`, or `recover()` on anything — it reads
one field (`health_snapshot.overall`) and returns a new, immutable
`CircuitDecision`. `CircuitDecision` is a frozen dataclass. Confirmed
by `tests/test_runtime_circuit_breaker.py::test_no_broker_authentication_dispatch_or_recovery_calls`.

## Existing work is unaffected

`guarded_run_shadow()`/`guarded_run_read_only()` only gate *new*
pipeline executions — they take a `RuntimeHealthSnapshot` and a
`PipelineInput` and either run a fresh pipeline or don't; they have no
concept of, and never touch, any already-in-flight
`ExecutionSession`/`RuntimeSession`/`BrokerSession` from a prior call.
This sprint introduces no code path that could interrupt one.

## Composition-root integration

The guarded wrappers accept the `CompositionRoot` (Series 54) produced
by `startup()` directly and read only its already-public `config.mode`
to enrich `CircuitDecision.reason` — they never construct, connect, or
call anything on `root.broker`/`root.execution_engine` themselves
(that remains entirely Series 54's/`runtime.py`'s responsibility, only
reached when the circuit is `CLOSED`).

## Verification before declaring completion

- New tests, all passing; full regression suite green; qualification
  fingerprint unchanged. See the Series 56 deployment report for exact
  counts.
- No previous Engineering Series file modified.
