# Runtime Session Manager Architecture

**BUJJI Options OS v3 — Engineering Series 48, Sprint 1 (v1)**

## Status

Deployed. This is the first module of "Phase 2" (operational
runtime), per the project's own recommendation following the Series
47 Runtime Safety Gate. It lives at `bujji/runtime_session/` —
outside the Trading Brain, the Runtime Execution Orchestrator, the
Broker Adapter, and the Runtime Safety Gate — depending only on the
frozen `RuntimeAuthorization` model (Series 47).

## Phase 1 vs. Phase 2

Series 31–47 established BUJJI's deterministic decision and
authorization pipeline — replay-qualified, fingerprint-stable,
side-effect-free. Series 48 onward enters **Phase 2**: operational
runtime, where modules begin interacting with the outside world
(sessions, authentication, broker connectivity). This module is
deliberately still Phase-1-disciplined in its own right — pure,
deterministic, side-effect-free — even though its subject matter (a
live session's lifecycle) is Phase 2's first concern. Phase 1's
guarantees are never weakened by Phase 2 work; they are the foundation
Phase 2 builds on.

## Purpose and Philosophy

Business decisions are complete. Authorization is complete. This
module owns state transitions only — the lifecycle of an
already-authorized `ExecutionSession`. It never authenticates,
connects to a broker, submits an order, retries, polls, or reconciles.
Those are exactly the operational services later Engineering Series
will plug into this lifecycle — never built here.

## Inputs: Two Objects, Nothing Else

`engine.py::create_session()` accepts exactly a `RuntimeAuthorization`
(Series 47) and a `RuntimeSessionPolicy` — never a broker, an
authentication module, a token manager, a network call,
`ExecutionEngine`, or a broker SDK.

## The Session State Machine

Eight finite states, no broker-specific states, no authentication
states, no order states:

```
CREATED → INITIALIZED → READY → ACTIVE ⇄ PAUSED
                                    ↓
                                COMPLETED

(ABORTED reachable from CREATED, INITIALIZED, READY, ACTIVE, or PAUSED)
(FAILED reachable only from creation-time validation failure — never
 entered via a transition function)
```

`taxonomy.py::ALLOWED_TRANSITIONS` is the **one and only** adjacency
table this module consults. `engine.py::_transition()` is the **one
and only** place a state change is applied — every one of
`initialize()`, `mark_ready()`, `activate()`, `pause()`, `resume()`,
`complete()`, `abort()` calls through it. An attempted transition not
listed in the adjacency table is always a no-op: the session is
returned unchanged, never silently forced into an invalid state. No
implicit jumps, no hidden state.

## Pause / Resume: Policy-Gated

`pause()` and `resume()` additionally check
`RuntimeSessionPolicy.allow_pause_resume` before consulting the
adjacency table — if a session's own policy forbids pausing, both
functions are no-ops regardless of the current state.

## Creation: The Decision Table

Evaluated top to bottom; first matching rule wins.

| # | Condition | Failure Reason |
|---|---|---|
| 1 | `RuntimeAuthorization` or `RuntimeSessionPolicy` entirely missing | `INSUFFICIENT_DATA` |
| 2 | `authorization.decision != "ALLOW"` (i.e. it was `DENIED` or itself `INSUFFICIENT_DATA`) | `INVALID_AUTHORIZATION` |
| 3 | `"QUALIFICATION_FINGERPRINT_PRESENT"` not among `authorization.passed_checks` | `MISSING_FINGERPRINT` |
| 4 | `RuntimeSessionPolicy.policy_version` not recognized | `INVALID_POLICY` |
| 5 | The deterministically-derived `session_id` already exists among caller-supplied `existing_session_ids` | `DUPLICATE_SESSION` |
| — | All checks pass | `CREATED` |

**An honest note on the fingerprint check**: `RuntimeAuthorization`
(Series 47) does not carry the qualification fingerprint value itself
— only a record of which of its own nine safety checks passed. This
module verifies fingerprint presence by checking that
`"QUALIFICATION_FINGERPRINT_PRESENT"` is among `passed_checks` — a
defensive re-check of what the Runtime Safety Gate already validated,
consistent with this project's own "never trust upstream blindly"
discipline, rather than an independent re-derivation of the fingerprint
itself (which this module has no way to compute and must not attempt
to fabricate).

Any creation failure yields a terminal `FAILED` session with zero
lifecycle capability — never a partially-valid one. A `FAILED` session
has no allowed outbound transitions; it is a dead end by construction.

## Traceability

`lifecycle_trace` accumulates one clause per transition, matching the
specification's own worked example: *"Runtime Authorized (AUTHORIZED).
Session Created. CREATED."* — extended with `" INITIALIZED."`,
`" READY."`, etc. as each subsequent function runs.

## Determinism

`session_id` is derived via `hashlib.md5` over the source
authorization's own id and the creation timestamp — never `uuid4()`.
`timestamp`/`created_at`/`updated_at` are genuinely wall-clock-derived
by design (an injectable `clock` parameter defaulting to
`datetime.now`), the same pattern used throughout this project. Given
the same `RuntimeAuthorization` and `RuntimeSessionPolicy` and the same
fixed clock, `create_session()` always produces a byte-identical
`RuntimeSession`.

## Journaling

`bujji/journal/runtime_session_journal.py::RuntimeSessionJournal` —
append-only JSONL, own schema (`schema_version` field), independent of
every other journal in the codebase.

## Query API

`runtime_session/query.py::RuntimeSessionIndex` mirrors the
established `*Index` pattern: `ingest()`, then read-only `latest()`,
`history()`, `find_by_id()`, `find_by_state()`,
`existing_session_ids()` (feeding the duplicate-session check above),
`summary()`. No mutation method beyond `ingest`.

## What This Sprint Explicitly Does Not Build

Per the specification's own explicit exclusions, this sprint contains
zero: authentication, token refresh, broker connectivity (FYERS or
Zerodha), order submission, retries, polling, reconciliation, or any
modification to production execution components.

## Isolation Guarantees

- No import of a broker, an authentication module, a token manager, a
  network library, `bujji.execution`'s concrete `ExecutionEngine`, or
  any broker SDK anywhere in this package — only the frozen
  `RuntimeAuthorization` model this module consumes.
- No import of `bujji.core`, `bujji.trade`, `bujji.market`, `bujji.tick`,
  `bujji.broker`, MIC v2, or any Trading Brain module anywhere in this
  package.
- No authentication token, session credential, or broker connection
  field anywhere on `RuntimeSession`.
- Every function in this package is pure apart from the injectable
  clock; every id is `hashlib.md5`-derived, never `uuid4()`, and no
  randomness of any kind appears anywhere in this package.
- A session is only ever created in `CREATED` state when every
  creation check genuinely passed — never fabricated, never partial.

**The Runtime Session Manager is responsible only for managing the
lifecycle of an already-authorized execution session. It remains
completely isolated from authentication, broker connectivity, order
submission, retries, and reconciliation, which later Engineering
Series will introduce as separate, pluggable operational services atop
this lifecycle.**
