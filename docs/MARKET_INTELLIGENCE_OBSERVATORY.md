# Market Intelligence Observatory v1

**BUJJI Options OS v3 — Engineering Series 66**

Read-only. `bujji/observatory/{explanation.py, comparison.py,
timeline.py, report.py}` consume already-saved qualification report
JSON (Series 58/65's own `reports/*.json`) and produce explanations —
never a replay, never a recomputation, never a modification of any
input.

## What it does

- **`explanation.py`** — `explain_field`/`explain_session`: for each
  of Series 32's seven `PipelineInput` fields, reports the owning MIC
  v2 module and its real, documented upstream dependency chain
  (Series 62's own composition discovery), plus any relevant Series 61
  `Evidence` supplied. Confidence is the mean of matched Evidence
  confidences, or `None` — never fabricated.
- **`comparison.py`** — `compare_sessions`/`compare_corpora`: field-by-field
  diff between two already-recorded sessions, first divergence in
  canonical (upstream-first) order, and `ThresholdCrossing` records
  that honestly report `UNKNOWN` for the threshold name/values, since
  MIC v2 does not expose its internal numeric thresholds as data.
- **`timeline.py`** — `build_timeline`: the five-stage Evidence → MIC
  classification → Trading Brain interpretation → Strategy selection →
  Runtime outcome timeline for one session; `explain_trading_impact`
  turns a `SessionDiff` into the narrative format the spec's own
  example uses.
- **`report.py`** — `build_demo_report`: loads two saved qualification
  reports, compares every shared session, and produces a detailed
  explanation of one focus session.

## Why no MIC v2 or bridge changes were needed

Series 61 already returns real per-module `Evidence` (module, signal,
confidence); Series 62 already documents the real MIC v2 composition
chain used here as `FIELD_PROVENANCE`. The Observatory reuses both
without modification. Saved qualification reports (`reports/*.json`)
do not currently carry a raw Evidence list per session — the
Observatory reports this honestly (`"not recorded, never fabricated"`)
rather than re-invoking MIC v2 to backfill it, preserving this
sprint's "never recompute" rule. A future sprint could extend the
qualification recorder to persist Evidence per session if richer
per-session explanation is wanted.

## Verification

19 new tests, all passing. Full regression: 2008 passed, 0 failed.
Qualification fingerprint unchanged. No MIC v2, Trading Brain, Runtime,
Qualification Framework, or Replay Framework file was touched.
