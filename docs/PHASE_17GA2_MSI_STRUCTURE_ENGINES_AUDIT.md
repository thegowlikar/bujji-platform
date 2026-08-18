# Phase 17G.A (continued) — `msi_price_structure` / `msi_market_structure` Deep Audit

**Status: AUDIT ONLY. No code.** Full read of both engines' logic,
their input contract, and where that contract actually originates.
Conclusion up front: **Domain A's perception logic already exists,
already works generically over the canonical `Observation` type, and
the real remaining gap is a bridge, not a rebuild.**

---

## 1. What each package actually covers, precisely

| Package | Answers | Core outputs |
|---|---|---|
| `msi_price_structure` (Series 78) | **HOW** price is behaving | `trend_state`, `swing_state`, `compression_state`, `expansion_state`, `balance_state`, composite `structure_state` |
| `msi_market_structure` (later series) | **WHERE** price sits relative to structure | `identify_structural_levels`, `support_state`, `resistance_state`, `breakout_state`, `breakdown_state`, `retest_state`, `rejection_state`, `structural_balance` |

Both packages' own docstrings assert they are deliberately
**orthogonal**, and `msi_market_structure/taxonomy.py` documents an
explicit "Step 0.6 overlap-check disclosure" — the original engineers
already ran the same collision-avoidance discipline this engagement has
applied throughout (17H.5/17I.4/17J.0's own name/logic collision
checks), on this exact pair, before building the second package.

**Coverage against 17G's Domain list, mapped concretely:**
- Domain A (trend/swing/compression/expansion, structure breaks) —
  **covered by `msi_price_structure`** almost field-for-field.
- Domain A's "consolidation range boundaries" + parts of Domain B
  (supply/demand zone reaction) — **covered by
  `identify_structural_levels`/support-resistance/breakout/retest in
  `msi_market_structure`.**
- Domain D's acceptance/rejection/balance vocabulary — **partially
  covered** (`derive_rejection_state`, `derive_structural_balance` exist
  in `msi_market_structure`; full auction-theory volume-weighted
  acceptance is not present, a real remaining gap if ever needed).
- Domains C (liquidity), E (volatility regime), F (derivatives posture)
  — **not covered by either package** (confirmed by their function
  lists; neither touches OI, depth, or realized volatility). These
  remain genuinely open, unlike A/B/D.

## 2. The input contract, traced to its actual origin — the key finding

`msi_price_structure.engine.assess_price_structure(episodes, events, *,
timestamp, ...)` is a **pure function** over `Tuple[Episode,...]` /
`Tuple[MarketEvent,...]` — nothing in its signature or body assumes live
capture. Traced one level further back:

```
live_market_events.engine.detect_price_change(
    current: Observation, previous: Optional[Observation]
) -> Tuple[MarketEvent, ...]
```

**`Observation` here is `bujji.market_observation.models.Observation`
— the exact same canonical MOC type `build_observation()` produces,
the same type embedded in every `HistoricalObservation`
(`HistoricalObservation.observation`, Phase 17H.3 onward) and every
`RawObservation`.** `detect_price_change` computes
`delta = current_price - previous_price` between any two consecutive
`Observation`s — it has no live-only assumption baked in. Two
consecutive `HistoricalObservation.observation` objects, pulled from
`HistoricalObservationStore.range()`, would work exactly the same way.

`market_episode.engine.process_event()` (the grouping step between
raw `MarketEvent`s and `Episode`s) was not read in full line-by-line
this pass, but its signature (`process_event`, `advance_time`,
`is_compatible`) shows no live-cadence coupling either — it operates on
whatever `MarketEvent` stream it's handed, in order.

**Conclusion: the full chain — `HistoricalObservationStore` sequence →
`detect_price_change` (reused, unmodified) → `market_episode` grouping
(reused, unmodified) → `msi_price_structure`/`msi_market_structure`
(reused, unmodified) — is architecturally sound today.** Nothing found
in this audit requires rewriting the perception logic itself.

## 3. What's genuinely missing — the bridge, not the brains

No code currently constructs a `MarketEvent`/`Episode` sequence FROM
`HistoricalObservationStore`/`RealityMemoryCatalog` rows. Everything
that produces `MarketEvent`s today originates from live capture
(`capture_market_reality_session.py` and siblings feeding
`live_market_events`). Building this bridge is the real, scoped,
much-smaller remaining task:

1. Pull an ordered sequence of `HistoricalObservation.observation`
   objects for a date range/resolution from `HistoricalObservationStore.range()`
   (17H.4/17H.9, reused unchanged).
2. Feed consecutive pairs through `live_market_events.engine.detect_price_change()`
   (reused unchanged) to produce real `MarketEvent`s.
3. Feed those through `market_episode.engine.process_event()`
   (reused unchanged) to produce real `Episode`s.
4. Feed episodes+events through `msi_price_structure.assess_price_structure()`
   and `msi_market_structure.assess_market_structure()` (both reused
   unchanged) to get real `PriceStructureAssessment`/market-structure
   assessments over certified historical dates.

This is a bridge/adapter, not a new domain, and not a modification to
any of the four packages it touches.

## 4. What was NOT resolved by this audit — genuine open questions

- **Confirmation-lag/bitemporal safety.** 17G's stated "single most
  dangerous risk" was a swing pivot needing future bars to confirm. This
  engine's actual `swing_state` definition sidesteps that specific risk
  by construction — `SWING_CONFIRMED` means "a reversal in the trailing
  delta sign has already occurred as of the latest known event," never
  "this bar will turn out to have been a pivot." That's a genuinely
  different (and safer) definitional choice than 17G assumed, not
  something this audit fully re-derived the safety proof for — worth an
  explicit adversarial check before trusting it over historical replay,
  not assumed safe just because it looks causal on read.
- **Whether the existing thresholds (`config.py`'s `TREND_ESTABLISHED_MIN_RUN`,
  compression/expansion run lengths, balance ratio thresholds) were
  tuned assuming live-tick-density event streams.** Feeding this engine
  daily-bar-derived events instead of tick-derived events changes what
  "3 consecutive same-direction deltas" *means* (three up-days vs. three
  up-ticks) — same code, materially different phenomenon being
  described. Not wrong, but must be validated against real historical
  sequences before trusting the output, not assumed to transfer cleanly.
- **`market_episode.engine.process_event()`'s exact grouping window
  logic** was not read in full this pass — needs a complete read before
  any bridge is built, to confirm it has no live-clock-based windowing
  assumption (e.g., a time-based episode-closing rule keyed to
  wall-clock rather than event time would need adjustment for a
  historical replay).
- **Domains C/E/F remain genuinely unbuilt** — confirmed absent from
  both audited packages. Nothing in this finding closes those gaps.

## 5. Recommendation

**Do not build a new Domain A.** The next concrete, narrowly-scoped
piece of work — if and when this thread resumes toward Understanding —
is the bridge described in §3, preceded by a full read of
`market_episode.engine.process_event()` and a real, live-data
adversarial check of the confirmation-lag question in §4. That is
meaningfully smaller and lower-risk than anything "Domain A" implied
before this audit started.
