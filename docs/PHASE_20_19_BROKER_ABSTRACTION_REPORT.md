# Phase 20.19 — Broker Abstraction & Paper Execution Boundary

**Bujji OS v1.0 Roadmap, Cycle-1 lineage.** Closes the Interface Map's "Execution Intelligence → Broker Boundary → Paper Broker → Execution Feedback → Memory" row. Makes Bujji intelligence execution-system agnostic: the intelligence layer never knows FYERS, Dhan, Zerodha, or any broker API/order format. Brokers are replaceable adapters; Bujji intelligence is permanent.

---

## 1. Audit findings (Step 1)

| Component | Classification | Disposition |
|---|---|---|
| `bujji.broker.base.Broker` (production ABC) | **B) Reusable pattern only** | The real "broker-agnostic interface, swappable adapters" concept this phase mirrors — but its own abstract surface (`connect`/`place_order`/`cancel_order`/`get_order`/`resolve_atm_contract`) IS real order-placement vocabulary. `interface.BrokerAdapter` is deliberately **not** a subclass — no implementation of this phase's boundary can be coerced into that ABC's live-order capability. |
| `bujji.broker_adapter` (Engineering Series 40, MSI/Trading Brain lineage) | **B) Reusable pattern only** | A real, tested "translate an already-produced instruction set into broker-neutral operation names, never an actual broker call" adapter — its own docstring explicitly avoids importing `bujji.broker`/the FYERS SDK chain, for exactly the reason this phase also avoids it. Consumes `ExecutionInstructionSet` from a different lineage (Series 39), not Cycle 1's `ExecutionIntent`/`ExecutionPlan`. Its "never call a broker, only translate" discipline is followed; no code imported. |
| `bujji.core.execution_adapter` / `bujji.integration.execution_adapter` | **C) Wrong domain** | Real `ExecutionPlan → OrderRequest` translation and real production order submission via `ExecutionEngine.submit_and_confirm()`. Not imported. |
| `bujji.trading_brain.risk_governor.position_group_fill_reconciliation` (Gate A) | **B) Reusable pattern only** | Real broker cumulative-fill reconciliation math, requires real broker-reported quantities Cycle 1 does not have. Its "expected vs observed, never silently coerce a discrepancy" discipline is mirrored in `reconciliation.py`; no code imported. |
| `bujji.broker.simulation.{fill_simulator,slippage,market_snapshot}` (Gate F.2), `bujji.execution_intelligence` (Phase 20.18) | **A) Reusable directly** | `PaperBrokerAdapter` calls Phase 20.18's own `simulate_execution()` directly — no fill/slippage/latency math reimplemented. `BrokerResponse.fill_information` reuses `ExecutionResult` directly rather than duplicating its fields. |

## 2. Existing broker systems classification (summary)

The codebase already has: (a) a real production `Broker` ABC with real order-placement obligations, (b) a real, disciplined "translate without calling" adapter pattern for a different lineage, and (c) a real, tested paper simulator this phase already reuses (Phase 20.18). What was missing was a narrow, Cycle-1-scoped boundary interface that can route an execution request to a paper backend without ever inheriting order-placement capability. That is this phase's entire contribution — no existing system is duplicated.

## 3. Interface design

```python
class BrokerAdapter(abc.ABC):
    def submit_execution_request(request, plan, snapshot, *, seed=0) -> BrokerResponse: ...
    def get_execution_status(execution_reference) -> Optional[BrokerResponse]: ...
    def reconcile_execution(request, response) -> ReconciliationResult: ...
```

Deliberately not a subclass of `bujji.broker.base.Broker` — see audit row above. `ExecutionRequest` is a REQUEST (never an order): `direction` is honestly `None`, matching `ExecutionIntent.direction`'s own Phase 20.18 disclosure.

## 4. Paper adapter implementation

`PaperBrokerAdapter` is the ONLY concrete `BrokerAdapter`. `adapter_name` is always `"PAPER"` — never a real broker's name. Its `submit_execution_request()`:
- returns `STATUS_SIMULATION_UNAVAILABLE` (never a fabricated success) when `plan.simulation_required` is `False`, or when `simulate_execution()` itself returns `None`;
- otherwise calls Phase 20.18's real `simulate_execution()` and wraps its `ExecutionResult` as `fill_information`, with a paper-only `execution_reference` (`"PAPER-<strategy>"`, never an exchange order id);
- fails closed on any unexpected exception (`STATUS_ADAPTER_FAILED`) — **never falls back to a live adapter, because no live adapter exists in this package to fall back to.**

`get_execution_status()` is honestly stateless — this phase does not persist submitted requests, so it always returns `None` rather than fabricating a lookup.

## 5. Reconciliation design

`reconcile(request, response)` compares what Bujji asked for against what the adapter actually returned:
- `response is None` → `MISSING_RESPONSE`
- `STATUS_ROUTED_TO_PAPER` with both `fill_information` and `execution_reference` present → `CONSISTENT`
- `STATUS_ROUTED_TO_PAPER` with either missing → `INCONSISTENT` (a genuine adapter malformation, never hidden)
- `STATUS_SIMULATION_UNAVAILABLE` with `fill_information is None` → `CONSISTENT` (the honest "correctly skipped" case)
- `STATUS_SIMULATION_UNAVAILABLE` with `fill_information` present → `INCONSISTENT`
- `STATUS_ADAPTER_FAILED` → always `INCONSISTENT`, never silently treated as success

## 6. Files created

- `bujji/broker_boundary/{__init__,models,interface,paper_adapter,reconciliation,explain}.py`
- `tests/test_broker_boundary.py` (11 tests)
- `scripts/run_phase20_19_validation.py`

**No files modified.** `bujji.broker.simulation.*`, `bujji.execution_intelligence`, `bujji.decision_orchestration`, `bujji.risk_context_adapter`, `bujji.broker.base`, `bujji.broker_adapter` all confirmed untouched by mtime.

## 7. Validation results (real evidence, reused verbatim from Phase 20.5)

**Scenario A — Trend Following strong opportunity** (`EXECUTABLE_CANDIDATE`, `NOT_READY_FOR_CAPITAL_APPROVAL`):
```
TrendFollowing: execution request routed to PAPERBrokerAdapter.
Reconciliation successful. TrendFollowing: routed to PaperBrokerAdapter and a fill was
simulated. Reconciliation successful.
```

**Scenario B — Mean Reversion failed evidence** (`NO_OPPORTUNITY`):
```
No ExecutionIntent -- no ExecutionRequest -- no broker interaction. decision_state='NO_OPPORTUNITY'.
```

**Scenario C — Extreme risk restriction** (`WATCH`, `RESTRICTED`):
```
TrendFollowing: live broker unavailable by design -- PAPERBrokerAdapter did not run a
simulation (risk_context_status='RESTRICTED').
Reconciliation successful. TrendFollowing: simulation was correctly not run; no
fill_information present. Reconciliation successful.
```

`evidence_score` (78.62) confirmed unmodified throughout.

## 8. Tests (11, all passing)

1. Contract correctness: `ExecutionIntent → ExecutionRequest` translation, `direction` honestly `None`
2. Paper routing: request reaches `PaperBrokerAdapter`, `fill_information` populated, `execution_reference` correctly namespaced
3. No live broker leakage: AST-verified zero imports of `bujji.broker.fyers`/`hybrid`/`base`, `fyers_apiv3`, `dhanhq`, `zerodha`, `kiteconnect`
4. No order capability: zero `place_order`/`modify_order`/`cancel_order`/`get_open_positions`/`get_order` calls anywhere in the package
5–6. Reconciliation: matching states → `CONSISTENT`; missing response → `MISSING_RESPONSE`; malformed response → `INCONSISTENT` with mismatch explanation
7. Decision preservation: `evidence_score`/`allocation_class` byte-identical before/after; neither `ExecutionRequest` nor `BrokerResponse` carries evidence/ranking/qualification fields
8. Failure handling: `RESTRICTED` risk status → `STATUS_SIMULATION_UNAVAILABLE`, `adapter_name` still `"PAPER"` (never a live fallback, because none exists)
9. `get_execution_status()` honestly returns `None` (stateless, disclosed)
10. Explainability: every response and reconciliation result carries a non-empty, strategy-named explanation

## 9. Regression

Full suite: **6,405 passed, 0 failed** (6,394 baseline from Phase 20.18 + 11 new; clean run, no environmental flakes). `bujji/broker_boundary/`'s own 11 tests: 11/11 passing, both standalone and inside the full suite.

## 10. Safety verification

`grep`/`ast`-based tests confirm zero forbidden order-placement patterns and zero imports of any live-broker module or third-party broker SDK anywhere in `bujji/broker_boundary/`. `bujji.broker.simulation.*`, `bujji.execution_intelligence`, `bujji.decision_orchestration`, `bujji.risk_context_adapter`, `bujji.broker.base`, `bujji.broker_adapter` confirmed byte-identical by mtime — MIC, Memory, Decision Engine, Risk Governor, and Capital Intelligence are all untouched. `PaperBrokerAdapter` is the only concrete adapter; `adapter_name` is always `"PAPER"`.

## 11. Updated roadmap position

```
Execution Intelligence → Broker Boundary → Paper Broker → Execution Feedback → Memory
       ✅ (20.18)              ✅ (this phase, interface + paper adapter)          ⏳
```

Bujji can now route any real, evidence-qualified opportunity's execution intent through a replaceable, broker-agnostic boundary to a real paper simulation backend, with honest reconciliation and zero path to a live order anywhere in the code. The real next dependency for closing the "Execution Feedback → Memory" arrow is a Shadow Result composition step (flagged, not built, in Phase 20.18's own report) that would package `BrokerResponse`/`ReconciliationResult` alongside the existing `DecisionObservation` for persistence into Market Memory — left for a future phase, not fabricated here.
