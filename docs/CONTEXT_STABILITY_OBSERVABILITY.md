# Context Stability Observability — Engineering Series 70 Phase 2

Recording-only observability sprint. Surfaces detail that MIC v2's
publication pipeline already computes but previously discarded past a
single classification string, so BUJJI's qualification reports and
Observatory diffs can explain *why* `context_stability` changed, not
just *that* it changed. No MIC v2 source file is modified; no value is
recomputed, interpreted, or fabricated anywhere in this sprint.

## Phase 1 — call graph (read-only investigation)

```
Observation (candle/option_chain/vix payload, per session)
  -> Narrative
    -> Hypothesis
      -> Candidate
        -> Qualification
          -> Memory                 (MarketMemory, one per qualification
                                      cycle; REGIME_TRANSITION /
                                      PERSISTENT_CONTRADICTION are the two
                                      flagged memory_type values)
            -> Context (per-cycle)  (MarketContext: trend_context,
                                      volatility_context, liquidity_context,
                                      regime_context, conviction_context,
                                      stability_context -- six dimensions,
                                      each string-valued, one MarketContext
                                      per qualification cycle, keyed by its
                                      own reasoning_trace_id)
              -> ContextStability (whole-replay aggregate)
                                     (derive_stability() in
                                      mic_v2/context_stability/engine.py,
                                      called once at the end of the replay
                                      over the full accumulated `contexts`
                                      list: classification, transition_count,
                                      persistence_length, dimension_agreement,
                                      confidence_variance, context_lifetime,
                                      reasoning_summary)
```

`MarketContext` is defined in `mic_v2/context/models.py` (fields
confirmed by reading that file: `reasoning_trace_id`, `trend_context`,
`volatility_context`, `liquidity_context`, `regime_context`,
`conviction_context`, `stability_context`). `ContextStability`'s scalar
detail fields are computed once per replay by
`mic_v2/context_stability/engine.py::derive_stability()`, which reads
all six `MarketContext` dimensions across its window — the new
per-cycle context fields and the new whole-replay stability detail
fields are documented causal inputs/outputs of that same function, not
independent data.

## Phase 2 — changes made

### `bujji/mic_replay/publication_replay.py`

- `_BRIDGE_SCRIPT` (subprocess script run inside MIC v2's own
  interpreter, lines ~146-214): groups `memories` by
  `reasoning_trace_id`, walks `contexts` in order, and for each
  `MarketMemory` whose `memory_type` is `REGIME_TRANSITION` or
  `PERSISTENT_CONTRADICTION` appends a compact
  `{"timestamp": ..., "memory_type": ...}` dict to `transition_events`
  (lines 158-166). The JSON `out` dict (lines 168-214) gained:
  - Whole-replay `ContextStability` detail (off the single `stability`
    object already computed at end of replay):
    `stability_reasoning_summary`, `stability_transition_count`,
    `stability_persistence_length`, `stability_dimension_agreement`,
    `stability_confidence_variance`, `stability_context_lifetime`.
  - The other five `MarketContext` dimensions for the *latest* cycle
    (`trend_context` already survived as `market_context`):
    `context_volatility`, `context_liquidity`, `context_regime`,
    `context_conviction`, `context_stability_dimension` (deliberately
    named distinctly from the top-level whole-replay
    `context_stability` key — this one is
    `MarketContext.stability_context`, a per-cycle string dimension,
    not the `ContextStability` object).
  - `transition_events`: the compact list described above.
- `PublishedState` dataclass (lines 219-257): 11 new `Optional` scalar
  fields plus `transition_events: Tuple[Dict[str, Optional[str]], ...] = ()`,
  all defaulting so an older bridge/partial payload never crashes
  construction.
- `replay_published_state_for_session()` (lines 290-339): all new
  fields threaded from `result.get(...)`, defaulting to `None`/`()`
  when absent — never a hard `result[...]` lookup for any new field.

### `bujji/qualification/historical_runner.py` / `recorder.py`

The full `PublishedState` object is threaded through opaquely and
unmodified — `historical_runner.py` accepts an optional
`published_states` sequence and assigns `published_state` onto each
`QualificationRecord` (line ~220); `recorder.py`'s `QualificationRecord`
carries it as `published_state: Optional[Any] = None` (line ~93). No
per-field extraction happens at this layer by design — that happens
once, at the report layer.

### `bujji/qualification/report.py::_session_entry()`

Lines 142-153: all 12 new fields are read off `published_state` via
`getattr(published_state, <field>, None) if published_state else None`
— defensive on both "no `published_state` at all" (pre-Series-70
records) and "attribute genuinely absent" (defence in depth beyond the
dataclass default).

### `bujji/observatory/comparison.py::CANONICAL_FIELD_ORDER`

New fields inserted as two blocks, both **before** `context_stability`:

```python
CANONICAL_FIELD_ORDER: Tuple[str, ...] = (
    "market_context",
    "context_volatility",
    "context_liquidity",
    "context_regime",
    "context_conviction",
    "context_stability_dimension",
    "market_opinion",
    "stability_transition_count",
    "stability_persistence_length",
    "stability_dimension_agreement",
    "stability_confidence_variance",
    "stability_context_lifetime",
    "context_stability",
    "calibration",
    "governance",
    "lifecycle",
    "contract",
    ... # Trading-Brain runtime fields, unchanged by this sprint
)
```

**Placement rationale.** Both new blocks are causal inputs/detail of
`context_stability`, not outputs, so both sit upstream of it in the
canonical equality-diff order:

- `context_volatility` / `context_liquidity` / `context_regime` /
  `context_conviction` / `context_stability_dimension` are five of the
  six `MarketContext` dimensions that `derive_stability()` reads across
  its window to produce the `context_stability` classification.
- `stability_transition_count` / `stability_persistence_length` /
  `stability_dimension_agreement` / `stability_confidence_variance` /
  `stability_context_lifetime` are `derive_stability()`'s own scalar
  outputs, but they are exactly the counted/measured detail that its
  single `classification` string summarizes (classification is
  literally `transition_count / (context_lifetime - 1)`, thresholded).
  For a first-divergence read, seeing e.g. `transition_count` changed
  *explains why* `classification` changed — so it belongs upstream of
  it in equality-diff order, even though structurally it's computed
  alongside `classification` rather than before it.

Free-text `stability_reasoning_summary` and list-valued
`transition_events` are deliberately **excluded** from
`CANONICAL_FIELD_ORDER` (not good equality-diff fields — one is prose,
the other a variable-length list) but remain present as supplementary
keys in the session dict (`report.py`, `_session_entry()`) and in the
bridge's JSON output, for narrative/timeline use.

This placement was verified already correct as found on disk — no
correction was required. (A design placing these fields *after*
`context_stability` was reportedly explored during concurrent work on
this same feature by a separate agent, but the file on disk at the
time of this review already had the correct before-placement; no such
after-placement was found in `comparison.py` in its current state.)

## Test coverage

`tests/test_context_stability_observability.py` — 9 tests, already
present and consistent with the current code (no changes needed):

1. `test_published_state_carries_new_context_stability_fields` — all
   12 new fields populate correctly from a mocked bridge JSON result.
2. `test_published_state_new_fields_default_none_when_bridge_omits_them`
   — backward compatibility: a pre-Series-70 bridge JSON (no new keys)
   never crashes; every new field defaults to `None`/`()`.
3. `test_published_state_field_defaults_are_none_or_empty_tuple` — the
   dataclass itself is constructible without any Phase 2 kwargs.
4. `test_session_entry_surfaces_new_keys_from_published_state` — all 12
   new keys appear correctly in `_session_entry()`'s output dict.
5. `test_session_entry_new_keys_none_when_published_state_absent` —
   `published_state=None` (pre-Series-70 record) never crashes
   `_session_entry()`.
6. `test_new_scalar_context_fields_precede_context_stability_in_canonical_order`
   — the five `MarketContext` dimension fields sit before
   `context_stability`.
7. `test_stability_scalar_detail_fields_precede_context_stability_in_canonical_order`
   — the five `ContextStability` scalar detail fields sit before
   `context_stability`.
8. `test_free_text_and_list_fields_excluded_from_canonical_order` —
   `stability_reasoning_summary` and `transition_events` are absent
   from `CANONICAL_FIELD_ORDER`.
9. `test_context_stability_dimension_diverges_before_context_stability_itself`
   — an end-to-end `compare_sessions()` divergence-chain example: two
   sessions differing only in `context_stability_dimension` (with
   `context_stability` itself equal on both sides) produce
   `first_divergence == "context_stability_dimension"`, confirming
   Observatory correctness for this exact scenario the placement
   decision above exists to serve.

## Test counts (real, from this verification pass)

- BUJJI (`/opt/bujji/app`, `.venv/bin/python -m pytest -q`): **2061
  passed**, 1 warning (pre-existing `pkg_resources` deprecation
  warning from `fyers_apiv3`, unrelated to this sprint), 12.97s.
- MIC v2 (`/opt/bujji-mic-v2`, `.venv/bin/python -m pytest -q`): **1185
  passed**, 20.88s (MIC v2 source is untouched by this sprint; run to
  confirm no incidental regression).

## Determinism confirmation

Ran `replay_corpus_published_states()` twice over a real 5-session
subset of the on-disk corpus in `/tmp/m1/`
(`BhavCopy_NSE_FO_0_0_0_2026052{5,6,7,9}_F_0000.csv` +
`BhavCopy_NSE_FO_0_0_0_20260601_F_0000.csv`, with real VIX values from
`/tmp/m1/vix_by_date.json`) — the same real market data used by prior
Series 69 corpus runs already sitting on this host. Extracted all 12
new `PublishedState` fields for every session in both runs and
compared: **byte-identical** across both runs (verified via direct
Python `==` comparison of the extracted field dicts, session by
session). No new market data was fetched; no fabricated corpus was
used.

## Files touched (this sprint, verified against current on-disk state)

- `bujji/mic_replay/publication_replay.py`
- `bujji/qualification/historical_runner.py` (threading only, no field
  extraction — see above)
- `bujji/qualification/recorder.py` (threading only)
- `bujji/qualification/report.py`
- `bujji/observatory/comparison.py`
- `tests/test_context_stability_observability.py`

---

## Phase 3 — Observatory narrative upgrade

**Result: the existing `explain_trading_impact()` divergence-chain
narrative in `bujji/observatory/timeline.py` already surfaces the
Series 70 fields correctly with NO code change required for the
ordered chain itself.** This was expected: `first_divergence` and the
per-field consequence lines are both driven generically by walking
`comparison.CANONICAL_FIELD_ORDER` (Series 69 Phase 3's own
mechanism), and `CANONICAL_FIELD_ORDER` already places the seven new
Series 70 scalar/enum fields — `context_volatility,
context_liquidity, context_regime, context_conviction,
context_stability_dimension` (before `market_opinion`) and
`stability_transition_count, stability_persistence_length,
stability_dimension_agreement, stability_confidence_variance,
stability_context_lifetime` (immediately before `context_stability`)
— in the tuple. Confirmed live with synthetic session dicts:

```python
a = {'id': '2026-07-09', 'context_stability_dimension': 'REGIME', 'context_stability': 'STABLE'}
b = {'id': '2026-07-09', 'context_stability_dimension': 'VOLATILITY', 'context_stability': 'TRANSITIONING'}
diff = compare_sessions(a, b)
# diff.first_divergence == 'context_stability_dimension'   <-- upstream field wins, not context_stability
```

**Gap found and fixed:** `stability_reasoning_summary` (free text) and
`transition_events` (list-valued) are deliberately excluded from
`CANONICAL_FIELD_ORDER` (documented in `comparison.py`'s own
docstring) so they never appear in the equality-diff chain — but
`stability_reasoning_summary` literally explains *why*
`context_stability` changed (e.g. `"transition_rate=0.267 at or below
0.6"`), so it was worth surfacing as supplementary narrative text.

`explain_trading_impact()` now takes two new **optional** parameters,
`session_a` and `session_b` (the original raw session dicts) —
fully backward compatible, existing callers passing only `diff` are
unaffected. When both are supplied and `context_stability` is in
`diff.changed_fields`, a clearly-labeled supplementary block is
appended after the ordered divergence chain:

```
  [supplementary, non-canonical context -- not part of the ordered divergence chain]
  stability_reasoning_summary: 'transition_rate=0.100 at or below 0.6' -> 'transition_rate=0.267 at or below 0.6'
  transition_events: [] -> ['REGIME_TRANSITION@2026-07-09']
```

`bujji/observatory/report.py::build_demo_report()` was updated to pass
`focus_session_a`/`focus_session_b` through to
`explain_trading_impact()` so the demo report's focus narrative also
gets the supplementary block when applicable.

**Files touched:**
- `bujji/observatory/timeline.py` (`explain_trading_impact()` signature
  + supplementary block; no change to the CANONICAL_FIELD_ORDER walk)
- `bujji/observatory/report.py` (`build_demo_report()` now forwards
  the raw session dicts)
- `tests/test_market_intelligence_observatory.py` (6 new tests, see
  "Series 70 Phase 3" section at the bottom of the file)

**Test results:**
- `tests/test_market_intelligence_observatory.py`: 32 passed (26
  pre-existing + 6 new).
- Full BUJJI suite (`/opt/bujji/.venv/bin/python -m pytest -q`):
  **2067 passed**, 1 warning (same pre-existing `pkg_resources`
  warning), 13.43s. Baseline going in was 2061 passed — net +6, all
  new, zero regressions.

## Phase 4 — Historical verification

Per the spec, the existing saved report artifacts were **not**
sufficient to demonstrate the Series 70 schema: `reports/
historical_campaign_v2_1_full_corpus.json`, `_m1_after.json`, and even
Series 69's own `historical_campaign_series69_before.json`/`_after.json`
all predate this recording work and have no Series 70 fields
populated. Per the same allowance used in Series 69 Phase 4, the
report **artifact** was regenerated from the same, unchanged 41-day
corpus inputs (no new market data, no corpus changes, no MIC v2 logic
changes) — only re-running the artifact production through the
current `build_report()`.

The Series 69 Phase 4 scripts were still present on the remote host at
`/tmp/m1/run_before_deep.py` / `run_after_deep.py`, along with the
real Bhavcopy CSVs (`/tmp/m1/BhavCopy_NSE_FO_0_0_0_*_F_0000.csv`, 41
trading dates from 2026-05-25 through 2026-07-22) and real VIX values
(`/tmp/m1/vix_by_date.json`). Both scripts already call the current
`build_report()`, so they were copied verbatim to
`run_before_deep_s70.py` / `run_after_deep_s70.py` (only the output
filename and log-tag strings changed) and rerun.

### The threading bug (found, root-caused, and fixed 2026-07-24)

The first attempt at this Phase 4 run produced 41 sessions with all 12
of the new Series 70 fields (`context_volatility`, `context_liquidity`,
`context_regime`, `context_conviction`, `context_stability_dimension`,
`stability_transition_count`, `stability_persistence_length`,
`stability_dimension_agreement`, `stability_confidence_variance`,
`stability_context_lifetime`, `stability_reasoning_summary`,
`transition_events`) coming back `None`/empty for every single
session. This looked exactly like a library bug in the
recording/reporting path, so all three candidate library files were
read in full:

- `bujji/qualification/historical_runner.py`,
  `HistoricalQualificationRunner.run_corpus()` (around line 159 and
  line 220) — correctly reads
  `published_states[index] if published_states is not None else None`
  and threads it onto `QualificationRecord(..., published_state=published_state)`.
- `bujji/qualification/recorder.py` — `QualificationRecord.published_state:
  Optional[Any] = None` (line 93) is the correct field name and matches
  what `historical_runner.py` assigns.
- `bujji/qualification/report.py::_session_entry()` (lines 101-153) —
  correctly does `published_state = getattr(r, "published_state", None)`
  and then reads all 12 fields off it with
  `getattr(published_state, field, None) if published_state else None`.

All three of these were confirmed correct by inspection and by the
existing unit-test suite (2067/2067 passing, unchanged). **The bug
was not in the library.** It was in the two ad-hoc driver scripts
(`/tmp/m1/run_before_deep_s70.py` line 92 and
`/tmp/m1/run_after_deep_s70.py` line 87, both outside the repo/library
proper): each computed `pub_results = replay_corpus_published_states(observations)`,
correctly used `state = pub_results[i][1]` to build the pipeline-input
`kwargs` for each `ReplayScenario` via `to_pipeline_input_kwargs(state)`,
but then discarded `state` — the loop never collected it into a list —
and called

```python
recorder = runner.run_corpus(final_scenarios, timestamps)
```

with `published_states` omitted entirely, so `HistoricalQualificationRunner`
correctly defaulted every record's `published_state` to `None` exactly
as its docstring says it will when the caller doesn't supply the
argument.

**The fix** (in both scripts): collect the real `PublishedState` object
for each session into a `final_states` list parallel to
`final_scenarios`, and pass it through:

```python
final_scenarios = []
final_states = []          # NEW
for (obs, state), rec in zip(pub_results, ordered_records):
    ...
    final_scenarios.append(scenario)
    final_states.append(state)   # NEW

...
recorder = runner.run_corpus(
    final_scenarios, timestamps,
    published_states=final_states,   # NEW — was missing
)
```

This was verified end-to-end with a minimal one-session repro
(`/tmp/m1/repro_s70_bug.py`, session `NIFTY-2026-07-09`) before touching
the full 41-day scripts:

- `replay_corpus_published_states()` → `PublishedState` with all 12
  fields populated (e.g. `context_volatility=UNKNOWN`,
  `stability_transition_count=0`,
  `stability_reasoning_summary='context_lifetime=1 below minimum 2'`).
- `run_corpus()` **without** `published_states=` → `QualificationRecord.published_state`
  is `None`, and `_session_entry()` produces all 12 fields as `None` —
  reproducing the exact bug.
- `run_corpus()` **with** `published_states=final_states` →
  `QualificationRecord.published_state` is the real object, and
  `_session_entry()` produces all 12 fields with their real values,
  matching step (a) exactly.

No repo library file (`historical_runner.py`, `recorder.py`, `report.py`,
or the MIC v2 bridge in `publication_replay.py`) was modified — this
was purely a caller-side wiring omission in the two `/tmp/m1` driver
scripts, and the fix is confined to those scripts. The full suite was
rerun after the fix to confirm no regression: **2067 passed**, 1
warning (pre-existing `pkg_resources` warning), unchanged from
baseline.

### The corrected runs

- **BEFORE** (no `option_chain`/VIX reaching MIC v2):
  `/tmp/m1/run_before_deep_s70.py` → 41 sessions, saved as
  `reports/historical_campaign_series70_before.json`. `market_context`
  distribution: `{TRANSITION: 30, TRENDING_DOWN: 4, TRENDING_UP: 3,
  UNKNOWN: 2, SIDEWAYS: 2}`.
- **AFTER** (real `option_chain`/VIX, Series M1):
  `/tmp/m1/run_after_deep_s70.py` → 41 sessions, saved as
  `reports/historical_campaign_series70_after.json`. `market_context`
  distribution: `{TRANSITION: 26, TRENDING_UP: 6, TRENDING_DOWN: 4,
  UNKNOWN: 3, SIDEWAYS: 2}`.

Both runs share the identical corpus manifest checksum
(`09673a3dc182a3677df14ec3c610b427154243a9676f3c79facd4ba563b3729e`),
confirming the input corpus itself was not altered between the two
runs — only the option_chain/VIX visibility to MIC v2 differs, exactly
as in Series 69. All 12 Series 70 fields are now genuinely populated
(non-`None`) for every session in both reports (`context_stability_dimension`
is `CHANGING` for all 41 sessions in both; the other 11 fields vary
per-session).

`compare_corpora()` was rerun over the two corrected reports:
**41 sessions compared, 28 changed** (`stability_transition_count` is
now the most common first-divergence field: 16 of 28 changed sessions,
followed by `context_volatility`: 8, and `market_context`: 4 — a
materially different, and more informative, first-divergence
distribution than the pre-fix run, which could only ever show
`market_context` or `context_stability` itself since nothing upstream
of `context_stability` was populated).

### The 5 named sessions — corrected, with real Series 70 data

With the fix in place, every one of the 5 named sessions is now
genuinely explained by a real, non-`None` upstream Series 70 field
difference:

- **NIFTY-2026-07-09** — first divergence: `stability_transition_count`
  (`9 -> 14`), with real cascading differences in
  `stability_persistence_length` (`14 -> 6`),
  `stability_dimension_agreement` (`0.9326 -> 0.8953`),
  `stability_context_lifetime` (`48 -> 44`),
  `stability_reasoning_summary`
  (`'transition_rate=0.191 at or below 0.25' ->
  'transition_rate=0.326 at or below 0.6'`), and a genuinely different
  `transition_events` list (same early entries, diverging from
  2026-07-01 onward). `context_stability` (`MOSTLY_STABLE ->
  TRANSITIONING`) is a downstream consequence of the real
  `stability_transition_count` change, not the earliest-visible
  divergence. Downstream: `strategy` (`PREMIUM_VWAP_STRADDLE ->
  IRON_CONDOR`), full risk/capital warning-tier chain.
- **NIFTY-2026-07-03** — first divergence: `market_context`
  (`TRANSITION -> TRENDING_UP`), with `context_volatility` (`STABLE ->
  CONTRACTING`), `context_regime` (`TRANSITION -> TREND`),
  `stability_transition_count` (`6 -> 12`), and the rest of the
  stability-detail fields all genuinely differing. `context_stability`
  (`MOSTLY_STABLE -> TRANSITIONING`) is downstream both of
  `market_context` and of the now-real `stability_transition_count`
  change. Downstream also includes `strategy`
  (`DIRECTIONAL_PUT_SPREAD -> COVERED_CALL`), `outcome` (`COMPLETED ->
  FAILED`).
- **NIFTY-2026-06-12** — first divergence: `market_context`
  (`TRANSITION -> UNKNOWN`), with `context_regime` (`TRANSITION ->
  RANGE`), `stability_transition_count` (`4 -> 5`),
  `stability_persistence_length` (`6 -> 5`), and
  `stability_dimension_agreement` (`0.9167 -> 0.8981`) all genuinely
  differing. `context_stability` remains downstream of `market_context`,
  consistent with the pre-fix finding, but is now additionally
  corroborated by a real upstream stability-detail difference.
  Downstream: `strategy` (`DIRECTIONAL_PUT_SPREAD -> None`), full
  risk/capital/execution denial chain, `outcome` (`COMPLETED ->
  FAILED`).
- **NIFTY-2026-06-15** — first divergence: `stability_transition_count`
  (`4 -> 6`), with `stability_persistence_length` (`8 -> 5`) and
  `stability_dimension_agreement` (`0.925 -> 0.8917`) also genuinely
  differing. `context_stability` (`MOSTLY_STABLE -> TRANSITIONING`) is
  now correctly shown as downstream of the real stability-detail
  change, reversing the pre-fix finding that it was the
  earliest-visible divergence. Downstream: `strategy`
  (`DIRECTIONAL_PUT_SPREAD -> None`), full risk/capital/execution
  denial chain, `outcome` (`COMPLETED -> FAILED`).
- **NIFTY-2026-07-16** — first divergence: `context_volatility`
  (`STABLE -> CONTRACTING`), with `stability_transition_count` (`13 ->
  18`), `stability_persistence_length` (`14 -> 6`),
  `stability_dimension_agreement` (`0.9151 -> 0.8707`), and
  `stability_context_lifetime` (`54 -> 50`) all genuinely differing.
  `context_stability` is downstream, reversing the pre-fix finding.
  Downstream: `strategy` (`PREMIUM_VWAP_STRADDLE -> IRON_CONDOR`), same
  risk/capital warning-tier pattern as 07-09.

**Honest bottom line, corrected:** with real Series 70 data flowing
through, all 5 named sessions are now genuinely explained (fully or
partially) by an upstream Series 70 field difference — 3 of 5
(07-09, 06-15, 07-16) by `stability_transition_count` or
`context_volatility` directly; 2 of 5 (07-03, 06-12) by
`market_context` corroborated by real upstream stability-detail
differences. This reverses the earlier honest-but-limited finding
(which was correct given the data it had — every upstream field really
was `None` at the time). No session is force-explained: every field
cited above is a real, distinct, non-`None` value pulled from the
regenerated reports, not inferred or fabricated. `derive_stability()`
aggregates over the whole 41-day replay window per session, so a
session's per-cycle context dimensions can still agree while its
aggregate `stability_transition_count`/`transition_events` differ —
that mechanism is exactly what explains 07-09, 06-15, and 07-16 above,
where the single-cycle context fields the day of the named session
don't differ, but the whole-window aggregate does.

## Phase 5 — Stability statistics (measurement only), corrected

Recomputed directly from the corrected 41-session BEFORE/AFTER
reports (`reports/historical_campaign_series70_before.json` /
`_after.json`, regenerated 2026-07-24 after the threading fix above).
No interpretation beyond the numbers.

### `context_stability` distribution

| Classification | BEFORE (count) | AFTER (count) |
|---|---|---|
| MOSTLY_STABLE | 31 | 5 |
| TRANSITIONING | 8 | 34 |
| STABLE | 1 | 1 |
| INSUFFICIENT_HISTORY | 1 | 1 |
| HIGHLY_VARIABLE | 0 | 0 |
| UNKNOWN | 0 | 0 |

Total: 41 / 41 in both. (Unchanged from the pre-fix Phase 5 — this
top-level classification was never affected by the threading bug,
since it was always available via `scenario.context_stability`, not
`published_state`.)

### `stability_transition_count` / `stability_persistence_length` — now computable with real data

| Metric | BEFORE | AFTER |
|---|---|---|
| `stability_transition_count`: n | 41 | 41 |
| `stability_transition_count`: min | 0 | 0 |
| `stability_transition_count`: max | 14 | 21 |
| `stability_transition_count`: mean | 6.024 | 9.000 |
| `stability_persistence_length`: n | 41 | 41 |
| `stability_persistence_length`: min | 1 | 1 |
| `stability_persistence_length`: max | 14 | 6 |
| `stability_persistence_length`: mean | 10.220 | 5.195 |

The AFTER corpus shows materially more transitions per session
(mean 9.0 vs 6.0) and materially shorter persistence lengths (mean
5.2 vs 10.2) — consistent with the AFTER corpus's much higher
`TRANSITIONING` share (34/41 vs 8/41) in the classification table
above; real option_chain/VIX data reaching MIC v2 makes the modeled
context genuinely less stable across this 41-day window.

### `transition_events` — now computable with real data

- BEFORE: all 41 sessions carry a non-empty `transition_events` list;
  counts per session range from 0 to 44 (`Counter` of list length:
  `{0: 4, 19: 4, 5: 3, 34: 2, 38: 2, ...}` — 31 distinct counts across
  41 sessions).
- AFTER: all 41 sessions carry a non-empty `transition_events` list;
  counts per session range from 0 to 36 (`{0: 4, 18: 4, 25: 4, 5: 3,
  27: 2, 31: 2, ...}` — 28 distinct counts across 41 sessions).
- `context_stability_dimension`: `CHANGING` for all 41 sessions in
  both BEFORE and AFTER — the only one of the 12 fields that does not
  vary across this particular 41-day corpus.

### Determinism (corrected)

`run_after_deep_s70.py` was rerun a second time (`/tmp/m1/series70_after_deep_rerun2.json`)
against the same unchanged inputs. The `sessions[]` arrays from the
two runs — including all 12 Series 70 fields — were SHA-256 hashed
(`json.dumps(sessions, sort_keys=True, default=str)`) and found
**byte-identical**: both hashes equal
`486e8f1d8f4edd6aebe32346ef7dad20397b5187e3a5ccf7f61b2d1972f24988`.
