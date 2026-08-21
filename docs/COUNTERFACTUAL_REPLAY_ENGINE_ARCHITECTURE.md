# Counterfactual Replay Engine (CRE) — Series 102 Architecture

## What this is

CRE answers the one question Series 99-101 couldn't: "what other decisions
were legally available at that moment?" It never invents, never
optimises, never changes Production — it only re-runs the real,
unmodified frozen decision pipeline against a real, causally-truncated
subset of the same day's already-recorded data, and records the honest
result as a `CounterfactualSession`.

## The one architectural difference from Series 100/101

MLE and EPS are Production-import-free everywhere, always. **CRE cannot
be** — replaying a real alternative decision path requires calling the
real `SessionDriver`/`run_full_cadence` pipeline. This is handled by
isolating that one real requirement to a single file:

| File | Production imports? |
|---|---|
| `taxonomy.py`, `config.py`, `models.py`, `engine.py`, `serialization.py`, `journal.py`, `query.py` | **None** — same zero-import discipline as every Series 100/101 file, verified (`test_non_replay_files_stay_production_import_free`). |
| `replay.py` | **Yes, by design** — the one disclosed, narrow, independently-reviewable exception. Its imports are checked against an explicit allowlist (`test_replay_py_imports_are_within_the_explicit_allowlist`) so the exception cannot silently widen later. |

`replay.py` reuses the exact real replay calling convention established
throughout Sprints 115–122 (`SessionDriver` + `run_full_cadence`,
real Bhavcopy/candle data) — now formalized into a tested, importable
module instead of a throwaway `/tmp` script.

## Package structure

`bujji/msi_counterfactual_replay/`, same 9-file-family house convention:

| File | Responsibility |
|---|---|
| `taxonomy.py` | `PATH_LABEL_BASELINE`/`ALTERNATIVE`, `LEGALITY_LEGAL`/`ILLEGAL`, `PHASE1_MAX_EXPLORED_PATHS = 2`. |
| `models.py` | `ExploredPath`, `CounterfactualSession` — every field the mission requires (replay id, timestamp, assumptions, explored/rejected paths, legality, earliest causal timestamp, supporting references). |
| `engine.py` | Pure: `validate_causality`, `validate_legality`, `build_counterfactual_session`. |
| `replay.py` | The one real-Production-invoking function: `run_real_path`. |
| `serialization.py`, `journal.py`, `query.py` | Standard house conventions. |

## Causality (Deliverable 4)

One rule, checked literally: every real data timestamp an `ExploredPath`
used must be `<=` its own `decision_timestamp`. `validate_causality`
names the exact offending timestamp on failure
(test-verified: `test_causality_violation_names_the_exact_offending_
timestamp`). `replay.run_real_path` enforces this at the source — it
truncates the real candle list to `<= cutoff_timestamp` *before* handing
anything to the real pipeline, so a future candle is never even
constructed into an observation, let alone fed to a decision function.

## Legal decision space (Deliverable 3)

Phase 1's own, disclosed limit: exactly one `BASELINE` path and one
`ALTERNATIVE` path, never more (no exhaustive search — enforced, not just
documented: `test_legality_fails_with_no_exhaustive_search_more_than_
two_paths`). "Impossible" trades/strikes/timing/knowledge are structurally
excluded by construction, not filtered after the fact: `ExploredPath`'s
`thesis_type`/`selected_family` are always the real, unmodified output of
`run_real_path`'s real pipeline call — there is no code path that lets a
caller inject a fabricated outcome into a `CounterfactualSession`.

## Reproducibility (Deliverable 9)

Two independent guarantees, both test-verified:
1. `build_counterfactual_session` is a pure function — identical real
   `ExploredPath` inputs always produce a byte-identical `session_id` and
   `CounterfactualSession` (`test_session_deterministic_id_same_inputs_
   same_id`).
2. `replay.run_real_path` itself is deterministic — the same real day,
   same real cutoff, same real candles always produce the same real
   `thesis_type`/`selected_family`/`data_timestamps_used`
   (`test_replay_determinism_same_real_inputs_produce_byte_identical_
   path`) — inherited directly from the underlying frozen pipeline's own
   established determinism (no randomness, no wall-clock reads anywhere
   in the chain).

## Disclosure over silence

An `ILLEGAL` or acausal session is never hidden or discarded —
`build_counterfactual_session` still returns a real, complete
`CounterfactualSession`, just honestly marked `LEGALITY_ILLEGAL` with the
specific causality/legality reasoning attached
(test-verified: `test_session_is_illegal_and_still_recorded_when_
causality_fails`). This mirrors this project's established fail-closed-
and-disclose convention (e.g. freshness `STALE` states are recorded, not
suppressed).

## Golden replay tests (Deliverable 8)

Run against the real corpus's one real `NO_TRADE` day (2026-07-13, the
Sprint 116 finding):
- `test_golden_replay_baseline_matches_real_production_no_trade_day` —
  replaying the full real day reproduces the real, already-known
  `NO_TRADE` outcome, not a fabricated one.
- `test_golden_replay_alternative_is_causally_truncated_and_legal` — a
  real mid-day cutoff produces a real, different, legal, causal
  alternative path (observed in manual verification: `TREND_REVERSAL` was
  forming at midday, unresolved by end of day — a genuine, honest,
  non-dramatic counterfactual, not a constructed "gotcha").
- `test_golden_replay_production_state_unaffected_by_cre` — same
  zero-effect-on-Production guarantee already proven for MLE and EPS.

## Isolation

`tests/test_cre_isolation.py`, 5 tests: no Production module imports CRE
(same forbidden direction as Series 100/101); every non-`replay.py` file
stays Production-import-free; `replay.py`'s imports are checked against
an explicit allowlist; no file anywhere in the package ever references an
order-placing function; `engine.py` performs no IO.

## Integration (as specified, not built further)

Per the mission: "CRE produces replay artefacts. MLE consumes them.
Evidence Packets cite them. Knowledge Candidates interpret them.
Production never imports CRE." This sprint builds the artefact
(`CounterfactualSession`) and its production; no code in Series 100/101
was modified to consume it — that wiring is future, separately-reviewed
work, same discipline as Series 101's own migration notes.

## Verification summary

- Regression: **2920/2920 passing** (2896 pre-existing + 24 new CRE
  tests), zero pre-existing tests modified.
- Replay parity: **0 diffs** across all 41 real corpus days.
- Golden replay + determinism: verified against real corpus data, not
  synthetic fixtures.

## Explicitly not implemented (per mission's own Non-Goals)

Missed Opportunity Detector, recommendations, Engineering Proposals
generation, knowledge generation, optimisation, any Production change.
Implementation stops here — do not begin Series 103 without review.
