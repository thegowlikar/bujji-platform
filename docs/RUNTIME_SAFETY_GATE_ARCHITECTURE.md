# Runtime Safety Gate Architecture

**BUJJI Options OS v3 — Engineering Series 47, Sprint 1**

## Status

Deployed. This is the final checkpoint before any operational
infrastructure, per the project's own recommendation following the
Series 46 replay qualification. It lives at `bujji/runtime_safety/` —
outside the Trading Brain, outside the Runtime Execution Orchestrator,
and outside the Broker Adapter — depending only on the frozen
`ExecutionSession` model (Series 45).

## Purpose and Philosophy

The Trading Brain decides. The Runtime Execution Orchestrator
prepares. The Runtime Safety Gate authorizes. Nothing reaches
authentication or broker connectivity without passing through this
module. It performs authorization, not execution: it never
authenticates, refreshes a token, connects to a broker, retries,
polls, or reconciles.

## Inputs: Three Objects, Nothing Else

`engine.py::authorize()` accepts exactly an `ExecutionSession` (Series
45), a `QualificationPolicy`, and a `RuntimeSafetyPolicy` — never a
broker, an authentication module, a token manager, a network call,
`ExecutionEngine`, or a runtime connector.

`QualificationPolicy` is a deliberately narrow summary of a Series 46
replay result — `replay_status`, `qualification_fingerprint`,
`chain_valid`, `deterministic` — exactly as much as this gate needs to
check, never the full `QualificationReport`. `RuntimeSafetyPolicy`
carries only a `policy_version` and an `allow_authorized_with_warnings`
flag.

## Nine Safety Checks, Run Unconditionally

Whenever all three inputs are present, every one of the nine checks
runs — **never short-circuiting** after the first failure, so both
`passed_checks` and `failed_checks` are always complete and every
authorization decision can fully explain itself:

| Check | Fails when | Failure Reason |
|---|---|---|
| `EXECUTION_SESSION_VALID` | `execution_state` not in `READY`/`DISPATCH_PENDING`/`DISPATCHED`, or dispatch plan/order count mismatch | `INVALID_SESSION` |
| `QUALIFICATION_FINGERPRINT_PRESENT` | `qualification_fingerprint` is `None` or empty | `MISSING_FINGERPRINT` |
| `PIPELINE_COMPLETED_SUCCESSFULLY` | `chain_valid` is not `True` | `FAILED_REPLAY` |
| `DISPATCH_PLAN_NOT_EMPTY` | Zero dispatch instructions | `EMPTY_DISPATCH_PLAN` |
| `CLIENT_ORDER_IDS_UNIQUE` | Two or more `OrderRequest`s share a `client_order_id` | `DUPLICATE_CLIENT_ORDER_ID` |
| `ORDER_COUNT_CONSISTENT` | Dispatch plan length ≠ order request count | `INVALID_SESSION` |
| `REPLAY_QUALIFICATION_PASSED` | `replay_status != "PASSED"` or `deterministic is not True` | `FAILED_REPLAY` |
| `CONFIGURATION_VERSION_RECOGNIZED` | `ExecutionSession.version` not in the recognized set | `INVALID_CONFIGURATION` |
| `RUNTIME_POLICY_RECOGNIZED` | `RuntimeSafetyPolicy.policy_version` not in the recognized set | `INVALID_CONFIGURATION` |

No broker connectivity check, no network check, and no account
balance check exists among these nine — this gate has no way to reach
any of those even if it wanted to.

## Authorization States

| State | When |
|---|---|
| `INSUFFICIENT_DATA` | Any of the three required inputs was entirely missing; no check could be run at all. |
| `DENIED` | At least one of the nine checks failed. |
| `AUTHORIZED_WITH_WARNINGS` | Every check passed, but `execution_state != "DISPATCHED"` (the session is `READY` or `DISPATCH_PENDING` — validated and dispatch-ready, but not yet confirmed dispatched) and `RuntimeSafetyPolicy.allow_authorized_with_warnings` is `True`. |
| `AUTHORIZED` | Every check passed and the session has already reached `DISPATCHED`. |

`decision` is a coarser three-way summary:
`AUTHORIZED`/`AUTHORIZED_WITH_WARNINGS → ALLOW`, `DENIED → DENY`,
`INSUFFICIENT_DATA → UNKNOWN`.

A session is **never partially authorized** — either every critical
check passes (yielding `AUTHORIZED` or `AUTHORIZED_WITH_WARNINGS`, both
of which mean `ALLOW`), or it is denied outright.

## Validation Rules: Deterministic, No Heuristics

Every check above is a plain boolean comparison against an already-
produced value or a finite recognized-version set — no heuristic, no
machine learning, no adaptive threshold, and no randomness anywhere in
this module.

## Traceability

`authorization_trace` states every failed check, every passed check,
any warnings, and the final authorization state — matching the
specification's own worked example format: *"Replay Qualification →
PASSED → Dispatch Plan → VALID → Fingerprint → MATCHED →
AUTHORIZED."*

## Determinism

`authorization_id` is derived via `hashlib.md5` over the source
session's own id (or `"NONE"`), the authorization state, and the
timestamp — never `uuid4()`. `timestamp` is genuinely wall-clock-
derived by design (an injectable `clock` parameter defaulting to
`datetime.now`), the same pattern used throughout this project. Given
the same three inputs and the same fixed clock, `authorize()` always
produces a byte-identical `RuntimeAuthorization`.

## Journaling

`bujji/journal/runtime_safety_journal.py::RuntimeSafetyJournal` —
append-only JSONL, own schema (`schema_version` field), independent of
every other journal in the codebase.

## Query API

`runtime_safety/query.py::RuntimeAuthorizationIndex` mirrors the
established `*Index` pattern: `ingest()`, then read-only `latest()`,
`history()`, `find_by_id()`, `find_by_state()`, `find_by_session()`,
`summary()`. No mutation method beyond `ingest`.

## What This Sprint Explicitly Does Not Build

Per the specification's own explicit exclusions, this sprint contains
zero: authentication, token refresh, broker connectivity (FYERS or
Zerodha), order submission, retries, polling, reconciliation, or any
modification to production execution components.

## Isolation Guarantees

- No import of a broker, an authentication module, a token manager, a
  network library, `bujji.execution`'s concrete `ExecutionEngine`, or
  any runtime connector anywhere in this package — only the frozen
  `ExecutionSession` model this module consumes.
- No import of `bujji.core`, `bujji.trade`, `bujji.market`, `bujji.tick`,
  `bujji.broker`, MIC v2, or any Trading Brain module upstream of the
  Runtime Execution Orchestrator anywhere in this package.
- No authentication token, session credential, or broker connection
  field anywhere on `RuntimeAuthorization`.
- `authorize()` is a pure function apart from its injectable clock;
  every id is `hashlib.md5`-derived, never `uuid4()`, and no
  randomness of any kind appears anywhere in this package.
- An authorization is only ever `AUTHORIZED`/`AUTHORIZED_WITH_WARNINGS`
  when every one of the nine checks genuinely passed — never
  fabricated, never partial.

**The Runtime Safety Gate's sole responsibility is to determine
whether a replay-qualified execution session is safe to advance toward
live infrastructure. It performs authorization, not execution — the
one explicit, auditable checkpoint every invariant this project has
built, from qualification fingerprint to replay status to dispatch
integrity, must pass before any live side effects become possible.**
