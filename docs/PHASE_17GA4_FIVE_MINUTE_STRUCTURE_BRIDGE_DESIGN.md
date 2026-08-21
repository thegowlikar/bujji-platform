# Phase 17G.A (continued) — 5-Minute Structure Bridge: Design

**Status: DESIGN ONLY. No code.** Scopes the bridge the prior two audit
passes concluded is the real remaining work, now informed by a fourth
piece of directly relevant prior art found while scoping this: the
**Volatility Structure Bridge** (`docs/VOLATILITY_STRUCTURE_BRIDGE.md`,
Series 88) — an already-built, already-validated bridge of the exact
same *kind* (reuse pure legacy math, avoid impure wrapper classes, keep
new code to translation/plumbing only) for a different domain
(volatility, not price structure). This design follows its established
pattern rather than inventing a new one.

---

## 1. What Series 88's VSB teaches, applied here

VSB's own capability audit found that `VolatilityBrain.analyze()`,
`GreeksBrain.analyze()`, `RegimeBrain.analyze()` all call `now_ist()`
(wall-clock) internally and are therefore **"unsuitable for direct
reuse in this deterministic, replay-safe arc"** — but every inner pure
function they call is genuinely reusable, and VSB imports those
directly, never the impure `.analyze()` wrappers. It verifies this with
an AST-based test asserting no `now_ist()`/`datetime.now()` call exists
anywhere in the new bridge package.

**Checked here, same question, same answer**: `msi_price_structure.engine`/
`msi_market_structure.engine`/`market_episode.engine`/
`live_market_events.engine` are all pure (confirmed across the last two
audit passes — no wall-clock reads, `current_time`/`timestamp` always
caller-supplied). `runner.py` in both MSI packages is a thin
journal/publish wrapper around the pure `assess_*_for_episodes()`
functions, itself introducing no wall-clock coupling either — but per
VSB's own discipline, **this bridge should still call `engine.py`'s
functions directly, not `runner.py`'s wrappers**, since the runners'
journaling/publishing side effects are live-wiring concerns this bridge
does not need and should not silently trigger.

## 2. Scope (per the 5-min-only recommendation from the prior audit)

One instrument, one date range, 5-minute resolution only. Daily remains
explicitly out of scope (needs its own parameter/grouping-strategy
decision, per `PHASE_17GA3`). Per **INV-8**
(`market_episode.engine`, confirmed in the full read), one instrument's
observation stream is processed per call sequence — spot, futures, and
VIX each get an independent run, never interleaved.

## 3. Pipeline

```
HistoricalObservationStore.range(instrument_identity, RESOLUTION_FIVE_MINUTE,
                                  from_ts, to_ts)                              [reused unchanged, 17H.9]
  -> ordered list of HistoricalObservation, ascending timestamp
  -> for each consecutive pair (previous.observation, current.observation):
       live_market_events.engine.detect_price_change(current, previous)      [reused unchanged]
         -> zero or more MarketEvent (PRICE_CHANGED, maybe PRICE_GAP_DETECTED)
       market_episode.engine.process_event(episodes, event)                  [reused unchanged]
         -> grows/creates an Episode
       market_episode.engine.advance_time(episodes, current.observation.identity.timestamp)  [reused unchanged]
         -> applies ACTIVE->QUIESCENT->CLOSED using the bar's own timestamp as "now"
  -> at a chosen query point (see §6):
       msi_price_structure.engine.assess_price_structure(episodes, events, timestamp=...)     [reused unchanged]
       msi_market_structure.engine.assess_market_structure(episodes, events, timestamp=...)   [reused unchanged]
```

Every step reuses an existing, already-tested pure function. The bridge
contributes only the loop and the source substitution (Historical
Reality instead of live capture).

## 4. The one honesty correction needed: `detection_context`

`live_market_events.engine.detect_price_change()` calls `_make_event()`
**without** forwarding a `detection_context` parameter, so every event
it produces defaults to `DEFAULT_DETECTION_CONTEXT_LIVE = "LIVE"` —
confirmed by reading `_make_event`'s signature. Claiming `"LIVE"`
detection context for a historical replay would misrepresent
provenance, exactly the kind of dishonesty this project's lineage
discipline exists to prevent.

**Fix, without touching `live_market_events.engine`**: after calling
`detect_price_change()`, the bridge re-stamps each returned event's
provenance via `dataclasses.replace()` — the identical pattern already
used in `RawObservationStore.append()` to re-stamp a resolved
`certification_ref` onto a stored record without re-deriving the whole
object:

```python
event = dataclasses.replace(
    event, provenance=dataclasses.replace(
        event.provenance, detection_context=DEFAULT_DETECTION_CONTEXT_REPLAY,
    ),
)
```

`market_episode.engine`'s `process_event`/`advance_time` both already
accept `detection_context` as an explicit keyword argument — no
correction needed there, just pass `"REPLAY"` through directly.

## 5. Why the existing 300s/900s/1800s defaults work here, restated concretely

Consecutive 5-min bars are exactly 300 seconds apart during a session,
so `is_compatible()`'s proximity check passes bar-to-bar with **zero
parameter changes**. At session close, the next bar is the following
trading day's 09:15 (or later, across a weekend) — many hours away,
which exceeds `close_after_seconds` (1800s) by a wide margin, so
`advance_time()` naturally transitions ACTIVE→CLOSED overnight using
the same unmodified defaults. **This means each trading session
naturally produces its own closed episode set, with no explicit
session-boundary logic needed in the bridge at all** — an emergent
correctness property from reusing the existing engine faithfully,
not something the bridge has to implement.

## 6. Assessment query point — on-demand, no new store

Mirroring `RealityMemoryCatalog`'s own precedent (17J.1: "recompute
rather than trust a stale cache"), this bridge does **not** persist a
continuous stream of `PriceStructureAssessment`/market-structure
assessments. It exposes a function like:

```python
def structure_as_of(instrument_identity: str, as_of_timestamp: str,
                     *, historical_store, lookback_bars: int = N) -> tuple:
    """Rebuilds episodes/events from the lookback window ending at
    as_of_timestamp, then returns (PriceStructureAssessment,
    MarketStructureAssessment) computed AS OF that instant."""
```

This keeps the bridge a pure, on-demand query — exactly the same
"first durable Understanding-tier persistence" question flagged and
deferred in 17J.0/17J.2 stays deferred here too, not silently decided
by accretion. If a materialized, continuously-updated structure stream
is ever wanted, that is a separate, later, explicit decision — not a
side effect of building this bridge.

`lookback_bars` (or an equivalent window-size parameter) is itself a
real, undecided parameter — how much history feeds one assessment
changes what "trend_state ESTABLISHED" means in practice. Not resolved
here; needs a value chosen deliberately before implementation, informed
by the validation run in §7.

## 7. Mandatory validation before trusting any output

Per this thread's own established discipline (17H.9's real ingestion
findings, 17J.2's mandatory adversarial-check requirement): before this
bridge is treated as producing anything trustworthy, run it over a
known, real historical window — **2020-03-23, the COVID crash session**
(already used as the validation date in 17H.7/17I.4) — for spot, at
5-minute resolution, and have a human check whether the resulting
`trend_state`/`swing_state`/`structural_levels` sequence plausibly
matches what actually happened that session (a sharp, established
downtrend with clear structure breaks), not just that the code runs
without error. A bridge that runs cleanly but produces nonsensical
structure reads on a session this well-documented would be a real
failure, not a passing test.

## 8. New code surface, kept minimal

One new package, `bujji/reality_structure_bridge/` (checked: no
existing name collision) — no modification to
`live_market_events`, `market_episode`, `msi_price_structure`, or
`msi_market_structure`. Contents: the iteration/pairing loop (§3), the
provenance correction (§4), and the on-demand `structure_as_of()`
query function (§6). No new taxonomy, no new observation identity
system, no new certification access_method (this bridge reads only
from `HistoricalObservationStore`, whose certification was already
established in 17H.9 — nothing new is captured from a broker here).

## 9. Restrictions carried forward, unchanged

No modification to any of the four reused packages. No new indicator,
label, signal, or strategy logic — output stays exactly the vocabulary
`msi_price_structure`/`msi_market_structure` already define
(trend/swing/compression/expansion/balance, support/resistance/
breakout/retest). No persistence decision made. No daily-resolution
work. Validation (§7) is mandatory before any downstream consumption of
this bridge's output is trusted.
