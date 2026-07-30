# EQ1 — BUJJI Execution Qualification Sprint

**Trading Brain v3 (`bujji/production_runtime/`) — Production Qualification**

This is an evidence-gathering sprint. The only code executed was a real, unmodified call into existing engine functions (no `bujji/` source was written or changed) plus one small, uncommitted diagnostic script used to drive it and inspect results. All numbers, traces, and states quoted below are real output, captured on the server, not reconstructed from memory or test names alone — where a claim rests only on a test's name rather than its actual (re-)run output, that is stated explicitly.

---

## Deliverable 1 — Qualification Matrix

Legend: **Exists** (real, non-stub code found) / **Tested** (real unit tests found, count noted) / **Executed** (called at least once outside its own test suite, in this sprint or before) / **Proven** (executed with *real* market data producing a real, inspectable output, in this sprint) / **Ready** (this sprint's own judgment on production-readiness).

| Component | Exists | Tested | Executed | Proven (real data) | Ready |
|---|---|---|---|---|---|
| Evidence Interpreter | YES | YES (46) | YES (this sprint) | **YES** | YES |
| Market State | YES | YES (63) | YES (this sprint) | **YES** | YES |
| Strategy Selector | YES | YES (78 across 2 files) | YES (this sprint) | **YES** — selected `DIRECTIONAL_CALL_SPREAD` on real evidence | YES |
| Risk Brain | YES | YES (72) | YES (this sprint) | **YES** — real `APPROVED`/`ALLOW` | PARTIAL — market-condition gate only, no numeric limits (see Gap Register) |
| Capital Brain (`trading_brain/`) | YES | YES (63) | YES (this sprint) | **YES** — real `STANDARD`/`APPROVED` | PARTIAL — policy label only, no capital/margin number |
| Execution Planner | YES | YES (64) | YES (this sprint) | **YES** — real `PLANNED` status | YES for its narrow scope |
| (trading_brain) Execution Engine | YES | YES (51) | YES (this sprint) | **YES** — real `READY_FOR_ADAPTER`, 4 abstract actions | YES for its narrow scope |
| Broker Adapter | YES | not directly audited this sprint | YES (this sprint) | **YES** — real `TRANSLATED` actions targeting FYERS operation names | YES for its narrow scope |
| Nifty Contract Builder | YES | YES (64+) | YES (this sprint) | **YES** — real strikes (24250/24300 CE) matched against a real Bhavcopy chain | YES, with the caveat that chain input must be supplied (no live-fetch integration confirmed) |
| Position Sizing | YES | YES (67) | YES (this sprint) | **YES** — real 2 lots/leg, 150 qty | YES for its narrow scope |
| Order Construction | YES | YES (69) | YES (this sprint) | **YES** — 2 real `OrderRequest`s, real `client_order_id`s | YES for its narrow scope |
| Runtime Execution (dispatch) | YES | YES (57) | YES (this sprint) | **YES** — real `DISPATCHED` state | YES for its narrow scope |
| Runtime Safety (authorization) | YES | YES (57) | YES (this sprint) | **YES** — 9/9 checks passed, 1 explainable warning | YES for its narrow scope |
| Runtime Session / Authentication | YES | not directly audited this sprint | YES (this sprint) | **YES** — real `ACTIVE`/`AUTHENTICATED`/`READY` states | not independently verified this sprint |
| **`bujji/execution/engine.py::ExecutionEngine`** (real broker I/O) | YES | YES (96 tests re-run this sprint, all passing, across 7 files) | YES (pre-existing tests + this sprint's own indirect exercise via dispatch) | PARTIAL — proven against PaperBroker (this sprint's run reached `DISPATCHED`); never proven against a real FyersBroker `place_order()` call (correctly, per the sprint's own "do not touch live broker" rule) | PARTIAL |
| **PaperBroker** | YES | tested indirectly (via `test_hybrid_paper_broker.py`, `test_execution_and_e2e.py`); no dedicated `test_paper.py` | YES | **YES** — real fills recorded: `BUY 150 NIFTY2680424250CE @120.0`, `SELL 150 NIFTY2680424300CE @120.0` | YES, **with a real, newly-confirmed fidelity limitation** — see below |
| **FyersBroker** | YES | YES (mapping-level, `test_fyers_transport_mapping.py`, 23 tests) | Construction-only, never connected in this or any prior sprint | NO — never exercised with real network I/O | NOT READY |
| Margin validation (real numeric, `bujji/capital/`) | YES, **but in a different, unconnected module** | YES (`test_capital_engine.py`, `test_capital_providers.py` — zero-margin rejection, uncertified-margin blocking) | Not confirmed wired into `production_runtime`'s pipeline | NO — this sprint's real trace produced a `CapitalDecision`/`OrderRequest` with **no margin figure anywhere** | NOT READY as wired into this pipeline today |
| Circuit breaker / rate limiter | YES, in `bujji/runtime_*` (older pipeline) | YES (`test_runtime_circuit_breaker.py`, `test_runtime_rate_limiter.py`) | Not confirmed wired into `production_runtime` specifically | NOT CONFIRMED | NOT READY as wired into this pipeline today |

---

## Deliverable 2 — Complete Order Trace (real, executed this sprint)

**Real market evidence used**: the same 2026-07-29 Bhavcopy already used in Live Shadow Session #3 (`data/bhavcopy/BhavCopy_NSE_FO_0_0_0_20260729_F_0000.csv`) — real spot `24250.2`, real nearest expiry `2026-08-04`, 40 real near-ATM strike/premium entries loaded into a real `NiftyOptionChainSnapshot`.

```
PipelineInput(TRENDING_UP/BULLISH/STABLE/CALIBRATED/APPROVED/ACTIVE/COMPLETE)
  → evidence_interpreter.interpret()
      → TradingOntologySnapshot(market_state=TREND, opportunity_state=HIGH_EDGE,
                                  risk_state=NORMAL, strategy_intent=DIRECTIONAL_BULLISH,
                                  capital_intent=NORMAL, confidence=VERY_HIGH)
  → market_state.assess()
      → MarketStateAssessment(market_state=TREND, confidence=VERY_HIGH)
  → strategy_selector.select()
      → StrategyDecision(selected_strategy=DIRECTIONAL_CALL_SPREAD, status=SELECTED)
  → risk_brain.assess()
      → RiskAssessment(status=APPROVED, approval=ALLOW)
  → capital_brain.authorize()
      → CapitalDecision(capital_intent=STANDARD, status=APPROVED)
  → execution_planner.plan()
      → ExecutionPlan(status=PLANNED)
  → trading_execution_engine.orchestrate()
      → ExecutionInstructionSet(status=READY_FOR_ADAPTER,
                                  actions=[VALIDATE_PLAN, VALIDATE_CONTROLS,
                                           AUTHORIZE_EXECUTION, WAIT_FOR_ADAPTER])
  → broker_adapter.translate()
      → BrokerExecutionRequest(broker=FYERS, execution_status=TRANSLATED,
             VALIDATE_PLAN→VALIDATE_ORDER_PREREQUISITES,
             VALIDATE_CONTROLS→VALIDATE_ORDER_CONTROLS,
             AUTHORIZE_EXECUTION→PREPARE_PLACE_ORDER, WAIT_FOR_ADAPTER→PENDING)
  → nifty_contract_builder.build_contracts()  [REAL Bhavcopy chain]
      → ContractConstructionResult(status=CONSTRUCTED, 2 legs:
             BUY  24250 CE  "ATM"  (matched NSE:NIFTY2680424250CE)
             SELL 24300 CE  "OTM1" (matched NSE:NIFTY2680424300CE))
  → position_sizing.size_position()
      → PositionPlan(validation=PASSED, lots_per_leg=2)
  → order_construction.construct_orders()
      → OrderConstructionResult(status=CONSTRUCTED, 2 real OrderRequests,
             side=BUY qty=150 / side=SELL qty=150, order_type=MARKET, product=MIS)
  → runtime_execution.build_session() → queue_for_dispatch() → dispatch(executor=ProductionExecutionAdapter)
      → ExecutionSession(execution_state=DISPATCHED)
  → runtime_safety.authorize()
      → RuntimeAuthorization(decision=ALLOW, state=AUTHORIZED_WITH_WARNINGS,
             passed=[EXECUTION_SESSION_VALID, QUALIFICATION_FINGERPRINT_PRESENT,
                     PIPELINE_COMPLETED_SUCCESSFULLY, DISPATCH_PLAN_NOT_EMPTY,
                     CLIENT_ORDER_IDS_UNIQUE, ORDER_COUNT_CONSISTENT,
                     REPLAY_QUALIFICATION_PASSED, CONFIGURATION_VERSION_RECOGNIZED,
                     RUNTIME_POLICY_RECOGNIZED],  (9/9)
             warnings=[SESSION_NOT_YET_DISPATCHED])  ← benign, chronological (authorization
                                                          runs before dispatch by design)
  → runtime_session / authentication engines
      → RuntimeSession(ACTIVE), BrokerSession(AUTHENTICATED/READY)
  → ProductionExecutionAdapter.submit_and_confirm() → ExecutionEngine.submit_and_confirm()
      → Broker.place_order()  [PaperBroker, real in-memory fill]
  → order_submitted = True
```

**Real PaperBroker ledger after dispatch** (queried live, this sprint): `[{'symbol': 'NSE:NIFTY2680424250CE', 'side': 'BUY', 'qty': 150, 'avg_price': 120.0}, {'symbol': 'NSE:NIFTY2680424300CE', 'side': 'SELL', 'qty': 150, 'avg_price': 120.0}]`

**A real fidelity limitation, found and explained, not left as a mystery**: both legs filled at an identical `120.0`, despite the real Bhavcopy showing genuinely different premiums for these strikes (~124 for 24250 CE, ~100 for 24300 CE). Traced to source: `bujji/broker/paper.py` — `self._premium.setdefault(symbol, 120.0)`, a hardcoded default fill price used whenever no `limit_price` is supplied and no prior price has been recorded for that symbol. **The real Bhavcopy premiums were used correctly upstream, for strike *selection*** (contract-builder's own construction trace literally quotes the real spot and ATM math) — **but they are never propagated into PaperBroker's own fill simulation.** This is a real, concrete, previously-unconfirmed gap in paper-trading fidelity: PnL simulated from a multi-day paper run would not reflect real premium spreads between legs.

---

## Deliverable 3 — Full Paper Trade

**Result: SUCCEEDED, first time in this codebase's history.** Every stage in the sprint's own requested list was demonstrated with real evidence in the trace above: decision generated ✓, strategy selected ✓ (`DIRECTIONAL_CALL_SPREAD`), position sized ✓ (2 lots/leg), order constructed ✓ (2 real `OrderRequest`s), order dispatched ✓ (`DISPATCHED`), paper fill ✓ (real ledger entries), position tracking ✓ (`get_open_positions()` returned the real fills). **Exit and reconciliation were not exercised in this sprint** — this run constructed one entry and stopped; no exit order was constructed or dispatched, and `reconcile()` was not called. This is a genuine, disclosed gap in this sprint's own coverage, not a pipeline failure — the entry path is proven; the exit path was simply not driven in this run.

No stage failed. Nothing required the run to stop early.

---

## Deliverable 4 — Safety Boundary Verification (concrete, executed evidence)

**Q1: Can SHADOW mode reach a real, unguarded FyersBroker?**
Executed: `build_composition_root(RuntimeConfig(mode="SHADOW", broker_name="fyers"))`.
Result: **construction succeeded.** `type(root.broker).__name__ == "FyersBroker"`. `root.broker.place_order` resolves to the real, unstubbed `FyersBroker.place_order` — confirmed by qualname, **not** a `disable_live_execution()`-wrapped stub. **Yes, structurally possible.**

**Q2: Can PAPER ever reach live execution?**
Executed: `build_composition_root(RuntimeConfig(mode="PRODUCTION_READY", broker_name="paper"))` — succeeds, constructs a real `PaperBroker`. No code path found anywhere that silently substitutes a different broker than `broker_name` declares. **No — paper stays paper, correctly, in every combination tested.**

**Q3: Can LIVE accidentally instantiate PaperBroker?** Same evidence as Q2, inverted: `broker_name` is the sole determinant, and it is honored exactly. **No.**

**Q4: Is mode enforced structurally, or only by configuration?**
Executed: read `RuntimeConfig.__post_init__()` in full (quoted verbatim in this sprint's own diagnostic output) — it validates that `mode` and `broker_name` are each individually *recognized* values, but performs **zero cross-validation between them.** Executed: searched `run_shadow()`'s real source for any `isinstance(root.broker, PaperBroker)` check or equivalent — **none exists.** The only occurrence of the string "PaperBroker" anywhere in `run_shadow()`'s source is a code comment.

**Conclusion, stated plainly: mode is enforced only by configuration and convention, not by code.** A `RuntimeConfig(mode="SHADOW", broker_name="fyers")` root, passed to `run_shadow()` — a function whose own docstring promises *"never a live broker order"* — would proceed through the entire real pipeline traced in Deliverable 2 and call `FyersBroker.place_order()` for real. This was **not executed** in this sprint (per the explicit "do not touch live broker" rule) — evidence stops at proving the construction succeeds and no guard intercepts it, which is sufficient to prove the gap without taking the risk.

---

## Deliverable 5 — Failure Qualification

Rather than re-inventing failure injection from scratch, this sprint re-ran the real, existing test suites that already drive the real `ExecutionEngine` + `PaperBroker` (not mocks) through these exact scenarios, to get fresh, current pass/fail evidence rather than trusting test names alone:

**Re-run this sprint: 96/96 tests passed**, across `test_tier1_capital_protection.py`, `test_sprint3_failure_injection.py`, `test_e1_e2_auth_expiry.py`, `test_execution_and_e2e.py`, `test_shadow_runtime.py`, `test_composition_root.py`, `test_production_execution_adapter.py`.

| Scenario | Expected behaviour | Actual behaviour (from real, re-run tests) | Evidence |
|---|---|---|---|
| Order rejected | Raise `ExecutionError`, no position opened | Confirmed | `test_execution_and_e2e.py` design + `ExecutionEngine.submit_and_confirm` source |
| Partial fill | Truthful `filled_quantity`, caller sizes off actuals | Confirmed | `test_c4_entry_sizes_off_actual_fill` PASSED |
| Duplicate acknowledgement | Same `client_order_id` adopted, not re-placed | Confirmed | `test_c3_duplicate_submit_same_cid_is_adopted` PASSED |
| Lost acknowledgement | Query-before-retry, never blind duplicate | Confirmed | `test_c3_no_duplicate_when_place_errors_after_accept` PASSED |
| Restart during open position | Resume/reconcile correctly (in-position, orphaned, or already-flat) | Confirmed, 3 distinct scenarios | `test_c1_resume_open_position`, `test_c1_position_already_flat`, `test_c1_orphan_position_flattened` all PASSED |
| Broker unavailable (disconnect during planning) | Rolls back to READY, no stuck state | Confirmed | `test_broker_disconnect_during_planning_rolls_back_to_ready` PASSED |
| Session/auth expiry | Never opens a position; preserves an existing one; retries on exit | Confirmed, 15 distinct scenarios | `test_e1_e2_auth_expiry.py`, all PASSED |
| Timeout (order not filled in time) | Cancel remainder | **Present in `ExecutionEngine._await_fill` source (read directly), but no test with this exact scenario name was found in this sprint's search** — the behavior exists in code; a dedicated test proving it was not located | **Code confirmed, dedicated test NOT confirmed — real gap in test coverage, not necessarily in behavior** |
| Cancel failure (cancel itself errors) | Best-effort, logged, never raises | `_safe_cancel` source confirms `except Exception: log, never raise` | Code confirmed; dedicated test not located this sprint |
| Margin rejection | Reject trade when margin insufficient | **Real logic exists** (`test_capital_engine.py::test_zero_available_margin_rejects_trade`, PASSED when re-run) — **but in `bujji/capital/`, not confirmed wired into `production_runtime`'s pipeline** (this sprint's own real trace shows no margin check occurred before dispatch) | Capability proven in isolation; integration into this specific pipeline NOT proven |

**Bottom line on Deliverable 5: the mechanisms that matter most (idempotency, partial fills, restart recovery, auth-expiry safety) are real, tested, and re-confirmed passing today. Two real gaps: timeout/cancel-failure have code but no dedicated test found; margin rejection exists but is not wired into this pipeline.**

---

## Deliverable 6 — Gap Register

| # | Gap | Code absent / present-untested / tested-not-executed / operationally validated | Classification |
|---|---|---|---|
| G1 | `RuntimeConfig` mode/broker cross-validation missing; `run_shadow()` has no structural PaperBroker guard | Code absent (the guard itself doesn't exist) | **BLOCKER** |
| G2 | Real numeric margin validation not wired into `production_runtime` (exists elsewhere, disconnected) | Tested-but-not-integrated | **BLOCKER** — no order should dispatch without this, and today none does check it in this pipeline |
| G3 | Numeric risk limits (max capital, max daily loss, max simultaneous positions) not found anywhere in `risk_brain` or `runtime_safety` | Code absent | **BLOCKER** |
| G4 | PaperBroker fill-price fidelity — hardcoded 120.0 default, real premiums not propagated | Operationally validated (confirmed exactly how it behaves) but the behavior itself is a real fidelity gap | **HIGH** — would materially distort any PnL evidence gathered from a paper-trading run until fixed |
| G5 | Exit/position-management path not exercised in this sprint's own real run | Code present (referenced by C2/C4 tests), tested, but end-to-end exit was not executed today with real data | **HIGH** — Deliverable 3 only proved entry; exit needs its own equivalent real run before claiming full lifecycle proof |
| G6 | Emergency Flatten as an independent, operator-invokable action (not just a side effect of specific recovery scenarios) | Code absent as a distinct primitive | **HIGH** |
| G7 | Timeout / cancel-failure dedicated tests not located | Code present, tested-not-confirmed (test may exist under an unsearched name) | **MEDIUM** — re-check before treating as a real coverage gap vs. a search miss |
| G8 | Circuit breaker / rate limiter exist (older pipeline) but not confirmed wired into `production_runtime` | Tested-but-not-integrated | **MEDIUM** |
| G9 | No dedicated `test_paper.py` for `PaperBroker` in isolation | Present, indirectly tested only | **LOW** |
| G10 | Live FyersBroker `place_order`/`get_order`/`cancel_order` never exercised against a real network call, anywhere, ever | Code present, tested only at the payload-mapping level | **BLOCKER for going live specifically** (not for paper trading, where this doesn't matter) |

---

## Deliverable 7 — Final Qualification Verdict

**1. Can Trading Brain v3 complete an end-to-end paper trade today?**
**PARTIALLY.** Entry is fully proven, today, with real market data, for the first time (Deliverable 2/3). Exit was not exercised in this sprint. Margin and numeric risk limits are not part of the dispatch path at all today.

**2. What is the earliest point where the pipeline fails?**
It does not fail on the entry path — this sprint's real run reached `order_submitted=True` cleanly with no errors, no stage returning a `FAILED`/blocking status. The earliest **structural weakness** (not a failure, a silent gap) is at composition-root construction: nothing there prevents a `SHADOW`-mode object graph from holding a real, capable `FyersBroker` (G1).

**3. What evidence supports that conclusion?**
The full real trace in Deliverable 2, the safety-boundary evidence in Deliverable 4 (construction succeeds, no guard exists in `run_shadow()`'s source), and the 96/96 passing re-run of the existing failure-injection suite in Deliverable 5.

**4. What is the minimum engineering work required before multi-day paper trading?**
In order: (a) close G1 (structural shadow/live guard — this is the one item that would be irresponsible to defer, even for paper trading, since a config mistake today has no code-level backstop); (b) run and prove the exit path with the same real-data rigor as this sprint's entry run (G5); (c) fix or explicitly accept G4's fill-fidelity limitation before trusting any PnL number a paper run produces. G2/G3 (margin, numeric risk limits) are BLOCKERs for a *live* pilot, not for paper trading itself — paper trading with fake money doesn't need real margin enforcement to be useful evidence, but should be explicitly labeled as "not risk-limit-tested" while they remain open.

**Is Trading Brain v3 a prototype or an operational system?**
Neither label fits cleanly, and forcing one would be less honest than describing it: it is **a real, substantially tested, individually-proven pipeline whose parts have now, for the first time, been proven to compose correctly end-to-end on real data** — but it has never been proven under its own numeric risk controls (because those controls don't exist in this pipeline yet), never proven on a real broker, and never proven across more than one real run. That is a specific, evidenced position between "prototype" and "operational," not a hedge.
