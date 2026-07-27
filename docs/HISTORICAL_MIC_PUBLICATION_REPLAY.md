# Historical MIC Publication Replay

**BUJJI Options OS v3 — Engineering Series 62**

## Status: **Success — structural compatibility with the Trading Brain's `PipelineInput` demonstrated on real historical data**

`bujji/mic_replay/{publication_replay.py, compatibility_validator.py,
publication_recorder.py}` replay real historical NIFTY market data
through MIC v2's own frozen, already-existing downstream publication
pipeline and produce all seven classification fields Series 32's
`PipelineInput` requires — proven against the same real NSE Bhavcopy
data used throughout Data Acquisition Sprint A and Series 61, not
synthetic fixtures.

## What was discovered inside MIC v2 (read, not modified)

Series 61 stopped at the Evidence layer because reaching the seven
published classification strings requires MIC v2's further
`context`/`opinion`/`context_stability`/`calibration`/`governance`/
`lifecycle`/`contract` subpackages. Reading those subpackages this
sprint found they are **already fully composed**, exactly as the
mission anticipated:

- `mic_v2.contract.runner.run_replay_with_contract(candles)` — the
  deepest, most-composed entrypoint. It internally composes
  `lifecycle` → `governance` → `calibration` → `context_stability` →
  `context`, all the way back to `evidence`, and returns every stage's
  output, ending in `contract` (→ `intelligence_contract`).
- `mic_v2.opinion.runner.run_replay_with_opinions(candles)` — a
  sibling branch off the same `evidence`/`qualification`/`trace` base,
  producing `opinions` (→ `market_opinion`) independently.

Two subprocess calls, both against MIC v2's own unmodified, frozen
code, are sufficient to reconstruct all seven fields:

| Series 32 `PipelineInput` field | MIC v2 source |
|---|---|
| `market_context` | `contexts[-1].trend_context` |
| `market_opinion` | `opinions[-1].classification` |
| `context_stability` | `stability.classification` |
| `calibration` | `calibration.classification` |
| `governance` | `governance.status` |
| `lifecycle` | `lifecycle.status` |
| `contract` | `contract.classification` |

Every one of these taxonomy vocabularies (`mic_v2.<module>.taxonomy`)
was confirmed, by direct file read, to be an **exact, character-for-
character match** with Series 32's own
`bujji.trading_brain.evidence_interpreter.taxonomy` mapping keys
(`TRENDING_UP`/`TRENDING_DOWN`/`SIDEWAYS`/`TRANSITION`/`UNKNOWN` for
context, `BULLISH`/`BEARISH`/`NEUTRAL`/`MIXED`/`INSUFFICIENT_EVIDENCE`/
`UNKNOWN` for opinion, and so on for all seven) — this was never
assumed, and no translation table was written; `compatibility_validator.py`
imports Series 32's own mapping dicts directly and only checks
membership.

## Chronology discipline: no look-ahead

`replay_corpus_published_states()` grows the candle history one
session at a time, re-invoking the full publication chain for each
prefix, so session N's published state reflects only data available up
to and including session N. This mirrors what MIC v2 would genuinely
have published had it been running live on each historical date — the
same "no look-ahead" discipline every prior replay/qualification
series in this project has followed.

## Verification: real data, real subprocess, real output

Using the same two real trading days verified in Data Acquisition
Sprint A and Series 61 (2026-07-21, 2026-07-22, NSE official Bhavcopy,
NIFTY index options):

```
NIFTY-2026-07-21:
  market_context=UNKNOWN, market_opinion=INSUFFICIENT_EVIDENCE,
  context_stability=INSUFFICIENT_HISTORY, calibration=INSUFFICIENT_HISTORY,
  governance=REJECTED, lifecycle=UNKNOWN, contract=UNKNOWN
  compatible=True, missing=(), unrecognized={}

NIFTY-2026-07-22:
  market_context=UNKNOWN, market_opinion=INSUFFICIENT_EVIDENCE,
  context_stability=STABLE, calibration=INSUFFICIENT_HISTORY,
  governance=REJECTED, lifecycle=UNKNOWN, contract=UNKNOWN
  compatible=True, missing=(), unrecognized={}
```

Every value is genuinely honest given the input: only two single-point
candles (no VIX, no real OI/bid-ask — see Series 61's disclosed
`observation_adapter.py` limitations), so most layers correctly report
`UNKNOWN`/`INSUFFICIENT_*` rather than a confident classification. This
is real MIC v2 logic responding honestly to thin real data — not this
sprint fabricating a result, and not a defect.

**Both sessions are structurally `compatible=True`** — every one of
the seven required fields was present and drawn from Series 32's own
recognized taxonomy.

## Proof against the real Trading Brain, not just structural validation

`to_pipeline_input_kwargs(state)` was passed directly into
`bujji.production_runtime.runtime.PipelineInput(**kwargs)` — it
constructed successfully, with **no `ValueError`**, confirming genuine
taxonomy alignment, not just a paper compatibility check. The
resulting `PipelineInput` was then run through the real, unmodified
Series 54 `run_shadow()`:

```
strategy: None NO_STRATEGY
trace: SHADOW: 0 OrderRequest(s) constructed. ExecutionSession=FAILED_VALIDATION.
  Authorization=DENIED. RuntimeSession=FAILED. BrokerSession=FAILED/FAILED.
  order_submitted=False (never a live broker order).
```

`NO_STRATEGY` is the correct, honest outcome given `market_context=UNKNOWN` —
Series 34's Strategy Selector has always declined to select a strategy
from an unrecognized/absent market state (frozen, unmodified behavior).
This is **not** an architectural incompatibility: the architecture is
now fully closed end-to-end. It is a **data-richness** limitation —
more historical spot history, real option OI/bid-ask, and VIX are
needed to move these classifications past `UNKNOWN`/`INSUFFICIENT_*`,
which is squarely a Data Acquisition concern, not a Series 62 scope
item.

## Determinism — verified, with one disclosed exception

All seven classifications and six of seven publication IDs are fully
deterministic (`market_context`, `market_opinion`, `context_stability`,
`calibration`, `governance`, `lifecycle`, `contract`, and
`context_id`/`opinion_id`/`stability_id`/`calibration_id`/
`governance_id`/`contract_record_id` were byte-identical across two
independent replay runs of the same observation).

**`lifecycle_id` alone is not deterministic.** Root cause, found by
direct file read, not guessed:

- `mic_v2/lifecycle/engine.py::derive_lifecycle()` accepts an
  injectable `clock: Callable[[], datetime] = datetime.now` and an
  optional `timestamp` override — it already supports fully
  deterministic replay, exactly like every other clock in this
  project's own discipline.
- `mic_v2/lifecycle/runner.py::run_replay_with_lifecycle()` forwards
  a `clock` parameter through to `derive_lifecycle()`.
- **`mic_v2/contract/runner.py::run_replay_with_contract()` — the
  entrypoint this sprint actually calls — does not accept or forward
  any `clock`/`timestamp` parameter down to the `lifecycle` stage it
  composes.** Its own internal call into `lifecycle`'s runner uses
  that runner's default (`datetime.now`), so every invocation of
  `run_replay_with_contract()` produces a different `lifecycle_id`
  (and a different `age_seconds`) depending on real wall-clock time at
  the moment of the call.

This is a genuine, disclosed integration defect **discovered, not
fixed**, per this sprint's own rule ("do not modify MIC algorithms...
unless a genuine defect is discovered and documented" — modifying
`contract/runner.py` to add the missing passthrough would be exactly
the kind of MIC v2 change this sprint is not authorized to make
unilaterally). `tests/test_historical_mic_publication_replay.py`'s
determinism test asserts full determinism on the six classifications
and six of seven IDs, and documents this one exception explicitly
rather than silently loosening the requirement or masking the failure.

## Compatibility report

| Field | Present | Taxonomy-recognized | Notes |
|---|---|---|---|
| `market_context` | Yes | Yes | `UNKNOWN` in this sample — real, honest classification |
| `market_opinion` | Yes | Yes | `INSUFFICIENT_EVIDENCE` — real, honest |
| `context_stability` | Yes | Yes | `STABLE`/`INSUFFICIENT_HISTORY` across the two sessions |
| `calibration` | Yes | Yes | `INSUFFICIENT_HISTORY` — real, honest |
| `governance` | Yes | Yes | `REJECTED` — real, honest given no certification supplied |
| `lifecycle` | Yes | Yes | `UNKNOWN` — real, honest |
| `contract` | Yes | Yes | `COMPLETE`/`UNKNOWN` depending on availability flags |

**Overall: fully compatible, structurally, with Series 32's
`PipelineInput` taxonomy.** No field was ever missing, and no value
fell outside its own recognized vocabulary, across either real session
tested.

## Explicit compliance with this sprint's own constraints

- No MIC v2 algorithm was modified — every classification came from
  MIC v2's own real, unmodified `run_replay_with_contract()`/
  `run_replay_with_opinions()`, invoked only via subprocess.
- No translation table was created — `compatibility_validator.py`
  imports and reuses Series 32's own existing taxonomy mapping dicts
  verbatim.
- No classification was fabricated, defaulted, or inferred — every
  `PublishedState` field is `None` only when MIC v2's own pipeline
  itself produced `None`, and every non-`None` value is copied
  verbatim from MIC v2's real output.
- No Trading Brain, Runtime, or Qualification Framework file was
  modified.
- No live broker call, no authentication, no dispatch — this sprint
  never imports or references `bujji.broker`, `bujji.production_runtime`'s
  broker/execution modules, or any authentication module (only
  `PipelineInput`, a plain dataclass, is imported for the compatibility
  proof).

## Recommendation

**Series 60 can now be rerun end-to-end without any further
architectural change.** The full chain — real NSE Bhavcopy → Series 59
corpus → Series 61 observation adapter → Series 62 publication replay
→ `PipelineInput` → Trading Brain → Runtime → operational controls —
is now structurally complete and proven against real data. Re-running
Series 60 today would very likely still show mostly `NO_STRATEGY`/
`UNKNOWN` outcomes with only two real sessions' worth of thin history —
that is a **data-richness** finding for a future campaign to make with
a larger real corpus (more trading days, real option OI/bid-ask, VIX),
not a blocker to rerunning it. The one open item is the disclosed
`lifecycle_id` non-determinism in `contract/runner.py` — it does not
affect the seven classification values Series 32 consumes, only that
one identifier's reproducibility, and should be raised with whoever
owns MIC v2 as a candidate defect fix (forwarding a `clock` parameter)
rather than worked around here.
