# Historical Qualification Framework

**BUJJI Options OS v3 — Engineering Series 58**

## Status

Deployed. `bujji/qualification/{historical_runner.py, recorder.py,
report.py}` add `HistoricalQualificationRunner`,
`QualificationRecorder`, and `QualificationReport` — a framework that
replays a corpus of historical sessions through the *entire* integrated
runtime built across Series 54–57: Production Composition Root →
Health Aggregator → Circuit Breaker → Rate Limiter → Shadow Runtime.
Qualification observes. It never optimizes, tunes, or changes runtime
behavior — this sprint computes zero P&L and makes zero strategy
decisions of its own.

## What's genuinely new vs. what's reused

Every stage of the pipeline this framework drives is an unmodified
call into a prior, frozen series, reached exclusively through
`bujji.production_runtime.rate_limiter.guarded_run_shadow()` (Series
57), which itself only reaches Series 54's `run_shadow()` when the
Circuit Breaker (Series 56) and Rate Limiter (Series 57) both agree
admission is safe. The only new logic in this sprint is: iterating a
corpus, threading a deterministic per-session clock through the
admission chain, and recording what happened.

## Reusing Series 46's `ReplayScenario`

Rather than inventing a second "historical session" shape, the corpus
is a `Sequence[bujji.qualification.replay_models.ReplayScenario]`
(Series 46) — it already bundles every external input the pipeline
needs (`market_context`/`market_opinion`/.../`spot_snapshot`/
`option_chain`), is frozen, and is never mutated by this framework.
The replay corpus itself (real historical spot/option-chain data) is
explicitly out of scope for this sprint, per the specification — the
framework is designed so a real corpus (a list of `ReplayScenario`s
sourced from historical data) can be plugged in later without any
change to `historical_runner.py`.

## A disclosed naming overlap, not a collision

A different `QualificationReport` already exists at
`bujji.qualification.replay_models.QualificationReport` (Series 46) —
it summarizes one `ReplayScenario` run through the deterministic
pipeline *in isolation*, outside the runtime/admission-control layer.
This sprint's `bujji.qualification.report.QualificationReport`
summarizes many replay sessions run through the *full integrated
runtime*. The two classes live in different modules
(`replay_models.py` vs. `report.py`) under the same `bujji.qualification`
package; neither imports the other, and nothing shadows anything —
unlike Series 54's genuine `bujji/app` package-vs-module collision,
this is a same-name-different-module situation that Python handles
without ambiguity as long as callers import by fully-qualified path
(`from bujji.qualification.report import QualificationReport`, not a
bare `from bujji.qualification import QualificationReport`).
Documented here rather than silently renamed, since renaming this
sprint's own new class was not necessary to resolve any actual
conflict.

## Deterministic per-session clock

`run_corpus(scenarios, timestamps, recorder=None)` requires
`len(timestamps) == len(scenarios)` — one injected `datetime` per
session. That single timestamp is the sole clock reading used for that
session's health snapshot, circuit decision, rate-limit decision, and
Shadow Runtime run (via `dataclasses.replace()` producing session-scoped
copies of the aggregator/breaker/limiter with that one clock — the
system clock is never read anywhere in this module). This is what
makes report generation reproducible: the same `(scenarios,
timestamps)` pair always produces byte-identical
`QualificationRecord`s and therefore a byte-identical
`QualificationReport` (report `timestamp`/`report_id` aside, which are
themselves derived from the caller's own injected clock passed to
`build_report()`).

## Rate-limiter admission tracking across the corpus

The runner tracks `previous_admission_timestamp` as its own local
variable across the loop, updating it to the current session's
timestamp only when that session's `RateLimitDecision.permitted` is
`True`. This is consistent with `rate_limiter.py`'s own stateless
design (Series 57): the limiter itself never remembers anything: the
runner, as caller, owns remembering it.

## What each `QualificationRecord` captures

`replay_identifier`, `timestamp`, `strategy_decision`, `risk_decision`,
`capital_decision`, `execution_plan` (all four `None` when the session
was rejected before running the decision pipeline), the exact
`health_snapshot`/`circuit_decision`/`rate_limit_decision` objects
produced for that session (never copies or summaries — full
traceability back through Series 55–57), a `RuntimeOutcome`
(`COMPLETED` / `REJECTED_CIRCUIT` / `REJECTED_RATE_LIMIT` / `FAILED`,
each with a human-readable `reason` and, when applicable, the full
`ShadowResult`), and `qualification_fingerprint` (read verbatim from
`root.config`, never recomputed — consistent with Series 54's own
"`QualificationPolicy` is a build-time attestation" design point).

`QualificationRecorder.record()` only ever appends; `.records` always
returns a fresh tuple, so nothing already recorded can be overwritten,
reordered, or removed.

## What `QualificationReport` computes

Pure counting over already-recorded, already-classified evidence:
`total_replay_sessions`, `completed_runs`, `rejected_runs`,
`runtime_failures`, `health_state_counts`, `circuit_state_counts`,
`rate_limit_state_counts`, `insufficient_data_occurrences` (summed
across all three admission-control layers), plus
`replay_identifiers`/`qualification_fingerprints` tuples for full
traceability back to every session that contributed. No P&L, no
strategy scoring, no optimization.

## Shadow-only / no-live-interaction guarantee

Every session's execution, when admitted, dispatches into
`root.broker` — which is `PaperBroker` for every `RuntimeConfig` this
framework's own tests construct, exactly as Series 54 established for
Shadow mode. This module never constructs a `FyersBroker`, never calls
`connect()`/`authenticate()`/`dispatch()` itself (those calls happen
only inside the already-frozen `run_shadow()`, gated by the
already-frozen Circuit Breaker/Rate Limiter), and never mutates a
`ReplayScenario` it is given.

## Verification before declaring completion

See the Series 58 deployment report for exact test counts, an example
qualification report from a small replay corpus, and the qualification
fingerprint comparison.
