# Authentication & Broker Session Manager Architecture

**BUJJI Options OS v3 — Engineering Series 49, Sprint 1 (v1)**

## Status

Deployed. This module owns broker identity and broker-session state,
per the project's own recommendation following the Series 48 Runtime
Session Manager. It lives at `bujji/authentication/` — outside the
Trading Brain and every other runtime module built so far —
depending only on the frozen `RuntimeSession` model (Series 48).

## Purpose and Philosophy

The Runtime Session Manager owns the lifecycle of an execution
session. This module owns broker identity and broker-session state —
it never places an order, dispatches a trade, retries execution,
polls fills, or reconciles positions.

## Critical Design Principle: Reuse, Never Duplicate

Per the specification's own instruction and the Series 41 architecture
review's own finding — production already has a working
`FyersTokenManager` (daily access-token refresh via refresh-token
grant) and `FyersBroker.connect()` (token validation against the
`profile` endpoint) — this module **wraps and orchestrates** that
existing capability through an abstract interface. It never
reimplements token refresh, never duplicates broker-specific auth
logic, and never imports a broker SDK directly.

`engine.py::AuthenticationProviderInterface` is a structural
`typing.Protocol` (`authenticate() -> AuthenticationOutcome`) — the
same seam pattern established by the Runtime Execution Orchestrator's
`ExecutionEngineInterface` (Series 45) and the Broker Adapter's naming
reuse (Series 40). A production wrapper around `FyersTokenManager`/
`FyersBroker.connect()` would satisfy this shape without any change on
its part; this module's own deterministic layer never needs to import
either to be correct or testable. `AuthenticationOutcome` is a type
**this module owns** — every provider, real or a test stub, must
construct one; this module never adopts production's own token/session
response shape directly.

## Inputs: Two Objects, Nothing Else

`engine.py::create_broker_session()` accepts exactly a `RuntimeSession`
(Series 48) and an `AuthenticationPolicy` — no broker, no
authentication module beyond the injected `AuthenticationProviderInterface`,
no token manager import, no network call, no broker SDK.

## Two Separate State Machines

`authentication_state` and `session_state` are deliberately kept
apart — a `BrokerSession` can be `AUTHENTICATED` but not yet
`CONNECTED`/`READY`:

```
authentication_state:  UNAUTHENTICATED → AUTHENTICATING → AUTHENTICATED → EXPIRED
                                                       └──────────────→ FAILED

session_state:          DISCONNECTED → CONNECTED → READY
                                                 └──→ EXPIRED / FAILED (mirrors authentication_state)
```

Five explicit functions, each a no-op unless its own precondition on
both state machines is met:

| Function | Precondition | Effect |
|---|---|---|
| `create_broker_session()` | `RuntimeSession` in `READY`/`ACTIVE`, recognized policy | `UNAUTHENTICATED` / `DISCONNECTED` |
| `begin_authentication()` | `authentication_state == UNAUTHENTICATED` | → `AUTHENTICATING` |
| `complete_authentication()` | `authentication_state == AUTHENTICATING` | calls `provider.authenticate()` once; success → `AUTHENTICATED` (identity + expiry populated); failure → `FAILED`/`FAILED` |
| `connect()` | `authentication_state == AUTHENTICATED`, `session_state == DISCONNECTED` | → `CONNECTED` |
| `mark_ready()` | `authentication_state == AUTHENTICATED`, `session_state == CONNECTED` | → `READY` |
| `check_expiry()` | `authentication_state == AUTHENTICATED`, `expires_at` present and passed | → `EXPIRED`/`EXPIRED` |

No implicit jumps: every transition is one explicit function call.

## Validation: The Decision Table (`create_broker_session`)

| # | Condition | Failure Reason |
|---|---|---|
| 1 | `RuntimeSession` or `AuthenticationPolicy` entirely missing | `INSUFFICIENT_DATA` |
| 2 | `RuntimeSession.session_state` not `READY` or `ACTIVE` | `INVALID_RUNTIME_SESSION` |
| 3 | `AuthenticationPolicy.policy_version` not recognized | `INVALID_POLICY` |
| — | All checks pass | `UNAUTHENTICATED` / `DISCONNECTED` |

Any failure yields a terminal `FAILED`/`FAILED` `BrokerSession` — never
a partially-valid one.

## Expiry: Deterministic, Clock-Driven

`check_expiry()` compares `clock()` against the stored `expires_at`
(an ISO timestamp) — no network round-trip, no re-authentication
attempt, purely a deterministic comparison given the injectable clock.
`expires_at` is set from the provider's own `AuthenticationOutcome.expires_at`
if supplied (letting a real wrapper report the broker's actual token
expiry), or falls back to `clock() + AuthenticationPolicy.session_ttl_seconds`
otherwise.

## Traceability

`authentication_trace` accumulates one clause per transition — matching
the specification's own worked example: *"Runtime Session READY.
UNAUTHENTICATED. DISCONNECTED."* — extended with `" AUTHENTICATING."`,
`" AUTHENTICATED."`, `" CONNECTED."`, `" READY."` as each subsequent
function runs.

## Determinism

`broker_session_id` is derived via `hashlib.md5` over the source
runtime session's own id and the creation timestamp — never `uuid4()`.
Every timestamp is genuinely wall-clock-derived by design (an
injectable `clock` parameter defaulting to `datetime.now`), the same
pattern used throughout this project. Given the same `RuntimeSession`,
`AuthenticationPolicy`, and the same fixed clock, `create_broker_session()`
always produces a byte-identical `BrokerSession`; given the same
provider outcome and the same clock, every subsequent transition is
equally deterministic.

## Journaling

`bujji/journal/authentication_journal.py::AuthenticationJournal` —
append-only JSONL, own schema (`schema_version` field), independent of
every other journal in the codebase.

## Query API

`authentication/query.py::BrokerSessionIndex` mirrors the established
`*Index` pattern: `ingest()`, then read-only `latest()`, `history()`,
`find_by_id()`, `find_by_authentication_state()`,
`find_by_session_state()`, `summary()`. No mutation method beyond
`ingest`.

## What This Sprint Explicitly Does Not Build

Per the specification's own explicit exclusions, this sprint contains
zero: order submission, execution retries, fill polling, position
reconciliation, or broker-specific business logic of any kind.

## Isolation Guarantees

- No import of a broker SDK, `bujji.broker`, `bujji.execution`'s
  concrete `ExecutionEngine`, FYERS, or Zerodha anywhere in this
  package's deterministic layer — only the frozen `RuntimeSession`
  model this module consumes, and a structural `Protocol` describing
  (never importing) an authentication provider's shape.
- No import of `bujji.core`, `bujji.trade`, `bujji.market`, `bujji.tick`,
  MIC v2, or any Trading Brain module anywhere in this package.
- No order id, fill price, retry count, or reconciliation field
  anywhere on `BrokerSession`.
- Every function in this package is pure apart from the injectable
  clock and the single `provider.authenticate()` call
  `complete_authentication()` delegates to; every id is
  `hashlib.md5`-derived, never `uuid4()`.
- A `BrokerSession` is only ever `AUTHENTICATED` when an injected
  provider genuinely reported success — never fabricated.

**The Authentication & Broker Session Manager's sole responsibility is
to establish and manage authenticated broker sessions using existing
production authentication mechanisms, wrapped through an abstract
interface. It remains completely isolated from order execution,
retries, reconciliation, and trading decisions, which later
Engineering Series will introduce as separate, pluggable operational
services atop this authenticated session.**
