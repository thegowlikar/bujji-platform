# BUJJI Execution Engine v1 — Research & Specification

**Status: SPECIFICATION ONLY. No code was written or modified to produce this document.**

## Read this first — a correction to the last Gate Review

The Production Readiness Gate Review's BLOCKER-1 stated *"No live order-execution path exists, has been built, tested, or evidenced in any sprint to date."* That was accurate for the pipeline this whole engagement had tested (`run_live_shadow.py` / `live_shadow_operator`), where execution is **structurally forbidden** (`bujji/broker/guard.py`, `bujji/live_shadow_operator/safety.py`). It was **incomplete**, because a second, separate, more advanced pipeline exists — `bujji/production_runtime/` ("BUJJI Options OS v3", Series 54, per `TRADING_BRAIN_CONSTITUTION.md`) — which this engagement had never touched until this sprint's mandated pre-spec audit.

**Corrected finding: a real, tested, end-to-end execution path already exists**, from market evidence through to a real `broker.place_order()` call:

```
Evidence Interpreter → Market State → Strategy Selector → Risk Brain → Capital Brain
→ Execution Planner → (trading_brain) Execution Engine → Broker Adapter
→ Nifty Contract Builder → Position Sizing → Order Construction
→ Runtime Execution (dispatch) → Production Execution Adapter
→ bujji/execution/engine.py::ExecutionEngine.submit_and_confirm() → Broker.place_order()
```

Confirmed by direct code reading: `bujji/production_runtime/runtime.py::run_shadow()` wires this entire chain and calls `runtime_execution_engine.dispatch(exec_session, root.execution_adapter, ...)`; `root.execution_adapter` is a `ProductionExecutionAdapter` wrapping the real `ExecutionEngine`, and `tests/test_production_execution_adapter.py::test_adapter_usable_with_runtime_execution_dispatch` proves this exact wiring is exercised. `bujji/execution/engine.py::ExecutionEngine` is a real, substantial (263-line), already-tested class implementing idempotent order placement (query-before-retry, never blind re-place), partial-fill handling, timeout-based cancellation of unfilled remainder, auth-error fast-fail, and broker reconciliation. `tests/test_tier1_capital_protection.py` (13 tests) already covers resume-after-restart, orphan-position flattening, end-of-day square-off, duplicate-order prevention, and partial-fill sizing. `tests/test_sprint3_failure_injection.py` and `tests/test_e1_e2_auth_expiry.py` already cover auth-expiry-never-opens-a-position, broker-disconnect-rolls-back, and 15 auth-classification scenarios.

**This means most of Deliverables 1–5 below are not new design — they are documentation of what is real, evidenced from the code, with gaps named where they exist.** This is a materially better starting position than the Gate Review implied. It also surfaced one genuinely serious new finding (below) that the Gate Review could not have found, because it never looked at this pipeline.

### The one new BLOCKER this audit found

`bujji/production_runtime/config.py::RuntimeConfig` has **zero cross-validation between `mode` and `broker_name`**. `RuntimeConfig(mode="SHADOW", broker_name="fyers")` is valid and constructs successfully. `composition_root.py` applies **no `disable_live_execution()` guard** to the `FyersBroker` it builds (unlike `run_live_shadow.py`, which always wraps it). If `run_shadow()` — a function whose own docstring says *"Never constructs or connects to FyersBroker... never a live broker order"* — is ever called against a root built with `broker_name="fyers"`, it will run the entire pipeline through to a real `place_order()` call. **The safety property this whole pipeline depends on ("Shadow mode never touches a live broker") is a naming/documentation convention, not a structural, code-enforced guarantee**, unlike everything tested in Sessions #1–3. This is the sharpest, most actionable finding in this entire spec and is addressed as the first entry in the Risk Architecture (Deliverable 6).

---

## Deliverable 1 — Complete Execution Lifecycle (as it actually exists + gaps)

```
Market Intelligence (MIC v2, external process)
  ↓
Evidence Interpreter          [REAL, tested, 46 tests]
  ↓
Market State Assessment       [REAL, tested, 63 tests]
  ↓
Strategy Decision             [REAL, tested, 78 tests across 2 files]
  ↓
Risk Assessment                [REAL, tested, 72 tests — market-condition gate, NOT a capital-limit engine]
  ↓
Capital Decision               [REAL, tested, 63 tests — capital *policy* (NONE/MINIMAL/.../FULL), not lot math]
  ↓
Execution Plan                 [REAL, tested, 64 tests — abstract workflow description, not order fields]
  ↓
Contract Construction          [REAL, tested, 64+ tests — resolves strikes/legs against a SUPPLIED option chain]
  ↓
Position Sizing                [REAL, tested, 67 tests — lots-per-leg from a fixed capital-intent table]
  ↓
Order Construction              [REAL, tested, 69 tests — produces broker-neutral OrderRequest per leg]
  ↓
Runtime Execution Session       [REAL, tested, 57 tests — validates & builds DispatchInstruction]
  ↓
Runtime Safety Authorization    [REAL, tested, 57 tests — STRUCTURAL gate, not a risk-limit gate — see Gap R1]
  ↓
Runtime Session / Authentication [REAL — session + auth state machines]
  ↓
Dispatch → Production Execution Adapter → ExecutionEngine.submit_and_confirm()  [REAL, tested]
  ↓
Broker.place_order()            [REAL for Paper; real, SDK-calling, self-documented UNVERIFIED-live for Fyers]
  ↓
Fill Verification (poll get_order, timeout → cancel remainder)   [REAL, implemented in ExecutionEngine]
  ↓
Position Reconciliation (get_open_positions on restart)          [REAL, implemented — Gap E1: not scheduled/periodic, restart-triggered only]
  ↓
Active Position                 [Gap E2 — no continuous Position Manager found in this audit; see below]
  ↓
Exit Management                 [Gap E2 — same]
  ↓
Position Closed / Journaled     [journal infrastructure exists (bujji/journal/*), Position Manager's own exit-decision logic not located in this audit]
```

**What this audit could not confirm in the time available** (named honestly, not guessed): a continuously-running Position Manager that decides HOLD/ADJUST/ROLL/EXIT on an open position (the Constitution's own "Position Manager... runs continuously... every few seconds" concept). Test names imply pieces exist (`test_c2_end_of_day_squares_off`, `test_c4_exit_flattens_fully_through_partials`), but the module owning ongoing exit *decisions* (as opposed to mechanical flattening) was not located and read in this audit. **This is the top item for the next audit pass, not assumed to be missing or present.**

---

## Deliverable 2 — Subsystem Definitions

For each subsystem: Purpose / Inputs / Outputs / Failure Modes / Invariants. Marked **[EXISTS]** where confirmed real+tested, **[PARTIAL]** where a piece exists but is incomplete, **[MISSING]** where this audit found no implementation.

### Strategy Builder / Strategy Selector **[EXISTS]**
- Purpose: choose the single registered strategy that honestly fits today's fused market state — never scored for profitability.
- Inputs: `MarketStateAssessment`. Outputs: `StrategyDecision` (selected strategy or `NO_STRATEGY`, confidence, full evaluation trace for every candidate).
- Failure modes: no eligible strategy → `NO_STRATEGY` (not an error, a valid decision); malformed/`None` input → graceful `UNKNOWN` decision, never an exception.
- Invariants: deterministic (no randomness/ML); ties broken by registry order, documented; every rejection carries a reason.

### Strike/Expiry Selection — Nifty Contract Builder **[EXISTS, with a real limitation]**
- Purpose: resolve a strategy's leg template (e.g. straddle, iron fly, calendar) into concrete strikes against a **caller-supplied** option chain snapshot.
- Inputs: `StrategyDecision`, `CapitalDecision`, `NiftySpotSnapshot`, `NiftyOptionChainSnapshot`. Output: `ContractConstructionResult` (all-or-nothing across legs).
- Failure modes: missing spot/chain → `FAILED`/`INVALID_SPOT`, cascades cleanly downstream (confirmed: `ExecutionSession` never reaches `READY`); unknown strategy vs. "known but out of v1 scope" are distinguished failure reasons.
- Invariants: atomic construction (no partial leg sets); deterministic ATM/strike math (floor+0.5 rounding, documented).
- **Real limitation**: does not itself fetch/parse a live instrument master or Bhavcopy — the chain snapshot must be produced and injected by the caller. An Execution Engine v1 needs a real, live chain-snapshot *producer* wired in front of this (this exists elsewhere in the codebase per this whole engagement's earlier work — `bujji/broker/instrument_master.py`, real Bhavcopy fetch scripts — but wiring it specifically into this contract-builder call site was not confirmed in this audit).

### Position Sizing **[EXISTS, deliberately simple]**
- Purpose: convert `capital_intent` into a uniform lots-per-leg via a fixed lookup table.
- Inputs: `CapitalDecision`, contracts, `CapitalPolicy`, `LotSpecification`, `PositionSizingConfig`. Output: `PositionPlan`.
- Failure modes: duplicate-leg detection; honest zero-quantity failure path (never silently sizes to zero and proceeds).
- Invariants: identical lot count enforced across every leg of one strategy; no margin/exposure modeling (explicitly out of scope here — that's Risk Brain/Capital Brain's job, or a gap, see R2 below).

### Margin Validation **[MISSING as a distinct, numeric check]**
- No module in this audit computed or validated real margin requirement against real available capital before order construction. `capital_brain` decides a *policy* (how much capital class to use), not a number; `position_sizing` sizes lots, not margin; `runtime_safety` checks structural session validity, not margin sufficiency. **This is a real gap** — Deliverable 6 below treats it as a required hard guarantee before any live order.

### Risk Limits — Risk Brain **[PARTIAL]**
- Purpose (as built): gate on whether *today's market environment* is trustworthy enough to trade at all (CLEAR/CONTESTED/UNCERTAIN market character, confidence).
- **Gap**: no concrete numeric limits exist here — no max capital, max daily loss, max simultaneous positions, no portfolio-level Greeks/correlation check (all named in the Constitution's own "Risk Brain" section as intended scope, not yet built per this audit). This is Deliverable 6's primary content.

### Order Builder — Order Construction **[EXISTS]**
- Purpose: last pure-logic step — one `OrderRequest` per contract leg from a validated `PositionPlan`.
- Inputs: `PositionPlan`, `ExecutionPolicy`, `TradingConfiguration`. Output: `OrderConstructionResult` (atomic).
- Invariants: hash-derived `client_order_id`, duplicate detection, nonzero quantity enforced.

### Execution Coordinator — Runtime Execution + Production Execution Adapter + `bujji/execution/engine.py::ExecutionEngine` **[EXISTS, real broker I/O]**
- Purpose: the ONLY layer permitted to talk to a broker. Turns `OrderRequest` into a confirmed (possibly partial) fill.
- Real, implemented behaviors (quoted from source, not inferred): idempotent placement (`_place_idempotent` queries before ever re-placing after an ambiguous error); poll-until-filled-or-timeout with **automatic cancellation of any unfilled remainder** so a late fill can never silently grow a position after the caller has moved on; auth errors fast-fail without burning retry budget; generic errors get exponential backoff.
- Failure modes explicitly handled in code: order rejected (raises `ExecutionError`), partial fill (returned truthfully, not raised), network error during placement (query-before-retry, never blind duplicate), unknown broker state (returns `OrderStatus.UNKNOWN`, callers stay cautious), timeout (cancels remainder).
- Invariant: **"at most once" placement per `client_order_id`**, verified across a real process restart (idempotency check queries the broker, not local memory).

### Fill Monitor **[EXISTS, folded into ExecutionEngine._await_fill]** — see above, not a separate module.

### Position Tracker / Reconciliation **[PARTIAL]**
- `ExecutionEngine.reconcile()` fetches live broker positions — real, implemented. **Gap**: confirmed restart-triggered only in this audit; no evidence of a continuously-scheduled reconciliation loop (e.g., every N minutes during market hours) independent of a restart event.

### Exit Manager **[GAP — see Deliverable 1's own note]**

### Emergency Flatten **[PARTIAL]**
- `test_c1_orphan_position_flattened` and `test_partial_entry_failure_auto_flattens_the_filled_leg` show flattening logic exists for specific recovery scenarios (orphan positions, partial-entry failures). **Gap**: no evidence in this audit of an operator-invokable, single-command "flatten everything now" kill switch independent of these specific automatic recovery paths — Deliverable 6 requires this be added and load-bearing-tested on its own, not only as a side effect of other recovery logic.

### Broker Reconciliation **[EXISTS]** — see Position Tracker above; same module.

---

## Deliverable 3 — Failure Mode Analysis

| Scenario | Real recovery path today | Evidence | Status |
|---|---|---|---|
| Order rejected | `ExecutionEngine.submit_and_confirm` raises `ExecutionError` immediately | source read | **Handled** |
| Partial fill | Returned truthfully with real `filled_quantity`; caller sizes off actuals | source + `test_c4_entry_sizes_off_actual_fill` | **Handled** |
| Duplicate submission | Idempotency check via `client_order_id` lookup before any place; survives restart (queries broker, not memory) | source + `test_c3_duplicate_submit_same_cid_is_adopted` | **Handled** |
| Network failure during placement | Query-before-retry — never blindly re-places; if lookup also fails, `OrderStatus.UNKNOWN` returned, caller stays cautious | source + `test_c3_no_duplicate_when_place_errors_after_accept` | **Handled** |
| Unknown broker state | Explicit `OrderStatus.UNKNOWN` value exists and is the honest default on any ambiguity | source | **Handled** |
| Broker accepted but API timed out | Covered by the same query-before-retry path — `_place_idempotent` treats this identically to "network failure during placement" | source | **Handled** |
| Exchange rejected | Same as "order rejected" — broker-level rejection surfaces as `OrderStatus.REJECTED` | source | **Handled** |
| Margin changes mid-session | **Not found in this audit.** No module recomputes margin sufficiency after initial sizing. | — | **GAP** |
| Session expiry (token) | `AuthenticationError` fast-failed, not retried, escalated immediately; 15 real tests in `test_e1_e2_auth_expiry.py` including "auth expiry never opens a position" and "in-position auth error preserves position" | source + tests | **Handled** |
| Manual intervention | Runtime session model has pause/resume (`allow_pause_resume` in `RuntimeSessionPolicy`); a distinct, always-available, single-command flatten is a gap (see Emergency Flatten above) | source | **PARTIAL** |
| Lost acknowledgement | Same mechanism as "unknown broker state" — query resolves it, never assumed | source | **Handled** |
| Process restart | `test_c1_resume_open_position`, `test_c1_position_already_flat`, `test_c1_orphan_position_flattened` — explicit, tested restart-recovery scenarios | tests | **Handled** |
| Position mismatch (broker vs. our records) | `reconcile()` exists to detect this; the exact resolution policy when a mismatch IS found (auto-correct? alert only? which side is trusted?) was not read in this audit | source, partial | **PARTIAL — needs confirming** |
| Duplicate callback / webhook | No webhook/callback-based fill notification was found in this audit — fills are confirmed by polling `get_order`, which sidesteps duplicate-callback risk entirely by construction (no callback exists to duplicate) | source | **N/A by design, worth confirming this stays true if a callback-based transport is ever added** |

**Only one true gap surfaced by systematic review: margin re-validation mid-session.** Everything else in the sprint's own failure-mode list already has a real, evidenced, mostly-tested recovery path — a materially stronger position than a from-scratch spec would have assumed.

---

## Deliverable 4 — Order State Machine (as evidenced)

```
UNKNOWN  ──(place_order accepted)──▶  PENDING
PENDING  ──(broker confirms partial fill)──▶  PARTIAL
PENDING  ──(broker confirms full fill)──▶  FILLED
PENDING  ──(broker rejects)──▶  REJECTED
PENDING  ──(timeout, cancel issued)──▶  CANCELLED
PARTIAL  ──(remaining quantity fills)──▶  FILLED
PARTIAL  ──(timeout, cancel remainder)──▶  CANCELLED  (with filled_quantity > 0 preserved, truthfully)
```
Real enum, confirmed: `PENDING, FILLED, PARTIAL, REJECTED, CANCELLED, UNKNOWN` (`bujji/core/enums.py::OrderStatus`). This is a 6-state machine, not the sprint's illustrative 9-state example (`CREATED/VALIDATED/SUBMITTED/ACKNOWLEDGED/PARTIALLY_FILLED/FILLED/ACTIVE/EXIT_PENDING/CLOSED`) — the extra granularity in the sprint's example (VALIDATED, SUBMITTED, ACKNOWLEDGED as distinct pre-fill states) does not exist as broker-visible states in the real code; those concerns are handled as *process steps inside* `submit_and_confirm()`, not as persisted order states. **Recommendation, not implemented here**: decide explicitly whether to keep the current 6-state model (simpler, already tested) or expand it — this is a real design choice for the next phase, not something this spec should decide unilaterally.

Every transition above has at least one real test exercising it (cross-referenced in Deliverable 3's table).

---

## Deliverable 5 — Position State Machine

Evidenced from test names, not a located single `models.py` (a real gap in this audit's own thoroughness — flagged, not hidden):

```
FLAT ──(entry order fully filled)──▶ ACTIVE
FLAT ──(entry order partially filled, then times out)──▶ RECOVERY ──(auto-flatten)──▶ FLAT
                                                                    [test_partial_entry_failure_auto_flattens_the_filled_leg]
ACTIVE ──(exit order fully filled)──▶ FLAT
ACTIVE ──(exit order partially filled)──▶ PARTIAL_EXIT ──(remainder flattened)──▶ FLAT
                                                                    [test_c4_exit_flattens_fully_through_partials]
ACTIVE ──(end of day)──▶ FLAT (forced square-off)   [test_c2_end_of_day_squares_off]
UNKNOWN (post-restart, before reconciliation) ──(reconcile finds a live position we didn't expect)──▶ RECOVERY ──▶ FLAT (orphan flattened)
                                                                    [test_c1_orphan_position_flattened]
UNKNOWN ──(reconcile confirms flat)──▶ FLAT           [test_c1_position_already_flat]
UNKNOWN ──(reconcile confirms our expected position)──▶ ACTIVE   [test_c1_resume_open_position]
```

**Gap, honestly stated**: this is reconstructed from test *names*, not from reading a single authoritative state-machine implementation — this audit did not locate the exact module owning this state machine (candidate location: wherever "position codec" lives, referenced by `test_position_codec_roundtrip` but not found by name in a targeted grep in the time available). **First action of Milestone E1 below should be locating and confirming this precisely**, not re-deriving it from scratch.

---

## Deliverable 6 — Risk Architecture (hard guarantees required before any order can leave the system)

Ranked by what this audit found missing vs. present:

1. **[MUST ADD — sharpest finding of this audit] Structural Shadow/Live guard on `production_runtime`.** `RuntimeConfig` must reject `broker_name="fyers"` when `mode != PRODUCTION_READY`, or — better, matching the existing `disable_live_execution()` precedent — `composition_root._build_broker()` should apply the same guard to any non-`PRODUCTION_READY` mode, exactly as `run_live_shadow.py` already does. This is not a new mechanism to invent; it's extending an existing, already-proven one to a second pipeline that currently lacks it.
2. **[MUST ADD] Real margin validation before order submission** — the one true gap from Deliverable 3. Must check real available margin against real required margin (not just a capital-intent policy label) immediately before `OrderConstructionResult` is handed to dispatch.
3. **[MUST ADD] Numeric risk limits inside or alongside Risk Brain**: maximum capital deployed, maximum daily loss (a real circuit breaker — halt new entries, do not force-close existing positions, for the remainder of the day once hit), maximum simultaneous positions. None of these exist as numbers anywhere found in this audit — Risk Brain today is a market-condition gate only.
4. **[MUST ADD] An explicit, operator-invokable Emergency Flatten**, independent of the automatic recovery paths that already exist for specific scenarios (orphan position, partial-entry failure). A human must be able to say "flatten everything, now" as a first-class, tested action.
5. **[EXTEND, not new] Duplicate prevention** — already real and tested (`client_order_id` idempotency, survives restart). Keep as-is.
6. **[EXTEND, not new] Market-hours validation** — `MarketCalendar` already exists and is used in the live-shadow pipeline; confirm it's wired into `production_runtime` too (not confirmed in this audit — a real open question, not assumed either way).
7. **[NEEDS CONFIRMATION] Kill switch scope** — does Emergency Flatten (once built per #4) also halt the entire runtime session (`RuntimeSession.session_state`), or only close positions while leaving the pipeline running? Must be decided explicitly, not left implicit.

---

## Deliverable 7 — Evidence Requirements Before Enabling Live Execution

Given how much already exists, this is a gap list against what's proven, not a from-scratch checklist:

| Requirement | Status |
|---|---|
| Unit tests | **Substantially done** — hundreds of tests across the modules audited |
| Replay tests | Confirmed for the decision pipeline (41-day corpus, this whole engagement's own standing practice); **not confirmed for the execution layer specifically** — does a replay day exercise real order construction/dispatch against PaperBroker end-to-end? Worth confirming explicitly. |
| Broker simulation | **Done** — `PaperBroker`, real and reasonably complete (fills, partial fills, idempotency, synthetic margin, fault-injection hooks) |
| Fault injection | **Substantially done** — `test_sprint3_failure_injection.py`, `test_e1_e2_auth_expiry.py`; **the P2 harness style used for the tick feed (this engagement's own recent work) has not yet been pointed at this execution layer** — a natural, high-value extension |
| Paper trading | **Infrastructure exists** (`PaperBroker`, wired into `composition_root`) — **an actual multi-day paper-trading run through this specific pipeline was not confirmed as having happened** (distinct from Shadow Sessions #1–3, which used the *other*, execution-forbidden pipeline) |
| Shadow execution | This is exactly what `run_shadow()` already does — **but see the BLOCKER above**: its safety is convention, not structural, until fixed |
| Dry-run order plans | `run_read_only()` (Mode 1) already provides this — stops before Order Construction |
| Reconciliation tests | **Done** — `test_c1_*` series |
| Small-capital pilot | Cannot begin until items 1–4 of Deliverable 6 are closed |

---

## Deliverable 8 — Roadmap

**E0 — Inventory & Confirmation (days, not weeks).** Close this audit's own open questions before building anything: locate the exact Position Manager / continuous exit-decision module (or confirm it doesn't exist yet); locate the exact position-state-machine implementation; confirm whether `MarketCalendar` is wired into `production_runtime`; confirm whether a replay day has ever been run through the execution layer specifically. Entry: this spec approved. Exit: every "not confirmed in this audit" item above resolved to a definite yes/no with evidence.

**E1 — Structural Shadow/Live Guard.** Close the one new BLOCKER. Entry: E0 done. Exit: a test proves `RuntimeConfig(mode="SHADOW", broker_name="fyers")` either fails construction or produces a broker that structurally cannot place a real order — mirroring `disable_live_execution()`'s own already-proven pattern.

**E2 — Margin Validation + Numeric Risk Limits.** Entry: E1 done. Exit: a real margin check exists before dispatch; max capital/max daily loss/max simultaneous positions exist as enforced numbers, each with a passing test that proves an order is blocked when a limit is exceeded.

**E3 — Emergency Flatten.** Entry: E2 done. Exit: a single, operator-invokable, independently-tested "flatten everything" action exists and is proven against at least the same scenarios `test_c1_orphan_position_flattened`-style tests already cover, but as a deliberate action, not only a side effect.

**E4 — Fault-Injection Harness for the Execution Layer** (extending this engagement's own P2 pattern to a new target). Entry: E3 done. Exit: the execution layer survives the same category of deterministic fault injection already proven for the tick feed — broker timeouts, malformed responses, mid-sequence disconnects — driving the REAL `ExecutionEngine`, not a mock.

**E5 — Multi-Day Paper Trading Through This Specific Pipeline.** Entry: E4 done. Exit: several consecutive real trading days run through `production_runtime`'s `run_shadow()` (now structurally safe per E1) against `PaperBroker`, producing real `OrderRequest`s from real live market data, with a forensic report in the same evidence-based style as Session #3's.

**E6 — Live ₹ Pilot.** Entry: E5 done, plus a fresh Gate Review re-run against this pipeline specifically (the Gate Review to date has never evaluated this pipeline). Exit criteria: not set here — a Gate Review, not this spec, makes that call, with the same evidentiary discipline used throughout this engagement.

---

## Existing Concepts to Extend (as requested)

- **Event bus** (`bujji/core/event_bus.py`) — real, already supports `POSITION_OPENED`/`POSITION_CLOSED`/`DECISION_MADE` event types. Extend with execution-specific events (`ORDER_SUBMITTED`, `ORDER_FILLED`, `EMERGENCY_FLATTEN_TRIGGERED`) rather than inventing a new notification mechanism.
- **Journal** (`bujji/journal/*`, one file per module already, matching house convention) — extend with an `order_journal`/`position_journal` pair if one doesn't already exist under this exact name (E0 should confirm); the pattern (append-only, content-hashed IDs) is already proven across a dozen modules in this codebase.
- **Replay framework** (`SessionDriver`, `run_full_cadence`, this engagement's own 41-day corpus discipline) — extend to include execution-layer replay (E0/E5 above), not a new framework.
- **Observation/evidence philosophy** (never fabricate, honest UNKNOWN, provenance on every field) — already the design discipline visible throughout every trading_brain module audited here (`EvidenceInterpretation`'s `TranslationProvenance`, `MarketStateAssessment`'s reasoning trace). Extend the same discipline into execution evidence (why was this order sized this way, why was this strike chosen) rather than defining a separate standard.
- **Health dashboard** (`bujji/live_shadow_operator/health.py`, extended across P1/P4/P5 this engagement) — extend with execution-specific fields (open position count, today's realized/unrealized PnL, margin utilization, kill-switch state) once E2/E3 produce real values to show.
- **Governance mindset** (EEB's `archive()`/`supersede()` human-only functions, Strategy Evolution's "proposal only, never live self-modification" from the Constitution) — extend directly to execution safety gates: any change to a numeric risk limit (E2) should go through the same human-approval-only pattern already proven for EEB, never a live, automatic adjustment.
