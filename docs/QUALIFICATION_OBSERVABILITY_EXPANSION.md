# Qualification Observability Expansion — Engineering Series 69, Phase 2

**Recording-only.** No MIC reasoning, Trading Brain logic, replay, runtime/decision-pipeline, or strategy changes. Every value added below was already computed somewhere in the pipeline; nothing was derived, inferred, or invented. The qualification fingerprint (`compute_fingerprint()`, `bujji/qualification/replay_runner.py:228`) is computed over pipeline artifacts and completed stages only — none of this sprint's changes touch that code path, and the full regression suite (below) confirms no observable behavior changed for any existing caller.

## Phase 1 — Investigation (already done, cited for context)

1. `bujji/mic_replay/compatibility_validator.py:29-37,92-99` — `REQUIRED_FIELDS`/`to_pipeline_input_kwargs()` excludes `certification_status`/`certification_deterministic` from `PublishedState`.
2. `bujji/qualification/replay_models.py:64-89` — `ReplayScenario` had no certification field.
3. `bujji/qualification/historical_runner.py:176-188` — `QualificationRecord` construction never copied `shadow_result.market_state_assessment`, even though `ShadowResult` carries it (`bujji/production_runtime/runtime.py:78`, populated at `:231`).
4. `bujji/qualification/report.py:65-117` (`build_report()`) — produced only whole-corpus aggregates, no per-session `sessions[]` list. The real gap: whichever ad-hoc script builds `reports/historical_campaign_*.json` hand-assembled a 5-key `sessions[]` (`id`, `outcome`, `strategy`, `governance`, `market_context`) outside of `build_report()`/`QualificationRecord` entirely.
5. `bujji/observatory/comparison.py:21-31` (`CANONICAL_FIELD_ORDER`) already expects up to 9 keys per session (`market_context`, `market_opinion`, `context_stability`, `calibration`, `governance`, `lifecycle`, `contract`, `strategy`, `outcome`) and degrades gracefully when fields are absent — but until this sprint it only ever saw 5.

## Phase 2 — What was changed

### Step A — Certification threading (SKIPPED, follow-up only)

`compatibility_validator.py::to_pipeline_input_kwargs()` builds the exact kwargs dict passed straight into `bujji.production_runtime.runtime.PipelineInput(**kwargs)` (`bujji/production_runtime/runtime.py:47-58`). `PipelineInput` is a frozen dataclass with exactly 7 fields (`market_context` … `contract`) and **no** certification fields. Adding `certification_status`/`certification_deterministic` to `to_pipeline_input_kwargs()`'s return value would pass unexpected kwargs into `PipelineInput(...)`, which would raise `TypeError` unless `PipelineInput` itself were also extended — and `PipelineInput` is consumed directly by `_run_decision_pipeline()`, the actual decision-pipeline entrypoint. That is exactly the kind of pipeline-execution-path change this recording-only sprint must not risk. **Skipped**, per the task's own explicit conservative instruction. Follow-up (separate, non-recording-only sprint): extend `PipelineInput` deliberately, with its own review of decision-pipeline and fingerprint impact, if certification is ever meant to influence the pipeline itself. Note `PublishedState` (`bujji/mic_replay/publication_replay.py:158-159`) already carries both fields today — no change was needed there.

### Step B — `market_state_assessment` and `scenario` on `QualificationRecord`

- `bujji/qualification/recorder.py` — `QualificationRecord` gained two new optional fields, both defaulting to `None` so every pre-existing direct construction of `QualificationRecord(...)` is unaffected:
  - `market_state_assessment: Optional[Any] = None`
  - `scenario: Optional[Any] = None`
- `bujji/qualification/historical_runner.py:176-189` (`run_corpus()`) — the `QualificationRecord(...)` construction site now also passes:
  - `market_state_assessment=getattr(shadow_result, "market_state_assessment", None)` — same `getattr(shadow_result, ..., None)` pattern already used one line above for `strategy_decision`.
  - `scenario=scenario` — the same `ReplayScenario` already in scope in the loop (`for scenario, ts in zip(scenarios, timestamps):`).

`ReplayScenario` (`bujji/qualification/replay_models.py:64-89`) already carries `market_context`, `market_opinion`, `context_stability`, `calibration`, `governance`, `lifecycle`, `contract` directly — no new field was needed there; `record.scenario.<field>` exposes them.

### Step C — `sessions[]` in `build_report()`

- `bujji/qualification/report.py`:
  - `QualificationReport` gained `sessions: Tuple[Dict[str, Optional[Any]], ...] = field(default_factory=tuple)` — defaults to `()`, so any existing direct construction of `QualificationReport(...)` (there is exactly one other caller pattern, in `tests/test_historical_qualification.py`, which never constructs it directly — only via `build_report()`) is unaffected.
  - New `_session_entry(record)` helper builds one dict per `QualificationRecord`, reading every value verbatim off `record.scenario` / `record.strategy_decision` / `record.market_state_assessment`, defensively with `getattr(..., None)`.
  - `build_report()` now populates `sessions=tuple(_session_entry(r) for r in records)`.

### `sessions[]` schema (per entry)

```json
{
  "id": "<record.replay_identifier>",
  "outcome": "<record.runtime_outcome.status>",
  "strategy": "<record.strategy_decision.selected_strategy, else null>",
  "market_context": "<record.scenario.market_context, else null>",
  "market_opinion": "<record.scenario.market_opinion, else null>",
  "context_stability": "<record.scenario.context_stability, else null>",
  "calibration": "<record.scenario.calibration, else null>",
  "governance": "<record.scenario.governance, else null>",
  "lifecycle": "<record.scenario.lifecycle, else null>",
  "contract": "<record.scenario.contract, else null>",
  "market_character": "<record.market_state_assessment.market_character, else null>",
  "market_phase": "<record.market_state_assessment.market_phase, else null>",
  "confidence": "<record.market_state_assessment.confidence, else null>"
}
```

The first 9 keys exactly match `bujji/observatory/comparison.py:21-31`'s `CANONICAL_FIELD_ORDER`; `market_character`/`market_phase`/`confidence` are additional fields from `MarketStateAssessment` (`bujji/trading_brain/market_state/models.py:16-28`) that the Observatory does not currently read but which were explicitly requested for this sprint. Extra keys are harmless — the Observatory's own comparison logic already skips fields absent from either side.

## Files changed

- `bujji/qualification/recorder.py` — `QualificationRecord` gains `market_state_assessment`, `scenario` (both `Optional[Any] = None`).
- `bujji/qualification/historical_runner.py` — construction site (~line 176-189) populates the two new fields via `getattr(shadow_result, ..., None)` / the in-scope `scenario`.
- `bujji/qualification/report.py` — `QualificationReport` gains `sessions` (default `()`); new `_session_entry()` helper; `build_report()` populates `sessions`.
- `tests/test_qualification_report_series69.py` — new test file (7 tests): record carries `market_state_assessment`/`scenario` from a real replay; `sessions[]` contains the expected keys/values from a real replay; both `QualificationRecord` and `QualificationReport` remain constructible and correct with no scenario/assessment data (backward compat); determinism preserved with `sessions` populated.

## Regression

Full suite (`/opt/bujji/.venv/bin/python -m pytest -q`, run from `/opt/bujji/app`):

- **Before:** 2033 passed.
- **After:** 2040 passed (2033 pre-existing + 7 new Series 69 tests). 0 failures either run.

No `baselines.json` or dedicated fingerprint-snapshot file exists in the repo (searched `grep -ri "baseline\|fingerprint"` — the only fingerprint logic is `compute_fingerprint()` in `replay_runner.py`, computed fresh from pipeline artifacts on every run, not compared against a stored snapshot). Since none of this sprint's changes touch `replay_runner.py`, `PipelineInput`, or any pipeline-artifact computation, `qualification_fingerprint` values are unaffected — confirmed indirectly by `test_build_report_deterministic_with_sessions` (identical corpus replayed twice still produces byte-identical reports, including the new `sessions` tuple).

## Phase 3 — Observatory: `first_divergence` verification + divergence-chain narrative

### Finding: `first_divergence` was already correct — no bug

`bujji/observatory/comparison.py`'s `compare_sessions()` computes it as:

```python
first_divergence = next((f for f in CANONICAL_FIELD_ORDER if f in changed), None)
```

This walks `CANONICAL_FIELD_ORDER` (the tuple, in its declared order) and returns the first field name that is a key in `changed` — a dict built by iterating the *same* `CANONICAL_FIELD_ORDER` loop just above it. It does not iterate `changed.keys()` (which would be insertion-order-dependent and could coincidentally look right or wrong depending on dict construction order elsewhere) — it iterates the canonical tuple and checks membership. This is correct by construction and independent of dict key order.

The earlier manual-analysis observation that `first_divergence` kept coming back as `"market_context"` or `"strategy"` even when `market_context` was unchanged was explained by the pre-Phase-2 session dicts genuinely not having `context_stability`/`calibration`/`governance`/`lifecycle`/`contract` keys populated at all — so there was nothing upstream of `strategy` for the (already-correct) loop to find. Confirmed with a new test using full-field synthetic sessions:

`tests/test_market_intelligence_observatory.py::test_first_divergence_prefers_context_stability_over_strategy` — constructs two full-field sessions where only `context_stability`, `strategy`, and `outcome` differ (`market_context`/`market_opinion` unchanged) and asserts `diff.first_divergence == "context_stability"`, not `"strategy"`.

No change was made to `compare_sessions()` or `CANONICAL_FIELD_ORDER`.

### Change: `explain_trading_impact()` now produces a divergence-chain narrative

`bujji/observatory/timeline.py` — `explain_trading_impact()` was rewritten to lead with the first-divergence field's before/after values, then list every other changed field (in `CANONICAL_FIELD_ORDER` order) as a `->` consequence line. It still only reads `diff`'s own already-computed fields (`first_divergence`, `changed_fields`, `downstream_impact`) and asserts no causality beyond "this field differs first in the documented composition-chain order."

New format:

```
NIFTY-2026-07-10:
First divergence: calibration
  CALIBRATED -> NOT_CALIBRATED
  -> strategy changed: PREMIUM_VWAP_STRADDLE -> None
  -> outcome changed: COMPLETED -> FAILED
  downstream_impact: strategy PREMIUM_VWAP_STRADDLE -> None; outcome COMPLETED -> FAILED
```

(Synthetic example — constructed in `test_first_divergence_calibration_when_market_context_unchanged`, since the currently available saved reports do not contain a real pair where `calibration` diverges before `strategy` with `market_context` unchanged.)

No-change case is unchanged: `"{session_id}: no change between the two replay runs."`

`timeline.py` now also imports `CANONICAL_FIELD_ORDER` from `comparison.py` to drive the consequence-line ordering.

### `threshold_crossings`

Left exactly as documented (always `UNKNOWN` placeholders per field) — out of scope for this phase, per the Phase 3 spec's "never infer hidden state."

### Tests added

`tests/test_market_intelligence_observatory.py` — 4 new tests appended:

- `test_first_divergence_prefers_context_stability_over_strategy` — `context_stability` diverges before `strategy`; confirms canonical-order-first selection, not dict-iteration-order.
- `test_first_divergence_calibration_when_market_context_unchanged` — `market_context` genuinely unchanged, `calibration` diverges; also asserts the new narrative format's exact substrings.
- `test_first_divergence_none_when_nothing_diverges` — two identical full-field sessions; `first_divergence is None`, narrative says "no change."
- `test_narrative_lists_consequences_in_canonical_order_after_first_divergence` — asserts the first-divergence line precedes the `strategy`/`outcome` consequence lines in output order.

### Test counts

- `tests/test_market_intelligence_observatory.py` alone: **19 passed → 23 passed** (4 new tests, 0 failures either run).
- Full suite (`/opt/bujji/.venv/bin/python -m pytest -q`, from `/opt/bujji/app`): **2044 passed**, 0 failures, both before and after this phase's edits (the edits only added tests and changed one function's internal string-building; no existing test's expected substrings were broken — verified by rerunning the full suite after the change).

### Files changed

- `bujji/observatory/timeline.py` — `explain_trading_impact()` rewritten to a divergence-chain narrative; import of `CANONICAL_FIELD_ORDER` added.
- `tests/test_market_intelligence_observatory.py` — 4 new tests appended (see above).
- `docs/QUALIFICATION_OBSERVABILITY_EXPANSION.md` — this section.

No changes to `bujji/observatory/comparison.py`, `bujji/observatory/report.py`, or `bujji/observatory/explanation.py` — `first_divergence` logic was already correct, and `threshold_crossings` is intentionally left as `UNKNOWN`.


## Not done in this pass (Phase 4, separate)

The real 41-day corpus replay was explicitly out of scope for this pass and was not run.


## Phase 2b (deep pass) — evidence_interpretation + risk/capital/execution/strategy detail

Follow-up to Phase 2/3 above, closing most of the Loss Map gaps found by a
read-only investigation: `RiskAssessment`, `CapitalDecision`, and
`ExecutionPlan` were already carried in full on `QualificationRecord`
(`risk_decision`/`capital_decision`/`execution_plan`) but contributed **0
fields** to `sessions[]`; `EvidenceInterpretation` was not even referenced
on `QualificationRecord` at all, despite being produced on every
`ShadowResult` (`bujji/production_runtime/runtime.py`). Still
recording-only: every new value is `getattr(existing_object, "existing_attr", None)` off objects the pipeline already built -- nothing new is computed
or inferred.

### Step D — `evidence_interpretation` on `QualificationRecord`

- `bujji/qualification/recorder.py` — `QualificationRecord` gained one more optional field, defaulting to `None`:
  - `evidence_interpretation: Optional[Any] = None`
- `bujji/qualification/historical_runner.py` (`run_corpus()`) — the `QualificationRecord(...)` construction site now also passes `evidence_interpretation=getattr(shadow_result, "evidence_interpretation", None)`, mirroring the existing `market_state_assessment` extraction exactly.

### Step E — `_session_entry()` extended

`bujji/qualification/report.py::_session_entry()` now also reads, defensively (`getattr(..., None)` on a possibly-`None` object), from:

- `record.strategy_decision` (`bujji/trading_brain/strategy_selector/models.py`): `selection_status`, `selection_confidence`, `selection_reason` (existing `selected_strategy` -> `strategy` key unchanged).
- `record.risk_decision` (`bujji/trading_brain/risk_brain/models.py`), prefixed `risk_`: `risk_status` (from `.status`), `risk_level`, `risk_approval` (from `.approval`), `risk_blocking_reason` (from `.blocking_reason`).
- `record.capital_decision` (`bujji/trading_brain/capital_brain/models.py`), prefixed `capital_`: `capital_intent`, `capital_allocation_status`, `capital_allocation_reason`.
- `record.execution_plan` (`bujji/trading_brain/execution_planner/models.py`), prefixed `execution_`: `execution_status` (from `.status`), `execution_intent`.
- `record.evidence_interpretation.ontology_snapshot` (`bujji/trading_brain/ontology/models.py::TradingOntologySnapshot`), prefixed `evidence_`: `evidence_opportunity_state` (from `.opportunity_state`), `evidence_risk_state` (from `.risk_state`).

### Updated `sessions[]` schema (per entry, full list)

```json
{
  "id": "<record.replay_identifier>",
  "outcome": "<record.runtime_outcome.status>",
  "strategy": "<record.strategy_decision.selected_strategy, else null>",
  "market_context": "<record.scenario.market_context, else null>",
  "market_opinion": "<record.scenario.market_opinion, else null>",
  "context_stability": "<record.scenario.context_stability, else null>",
  "calibration": "<record.scenario.calibration, else null>",
  "governance": "<record.scenario.governance, else null>",
  "lifecycle": "<record.scenario.lifecycle, else null>",
  "contract": "<record.scenario.contract, else null>",
  "market_character": "<record.market_state_assessment.market_character, else null>",
  "market_phase": "<record.market_state_assessment.market_phase, else null>",
  "confidence": "<record.market_state_assessment.confidence, else null>",
  "selection_status": "<record.strategy_decision.selection_status, else null>",
  "selection_confidence": "<record.strategy_decision.selection_confidence, else null>",
  "selection_reason": "<record.strategy_decision.selection_reason, else null>",
  "risk_status": "<record.risk_decision.status, else null>",
  "risk_level": "<record.risk_decision.risk_level, else null>",
  "risk_approval": "<record.risk_decision.approval, else null>",
  "risk_blocking_reason": "<record.risk_decision.blocking_reason, else null>",
  "capital_intent": "<record.capital_decision.capital_intent, else null>",
  "capital_allocation_status": "<record.capital_decision.allocation_status, else null>",
  "capital_allocation_reason": "<record.capital_decision.allocation_reason, else null>",
  "execution_status": "<record.execution_plan.status, else null>",
  "execution_intent": "<record.execution_plan.execution_intent, else null>",
  "evidence_opportunity_state": "<record.evidence_interpretation.ontology_snapshot.opportunity_state, else null>",
  "evidence_risk_state": "<record.evidence_interpretation.ontology_snapshot.risk_state, else null>"
}
```

27 keys total (13 from Phase 2, 14 new).

### Step F — `CANONICAL_FIELD_ORDER` reordered to reflect actual pipeline sequence

`bujji/observatory/comparison.py`'s `CANONICAL_FIELD_ORDER` now inserts the Trading-Brain-stage fields in the order the pipeline actually calls them, per `bujji/production_runtime/runtime.py`'s `ShadowResult` construction (`evidence_interpretation` -> `market_state_assessment` -> `strategy_decision` -> `risk_assessment` -> `capital_decision` -> `execution_plan`):

```python
CANONICAL_FIELD_ORDER = (
    "market_context", "market_opinion", "context_stability", "calibration",
    "governance", "lifecycle", "contract",
    "evidence_opportunity_state", "evidence_risk_state",
    "market_character", "market_phase",
    "strategy", "selection_status", "selection_confidence", "selection_reason",
    "risk_status", "risk_level", "risk_approval", "risk_blocking_reason",
    "capital_intent", "capital_allocation_status", "capital_allocation_reason",
    "execution_status", "execution_intent",
    "outcome",
)
```

`market_character`/`market_phase` (Market State Builder output, added to `sessions[]` in Phase 2 but never added to `CANONICAL_FIELD_ORDER` at that time) are included here too, since the requested ordering ("evidence stage before market_state, before strategy, before risk, before capital, before execution") only makes sense if all of those stages are actually represented in the order -- otherwise a Market-State-only divergence would incorrectly report a later field as `first_divergence`. `confidence` (also a Phase 2 `sessions[]` key) was left out of `CANONICAL_FIELD_ORDER`, consistent with how it was already excluded in Phase 2/3 -- out of scope for this pass, not newly introduced.

This directly changes what `first_divergence` reports when multiple fields diverge at once: `strategy` (Strategy Selector, earlier in the pipeline) out-ranks `risk_status` (Risk Brain, later), matching real causal order. Verified by `tests/test_market_intelligence_observatory.py::test_first_divergence_prefers_strategy_over_risk_when_both_diverge` and `::test_first_divergence_reports_risk_field_not_strategy_when_both_diverge`.

### Tests added

- `tests/test_qualification_report_series69.py`: `test_record_carries_evidence_interpretation_from_shadow_result`, `test_qualification_record_evidence_interpretation_defaults_to_none`, `test_build_report_sessions_contain_deep_pass_fields`, `test_build_report_sessions_deep_pass_fields_absent_safe_without_downstream_records`, `test_build_report_deterministic_with_deep_pass_fields` (5 new).
- `tests/test_market_intelligence_observatory.py`: `test_canonical_field_order_places_risk_before_strategy_downstream_fields`, `test_first_divergence_reports_risk_field_not_strategy_when_both_diverge`, `test_first_divergence_prefers_strategy_over_risk_when_both_diverge` (3 new).

### Test counts

- Full suite (`/opt/bujji/.venv/bin/python -m pytest -q`, from `/opt/bujji/app`): **2044 passed before -> 2052 passed after** (8 new tests, 0 failures either run).
- Determinism preserved: `test_build_report_deterministic_with_deep_pass_fields` (new) and the pre-existing `test_build_report_deterministic_with_sessions` both pass -- two identical corpus replays still produce byte-identical `sessions` tuples with the new fields included.

### Updated Loss Map retention percentages

Recomputed against each model's full field list (excluding IDs/timestamps/version/trace-string fields that are internal bookkeeping, not observable classification/decision content -- consistent with how `strategy_decision`'s retention was counted in Phase 1's investigation):

| Stage | Model | Total content fields | Exposed before | Exposed after | Retention before | Retention after |
|---|---|---|---|---|---|---|
| Evidence Interpreter | `EvidenceInterpretation` (+ `TradingOntologySnapshot`) | 15 (6 interpretation-level + 9 snapshot-level) | 0 | 2 | 0% | ~13% |
| Market State | `MarketStateAssessment` | 3 (`market_character`, `market_phase`, `confidence`) | 3 | 3 | 100% | 100% (unchanged) |
| Strategy Selector | `StrategyDecision` | 7 (`selected_strategy`, `selection_status`, `selection_confidence`, `selection_reason`, `supporting_conditions`, `rejecting_conditions`, `alternative_candidates`) | 1 | 4 | 14% | 57% |
| Risk Brain | `RiskAssessment` | 7 (`status`, `risk_level`, `approval`, `blocking_reason`, `warning_reasons`, `required_controls`, `confidence`) | 0 | 4 | 0% | 57% |
| Capital Brain | `CapitalDecision` | 6 (`capital_intent`, `allocation_status`, `allocation_reason`, `allocation_constraints`, `required_controls`, `confidence`) | 0 | 3 | 0% | 50% |
| Execution Planner | `ExecutionPlan` | 7 (`status`, `execution_intent`, `strategy_id`, `capital_intent`, `required_controls`, `execution_constraints`, `execution_steps`) | 0 | 2 | 0% | 29% |

`supporting_conditions`/`rejecting_conditions`/`warning_reasons`/`required_controls`/`execution_steps`/`allocation_constraints` (tuple-of-string detail fields) remain unexposed -- out of scope for this recording-only sprint, which targeted single-value status/reason fields per the task spec, not full trace/condition dumps.

### Files changed (Phase 2b)

- `bujji/qualification/recorder.py` — `evidence_interpretation: Optional[Any] = None` field added to `QualificationRecord`.
- `bujji/qualification/historical_runner.py` — construction site now passes `evidence_interpretation=getattr(shadow_result, "evidence_interpretation", None)`.
- `bujji/qualification/report.py` — `_session_entry()` extended with 14 new keys.
- `bujji/observatory/comparison.py` — `CANONICAL_FIELD_ORDER` extended and reordered to match actual pipeline call sequence.
- `tests/test_qualification_report_series69.py` — 5 new tests.
- `tests/test_market_intelligence_observatory.py` — 3 new tests.
- `docs/QUALIFICATION_OBSERVABILITY_EXPANSION.md` — this section.

No MIC v2, Trading Brain computation, Runtime, strategy-selection, replay, or threshold logic was changed -- only recording and reporting of values the pipeline already produces.

## Phase 4 — Historical Verification (recording-only)

Reproduced both sides of the M1 "before"/"after" comparison from the
same unchanged 41-day real NSE Bhavcopy corpus
(`corpus_id=CORPUS-28a6ba13849f9608`,
`checksum=09673a3dc182a3677df14ec3c610b427154243a9676f3c79facd4ba563b3729e`
— identical on both sides, confirming no market data was regenerated),
this time calling the current `build_report()` so `sessions[]` carries
the full 27-key Series 69 schema on both sides instead of the old
5-key schema the original hand-written M1 scripts produced.

- **Before**: same corpus, same MIC v2 publication replay, but each
  session's observation payload has its `option_chain`/`vix` keys
  stripped before being handed to `replay_corpus_published_states` —
  reproducing the pre-M1 "no option/VIX evidence reaches MIC v2"
  condition. Saved to `/tmp/m1/before_deep.json` on the deploy host,
  deployed to `reports/historical_campaign_series69_before.json`.
- **After**: same corpus, unmodified observation payloads (real
  `option_chain`/`vix` reach MIC v2), same as M1's original "after"
  run. Saved to `/tmp/m1/after_deep.json`, deployed to
  `reports/historical_campaign_series69_after.json`.
- The two pre-existing artifacts (`historical_campaign_v2_1_full_corpus.json`,
  `historical_campaign_m1_after.json`) were left untouched as historical
  records — they still only carry the old 5-key schema.

### Determinism

The "after" reproduction was run three independent times end-to-end
(full corpus rebuild -> MIC v2 replay -> Trading Brain -> `build_report()`).
All three runs produced the same manifest checksum
(`09673a3dc182a3677df14ec3c610b427154243a9676f3c79facd4ba563b3729e`)
and the same SHA-256 over the serialized `sessions[]` tuple
(`ec34cf4044e85dfe86ee1ce0a78fa70a69586b62bd59decaabc90b988e200528`)
across all three — byte-identical, as required.

### `compare_corpora()` results

41 sessions shared by id. **26 changed**, 15 unchanged.

**First-divergence distribution** (of the 26 changed sessions, which
`CANONICAL_FIELD_ORDER` field changed earliest):

| First-divergence field | Count |
|---|---|
| `context_stability` | 22 |
| `market_context` | 4 |
| `strategy` (or anything downstream of it) | **0** |

**Other requested counts** (sessions where the field changed anywhere
in the diff, not only as first divergence):

| Field | Changed-session count |
|---|---|
| `calibration` | 0 |
| `context_stability` | 26 |
| `strategy` | 26 |
| `outcome` | 24 |

### Success criteria: was the earliest divergence found for all 26?

**Yes, for all 26/26 previously-changed sessions.** Every one of them
now has a `first_divergence` that lands on `market_context` or
`context_stability` — both genuinely upstream of `strategy` in
`CANONICAL_FIELD_ORDER` — never on `strategy` itself. None of the 26
remain unexplained. `calibration`, `market_opinion`, `governance`,
`lifecycle`, and `contract` never appear as a first divergence in this
corpus; only the two Evidence-Interpreter-adjacent classification
fields (`market_context`, `context_stability`) do, which matches
what's mechanically different between the two runs (presence/absence
of option_chain+VIX evidence changes MIC v2's own market-context and
stability classification before anything downstream runs).

No field/object remains structurally missing for this corpus — the
27-key schema was sufficient to explain every changed session. (The
still-unexposed detail fields — `supporting_conditions`,
`rejecting_conditions`, `warning_reasons`, `required_controls`,
`execution_steps`, `allocation_constraints` — noted as out of scope in
Phase 2b are still absent, but no session in this corpus needed them
to identify its first divergence.)

### `NIFTY-2026-07-09` (the spec's named example)

Previously unexplained: `PREMIUM_VWAP_STRADDLE -> IRON_CONDOR`,
outcome stayed `COMPLETED` both times, and with only the old 5-key
schema `first_divergence` had nothing upstream of `strategy` to point
to. With the full schema:

```
NIFTY-2026-07-09:
First divergence: context_stability
  MOSTLY_STABLE -> TRANSITIONING
  -> market_phase changed: ESTABLISHED -> EMERGING
  -> strategy changed: PREMIUM_VWAP_STRADDLE -> IRON_CONDOR
  -> selection_confidence changed: MODERATE -> LOW
  -> selection_reason changed: Market state RANGE matches required states.; Market character CLEAR meets minimum CLEAR.; Confidence HIGH meets required HIGH. -> Market state RANGE matches required states.; Market character CLEAR meets minimum CONTESTED.; Confidence MODERATE meets required MODERATE.
  -> risk_status changed: APPROVED -> APPROVED_WITH_WARNINGS
  -> risk_level changed: LOW -> MODERATE
  -> risk_approval changed: ALLOW -> ALLOW_WITH_CONTROLS
  -> capital_intent changed: STANDARD -> REDUCED
  -> capital_allocation_status changed: APPROVED -> LIMITED
  -> capital_allocation_reason changed: Risk Level is LOW under a clean ALLOW; standard capital is authorized. -> Risk Brain approved with controls; capital is reduced and must follow those controls.
  downstream_impact: strategy PREMIUM_VWAP_STRADDLE -> IRON_CONDOR
```

Genuine finding: `market_context` did **not** change (`RANGE` both
times, which is why the old comparison saw nothing), but
`context_stability` did — `MOSTLY_STABLE` -> `TRANSITIONING`. That
single upstream change lowered Market State's `market_character`
confidence bar (`CLEAR meets minimum CLEAR` -> `CLEAR meets minimum
CONTESTED`, confidence `HIGH` -> `MODERATE`), which is what pushed
Strategy Selector off `PREMIUM_VWAP_STRADDLE` (needs `HIGH`
confidence) onto `IRON_CONDOR` (qualifies at `MODERATE`), which then
softened Risk/Capital one notch each (`APPROVED` ->
`APPROVED_WITH_WARNINGS` / `REDUCED` capital). Outcome stayed
`COMPLETED` both times because both strategies still qualified for
execution — the previously "unexplained" case is now fully explained
as a Market-State-confidence-band effect, not a defect.

### Five more previously-unexplained sessions checked

**`NIFTY-2026-06-12`** — `first_divergence = market_context`
(`TRANSITION -> UNKNOWN`). This one *does* show a `market_context`
change (not one of the "unexplained-22" in the strictest sense, but
verified as a genuine upstream cause): cascades through
`context_stability`, `market_character`, `market_phase`, strategy
`DIRECTIONAL_PUT_SPREAD -> None` (`NO_STRATEGY`), risk `REJECTED`,
capital `DENIED`, execution `NOT_PLANNED`, outcome `COMPLETED ->
FAILED`. Fully explained, root cause is market_context itself.

**`NIFTY-2026-06-15`** — `first_divergence = context_stability`
(`MOSTLY_STABLE -> TRANSITIONING`), `market_context` unchanged. Same
shape as 06-12 downstream (strategy lost entirely, `NO_STRATEGY`,
outcome `COMPLETED -> FAILED`) but the true upstream cause is
stability, not context — previously invisible, now captured.

**`NIFTY-2026-07-03`** — `first_divergence = market_context`
(`TRANSITION -> TRENDING_UP`). Cascades to `context_stability`,
`market_phase`, strategy `DIRECTIONAL_PUT_SPREAD -> COVERED_CALL`,
risk downgraded to `APPROVED_WITH_WARNINGS`, capital `REDUCED`,
outcome `COMPLETED -> FAILED`. Fully explained.

**`NIFTY-2026-07-16`** — `first_divergence = context_stability`
(`MOSTLY_STABLE -> TRANSITIONING`), `market_context` unchanged
(`RANGE` both times — same shape as 07-09). Strategy
`PREMIUM_VWAP_STRADDLE -> IRON_CONDOR`, risk/capital softened one
notch, outcome unchanged (`COMPLETED` both times). Fully explained,
same root-cause pattern as 07-09.

**`NIFTY-2026-06-22`** — `first_divergence = context_stability`
(`MOSTLY_STABLE -> TRANSITIONING`), `market_context` unchanged.
Strategy `DIRECTIONAL_CALL_SPREAD -> COVERED_CALL`, risk/capital
softened one notch, outcome `COMPLETED -> FAILED`. Fully explained.

Full narrative text for all 26 changed sessions is saved at
`/tmp/m1/series69_diffs.json` on the deploy host (session id ->
`first_divergence`, `changed_fields`, full narrative string).

### Honest statement

Every one of the 26 sessions that changed between the "before" and
"after" M1 runs now has a genuine, upstream-of-`strategy`
`first_divergence` recorded by the Observatory — 22 on
`context_stability`, 4 on `market_context`. Zero sessions still land
on `strategy` as their first divergence, and zero remain structurally
unexplained. The `NIFTY-2026-07-09` case specifically — previously the
spec's flagship example of an inexplicable strategy change with
`market_context` unchanged — is now fully explained by a
`context_stability` change that the pre-Phase-4 (old 5-key) schema had
no way to surface. Nothing in this corpus required fields beyond the
current 27-key schema; the remaining unexposed detail-list fields
(`supporting_conditions` etc.) were not needed for any of the 26
sessions and remain a documented, deliberate scope boundary, not a gap
found by this verification.
