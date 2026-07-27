# Historical MIC Replay Engine

**BUJJI Options OS v3 — Engineering Series 61**

## Status: **Partial success — Evidence-level replay proven real; classification-string mapping not yet complete**

`bujji/mic_replay/{observation_adapter.py, replay_driver.py, recorder.py}`
successfully replay real historical NIFTY market data (Data
Acquisition Sprint A's NSE Bhavcopy corpus) through the **frozen,
unmodified, real** MIC v2 engine, producing genuine `Evidence`
artifacts — not a reimplementation, not a mock, not synthetic
intelligence. This is proven, not assumed: see the verification
section below.

What this sprint does **not** yet deliver is the further mapping from
MIC v2's Evidence layer to the seven classification strings
(`market_context`/`market_opinion`/`context_stability`/`calibration`/
`governance`/`lifecycle`/`contract`) that Series 32's Evidence
Interpreter — and therefore the Trading Brain — actually consumes.
That gap is disclosed explicitly below, not concealed, per this
project's own discipline and per the specification's own explicit
prohibition on "claiming compatibility without demonstrating it."

## Why this could be done at all — the process boundary, respected

Series 54 disclosed that MIC v2 lives in a completely separate Python
environment (`/opt/bujji-mic-v2/`, its own venv, its own repo) and
cannot be imported into BUJJI's own process. That finding is still
true and is not reversed here. This sprint never imports `mic_v2.*`
into `bujji.*`'s process. Instead, `replay_driver.py` invokes MIC v2's
own interpreter (`/opt/bujji-mic-v2/.venv/bin/python`) as a
**subprocess**, passing a small bridge script (kept as a Python string
constant, `_BRIDGE_SCRIPT`, never written to disk inside MIC v2's own
repository) via `-c`. That bridge script imports exclusively from
`mic_v2.*`, calls MIC v2's own **already-existing, already-frozen**
`mic_v2.runner.replay_runner.run_replay()` (discovered during this
sprint's own reading of MIC v2's codebase — it already anticipates a
`source="replay"` mode), and returns Evidence as JSON over stdout. No
MIC v2 source file was read for modification or touched in any way.

## What was discovered inside MIC v2 (read, not modified)

- `mic_v2.observation_builder.build_observation()` and
  `mic_v2.engine.run_cycle(obs, source="replay")` — the core,
  already-existing per-cycle evidence engine, 8 independent analyzer
  modules, each isolated (`try/except`) so one module's failure never
  crashes another or fabricates a substitute.
- `mic_v2.runner.replay_runner.run_replay(candles)` — an
  already-existing, already-frozen replay driver that threads spot
  history across candles and returns every `Evidence` object produced.
  This sprint calls this function completely unmodified.
- `mic_v2.qualification.runner.run_replay_with_qualifications()` — a
  further, more complete existing replay chain (Evidence → Narrative →
  Hypothesis → DecisionCandidate → QualifiedDecision) that was found
  but **not** used this sprint; using it, and separately mapping the
  `context`/`opinion`/`context_stability`/`calibration`/`governance`/
  `lifecycle`/`contract` subpackages' own publication APIs to the
  exact seven strings Series 32 needs, is exactly the remaining gap
  described below.

## The observation adapter — data only, no MIC v2 import

`observation_adapter.py` converts a Series 59 `HistoricalSessionRecord`
into plain, JSON-serializable dicts shaped exactly like MIC v2's own
`Candle`/`OptionChainLevel` dataclasses — matched by field name, never
by importing the dataclasses themselves. Two disclosed, honest
limitations, not fabrications:

- `HistoricalSessionRecord.spot` is a single settlement value, not a
  full historical OHLC bar. The candle payload uses
  `open=high=low=close=spot` — a degenerate, single-point candle,
  explicitly documented as such, never presented as real intraday
  range data.
- `HistoricalSessionRecord.option_chain_entries` carries
  `(strike, option_type, expiry, contract_symbol)` only — Data
  Acquisition Sprint A's NSE Bhavcopy ingestion does not currently
  carry open interest or bid/ask through. Every `OptionChainLevel`
  this adapter builds carries `ce_oi=pe_oi=0`,
  `ce_bid=ce_ask=pe_bid=pe_ask=None` — genuinely absent, never guessed.
  MIC v2's own `open_interest`/`option_premium` analyzer modules will
  therefore correctly produce little or no evidence from these
  historical replays; that is honest behavior given the input, not a
  bug in this adapter or in MIC v2.

## Verification: real data, real subprocess, real output

Using the same two real trading days validated in Data Acquisition
Sprint A (2026-07-21, 2026-07-22, NSE official Bhavcopy, NIFTY index
options):

```
2026-07-21  matched_rows=1628  built=True
2026-07-22  matched_rows=1584  built=True
observations built: ['NIFTY-2026-07-21', 'NIFTY-2026-07-22']
option chain levels per session: [126, 126]

candle_count: 2   evidence total: 2
  {'timestamp': '2026-07-21T15:30:00', 'module': 'time_of_day', 'signal': 'AFTER_HOURS', 'confidence': 1.0, ...}
  {'timestamp': '2026-07-22T15:30:00', 'module': 'time_of_day', 'signal': 'AFTER_HOURS', 'confidence': 1.0, ...}
```

Only `time_of_day` fired (correctly and honestly — 15:30 is genuinely
after the regular session for that module's own rule, and the other
seven analyzer modules require deeper spot history, VIX, or real
OI/bid-ask data this two-session, OI-less sample does not provide).
This is real MIC v2 logic responding honestly to real, if incomplete,
data — not this sprint fabricating a result.

## Determinism, verified

The same observation, run through `run_mic_replay()` twice, produced
byte-identical `Evidence` output both times (`r1.evidence == r2.evidence`).
Recorded intelligence IDs (`observation_id`/`intelligence_id`) are
`hashlib.md5`-derived from `(session_id, timestamp[, evidence_count])`
— never `uuid4()`, matching this project's identifier discipline
throughout.

## Example historical intelligence record

```
HistoricalIntelligenceRecord(
  replay_identifier='NIFTY-2026-07-22',
  observation_identifier='OBS-c3cad37e8626b95d',
  intelligence_identifier='INTEL-000a9c518432f090',
  timestamp='2026-07-22T15:30:00',
  mic_outputs=(
    {'timestamp': '2026-07-22T15:30:00', 'module': 'time_of_day',
     'signal': 'AFTER_HOURS', 'confidence': 1.0,
     'supporting_evidence': {'minute_of_day': 930, 'local_time': '15:30'}},
  ),
  provenance='MIC v2 run_replay via subprocess bridge, source=NSE Bhavcopy',
  qualification_fingerprint='RFP-0000000000000000',
)
```

## What remains: the classification-string mapping gap

This is the honest, disclosed core finding of this sprint. Series 32's
Evidence Interpreter — and therefore every downstream Trading Brain
stage — consumes exactly seven classification strings
(`market_context`, `market_opinion`, `context_stability`,
`calibration`, `governance`, `lifecycle`, `contract`), each drawn from
a closed vocabulary (`bujji/trading_brain/evidence_interpreter/taxonomy.py`).
MIC v2's `Evidence` objects (this sprint's actual output) are a
different, upstream layer — one analyzer module's raw observation, not
a published classification. Reaching the seven strings requires
wiring through MIC v2's own further subpackages
(`context/`, `opinion/`, `context_stability/`, `calibration/`,
`governance/`, `lifecycle/`, `contract/`), each with its own
engine/taxonomy/publication API that this sprint read the directory
listing of but did not map field-by-field or verify by real
invocation. Claiming that mapping is complete without having
demonstrated it — exactly what the specification explicitly forbids —
would misrepresent this sprint's actual state.

**Consequence for Series 58/59/Trading Brain compatibility:** Series
58's `HistoricalQualificationRunner` and Series 59's replay corpus
pipeline remain fully unmodified and fully compatible with whatever
`PipelineInput` they're given — that compatibility was never in
question and required no change here. What is *not yet* true is that
this sprint's own MIC replay output can *populate* a `PipelineInput`'s
seven classification fields — it currently cannot, because that
mapping was not completed. Running a real corpus through Series 58
today, using only this sprint's replay output, would still produce
`NO_STRATEGY` outcomes (as Data Acquisition Sprint A already observed
directly) — not because Series 58/59 need any change, but because the
classification-string bridge described here has one more mapping layer
left to build.

## Explicit compliance with this sprint's own constraints

- No MIC v2 algorithm was modified — every analyzer, every replay
  driver call is MIC v2's own, unmodified, and only ever invoked via
  subprocess, never imported.
- No Trading Brain, Runtime, or Qualification Framework file was
  modified.
- No synthetic intelligence was generated — every `Evidence` object
  recorded came from a real subprocess call into real MIC v2 code
  responding to real market data; no result was ever fabricated on a
  subprocess failure (`MicReplayError` is raised instead, and no
  fallback/default `Evidence` is manufactured).
- No live broker call, no authentication, no dispatch — this sprint
  never imports or references `bujji.broker`, `bujji.production_runtime`,
  or any authentication module.

## Recommendation

Do not yet re-run Series 60 expecting `COMPLETED` outcomes — the
classification-string mapping described above must be built first (a
distinct, scoped follow-on: mapping MIC v2's `context`/`opinion`/
`context_stability`/`calibration`/`governance`/`lifecycle`/`contract`
publication APIs onto Series 32's seven-field taxonomy, verified by
real invocation exactly as this sprint verified the Evidence layer).
Once that mapping exists, this sprint's `observation_adapter.py`/
`replay_driver.py`/`recorder.py` require no changes to support it —
they already prove the cross-repo subprocess bridge, the
option-chain-driven observation construction, and the determinism
guarantee that the rest of the chain depends on.
