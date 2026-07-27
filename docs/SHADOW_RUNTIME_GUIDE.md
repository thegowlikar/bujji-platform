# Shadow Runtime Guide

**BUJJI Options OS v3 — Engineering Series 54**

Operational guide to running Shadow mode — the default mode for
qualification — against the real, integrated production object graph.

## What Shadow mode is for

Shadow mode exercises the complete decision-to-dispatch pipeline using
production's own real adapter/engine code paths, but with
`root.broker` constructed as `bujji.broker.paper.PaperBroker` — a real,
unmodified production class that holds no network connection. It
proves the entire wiring (translation, session bookkeeping,
authentication flow, dispatch) works end-to-end without any risk of a
live order.

## Running Shadow mode

```python
from bujji.production_runtime.config import RuntimeConfig, RUNTIME_MODE_SHADOW
from bujji.production_runtime.startup import startup
from bujji.production_runtime.runtime import run_shadow, PipelineInput
from bujji.production_runtime.shutdown import shutdown

root, report = startup({"mode": RUNTIME_MODE_SHADOW, "broker_name": "paper"})
assert report.ready

pipeline_input = PipelineInput(
    market_context="TRENDING_UP",      # must be a real MIC v2 Market Context value
    market_opinion="BULLISH",          # must be a real MIC v2 Market Opinion value
    context_stability="STABLE",
    calibration="CALIBRATED",
    governance="APPROVED",
    lifecycle="ACTIVE",
    contract="COMPLETE",
)

result = run_shadow(root, pipeline_input, spot_snapshot=my_spot, option_chain=my_chain)
print(result.trace)
assert result.order_submitted in (True, False)   # never a live broker order either way

shutdown(root)
```

`pipeline_input` fields must be real MIC v2 classification strings —
see `bujji/trading_brain/evidence_interpreter/taxonomy.py` for the full
closed vocabulary per field. An unrecognized value raises `ValueError`
rather than being silently coerced (this is Series 32's own behavior,
unchanged).

`spot_snapshot`/`option_chain` should be real
`NiftySpotSnapshot`/`NiftyOptionChainSnapshot` instances (see
`bujji/trading_brain/nifty_contract_builder/models.py`) sourced from
existing replay inputs for qualification. Passing `None` for either is
supported and does not crash — the Contract Builder returns a
`FAILED`/`INVALID_SPOT` result, which cascades to `ExecutionSession`
never reaching `READY` and `order_submitted=False`. This is the
correct, graceful behavior for missing market data, not a bug: no
stage ever fabricates a snapshot it wasn't given.

## Reading a Shadow result

`ShadowResult.trace` names, in order: how many `OrderRequest`s were
constructed, `ExecutionSession.execution_state`,
`RuntimeAuthorization.authorization_state`/`decision`,
`RuntimeSession.session_state`, `BrokerSession.authentication_state`/
`.session_state`, and `order_submitted`. `order_submitted=True` means
`runtime_execution.dispatch()` reached `DISPATCHED` against
`PaperBroker` — an in-memory simulated fill, never a network call and
never a real order.

## Determinism and qualification

Given the same `PipelineInput`, `spot_snapshot`, `option_chain`, and
injected `clock`, `run_shadow()` produces byte-identical decisions and
IDs (every ID in this pipeline is `hashlib.md5`-derived, never
`uuid4()`). Running Shadow mode does not touch, and is not itself, a
Series 46 replay-qualification run — `QualificationPolicy` is read
verbatim from `RuntimeConfig`, which must already carry the result of
the last successful Series 46 run. Running Shadow mode never changes
`/opt/bujji/qualification/baselines.json`.

## Shutdown is a structural no-op in this mode

`PaperBroker` holds no open connection, thread, or socket, so
`shutdown()` in Shadow mode does not close anything real — it records
that fact honestly in its report rather than fabricating a disconnect
step. The same lifecycle contract exists so that Mode 3 (Production
Ready, real `FyersBroker` constructed) has a defined place to release
resources once a future series adds a live connection lifecycle; this
sprint does not add one.

## What Shadow mode never does

- Never constructs or connects to `FyersBroker`.
- Never places a real broker order (dispatch target is always
  `PaperBroker`).
- Never recomputes or overwrites the qualification fingerprint.
- Never fabricates a snapshot, an `OrderRequest`, or an
  `ExecutionSession` when the real inputs are missing or invalid.
