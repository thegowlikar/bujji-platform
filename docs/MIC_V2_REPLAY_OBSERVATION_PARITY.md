# MIC v2 Replay Observation Parity — Engineering Series M1

**MIC v2 (separate repository) — kept deliberately distinct from BUJJI's own series numbering, per instruction.**

## Phase 1 — Call graph

`build_observation()` is called directly (not via a shared base
function) inside each of 8 files' own per-candle loop:
`fusion/runner.py`, `hypothesis/runner.py`, `candidate/runner.py`,
`qualification/runner.py`, `trace/runner.py`, `memory/runner.py`,
`quality/runner.py`, `runner/replay_runner.py`. All other
`run_replay_with_*` entrypoints (`graph`, `counterfactual`,
`consistency`, `context`, `context_stability`, `calibration`,
`governance`, `lifecycle`, `contract`, `opinion`, `simulation`,
`experiment`, `certification`, …) never call `build_observation()`
themselves — they compose down to **`quality/runner.py`** (the true
base of the `contract`/`opinion`/`certification` chain BUJJI actually
uses) via `**journal_kwargs` catch-alls, forwarding any keyword
argument transparently without needing to name it.

**Consequence for scope:** only the 8 files with a genuine
`build_observation()` call site needed code changes. The other ~15
composition-layer files needed **zero** changes — their existing
`**journal_kwargs` pass-through already carries new keyword arguments
end to end, confirmed by direct test (see Phase 3).

## Phase 2 — Transport threading

Each of the 8 files gained two new optional parameters,
**appended after the last existing parameter** (not inserted early —
see the "bug found and fixed" note below), and their own
`build_observation(candle, history)` call became:

```python
_oi = (option_chain_by_timestamp or {}).get(candle.timestamp, ())
_vix_level, _vix_prev_close = (vix_by_timestamp or {}).get(candle.timestamp, (None, None))
obs = build_observation(
    candle, history, option_chain=_oi, vix_level=_vix_level, vix_prev_close=_vix_prev_close,
)
```

Pure dict lookup by exact candle timestamp — no interpolation, no
computation, no interpretation. A candle with no matching key gets
`()`/`None`, identical to today's behavior.

**A bug found and fixed during this sprint:** the first patch attempt
inserted the two new parameters immediately after `candles:
list[Candle],` (the first parameter). This broke every existing
**positional-argument** caller (their next positional argument silently
bound to the new parameter instead). Caught by MIC v2's own existing
test suite (`test_fusion.py`, 2 failures). Fixed by appending the new
parameters at the **end** of each signature instead, verified by an
immediate full-suite rerun (1185 passed) before proceeding.

## Phase 3 — Verification

Direct, real, non-mocked test: called `run_replay_with_contract()`
(the actual composed entrypoint) with real `OptionChainLevel`/VIX data
and a monkey-patched `build_observation` spy:

```
option_chain kwarg: (OptionChainLevel(strike=24000, ce_oi=1234, pe_oi=5678, ...),)
vix_level kwarg: 13.29
vix_prev_close kwarg: 12.6
```

And with nothing supplied:

```
option_chain kwarg: ()
vix_level kwarg: None
```

Confirms both the positive case (data reaches `ObservationInput`) and
the negative case (absent data stays absent) through the full
`contract` → `lifecycle` → `governance` → `calibration` →
`context_stability` → `context` → `consistency` → `counterfactual` →
`graph` → `quality` → `build_observation()` chain.

## Phase 4 — Determinism

Ran the same real call twice; compared field by field. **Every field
BUJJI's schema comparison touches matched exactly** (`contexts`,
`stability`, `calibration`, `governance`, `contract` all identical).
The **only** mismatch was `lifecycle.lifecycle_id` — the pre-existing,
already-disclosed Series 62 finding (`contract/runner.py` never
forwards a `clock` parameter to `lifecycle`'s own runner, so its
timestamp defaults to real wall-clock). Confirmed unrelated to this
sprint: the mismatch exists identically whether or not
`option_chain_by_timestamp`/`vix_by_timestamp` are supplied.

## Phase 5 — Measurement (41-day real corpus, complete real OI+VIX+spot on hand)

### Raw MIC v2 reasoning (direct `run_replay_with_qualifications`/`run_replay_with_context` calls)

| Metric | Before (no option_chain/vix) | After (real OI + real VIX) |
|---|---|---|
| Reasoning cycles | 60 | 55 |
| `NOT_QUALIFIED` | 60 (100%) | 40 (72.7%) |
| **`QUALIFIED`** | **0** | **15** |
| `REGIME_TRANSITION` memories | 44 | 36 |
| `market_context=TRANSITION` | 44 | 36 |
| `market_context=TRENDING_UP` | 5 | 8 |

**For the first time in this project's history, MIC v2 genuinely
qualified decisions in replay mode** — zero to fifteen, purely because
real evidence it already knew how to represent finally reached it.

### BUJJI-level qualification (full pipeline, same 41-day corpus)

| Metric | Before (Series 65, no OI/VIX) | After (Series M1) |
|---|---|---|
| `completed_runs` | 29/41 (70.7%) | **5/41 (12.2%)** |
| `DIRECTIONAL_PUT_SPREAD` selections | 24 | 3 |
| `COVERED_CALL` selections | 3 | 9 |

**This dropped, and that is the honest, complete result — not a
partial one.** The improvement in raw MIC-level reasoning quality
(fewer `NOT_QUALIFIED`, more `QUALIFIED`, less `REGIME_TRANSITION`) is
real and unambiguous. But the *specific* regime reclassifications this
richer evidence produced landed disproportionately on strategies that
map to `COVERED_CALL` — a strategy the Contract Builder still declines
with `STRATEGY_OUT_OF_V1_SCOPE` (Series 63, a known, documented,
intentional v1 boundary) — rather than on `DIRECTIONAL_PUT_SPREAD`
(fully supported), which had previously dominated. Manifest checksum
unchanged (`09673a3dc182a3677df14ec3c610b427154243a9676f3c79facd4ba563b3729e`)
confirms the underlying corpus was untouched; only intelligence
changed.

## Files modified

**MIC v2 (separate repository):**
`fusion/runner.py`, `hypothesis/runner.py`, `candidate/runner.py`,
`qualification/runner.py`, `trace/runner.py`, `memory/runner.py`,
`quality/runner.py`, `runner/replay_runner.py` — each gained two
optional, appended parameters and one call-site change. No other
file touched. No reasoning, threshold, or algorithm changed —
confirmed by MIC v2's own full regression suite (1185 passed, 0
failed, unchanged from baseline).

**BUJJI (separate, disclosed follow-up, not part of Series M1 itself):**
`bujji/mic_replay/publication_replay.py` — the bridge script now
builds `option_chain_by_timestamp`/`vix_by_timestamp` from the same
payload fields `observation_adapter.py` (Series 68) already produces,
and passes them to `run_replay_with_contract()`/`run_replay_with_opinions()`.
Nothing recomputed; a session with no OI/VIX in its payload
contributes nothing to either map.

## Regression

MIC v2: 1185 passed, 0 failed (unchanged from pre-M1 baseline).
BUJJI: 2033 passed, 0 failed (2029 prior + 4 new bridge-wiring tests).
BUJJI qualification fingerprint: `2328e0f77ec312eeca318946df293d91` —
unchanged.

## Recommendation

Do not tune, optimize, or adjust anything in response to the
completion-rate drop — per this sprint's own explicit mandate. The
correct next step is to use the **Series 66 Observatory** (unmodified,
still fully functional) to explain exactly *which* dates flipped
regime classification and *why*, before any decision is made about
whether `COVERED_CALL`'s v1-scope gap (Series 63) should finally be
closed. That is a distinct, separately-justified engineering decision
— not a natural continuation of a transport sprint.
