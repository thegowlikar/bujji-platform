# Real Opinion Source Wiring Architecture

**Integration Series 4 — Sprint 1: Real Opinion Source Wiring**

## Status

Deployed. Feature-flag gated — the real opinion source is only ever
consulted inside the same `intelligence_adapter.enabled` block every
prior Integration Series sprint uses. No trading behavior change.

## What This Sprint Does

Integration Series 3, Sprint 1 built a real Evaluation Framework whose
`opinion_source` always returned `None` (`default_opinion_source`),
because at the time MIC v2 published no directional opinion at all.
Engineering Series 19, Sprint 1 (MIC v2 side / Addendum 8) changed that.
This sprint connects the two: `IntelligenceSnapshot` now carries a
`market_opinion_id` reference, and a new, small, fully-isolated reader
(`bujji/intelligence/mic_adapter/opinion_reader.py`) resolves it into a
real classification, wired into the `EvaluationEngine`'s
`IntelligencePolicy` once, at `Orchestrator.__init__`.

## Wiring Point

The real opinion source is constructed **once, at orchestrator
construction time** — not per-decision. `_enter()`'s existing control
flow, call site, and try/except structure are completely unchanged;
only the `IntelligencePolicy` object the `EvaluationEngine` was already
using is now constructed with a real `opinion_source` closure instead
of the default one. The closure captures the configured
`opinion_journal_path`/`mic_v2_root` and calls
`opinion_reader.read_opinion_classification()`, which is itself fully
exception-isolated (its own internal `try/except`, never raises).

## Classification Translation (Not Fabrication)

MIC v2's `MarketOpinion.classification` is a 6-state taxonomy
(`BULLISH`/`BEARISH`/`NEUTRAL`/`MIXED`/`INSUFFICIENT_EVIDENCE`/`UNKNOWN`).
BUJJI's `production_direction` (`bujji.core.enums.Direction`) is a
3-state taxonomy (`BULLISH`/`BEARISH`/`NEUTRAL`). `translate_classification()`
passes the three matching values through verbatim and returns `None`
for the other three — `MIXED`, `INSUFFICIENT_EVIDENCE`, and `UNKNOWN`
have no honest BUJJI-side equivalent, and a `None` return correctly
routes through `IntelligencePolicy`'s existing `ABSTAINED` path
(`"no_directional_opinion_published"`) rather than being silently
coerced into `NEUTRAL` or any other value.

## Real Validation: Live, Not Just Unit-Tested

Beyond the dedicated unit tests, a direct replay validation constructed
a real `ConsumerRecord` (adapted from a real record already present on
this host) carrying a real `market_opinion_id`, pointed a real
`Orchestrator` at it with the feature flag enabled, and ran a real
candle sequence through it. The evaluation ran successfully inside the
live `_enter()` path and produced `ABSTAINED` /
`no_production_direction` — **not** `AGREED`, because BUJJI's actual
production strategy (a market-neutral ATM straddle) produces no
directional `intention.direction` on the observed decision, exactly the
structural limitation already documented in Addendum 7. This is
expected, correct behavior, not a defect: it confirms the wiring
correctly reaches `IntelligencePolicy.classify()`'s
`no_production_direction` branch precisely because there was nothing
directional to compare, not because the opinion resolution failed.

The dedicated unit tests (`test_real_wired_policy_produces_agreed`,
`test_real_wired_policy_produces_disagreed`) confirm the opinion
resolution and comparison logic itself, using a directly-supplied
`production_direction`, independent of whatever BUJJI's live strategy
happens to decide on any given replay.

## Isolation

- **Feature-flag gated, default off** — identical to every prior sprint.
- **No change to `_enter()`'s control flow** — the real opinion source
  is wired once, at startup; the per-decision call site is unchanged.
- **Exactly two `mic_v2.*` imports** now exist anywhere in
  `mic_adapter/` — `mic_v2.journal.consumer_journal` (Sprint 1) and
  `mic_v2.journal.opinion_journal` (this sprint) — both named
  explicitly in `test_exactly_two_permitted_mic_v2_imports_in_package`.
  This is a deliberate, disclosed relaxation of the prior "exactly one
  import" invariant, not an accidental loosening — the test fails on
  any third import appearing anywhere in the package.
- **No execution capability** anywhere in `opinion_reader.py` — no
  broker, no order, no network call beyond the local file read; never
  raises (any failure returns `None`).
- **Read-only, never caches** — `read_opinion_classification()` opens
  the Opinion Journal fresh on every call, matching
  `IntelligenceAdapter.load_latest_snapshot()`'s own discipline.
- **Backward compatible** — `IntelligenceSnapshot.market_opinion_id`
  defaults to `None`; the mapper uses `getattr()` (not a hard attribute
  access) so a `ConsumerRecord` predating Addendum 8 maps correctly
  with no error.

## A Note on the "Backward Compatible" Signature Extension

`opinion_source`'s contract extended from `(consumer_status,
snapshot_id) -> Optional[str]` to `(consumer_status, snapshot_id,
market_opinion_id) -> Optional[str]`. This is additive at the contract
level (the new parameter has meaning only the real reader needs), but
it is **not** silently compatible with a pre-existing two-parameter
callable — `IntelligencePolicy.classify()` now always calls
`opinion_source` with three positional arguments. The six pre-existing
two-argument test lambdas in `test_intelligence_evaluation.py` were
updated to accept (and ignore, via a default) the third parameter.
`default_opinion_source` itself was updated the same way. Any other
external caller supplying a custom two-argument `opinion_source` would
need the same one-line update. This is disclosed here rather than
overclaimed as fully backward compatible.
