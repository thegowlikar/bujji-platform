# Runtime Health Aggregation

**BUJJI Options OS v3 — Engineering Series 55**

## Status

Deployed. `bujji/production_runtime/health.py` adds
`RuntimeHealthAggregator` and `RuntimeHealthSnapshot` — a single,
deterministic, read-only observability layer over the runtime object
graph built by Series 54's composition root. Health is evidence, not
control: this module never reconnects, retries, recovers,
authenticates, dispatches, or mutates anything it inspects.

## What it inspects

Given whatever evidence a caller supplies, the aggregator classifies
six subsystems, each purely from an already-existing object's own
already-existing state field — never a new state, never a guess:

| Subsystem | Evidence object | Field inspected |
|---|---|---|
| `configuration` | `StartupReport` (Series 54) | `config_valid` |
| `startup` | `StartupReport` (Series 54) | `ready`, `failure_reason` |
| `runtime_session` | `RuntimeSession` (Series 48) | `session_state` |
| `authentication` | `BrokerSession` (Series 49) | `authentication_state`, `session_state` |
| `execution` | `ExecutionSession` (Series 45) | `execution_state` |
| `recovery` | `RuntimeRecoveryResult` (Series 53) | `recovery_status` |

It never inspects broker internals, market data, or trading decisions.

## Health vocabulary

Exactly five values, never free-form: `UNKNOWN`, `HEALTHY`,
`DEGRADED`, `UNHEALTHY`, `INSUFFICIENT_DATA`.

## Applicable vs. missing evidence — a deliberate design point

Not every subsystem is relevant to every run. A fresh boot with no
prior restart has no `RuntimeRecoveryResult` at all — that is not the
same as "recovery evidence was expected and is missing." Conflating
the two would either fabricate a recovery verdict for a run that never
needed one, or silently drag a genuinely healthy system's overall
verdict down to `INSUFFICIENT_DATA` for no real reason.

`RuntimeHealthAggregator.snapshot()` therefore distinguishes:

- **Omitted argument** (the default, an internal `_NOT_APPLICABLE`
  sentinel) → that subsystem is left out of the snapshot entirely. It
  contributes no evidence and does not affect `overall`.
- **Argument explicitly passed as `None`** → the caller is asserting
  "this evidence was expected for this run and is absent." The
  subsystem is scored `INSUFFICIENT_DATA`.

`configuration` and `startup` are always evaluated together from
`startup_report`, since every run — Read Only, Shadow, or Production
Ready construction-only — produces a `StartupReport`.

## Aggregation rules

`configuration` and `startup` are **critical**: without a valid
configuration or a successful startup, nothing else can be trusted.
`runtime_session`, `authentication`, `execution`, and `recovery` are
**operational**: their absence or failure degrades the system but does
not, by itself, mean the whole runtime is down.

Evaluated in order:

1. No subsystems supplied at all → `UNKNOWN`.
2. Any critical subsystem `UNHEALTHY` → overall `UNHEALTHY`.
3. Any critical subsystem `INSUFFICIENT_DATA` → overall
   `INSUFFICIENT_DATA` (we cannot even confirm the system started).
4. Any operational subsystem `INSUFFICIENT_DATA` → overall
   `INSUFFICIENT_DATA`.
5. Any operational subsystem `UNHEALTHY` or `DEGRADED` → overall
   `DEGRADED`.
6. All evaluated subsystems `HEALTHY` → overall `HEALTHY`.

This reproduces the specification's own worked examples exactly:
configuration invalid → `UNHEALTHY`; authentication and execution both
failed/unavailable (real, present objects in a failed state) →
`DEGRADED`; all required subsystems healthy → `HEALTHY`; evidence
genuinely missing → `INSUFFICIENT_DATA`.

## Composition-root integration

`RuntimeHealthAggregator.snapshot_from_composition_root(root, ...)`
reads only `root.config`'s already-public fields (`mode`,
`broker_name`, `replay_status`) to populate `runtime_mode`,
`broker_mode`, and `qualification_mode` — it never constructs,
connects, or calls anything on `root.broker` or `root.execution_engine`.
Session/execution/recovery evidence must still be supplied by the
caller (typically the return value of `runtime.run_read_only()` /
`run_shadow()`), since the aggregator does not own or hold onto any
runtime state itself.

## Logging

One structured `logger.info(...)` call per `snapshot()`, reusing the
existing `logging` module — no new logging framework, no new handler.

## Read-only guarantee

`RuntimeHealthSnapshot` and `SubsystemHealth` are both frozen
dataclasses. No method in `health.py` calls any mutating method on a
supplied object (`.connect()`, `.authenticate()`, `.dispatch()`,
`.recover()`, or any broker method) — confirmed by
`tests/test_runtime_health.py::test_health_check_never_calls_broker_or_network`
and `::test_health_check_is_read_only_no_broker_or_session_mutation`.

## Verification before declaring completion

- 20 new tests, all passing; full regression suite: 1812 passed
  (1792 prior + 20 new), 0 failed.
- Qualification fingerprint unchanged
  (`2328e0f77ec312eeca318946df293d91`, mtime `Jul 20 23:43`).
- No previous Engineering Series file modified.
- Zero broker calls, zero authentication calls, zero dispatch calls,
  zero recovery calls performed anywhere in this module.
