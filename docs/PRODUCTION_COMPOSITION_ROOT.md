# Production Composition Root

**BUJJI Options OS v3 — Engineering Series 54**

## Status

Deployed. For the first time, the entire object graph — MIC v2's
published output, the Trading Brain (Series 32–47), the Runtime
(Series 45, 47–53), and production's own broker/execution stack — is
constructed inside one process, via explicit dependency injection, with
zero globals and zero hidden singletons.

## Package location, and a discovered naming collision

The deliverables were specified as `bujji/app/{composition_root.py,
runtime.py, startup.py, shutdown.py, config.py}`. During deployment,
this collided with an **already-existing** `bujji/app.py` (dated
2026-07-19), which defines the production `Application` class used by
the process-lock/restart tests (`tests/test_f4_process_lock.py`).
Python resolves a package directory (`bujji/app/`) ahead of a
same-named module (`bujji/app.py`) on `sys.path`, so introducing the
new package silently shadowed `bujji.app.Application` — confirmed by a
full-suite regression run that failed two process-lock tests with
`ImportError: cannot import name 'Application' from 'bujji.app'`.

This is exactly the kind of integration truth this sprint is supposed
to surface, not conceal. Rather than renaming or touching the
pre-existing `bujji/app.py` (an existing Engineering Series file, off
limits per this sprint's own rule), the new package was renamed to
**`bujji/production_runtime/`**. All five deliverable files keep their
specified names and content; only the containing package changed. The
full regression suite (1792 tests) passes with this layout, and
`bujji.app.Application` imports correctly again.

## The MIC v2 process-boundary discovery

MIC v2 lives in a completely separate Python environment
(`/opt/bujji-mic-v2/`, its own venv, its own repository) from this
project's own package (`/opt/bujji/app/`). It cannot be imported or
instantiated inside this process — there is no shared interpreter, no
shared virtualenv, and no package path connects the two.

The composition root's pipeline therefore begins at the **Evidence
Interpreter** (Series 32), consuming already-published MIC v2
classification strings exactly as Series 32 has always done, rather
than constructing MIC v2 objects directly. This was true before this
sprint too — Series 54 does not introduce the boundary, it is the
first sprint to have to construct "everything" in one process and
therefore the first to have to state the boundary explicitly.

## `QualificationPolicy` as a build-time attestation

Series 47's `QualificationPolicy` (`replay_status`,
`qualification_fingerprint`, `chain_valid`, `deterministic`) describes
whether *this build* of the pipeline has already been replay-qualified
(Series 46) — a release-time fact, not something recomputed inside a
live runtime process. `RuntimeConfig` carries these four fields
verbatim, populated with the result of the *last successful* Series 46
replay-qualification run by whatever release process produced the
configuration. `runtime.py`'s `run_shadow()` constructs the
`QualificationPolicy` directly from `RuntimeConfig`, never by
re-running Series 46 or by inspecting the qualification baseline file
at runtime.

## True dependency order vs. the specification's own diagram

Consistent with the Series 46 precedent, the specification's own
architecture diagram places the NIFTY Contract Builder / Position
Sizing / Order Construction stages after the Broker Adapter. The
actual code dependencies say otherwise: NIFTY Contract Builder needs
only `StrategyDecision` + `CapitalDecision` (both already available
straight out of the decision pipeline), and Order Construction must
complete *before* `runtime_execution.build_session()` can accept
`OrderRequest`s. `runtime.py`'s `run_shadow()` therefore calls, in true
order: decision pipeline → trading-brain Execution Engine (Series 39)
→ Broker Adapter (Series 40, translation only, side-channel) → NIFTY
Contract Builder → Position Sizing → Order Construction → Runtime
Execution Orchestrator's `build_session()` → Safety Gate → Session
Manager → Authentication → dispatch. The Broker Adapter's translated
`BrokerExecutionRequest` is retained on `ShadowResult` for
observability but does not gate any later stage — the diagram's
apparent ordering is illustrative, not the true call graph, exactly as
already established in Series 46's own documentation.

## Reusing production's own `PaperBroker` as the Shadow dispatch target

The specification requires Shadow mode's Execution Adapter to
"terminate at a mock/no-op dispatch target" and never submit a real
order. Rather than inventing a new mock/duplicate broker class, Shadow
mode wires the real, unmodified, already-existing
`bujji.broker.paper.PaperBroker` as `root.broker`, with the real
`ProductionExecutionAdapter`/`ExecutionEngine` wrapped around it exactly
as they would be wrapped around any other broker. This exercises the
entire real adapter/engine code path — order translation, session
bookkeeping, retry/backoff configuration — with zero network I/O and
zero duplicated mock logic. `PaperBroker` holds no open
connection/socket/thread, so this also keeps shutdown a genuine
no-op in this mode (see `SHADOW_RUNTIME_GUIDE.md`).

## Mode 3 (Production Ready): construction only

`RuntimeConfig(mode="PRODUCTION_READY", broker_name="fyers")` causes
`build_composition_root()` to construct a **real**
`bujji.broker.fyers.FyersBroker` via `BrokerConfig(name="fyers")`.
`FyersBroker.__init__` performs no network I/O of its own — it only
builds an internal `FyersTokenManager` instance — so this construction
never connects to a live broker. `verify_production_ready_construction()`
checks only that `broker`, `execution_engine`, `authentication_adapter`,
and `execution_adapter` are non-`None`; it never calls `connect()`,
`authenticate()`, or `dispatch()`.

## The three explicitly-disclosed gaps (unchanged, not hidden)

1. **Runtime `OrderRequest` has no price field.** Only `MARKET` orders
   are fully supported by `order_construction`/`runtime_execution`;
   `LIMIT`/`STOP` orders remain unsupported. This composition root does
   not close this gap — it constructs `ExecutionPolicy(policy="MARKET")`
   and documents the limitation rather than fabricating price support.
2. **`ExecutionSession` reconstruction remains incomplete.** Where a
   prior series (Series 53, `RuntimeRecoveryResult`) already returns
   `execution_session=None` because the original `OrderRequest`s and
   dispatch plan aren't recoverable from production's own recovery
   snapshot, this composition root does not attempt to fabricate that
   history. Shadow-mode runs always build a *fresh*
   `ExecutionSession` from freshly-constructed `OrderRequest`s — it
   never claims to reconstruct a historical one.
3. **FYERS live order placement remains unverified.** Shadow mode's
   dispatch target is always `PaperBroker`, never `FyersBroker`; Mode 3
   (Production Ready) never calls `dispatch()` at all. No code path
   introduced by this sprint places, or could place, a real FYERS
   order.

## Verification before declaring completion

- Full object graph (broker, execution engine, both adapters, six
  policy objects) constructs successfully for both `paper` and `fyers`
  broker selection (Mode 3), confirmed by
  `tests/test_composition_root.py`.
- Read Only mode runs the complete decision pipeline
  (Evidence Interpreter → Execution Planner) and stops, confirmed
  deterministic across repeated runs with a fixed clock.
- Shadow mode runs the complete pipeline through dispatch against
  `PaperBroker`, confirmed to never submit a live broker order and to
  behave deterministically given the same inputs and clock.
- Startup sequence completes in the exact specified order and performs
  no market activity; `startup()` never raises an uncaught exception,
  even on invalid configuration or a simulated construction failure.
- Shutdown sequence is honest about being a structural no-op in
  Read-Only/Shadow mode (see `SHADOW_RUNTIME_GUIDE.md`).
- Full regression suite: **1792 passed**, 0 failed, after the
  `bujji/production_runtime/` rename.
- Qualification fingerprint (`/opt/bujji/qualification/baselines.json`,
  md5 `2328e0f77ec312eeca318946df293d91`, mtime `Jul 20 23:43`)
  **unchanged** before and after this sprint's full test run.
