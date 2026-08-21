"""bujji.mic_v0 — Phase 20.1 (Bujji Trading Intelligence Roadmap v1.2).

Market Intelligence Core v0 — CLASSIFICATION ONLY, never prediction,
never a trade decision, never a strategy implementation.

NAMING DISCLOSURE (found during this phase's mandatory pre-build
audit, Step 1): "Market Intelligence Core" is NOT a new term in this
codebase. `bujji.intelligence.regime_brain`, `.volatility_brain`, and
`.event_brain` (Phase 19.2.2) already call themselves "Market
Intelligence Core" in their own docstrings, and a SEPARATE, external
process (`/opt/bujji-mic-v2/`, referenced in `docs/DATA_ACQUISITION_
SPRINT_A.md`) uses "MIC" for an entirely different system this repo
does not contain. "MIC v0" is the charter's own frozen name and is
kept as specified — but this package is NOT a competing or replacement
concept. It is a thin, additive composition layer that sits on top of
the EXISTING `bujji.intelligence` brains, scoped to Cycle 1's
three-way classification (TREND|RANGE|UNCLEAR /
TREND_FOLLOWING|MEAN_REVERSION|NO_TRADE). Regime computation itself
(Kaufman Efficiency Ratio + realized volatility) is REUSED VERBATIM
from `regime_brain.RegimeBrain` — this package never reimplements it.

DATA SCOPE (Cycle 1, charter-locked): NIFTY futures/spot 5-minute
candles and India VIX only. No options data of any kind — historical
options data does not exist beyond one day in this system (Phase 20.0
finding, `docs/PHASE_20_0_HISTORICAL_DATA_CAPABILITY_REPORT.md`).

EVENT CONTEXT: always reported as `NOT_AVAILABLE`. No macro-event
calendar (RBI MPC, Union Budget, elections) exists anywhere in this
codebase — `bujji.intelligence.event_brain`'s own docstring already
disclosed this before this phase started. No risk classification in
this package may cite "event calendar" as evidence.

No broker import. No execution surface. No strategy-selection,
position-sizing, or order vocabulary — enforced structurally by
`tests/test_mic_v0/test_safety_boundary.py`.
"""
