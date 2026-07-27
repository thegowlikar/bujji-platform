# Runtime Rate Limiter

**BUJJI Options OS v3 — Engineering Series 57**

## Status

Deployed. `bujji/production_runtime/rate_limiter.py` adds
`RuntimeRateLimiter`, `RateLimiterConfig`, and `RateLimitDecision` —
deterministic admission-frequency control layered *after* the Series
56 Circuit Breaker. The Circuit Breaker answers "may the runtime
accept new work"; the Rate Limiter answers "how frequently." It is
independent of health and consumes `CircuitDecision`, never replacing
it.

## Statelessness — a deliberate design choice

Unlike a typical rate limiter, `RuntimeRateLimiter` holds no internal
"last admission time" state. The caller supplies
`previous_admission_timestamp` explicitly on every `evaluate()` call.
This is a direct, literal reading of the specification's own
traceability requirement — "No hidden state" — and keeps this module
architecturally consistent with `RuntimeHealthAggregator.snapshot()`
(Series 55) and `RuntimeCircuitBreaker.evaluate()` (Series 56), both
of which are already pure functions of their explicit inputs. Whoever
calls the rate limiter (a future scheduling/orchestration layer, out
of scope for this sprint) owns remembering the last admitted
timestamp; this module only ever computes elapsed time from what it is
given.

## Configuration

`RateLimiterConfig` is a frozen dataclass:
`min_interval_seconds` (the only decision-relevant field, default
`1.0`, must be `>= 0`), plus `mode`/`qualification_mode` carried
through purely for traceability in `RateLimitDecision.reason` — the
same pattern `CircuitDecision` already uses. Immutable after
construction; any attempted mutation raises `FrozenInstanceError`.

## Clock

`RuntimeRateLimiter.clock` is injectable (defaults to
`datetime.now`), exactly like every other clock in this project. The
decision logic never calls the system clock directly — `evaluate()`
calls `self.clock()` exactly once per invocation, and all elapsed-time
arithmetic is a comparison between that one call and the caller-supplied
`previous_admission_timestamp`.

## Decision vocabulary

Three values: `PERMITTED`, `RATE_LIMITED`, `INSUFFICIENT_DATA`.

## Decision rules

Evaluated in order:

1. Missing `config` or missing `clock` → `INSUFFICIENT_DATA`. No
   timestamp is fabricated for this case — `RateLimitDecision.timestamp`
   is left as an empty string rather than guessed, matching the
   project's "never fabricate evidence" discipline used throughout
   Series 55/56.
2. `circuit_decision is None`, or `circuit_decision.state != CLOSED`
   (i.e. `OPEN`, `HALF_OPEN`, or `INSUFFICIENT_DATA`) → `RATE_LIMITED`.
   This mirrors the architecture diagram's own "the rate limiter only
   executes if the circuit is CLOSED" note as an explicit, defensive
   check inside `evaluate()` itself, not only as an external gating
   convention.
3. Circuit `CLOSED` and no `previous_admission_timestamp` supplied →
   `PERMITTED` ("nothing to rate-limit against" — the first-ever
   admission is never refused for lacking a history it cannot have).
4. Circuit `CLOSED` and a `previous_admission_timestamp` supplied →
   compute `elapsed = now - previous_admission_timestamp`; `PERMITTED`
   if `elapsed >= min_interval_seconds`, else `RATE_LIMITED`.

No randomness, no adaptive learning, no scoring — the same
(`circuit_decision.state`, `previous_admission_timestamp`, `now`,
`min_interval_seconds`) tuple always produces the same decision.

## Traceability

Every `RateLimitDecision` carries: `state`, `permitted`, the exact
`circuit_decision` it was derived from (full traceability back through
Series 56 to Series 55's health evidence), `timestamp`,
`previous_admission_timestamp`, `configured_interval_seconds`,
`elapsed_seconds` (`None` when there was nothing to compare against),
and a human-readable `reason`.

## Runtime integration

`guarded_run_read_only()`/`guarded_run_shadow()` in this module accept
an already-computed `CircuitDecision` (Series 56) and gate Series 54's
`run_read_only()`/`run_shadow()` behind `evaluate()`, exactly as
Series 56's own guarded wrappers gate them behind the Circuit Breaker.
Per this sprint's "do not modify a frozen series" rule,
`runtime.py`/`circuit_breaker.py` are untouched; the full admission
chain (Health → Circuit → Rate Limiter → Pipeline) is composed by
calling Series 55, then Series 56, then this module's wrapper, in
that order, from outside all three frozen/prior modules.

## Existing work is unaffected

The guarded wrappers only gate a *new* `run_read_only()`/`run_shadow()`
call — they take no reference to, and never touch, any
already-returned `ReadOnlyResult`/`ShadowResult` from a prior call.
Rate-limiting a new admission has no way to reach back into a session
already in flight.

## Read-only guarantee

`evaluate()` never calls `connect()`, `authenticate()`, `dispatch()`,
or `recover()` on anything, and inspects no broker or market state —
only timestamps and the supplied `CircuitDecision`. Confirmed by
`tests/test_runtime_rate_limiter.py::test_no_broker_authentication_dispatch_or_recovery_calls`.

## Verification before declaring completion

See the Series 57 deployment report for exact test counts, the four
required example decisions, and the qualification fingerprint
comparison.
