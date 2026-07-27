# OI/VIX Shadow Investigation

**BUJJI Options OS v3 — Engineering Series 68**

## Phase 1: does `ObservationInput` already support OI/VIX?

**Yes.** `mic_v2/models/evidence.py::ObservationInput` already has:
```python
option_chain: tuple[OptionChainLevel, ...] = ()   # each level: ce_oi, pe_oi, ce_bid, ce_ask, pe_bid, pe_ask
vix_level: Optional[float] = None
vix_prev_close: Optional[float] = None
```
This was never a schema gap. The question is entirely about transport.

## Phase 2 & 3: evidence-flow map

Traced every call site, both in `bujji` and in MIC v2's own frozen source.

### OI

```
HistoricalSessionRecord.option_chain_liquidity   TRANSPORTED  (Series 64/65, real OpnIntrst from Bhavcopy)
        ↓
observation_adapter.build_option_chain_payload() TRANSPORTED  (Series 68 fix: now reads ce_oi/pe_oi from liquidity)
        ↓
bridge script payload dict ("option_chain")      TRANSPORTED  (present in the JSON payload)
        ↓
replay_driver.py's bridge script                 IGNORED      (builds `chain_by_ts` dict, dead code -- never used)
publication_replay.py's bridge script             ABSENT       (never reads "option_chain" key at all)
        ↓
mic_v2.observation_builder.build_observation()   NEVER CALLED WITH option_chain=
        (all 17 replay-mode call sites pass build_observation(candle, candle_history) -- two positional args only)
        ↓
ObservationInput.option_chain                    ALWAYS ()    (the field's own default, since never supplied)
        ↓
Hypothesis / Candidate / Memory / Context         NOT CONSUMED (structurally cannot see OI -- absent from the object they receive)
```

### VIX

```
HistoricalSessionRecord.vix                      TRANSPORTED  (Series 64/65, real value from FYERS pass-through)
        ↓
observation_adapter.build_observation_payload()  TRANSPORTED  (Series 68 fix: now includes "vix" key when present)
        ↓
bridge script payload dict ("vix")               TRANSPORTED  (present in the JSON payload)
        ↓
replay_driver.py / publication_replay.py bridges  ABSENT       (neither script reads a "vix" key at all -- confirmed by grep, zero matches before this sprint)
        ↓
mic_v2.observation_builder.build_observation()   NEVER CALLED WITH vix_level=/vix_prev_close=
        (same 17 call sites, same two-positional-argument pattern)
        ↓
ObservationInput.vix_level / vix_prev_close      ALWAYS None
        ↓
Hypothesis / Candidate / Memory / Context         NOT CONSUMED
```

## Phase 4: transport fixed where it could be; the rest is a genuine MIC v2 limitation, disclosed not repaired

**Fixed this sprint** (bujji's own code, legitimate transport repair):
- `build_option_chain_payload()` previously hardcoded `ce_oi=pe_oi=0`
  unconditionally, discarding `option_chain_liquidity` even though it
  already carried real OI. Now populates it correctly.
- `build_observation_payload()` previously never included `vix` at
  all, discarding `record.vix` even though it already carried a real
  value. Now includes it when present.

**Not fixed, and not fixable within this sprint's authorized scope**:
the payload now genuinely carries OI/VIX all the way to the bridge
script boundary — but every one of MIC v2's own frozen replay-mode
entrypoints (`run_replay`, `run_replay_with_contract`,
`run_replay_with_qualifications`, `run_simulation`, and every
individual stage's own `runner.py` — 17 call sites total, confirmed by
direct grep across the entire `mic_v2` source tree) calls
`build_observation(candle, candle_history)` with exactly two
positional arguments. There is no replay-mode entrypoint anywhere in
MIC v2 that accepts or forwards `option_chain=`/`vix_level=`. Only the
genuine live-feed path (`mic_v2/live/runner.py`) uses a *different*
`build_observation()` (from `live/observation_builder.py`, a distinct
function). This is a real, structural asymmetry in MIC v2 itself
between its live and replay modes — not a bujji-side gap, and not
something this sprint's mandate ("No MIC reasoning changes... No
production changes") permits fixing.

## Bottom line

**OI and VIX now flow correctly through every piece of code this
project owns, and stop exactly at MIC v2's own replay-runner call
sites — a precise, disclosed, single break point.** Reaching
`ObservationInput.option_chain`/`vix_level` in replay mode would
require either (a) MIC v2 itself adding `option_chain=`/`vix_level=`
parameters to its replay-mode runners (a MIC v2 change, out of this
sprint's scope), or (b) bujji calling `build_observation()`/`run_cycle()`
directly instead of MIC v2's composed runners (which would mean
reimplementing MIC v2's own replay loop — exactly the kind of
"bypassing a completed stage" this project has refused to do since
Series 61). Neither was attempted this sprint.

## Raised with MIC v2

MIC v2 has no issue tracker, git remote, or contribution process — it
is a standalone frozen dependency documented entirely through its own
`docs/*_ARCHITECTURE.md` files. This gap was filed the same way:
**`/opt/bujji-mic-v2/docs/REPLAY_OBSERVATION_TRANSPORT_GAP_ISSUE.md`**
(new file only, no existing MIC v2 code or doc modified). It documents
the exact call-site evidence above, what bujji has verified on its own
side, why bujji will not fix it itself, and a *suggested* (not
prescribed) fix scope — threading `option_chain=`/`vix_level=`/
`vix_prev_close=` through the 9 `runner.py` files' `run_replay_with_*`
signatures, mirroring how `journal_kwargs`/`graph_store` are already
threaded through the same composition chain. No priority or timeline
is implied; it is a record for whoever picks up MIC v2 work next.

## Verification

8 new tests, all passing. Full regression: 2029 passed, 0 failed
(2021 prior + 8 new). Qualification fingerprint unchanged
(`2328e0f77ec312eeca318946df293d91`) — expected, since nothing
downstream of `observation_adapter.py` reads the corrected fields yet.
