# BUJJI Options OS — TODO

Grouped by priority. Each item has enough context to hand to a fresh Claude session cold. **Note**: as of 2026-07-31, the system is under LSQ-1 (Live Shadow Qualification) — architecture/feature/strategy work is explicitly frozen during that period (see `docs/LSQ1_PROTOCOL.md`). Only bug fixes, reliability, data correctness, replay correctness, and operational stability changes are permitted until LSQ-1 concludes.

---

## P1 — Blockers (confirmed, evidence-backed, must close before any live-capital discussion)

### P1-1. `production_runtime` has no structural guard against a real broker in Shadow mode
**File**: `bujji/production_runtime/config.py::RuntimeConfig`, `bujji/production_runtime/composition_root.py`, `bujji/production_runtime/runtime.py::run_shadow()`.
**Evidence**: directly executed `RuntimeConfig(mode="SHADOW", broker_name="fyers")` — construction succeeds, produces a real, unguarded `FyersBroker` with a live `place_order` method. Read `run_shadow()`'s full source — the only occurrence of "PaperBroker" anywhere in it is a code comment, not an `isinstance()` check.
**Fix direction**: extend the existing, already-proven `disable_live_execution()` pattern (`bujji/broker/guard.py`) to any non-`PRODUCTION_READY` mode in `composition_root.py`, OR add `RuntimeConfig.__post_init__` cross-validation rejecting `broker_name="fyers"` outside `PRODUCTION_READY` mode. See `docs/EXECUTION_ENGINE_V1_SPEC.md` Milestone E1 for the full spec.

### P1-2. Confirmed silent connection-state corruption in `FyersTickFeed`
**File**: `bujji/broker/fyers_ws.py`.
**Evidence**: `docs/CONCURRENCY_LIFETIME_PROOF_P3.md` — a stale socket generation's callback, if it fires late, silently sets `is_connected=True`/increments `connect_count` on the CURRENT generation. Deterministic, executed proof exists (`tests/test_concurrency_lifetime_proof_p3.py`).
**Fix direction**: add generation tagging to `FyersTickFeed`'s internal socket references so a closure can detect it belongs to an abandoned generation before acting.

### P1-3. No numeric risk limits anywhere in the Trading Brain v3 pipeline
**Files**: `bujji/trading_brain/risk_brain/`, `bujji/runtime_safety/`.
**Evidence**: `docs/EQ1_EXECUTION_QUALIFICATION.md` — `risk_brain` gates on market *condition* trustworthiness only; `runtime_safety` is a structural/qualification-consistency gate, not a capital-limit engine. No max capital, max daily loss, or max simultaneous positions exists anywhere in this pipeline's dispatch path.
**Fix direction**: add real, numeric limits — either inside `risk_brain` or a new module — each with an enforced test proving an order is blocked when the limit is exceeded.

### P1-4. No real margin validation wired into `production_runtime`'s dispatch path
**Evidence**: a real margin engine exists (`bujji/capital/`, live-certified against a real FYERS SPAN margin API, see `docs/AUDIT_LOG.md` Pass 8) but is a completely separate, unconnected module from the Trading Brain v3 pipeline. The EQ1 sprint's own real end-to-end trace produced an `OrderRequest` with no margin figure anywhere.
**Fix direction**: wire `bujji/capital/`'s real, certified margin logic into `production_runtime`'s dispatch path before order construction completes.

---

## P2 — High-priority gaps (real, evidence-backed, not yet blocking but should close before scaling up)

### P2-1. No entry-order-construction path exists in `run_live_shadow.py`
This is the reason LSQ-1's per-trade qualification checks can't be exercised by real, spontaneous trades today. Portfolio Valuation and Exit Engine are wired and tested but structurally idle. **Explicitly out of scope during LSQ-1** (Rule #1) — first item to pick up once qualification concludes or a dedicated sprint is authorized.

### P2-2. Emergency Flatten — no explicit, operator-invokable kill switch
Automatic recovery exists for specific scenarios (orphan position, partial-entry failure) but there's no first-class, independently-tested "flatten everything now" action. See `docs/EXECUTION_ENGINE_V1_SPEC.md` Deliverable 6.

### P2-3. `PaperBroker` fill-price fidelity gap where `reference_price` isn't supplied
Falls back to a hardcoded `120.0` default. Mostly closed by the `reference_price` threading work this engagement did, but only where a caller actually populates the field — any new order-construction path must remember to populate it.

### P2-4. `reconnect_count` and `cadence_duration_seconds` are confirmed-broken operational metrics
Live-confirmed in Session #3: 6 real reconnects, metric reported 0; `cadence_duration_seconds` reads total session uptime, not per-cadence duration. Don't build dashboards trusting either without fixing them first.

### P2-5. `FyersBroker.place_order`/`cancel_order`/`get_order` never proven against a real network call
Only mapping-level tests exist (canned responses). A real, careful live-order verification (in a controlled, low-risk way) is needed before any live-capital discussion — see `docs/PRODUCTION_READINESS_GATE_REVIEW.md`.

### P2-6. `limit_price`/`reference_price` separation — verify no other call site regressed
The Semantic Cleanup Sprint fixed the one known instance (`bujji/integration/execution_adapter.py`). Worth a repo-wide grep for any OTHER place that might construct a `bujji.core.models.OrderRequest` with an observed price mistakenly passed as `limit_price`.

---

## P3 — Lower priority / housekeeping

### P3-1. Process lifecycle: `run_live_shadow.py` leaves a zombie process after "shutdown: clean"
SDK-owned (non-daemon `message_thread` in `fyers-apiv3`), not fixable from our side — see `docs/LIFECYCLE_INTEGRITY_PROOF_P5.md`. Workaround only: `os._exit()` after logging completes, or accept and monitor.

### P3-2. Leaked `asyncio` event loop in `_pre_market_checklist()`
`run_live_shadow.py::_pre_market_checklist()` never calls `loop.close()`. BUJJI-owned, minor, confirmed live via `lsof`/`/proc` inspection.

### P3-3. `bujji/core/` has 8 stale `.pre_sprintN_backup` files in-tree
(`orchestrator.py.pre_sprint4_backup` through `.pre_sprint7_backup`, plus `pipeline_stages.py.pre_sprint7_backup`.) Prune or confirm they're already fully captured in git history.

### P3-4. `data/` has dozens of stray `*.lock` files from ad-hoc test/debug runs
Mixed in with real production runtime state. Worth a cleanup pass (not investigated in depth — `data/` permissions restricted, contents not read per this engagement's own discipline around not reading files that may hold secrets).

### P3-5. NSE holiday calendar unverified
`MarketCalendar.holiday_calendar_verified=False`, self-disclosed. Requires an operator to populate and verify the real holiday list for the relevant years — currently mitigated only by a manual daily cross-check.

### P3-6. `bujji/runtime_session/` has no docstring in `__init__.py`
Purpose inferred only from its file-template pattern (matches the generic observation-engine shape used elsewhere) — worth a quick confirmation read before relying on this inference.

---

## Known Technical Debt (not urgent, but should be tracked)

- Three architectural generations coexist without a clear deprecation plan for the older two — worth a real decision (not made in this engagement) about whether Generation 1/2 get formally retired once Generation 3 + entry-construction (P2-1) is complete, or whether all three are meant to coexist long-term.
- `operator_journal.jsonl` (Generation 2) is a single, ever-growing file across sessions (17MB / 70 lines as of Session #3) rather than being rotated per-day — intentional for cross-day continuity (`resume_prior_closes`), but worth confirming this scales acceptably over months of LSQ-1.
- The Strategy Exit rule in `exit_engine` is a documented placeholder (always `False`) — real strategy-aware exit logic doesn't exist anywhere in this codebase yet; building it is real, non-trivial future work, explicitly not attempted per multiple sprints' own scope discipline.
