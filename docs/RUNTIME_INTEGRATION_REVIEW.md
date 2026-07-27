# Runtime Integration Review

**BUJJI Options OS v3 — Engineering Series 50**
**Type: Documentation and architectural analysis only. No code was written or modified for this sprint.**

## Read This First

This document reconciles two systems that now both exist:

- **Existing production runtime** (mapped in the Series 41 architecture
  review): `Broker` ABC, `FyersBroker`, `FyersTokenManager`,
  `FyersTickFeed`, `PaperBroker`/`HybridPaperBroker`, `ExecutionEngine`,
  `TradeManager`, `Orchestrator._recover()`, `core/state_machine.py`,
  `ops/health_monitor.py`, `ops/alerts.py`.
- **New Phase 2 runtime** (Series 45, 47, 48, 49): the Runtime
  Execution Orchestrator, the Runtime Safety Gate, the Runtime Session
  Manager, and the Authentication & Broker Session Manager.

No production file was modified for this sprint. No new runtime
behavior was implemented. This is verification of what already exists
on both sides, and how they are meant to fit together.

---

## Deliverable 1 — Ownership Boundaries and Integration Seams

### Existing production runtime (unchanged, Series 41 findings)

| Component | File | Owns |
|---|---|---|
| `Broker` ABC | `broker/base.py` | The one seam every broker implementation conforms to |
| `FyersBroker` | `broker/fyers.py` | Live FYERS execution/data adapter; auth via `connect()`, order placement/status/cancel (order-path unverified against a live account) |
| `FyersTokenManager` | `broker/fyers_token_manager.py` | Daily access-token refresh via refresh-token grant; atomic credential persistence |
| `FyersTickFeed` | `broker/fyers_ws.py` | Live WebSocket tick stream; relies on SDK's own `reconnect=True` |
| `PaperBroker` / `HybridPaperBroker` | `broker/paper.py`, `broker/hybrid.py` | Deterministic simulator; guarded live-data + paper-ledger composition |
| `ExecutionEngine` | `execution/engine.py` | The only module that calls a `Broker`; retry-with-backoff, idempotent placement, fill-polling, reconciliation |
| `TradeManager` | `trade/manager.py` | Single-position exit-condition evaluation |
| `Orchestrator._recover()` | `core/orchestrator.py` | Four-way restart recovery (resume / already-flat / orphan-flatten / clean-slate) |
| `core/state_machine.py::State` | `core/state_machine.py` | Position lifecycle: `WAITING → READY → CONFIRMED → IN_POSITION → EXITING → DONE_FOR_DAY` |
| `ops/health_monitor.py`, `ops/alerts.py` | `ops/` | Observability and alerting |

### New Phase 2 runtime (this project, Series 45/47/48/49)

| Component | File | Owns |
|---|---|---|
| Runtime Execution Orchestrator | `bujji/runtime_execution/` | Validates a set of `OrderRequest`s and prepares a deterministic dispatch plan (`ExecutionSession`); defines `ExecutionEngineInterface` |
| Runtime Safety Gate | `bujji/runtime_safety/` | Authorizes an `ExecutionSession` against qualification and safety policy (`RuntimeAuthorization`) |
| Runtime Session Manager | `bujji/runtime_session/` | Manages the lifecycle of an authorized session (`RuntimeSession`) |
| Authentication & Broker Session Manager | `bujji/authentication/` | Manages broker identity and broker-session state (`BrokerSession`); defines `AuthenticationProviderInterface` |

### The key finding: the integration seams already exist by design

Two of the four new Phase 2 modules were **already built with production
integration in mind**, not as an afterthought:

- `runtime_execution.engine.ExecutionEngineInterface` (Series 45) is a
  structural `typing.Protocol` exposing exactly
  `submit_and_confirm(order_request)` — the same method name and
  signature shape as production's real
  `execution.engine.ExecutionEngine.submit_and_confirm()`.
- `authentication.engine.AuthenticationProviderInterface` (Series 49)
  is a structural `typing.Protocol` exposing exactly
  `authenticate() -> AuthenticationOutcome` — designed to be satisfied
  by a thin wrapper around production's `FyersTokenManager.refresh()`
  and `FyersBroker.connect()`.

**Neither Protocol has yet been satisfied by a concrete production
wrapper.** That is the one genuine, still-open integration seam this
review identifies — not a missing design, a missing *instance*. See
Deliverable 6 and Deliverable 8.

### Ownership boundaries, stated precisely

- **Business decisions** (what to trade, how much): the Trading Brain
  (Series 31–44). Frozen, never touched by this review.
- **Deterministic execution preparation** (what broker-neutral orders
  to construct, how to sequence them): the Runtime Execution
  Orchestrator. It prepares; it does not call `ExecutionEngine` itself
  except through the injected `ExecutionEngineInterface`.
- **Authorization** (is this session even allowed to proceed): the
  Runtime Safety Gate. It never touches a broker.
- **Process/runtime lifecycle** (is the *runtime* itself running,
  paused, or done): the Runtime Session Manager. This is **not** the
  same lifecycle as `core/state_machine.py::State`, which describes
  *position* lifecycle. The two must never be merged — see the Risk
  Assessment.
- **Broker identity and session health**: the Authentication & Broker
  Session Manager's `BrokerSession` is Phase 2's own bookkeeping about
  an authentication *attempt's outcome* — it is never an independent
  source of truth about whether a real broker connection is alive.
  That truth lives entirely inside `FyersBroker`/`FyersTokenManager`.
  `BrokerSession` must only ever reflect what a real
  `AuthenticationProviderInterface` wrapper reports, never be inferred
  independently.
- **Actual broker communication, retry, idempotency, reconciliation**:
  `ExecutionEngine` and `Broker`, unchanged, exactly as mapped in
  Series 41. Nothing in Phase 2 reimplements any of this.

---

## Deliverable 2 — Complete Runtime Diagram

```
Trading Brain (Series 31-44, frozen)
        │
        ▼
Broker Adapter (Series 40 — translation only)
        │
        ▼
NIFTY Contract Builder / Position Sizing / Order Construction (Series 42-44)
        │
        ▼  OrderRequest(s)
        ▼
Runtime Execution Orchestrator (Series 45)
        │  ExecutionSession {READY / DISPATCH_PENDING / DISPATCHED}
        ▼
Runtime Safety Gate (Series 47)
        │  RuntimeAuthorization {AUTHORIZED / AUTHORIZED_WITH_WARNINGS / DENIED}
        ▼
Runtime Session Manager (Series 48)
        │  RuntimeSession {CREATED → INITIALIZED → READY → ACTIVE ⇄ PAUSED → COMPLETED/ABORTED}
        ▼
Authentication & Broker Session Manager (Series 49)
        │  BrokerSession {authentication_state, session_state}
        │
        │  ═══ ExecutionEngineInterface / AuthenticationProviderInterface ═══
        │  ═══ THE OPEN SEAM — no concrete wrapper exists yet ═══
        ▼
┌─────────────────────────────────────────────────────────┐
│         EXISTING PRODUCTION RUNTIME (Series 41)           │
│                                                             │
│  FyersTokenManager  ──refresh()──▶  FyersBroker.connect()  │
│         │                                    │              │
│         ▼                                    ▼              │
│  Broker ABC (base.py)  ◀── PaperBroker / HybridPaperBroker  │
│         │                                                   │
│         ▼                                                   │
│  ExecutionEngine (retry, idempotency, fill-poll, reconcile) │
│         │                                                   │
│         ▼                                                   │
│  TradeManager  ──▶  core/state_machine.py::State             │
│         │                                                   │
│         ▼                                                   │
│  Orchestrator._recover()  (restart recovery)                │
│         │                                                   │
│         ▼                                                   │
│  ops/health_monitor.py + ops/alerts.py                      │
└─────────────────────────────────────────────────────────┘
        │
        ▼
Exchange (via FYERS SDK)
```

Every box in the lower half of this diagram already exists in
production, unchanged. Every box in the upper half is Phase 2, already
built. The only missing piece is the concrete glue satisfying the two
named interfaces.

---

## Deliverable 3 — Responsibility Matrix

| Capability | Existing Production Owner | New Runtime Owner | Shared Boundary | Disposition |
|---|---|---|---|---|
| Authentication | `FyersTokenManager`, `FyersBroker.connect()` | `authentication.engine` (orchestration only) | `AuthenticationProviderInterface` | **Reuse** production auth unchanged; **extend** with a thin wrapper implementing the Protocol |
| Session lifecycle (runtime/process) | *(none — did not exist before Phase 2)* | `runtime_session.engine` | none | **New**, not a duplicate — production has no equivalent runtime-process lifecycle concept |
| Session lifecycle (position) | `core/state_machine.py::State` | *(none)* | none | **Leave unchanged** — never merge with `RuntimeSession`'s own states |
| Dispatch (constructing what to send) | *(none — `ExecutionEngine` takes one `OrderRequest` at a time)* | `runtime_execution.engine` (`ExecutionSession`, dispatch plan) | `ExecutionEngineInterface` | **New**, extends production: groups multiple `OrderRequest`s into one atomic multi-leg dispatch plan, something `ExecutionEngine` itself doesn't do |
| Dispatch (actually calling the broker) | `ExecutionEngine.submit_and_confirm()` | *(delegates only)* | `ExecutionEngineInterface` | **Reuse** unchanged; Phase 2 never calls `Broker` directly |
| Retry | `ExecutionEngine._with_retry()` | *(none)* | none | **Reuse** unchanged; **must never be duplicated** in Phase 2 |
| Reconciliation | `ExecutionEngine.reconcile()`, `Orchestrator._recover()` | *(none)* | none | **Reuse** unchanged; **must never be duplicated** |
| Health monitoring | `ops/health_monitor.py::HealthMonitor` | *(none yet)* | a future `RuntimeSession`/`BrokerSession` state as one more `HealthMonitor` input | **Extend** `HealthMonitor` with Phase 2 signals; never build a parallel monitor |
| Recovery (restart) | `Orchestrator._recover()` | *(none)* | `RuntimeSession`'s own lifecycle could gain a `RECOVERING`-equivalent step calling the same routine | **Reuse** the algorithm; **extend**, don't reimplement |
| Token refresh | `FyersTokenManager.refresh()` | *(orchestrated via `AuthenticationProviderInterface`)* | `AuthenticationProviderInterface` | **Reuse** unchanged |
| Journaling | `journal/*.py` (one file per subsystem, one-way) | `runtime_execution_journal.py`, `runtime_safety_journal.py`, `runtime_session_journal.py`, `authentication_journal.py` | none — each independent | **Reuse the pattern**, already followed correctly by Phase 2's own journals |
| Alerts | `ops/alerts.py::evaluate()` | *(none yet)* | Phase 2 lifecycle states as new always-fire conditions | **Extend**, never duplicate |
| Circuit breaker | *(does not exist anywhere)* | *(does not exist anywhere)* | — | **Missing everywhere** — genuinely new work, belongs to neither system yet |
| Rate limiter | *(does not exist anywhere)* | *(does not exist anywhere)* | — | **Missing everywhere** — genuinely new work |

---

## Deliverable 4 — Sequence Diagrams

### Successful execution (once the open seam is wired)

```
RuntimeAuthorization (AUTHORIZED)
        │
        ▼
RuntimeSession.create_session() → CREATED → INITIALIZED → READY → ACTIVE
        │
        ▼
BrokerSession.create_broker_session() → UNAUTHENTICATED/DISCONNECTED
        │
        ▼
BrokerSession.begin_authentication() → AUTHENTICATING
        │
        ▼
BrokerSession.complete_authentication(provider)
        │   provider wraps FyersTokenManager / FyersBroker.connect()
        ▼
BrokerSession → AUTHENTICATED → connect() → CONNECTED → mark_ready() → READY
        │
        ▼
[NOT YET BUILT] Dispatch Integration hands each DispatchInstruction's
OrderRequest to ExecutionEngine.submit_and_confirm() via the injected
ExecutionEngineInterface
        │
        ▼
ExecutionEngine (retry, idempotent lookup-before-place, fill-poll)
        │
        ▼
Broker (FyersBroker / PaperBroker)
```

### Authentication failure

```
BrokerSession.complete_authentication(provider)
        │   provider.authenticate() returns AuthenticationOutcome(success=False)
        ▼
BrokerSession → authentication_state=FAILED, session_state=FAILED
        │
        ▼
No dispatch is ever attempted -- RuntimeSession itself is unaffected
(it remains ACTIVE at the runtime-lifecycle level; the *broker*
session failed, not the *runtime* session). A future Dispatch
Integration series must check BrokerSession's own state before
attempting to hand anything to ExecutionEngine.
```

### Session expiry

```
BrokerSession (AUTHENTICATED, expires_at = T)
        │
        ▼
check_expiry(clock)  -- called at some future point, clock() >= T
        │
        ▼
BrokerSession → authentication_state=EXPIRED, session_state=EXPIRED
        │
        ▼
Re-authentication requires a fresh begin_authentication() →
complete_authentication() cycle -- production's own FyersTokenManager
already knows how to request a new token via the refresh-token grant;
Phase 2 orchestrates that call, never reimplements it.
```

### Broker unavailable

```
ExecutionEngine._with_retry() exhausts retry_attempts against a down
FyersBroker, raises ExecutionError.
        │
        ▼
[NOT YET BUILT] Dispatch Integration must catch this and transition
the ExecutionSession appropriately (a state this sprint's
runtime_execution package already has: ABORTED) -- it must NOT retry
again itself; ExecutionEngine already exhausted its own retry budget.
```

### Restart recovery

```
Process restarts.
        │
        ▼
Orchestrator._recover() (unchanged, production) reconciles saved
SessionStore snapshot against live broker positions -- exactly as it
does today, with zero Phase 2 involvement.
        │
        ▼
[NOT YET BUILT] A future Recovery Integration series would need to
decide: does a RuntimeSession/BrokerSession from before the restart
get recreated, or does Phase 2's own lifecycle simply start fresh
(CREATED) once Orchestrator._recover() has already restored the
*position*-level state? This is an open design question -- see the
Gap Analysis and Risk Assessment below.
```

---

## Deliverable 5 — Operational State Machines

Four state machines now coexist. None of them may be merged with any
other; each answers a different question.

| State Machine | Owner | Answers |
|---|---|---|
| `RuntimeSession.session_state` | `runtime_session/` (Phase 2) | "Is the Phase 2 runtime process itself running?" |
| `BrokerSession.authentication_state` / `.session_state` | `authentication/` (Phase 2) | "Is our broker identity currently valid, and is that session connected/ready?" |
| `ExecutionSession.execution_state` | `runtime_execution/` (Phase 2) | "Has this specific batch of orders been validated and handed off?" |
| `core/state_machine.py::State` | production (Series 41) | "What is the lifecycle of our one open position?" |

### Every legal transition

- `RuntimeSession`: `CREATED → INITIALIZED → READY → ACTIVE ⇄ PAUSED`;
  `ACTIVE → COMPLETED`; any non-terminal state `→ ABORTED`.
  Terminal: `COMPLETED`, `ABORTED`, `FAILED`.
- `BrokerSession.authentication_state`: `UNAUTHENTICATED →
  AUTHENTICATING → AUTHENTICATED → EXPIRED`; `AUTHENTICATING →
  FAILED`. Terminal: `EXPIRED`, `FAILED`.
- `BrokerSession.session_state`: `DISCONNECTED → CONNECTED → READY`;
  mirrors `authentication_state`'s `EXPIRED`/`FAILED`. Terminal:
  `EXPIRED`, `FAILED`.
- `ExecutionSession.execution_state`: `READY → DISPATCH_PENDING →
  DISPATCHED`; `DISPATCH_PENDING → ABORTED` (executor raised).
  Terminal: `DISPATCHED`, `ABORTED`, `FAILED_VALIDATION`.
- `core/state_machine.py::State` (production, unchanged): `WAITING →
  READY → CONFIRMED → IN_POSITION → EXITING → DONE_FOR_DAY`. Terminal:
  `DONE_FOR_DAY`. A separate, audited `restore()` bypass exists solely
  for `Orchestrator._recover()`.

### Interaction between them

None of these four state machines transitions another. The correct
interaction pattern (once Dispatch Integration exists) is: a
`RuntimeSession` in `ACTIVE`, with a `BrokerSession` in `READY`, is the
*precondition* for attempting to advance an `ExecutionSession` from
`DISPATCH_PENDING` to `DISPATCHED` — but advancing the `ExecutionSession`
never itself changes `RuntimeSession` or `BrokerSession`, and it
certainly never changes production's own `core.state_machine.State`
directly. That remains `TradeManager`'s and `Orchestrator`'s exclusive
concern, driven by real fill/position data from `ExecutionEngine`, not
by anything in Phase 2.

---

## Deliverable 6 — Gap Analysis

| Capability | Status |
|---|---|
| Runtime process lifecycle | **Already Exists** (Series 48) |
| Authorization gate | **Already Exists** (Series 47) |
| Deterministic multi-leg dispatch planning | **Already Exists** (Series 45) |
| Authentication orchestration shape | **Already Exists** (Series 49's `AuthenticationProviderInterface`) |
| A concrete wrapper satisfying `AuthenticationProviderInterface` around real `FyersTokenManager`/`FyersBroker.connect()` | **Missing** — the one genuinely open seam |
| A concrete wrapper satisfying `ExecutionEngineInterface` around real `ExecutionEngine.submit_and_confirm()` | **Missing** — the other genuinely open seam |
| Dispatch ownership | **Partially Exists**: planning exists (Series 45); actual hand-off to a concrete `ExecutionEngineInterface` implementation does not yet exist. Ownership itself is unambiguous (Runtime Execution Orchestrator plans, `ExecutionEngine` executes) — only the wiring is missing. |
| Retry ownership | **Already Exists**, unambiguous: `ExecutionEngine._with_retry()` alone. Phase 2 has never implemented, and must never implement, a second retry mechanism. |
| Reconciliation ownership | **Already Exists**, unambiguous: `ExecutionEngine.reconcile()` / `Orchestrator._recover()`. No Phase 2 module has, or should ever have, its own reconciliation logic. |
| Recovery ownership | **Already Exists** for position-level recovery (`Orchestrator._recover()`). **Missing** for runtime-level recovery: no series yet defines whether a `RuntimeSession`/`BrokerSession` should be recreated after a process restart, or simply start fresh. This is a genuine, disclosed gap — see Risk Assessment. |
| Health monitoring integration | **Partially Exists**: `HealthMonitor` exists and is extensible; Phase 2's own states are not yet wired into it as inputs. |
| Circuit breaker | **Missing everywhere** (confirmed again this sprint — still absent from both systems). |
| Rate limiter | **Missing everywhere** (confirmed again this sprint — still absent from both systems). |
| A second broker abstraction | **Must Never Be Added** — `Broker` ABC already exists and is correct. |
| A second retry mechanism | **Must Never Be Added.** |
| A second idempotency key scheme | **Must Never Be Added** — `OrderRequest.client_order_id` (reused verbatim since Series 44) is already the one scheme. |
| A second position-lifecycle state machine | **Must Never Be Added** — `core/state_machine.py::State` is the one authority; `RuntimeSession`'s own states must never be confused with or merged into it. |

---

## Deliverable 7 — Risk Assessment

### Risk: State divergence between `RuntimeSession` and `core.state_machine.State`

**Description**: both are "session-shaped" state machines, differently
scoped (runtime-process vs. position). A future engineer under time
pressure could be tempted to fold one into the other, or to drive
`core.state_machine.State` transitions from `RuntimeSession` events.
**Impact**: would silently blur the exact boundary Series 41 and this
review both establish — position lifecycle would become entangled with
runtime-process lifecycle, making both harder to reason about and
breaking the property that `core.state_machine.State`'s only bypass
(`restore()`) is exclusively used by `Orchestrator._recover()`.
**Mitigation**: this document, plus each module's own architecture doc,
explicitly names both state machines and states they must never merge.
Any future series proposing to touch `core/state_machine.py` should be
treated as a red flag requiring explicit justification.

### Risk: Duplicated retry or reconciliation logic

**Description**: `ExecutionEngineInterface`'s injected object could, in
principle, be implemented with its own retry loop around
`ExecutionEngine.submit_and_confirm()` — silently adding a second retry
layer on top of `ExecutionEngine`'s own `_with_retry()`.
**Impact**: doubled retry budgets, harder-to-reason-about backoff
timing, and a real risk of duplicate order submission if both layers
retry independently around an ambiguous network failure.
**Mitigation**: the concrete wrapper built in a future Dispatch
Integration series must be a thin pass-through with zero retry logic
of its own — call `ExecutionEngine.submit_and_confirm()` exactly once
per `DispatchInstruction`, and let any raised `ExecutionError` propagate
to `runtime_execution.engine.dispatch()`'s own existing
try/abort-on-exception handling (already built, Series 45).

### Risk: Duplicated or conflicting session tracking

**Description**: `BrokerSession` (Phase 2) and `FyersBroker`'s own
internal state (its `_cid_to_order_id` cache, its cached SDK client)
both describe "is this broker session usable right now" from two
different vantage points.
**Impact**: if `BrokerSession` is ever updated independently of what
`FyersBroker`/`FyersTokenManager` actually report, the two can diverge
— e.g. `BrokerSession` says `AUTHENTICATED` while the real token has
already expired.
**Mitigation**: `BrokerSession` must only ever transition based on
what an injected `AuthenticationProviderInterface` wrapper reports —
never inferred, assumed, or defaulted independently. The wrapper is the
single source of truth; `BrokerSession` is a *record* of what it
reported, not an independent judgment.

### Risk: Conflicting ownership of "should we act on this order"

**Description**: both `runtime_safety.engine.authorize()` (Series 47)
and `ExecutionEngine`'s own idempotency check (`_lookup` before
placing) answer a version of "should this proceed?" — at different
layers, for different reasons.
**Impact**: if a future Dispatch Integration series conflates the two
(e.g. treats `RuntimeAuthorization`'s `AUTHORIZED` as a substitute for
`ExecutionEngine`'s own idempotency lookup, skipping it), the
duplicate-submission protection Series 41 identified as a real,
partially-verified risk area would be weakened, not strengthened.
**Impact if unmitigated**: a duplicate order submission slipping
through precisely where this project has been most careful.
**Mitigation**: `RuntimeAuthorization` answers "is this session
authorized to advance at all" — a policy question. `ExecutionEngine`'s
idempotency check answers "has this exact order already been placed" —
a factual question. A future Dispatch Integration series must always
run both, never treat one as satisfying the other.

### Risk: Hidden coupling through shared configuration versions

**Description**: `RECOGNIZED_CONFIG_VERSIONS`,
`RECOGNIZED_RUNTIME_POLICY_VERSIONS`, `RECOGNIZED_SESSION_POLICY_VERSIONS`,
and `RECOGNIZED_POLICY_VERSIONS` are each declared independently in
four different Phase 2 packages (`runtime_execution`, `runtime_safety`,
`runtime_session`, `authentication`), all currently `("1.0.0",)`.
**Impact**: bumping one package's version without the others could
silently create an inconsistent deployment where some gates accept a
session other gates would reject — not a bug in any one module, but an
emergent one from four independently-versioned checks that happen to
need to stay in lockstep operationally.
**Mitigation**: not a code change (out of scope this sprint) but an
operational discipline: any future version bump across these four
packages should be reviewed together, and a future Production
Qualification series (see roadmap) should include an explicit
cross-package version-consistency check.

### Risk: Recovery-on-restart gap for Phase 2's own lifecycle

**Description**: `Orchestrator._recover()` already handles restart
recovery for *positions*. Nothing yet defines what happens to an
in-flight `RuntimeSession`/`BrokerSession`/`ExecutionSession` if the
process restarts mid-session.
**Impact**: undefined behavior on restart for the Phase 2 layer
specifically — not dangerous today (Phase 2 doesn't dispatch anything
yet), but must be resolved before Dispatch Integration ships, or a
restart mid-dispatch could leave Phase 2's own bookkeeping
inconsistent with what `Orchestrator._recover()` already correctly
determined about the actual position.
**Mitigation**: recommend this be explicitly scoped into the Recovery
Integration series (see roadmap) rather than assumed away.

---

## Deliverable 8 — Implementation Roadmap

Based on this review's findings — not proposed generically, but
derived from what is genuinely missing above:

1. **Dispatch Integration** (highest priority — closes the one
   concrete gap this review found twice: two Protocols already
   designed for exactly this, zero concrete wrappers existing). Build
   the thin `ExecutionEngineInterface` wrapper around
   `ExecutionEngine.submit_and_confirm()` and the thin
   `AuthenticationProviderInterface` wrapper around
   `FyersTokenManager`/`FyersBroker.connect()`. Both wrappers must
   contain zero retry logic of their own (Risk: duplicated retry,
   above).
2. **Recovery Integration** (second priority — a disclosed, currently
   undefined gap). Define what happens to Phase 2's own
   `RuntimeSession`/`BrokerSession`/`ExecutionSession` state on a
   process restart, explicitly composing with — never replacing —
   `Orchestrator._recover()`.
3. **Health Integration** (lower priority, low risk). Wire Phase 2's
   own lifecycle states into `HealthMonitor`/`ops/alerts.py` as
   additional inputs, per the Responsibility Matrix's own "extend,
   never duplicate" finding.
4. **Circuit Breaker** and **Rate Limiter** (confirmed, again, to be
   missing from both systems entirely). Genuinely new work — build
   once, alongside `ExecutionEngine._with_retry()`, never inside it and
   never inside Phase 2's own dispatch wrapper.
5. **Production Qualification** (final, only after 1–4): a
   qualification harness for the fully-wired Dispatch Integration,
   exercised only against `PaperBroker`, verifying the
   cross-package version-consistency risk above and everything the
   Series 46 End-to-End Replay Qualification already proved for the
   business-logic pipeline — now proven for the operational layer too.

**Explicitly not recommended by this review**: any new broker
abstraction, any new retry mechanism, any new idempotency scheme, or
any new position-lifecycle state machine — every gap analysis
conclusion above says these already exist and must be reused, not
rebuilt.

---

## Summary

The two systems compose cleanly. Phase 2's Series 45 and 49 were
already designed with the exact seams production needs
(`ExecutionEngineInterface`, `AuthenticationProviderInterface|`) —
this review's main finding is that those seams are real, correctly
scoped, and simply not yet connected to a concrete implementation. No
duplicated responsibility exists today. The risks identified above are
about what **could** go wrong in the next series (duplicated retry,
state-machine conflation, session-tracking divergence) if that wiring
is done carelessly — not about anything already built incorrectly.
The recommended next series, Dispatch Integration, is narrowly scoped
specifically to avoid every one of those risks.
