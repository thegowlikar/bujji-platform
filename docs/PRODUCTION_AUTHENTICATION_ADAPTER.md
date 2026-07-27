# Production Authentication Adapter

**BUJJI Options OS v3 — Engineering Series 51, Sprint 1**

## Status

Deployed. This closes the one concrete integration seam the Series 50
Runtime Integration Review identified: `AuthenticationProviderInterface`
(Series 49) was already designed correctly, but no concrete
implementation of it existed. This sprint adds exactly one — nothing
more.

## Mission Recap

Series 49 introduced the broker-neutral `AuthenticationProviderInterface`
Protocol. Series 50 proved the missing piece was not a new
authentication design — it was a concrete adapter satisfying that
Protocol using the existing production authentication stack. This
sprint implements exactly that adapter.

## What This Adapter Reuses, Unchanged

Per the Series 41 architecture review and confirmed again by directly
reading production source for this sprint:

- **`bujji.broker.fyers.FyersBroker.connect()`** is the single
  entry point this adapter calls. Reading its implementation confirms
  it already handles the entire authentication lifecycle internally:
  it validates the stored access token via the `profile` endpoint, and
  — only if that fails with an `AuthenticationError` — automatically
  calls `self._token_manager.refresh()` (its own internal
  `FyersTokenManager` instance) exactly once before retrying validation,
  raising the same `AuthenticationError` if refresh isn't configured or
  also fails.
- **`bujji.broker.fyers_token_manager.FyersTokenManager`** is never
  called directly by this adapter — `FyersBroker.connect()` already
  owns that call internally, exactly as designed in production. This
  adapter does not need, and does not have, its own reference to a
  `FyersTokenManager` instance at all.

**Neither class is modified.** Both remain exactly as mapped in
Series 41 and re-confirmed here.

## What This Adapter Does — Translation Only

`ProductionAuthenticationAdapter.authenticate()`:

1. Calls the wrapped broker object's own `connect()` method exactly
   once (bridged from synchronous to asynchronous via `asyncio.run()`
   — see the disclosed v1 limitation below).
2. If `connect()` returns without raising: translates to
   `AuthenticationOutcome(success=True, broker_identity=<supplied
   identity>, expires_at=None, error=None)`.
3. If `connect()` raises `bujji.broker.errors.AuthenticationError`:
   translates to `AuthenticationOutcome(success=False,
   broker_identity=None, expires_at=None, error=str(exc))`.
4. If `connect()` raises anything else: still translates to a
   `success=False` outcome (`error=repr(exc)`) — **never suppressed,
   never silently swallowed, never re-raised past this adapter**. Every
   production error, of any kind, always surfaces as a translated
   `AuthenticationOutcome`, recorded verbatim in the trace.

No new outcome type is introduced — `AuthenticationOutcome` is exactly
Series 49's own frozen model, unchanged.

## Why `expires_at` Is Always `None` Here

`FyersBroker.connect()` does not itself expose a token expiry
timestamp — it only validates or refreshes the token, then returns.
Fabricating an `expires_at` value this adapter cannot actually observe
would violate "never fabricate success" in spirit even for a
side-channel field. Leaving `expires_at=None` lets
`authentication.engine.complete_authentication()` (Series 49, already
built, unmodified) fall back to its own already-existing
`AuthenticationPolicy.session_ttl_seconds`-based default — exactly the
fallback path that code was designed for, reused here rather than
worked around.

## Why `broker_identity` Is Supplied, Not Reached-Into

This adapter never reaches into `FyersBroker`'s private attributes
(e.g. `_cfg.app_id`) to determine an identity string — that would
couple this adapter to production's internal implementation details
rather than its public contract. Instead, `broker_identity` is
supplied by the adapter's own constructor, from whatever configuration
already constructed the wrapped broker (the same `BrokerConfig.app_id`
a caller already has in hand).

## The Sync/Async Bridge — A Disclosed v1 Limitation

`AuthenticationProviderInterface.authenticate()` (Series 49) is a
plain synchronous method; `FyersBroker.connect()` (production) is
`async def`. `_run_async()` bridges the two via `asyncio.run()`. If
`authenticate()` is ever called from within an already-running event
loop, `_run_async()` raises explicitly rather than attempting to nest
event loops — a known, disclosed v1 limitation, not a silent
correctness gap. A future series integrating this adapter into an
already-async runtime (e.g. alongside `FyersTickFeed`'s own event loop)
would need to address this properly; it is out of scope here.

## No Broker SDK Import at This Module's Own Top Level

`authentication_adapter.py` imports only `bujji.authentication.models`
(Series 49, this project's own frozen type) and
`bujji.broker.errors` (a tiny, dependency-free exception module — no
FYERS SDK import). It never imports `bujji.broker.fyers.FyersBroker`
directly: the adapter's `__init__` accepts **any** object satisfying
the narrow shape `FyersBroker` already has (`async def connect(self) ->
None`, raising `AuthenticationError` on failure). Production's real
`FyersBroker` instance satisfies this shape without any change; this
sprint's own tests use a lightweight stub with the identical shape, so
no test here imports the FYERS SDK, opens a socket, or touches a real
broker account.

## Traceability

Every `authenticate()` call records, in `self.last_trace`: the runtime
request, the production method invoked, the production response
(success or the exact exception), and the resulting
`AuthenticationOutcome` — matching the specification's own requirement
that every invocation record all four. This trace is diagnostic
instance state, not a new frozen output type — the only value crossing
the `AuthenticationProviderInterface` boundary remains
`AuthenticationOutcome` itself.

## What This Sprint Explicitly Does Not Do

Per the specification's own explicit exclusions, this adapter contains
zero: token refresh logic of its own, credential storage, retry logic,
session caching, expiry calculation, or broker-specific business logic
beyond translating one method call's outcome.

## Verification Before Declaring Completion

- **The adapter is the only new code introduced.** Confirmed: this
  sprint's sole deliverable is `bujji/integration/authentication_adapter.py`
  plus its own documentation and tests.
- **Existing production authentication classes remain unchanged.**
  Confirmed: `bujji/broker/fyers.py` and
  `bujji/broker/fyers_token_manager.py` were read, never edited.
- **No retry logic, token management, or session management has been
  duplicated.** Confirmed: this adapter calls `connect()` exactly once
  per `authenticate()` call, with no loop, no backoff, and no
  credential handling of its own.
- **All tests pass**; see `tests/test_production_authentication_adapter.py`.
- **The qualification fingerprint for the deterministic pipeline
  (Series 31-50) remains unchanged** — this sprint adds an integration
  layer only, touching no business logic.

## Isolation Guarantees

- No import of the FYERS SDK, `bujji.broker.fyers.FyersBroker`,
  `bujji.broker.fyers_token_manager.FyersTokenManager`, or any network
  library at this module's own top level.
- No modification to any file under `bujji/broker/`.
- No retry loop, no credential persistence, no session cache, and no
  expiry computation anywhere in this adapter.
- Every production error is translated into a `success=False`
  `AuthenticationOutcome` — never swallowed, never re-raised past the
  adapter, never turned into a fabricated success.

**This adapter's sole responsibility is translation: it makes
production's existing, proven authentication stack satisfy
`AuthenticationProviderInterface` without changing that stack's
behavior in any way. This marks the transition from architecture into
production integration by reusing, rather than replacing, the proven
authentication infrastructure.**
