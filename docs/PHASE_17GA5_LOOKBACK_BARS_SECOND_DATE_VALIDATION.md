# Phase 17G.A — `lookback_bars=75` Second-Date Validation

**Status: VALIDATION RESULT.** `DEFAULT_LOOKBACK_BARS = 75` (first-pass
default, `bujji/reality_structure_bridge/bridge.py`) checked against a
second real historical date, deliberately chosen for a different market
character than the original validation date.

---

## Date chosen: 2018-10-26 (October 2018 correction)

2020-03-23 (the original validation date) was a sustained crash with a
sharp late-session reversal. 2018-10-26 is a genuinely different
regime: a choppy, two-way session with an early breakdown that
partially recovered into the close — a good test of whether
`lookback_bars=75` only "looks sane" on one kind of day.

## Results — two query points, same session

| | 10:00 IST (mid-morning) | 15:25 IST (near close) |
|---|---|---|
| `trend_state` | NO_TREND | NO_TREND |
| `swing_state` | CONFIRMED | CONFIRMED |
| `structure_state` | BALANCE | BALANCE |
| `structure_integrity` | COHERENT | COHERENT |
| `confidence` | HIGH | HIGH |
| `contradictions` | 0 | 0 |
| `support_state` | DEVELOPING | ESTABLISHED |
| `resistance_state` | ESTABLISHED | WEAK |
| `breakdown_state` | **CONFIRMED** | **DEVELOPING** |

**Plausibility check**: the read moves from a confirmed breakdown with
established resistance overhead (morning) to a softened, developing
breakdown with established support underneath (close) — consistent
with the real, documented shape of that session (an early sharp drop,
partial stabilization/recovery into the close). Zero contradictions,
`COHERENT` integrity, `HIGH` confidence at both points — the engine is
not producing noisy or self-contradicting reads on a genuinely
different kind of session than the first validation date.

## A real, worth-documenting characteristic (not a bug)

`trend_state: NO_TREND` at both query points, despite this being a
real down-day, is initially counter-intuitive — but correct given what
the metric actually measures: `derive_trend_state()` reads the
**trailing run of same-signed 5-min deltas within the 75-bar lookback
window**, a LOCAL, within-session measure of directional persistence,
not the day's net move. A choppy two-way session can have a large net
decline while never sustaining a long enough same-direction run of
5-min bars to register as `TREND_ESTABLISHED`/`TREND_EMERGING` — this
is a real, documented property of the metric at this lookback window,
not a defect. Worth keeping in mind for any future consumer of this
bridge: `trend_state` answers "is price extending a short local run
right now," not "what has today's session done overall."

## Conclusion

`lookback_bars=75` produces coherent, plausible, non-degenerate
assessments across two structurally different real market regimes
(sustained crash-then-reversal, and choppy breakdown-then-recovery).
No evidence found to revise the default. Documented, not silently
assumed, as this project's standing calibration-note discipline
requires (mirrors `bujji/intelligence/volatility_brain.py`'s own
"treat as a starting point, not a validated conclusion" framing —
this is now a validated-against-two-real-dates starting point, still
not a statistically calibrated one; a broader systematic sweep across
many dates remains a legitimate future refinement, not attempted here).
