# Market Direction Intelligence (MDI v1)
## BUJJI Engineering Series 85

**Status:** Real, implemented, tested. Connects ONLY to Series 78 (Price
Structure) and Series 79 (Market Structure), per this sprint's explicit
scope — NOT yet wired into Consensus (81) or Decision Synthesis (77).

---

## 1. Philosophy and ownership

Series 84's investigation (`docs/DIRECTIONAL_OWNERSHIP_INVESTIGATION.md`)
concluded that directional market bias is not a single-owner field but a
genuine multi-domain reconciliation problem — structural direction,
trend direction, momentum, options positioning, and futures positioning
are distinct, different-horizon signals that can legitimately disagree.
MDI exists as its own, dedicated intelligence layer for exactly that
reason: it is architecturally analogous to Consensus (Series 81) — a
reconciliation engine over independent inputs — but answers a different
question. Consensus asks "how much do our brains agree with each
other, in general?" MDI asks "given everything we know, which way is
the market leaning?" Both preserve disagreement rather than resolve it
silently; neither predicts an outcome or selects a trade.

## 2. Step 0 investigation resolution — the most important design decision in this sprint

Per the user's explicit instruction ("prefer exposing existing evidence
over duplicating reasoning"), before writing any reconciliation logic
this sprint re-verified Series 84's findings directly against the real
code and resolved each lens independently:

### Lens A — Price Structure Direction

**Confirmed:** `bujji/msi_price_structure/engine.py`'s `derive_trend_state`
computes `signs = [_sign(d) for d in deltas]` and uses `signs[-1]`
internally to determine the trailing run — but never returned that
sign. The public `trend_state` field (`NO_TREND`/`EMERGING_TREND`/
`ESTABLISHED_TREND`/`WEAKENING_TREND`) only encodes run-length.

**Resolution — expose, don't duplicate:** added `trend_direction_signal`,
a purely additive field on `PriceStructureAssessment` (`DIRECTION_UP` /
`DIRECTION_DOWN` / `None`), populated by a new `derive_trend_direction_signal`
function that re-reads the exact same delta-sign sequence
`derive_trend_state` already computes — it does **not** duplicate
`derive_trend_state`'s own reasoning; it exposes a value that function
already produced internally. Zero change to `derive_trend_state`'s
logic, return values, or taxonomy. `schema_version` bumped `1.0.0 ->
1.1.0`; `1.0.0` remains recognized for backward compatibility.
Series 78's own full test suite re-run before/after: **16/16 passed,
zero regressions**. Full BUJJI suite re-run: **2373/2373 passed, zero
regressions** (confirmed before building MDI itself). See
`docs/MSI_PRICE_STRUCTURE_INTELLIGENCE.md`'s own Series 85 addendum for
the complete writeup.

`derive_price_structure_lens` in this package consumes
`trend_direction_signal` directly — no independent re-derivation from
raw Episode/Event evidence was needed or performed for this lens.

### Lens B — Market Structure Direction

**Confirmed:** `bujji/msi_market_structure/engine.py`'s
`derive_breakout_state`/`derive_breakdown_state`/`derive_structure_location`
were **already public and already genuinely directional** for the
confirmed-break cases: `LOCATION_ABOVE_RESISTANCE` (a confirmed
resistance breakout) and `LOCATION_BELOW_SUPPORT` (a confirmed support
breakdown) are directional facts by construction — no exposure gap
existed here at all. For the remaining, purely-spatial
`structure_location` values (`NEAR_SUPPORT`/`NEAR_RESISTANCE`/
`INSIDE_RANGE`/`UNKNOWN`), re-reading `engine.py` confirmed **no
internal signal is computed anywhere in Series 79 for those cases** —
genuinely absent, not merely unexposed.

**Resolution:** **zero changes made to Series 79.** `derive_market_structure_lens`
consumes `breakout_state`/`breakdown_state`/`structure_location`
directly (already public); it honestly reports `UNKNOWN` for the
purely-spatial cases rather than attempting independent re-derivation
that would require inventing new interpretive logic Series 79 itself
never performed — that would exceed this sprint's "reconcile existing
lenses" scope, not merely duplicate it.

This asymmetry (Lens A needed one additive field; Lens B needed
nothing) is a real, disclosed finding, not an assumption — the two
lenses were investigated independently and genuinely differ.

## 3. The reconciliation model

`reconcile_lenses(lens_opinions: Tuple[LensOpinion, ...])` accepts an
arbitrary-length tuple — never a hardcoded 2-lens signature, per
Deliverable 2's explicit extensibility mandate. Logic:

1. Filter to "opinionated" lenses (`directional_lean != UNKNOWN`).
2. If none are opinionated: `overall_direction = UNKNOWN`,
   `overall_confidence = NONE`. Insufficient evidence, not disagreement.
3. If opinionated lenses split across bullish and bearish signs:
   `overall_direction = MIXED`, `overall_confidence = LOW`,
   `conflicting_lenses` = every non-neutral opinionated lens. **Genuine
   disagreement is a real, preserved outcome — never averaged, never
   collapsed into UNKNOWN or NEUTRAL.**
4. Otherwise (all opinionated lenses agree in sign, or are all
   `NEUTRAL`): `overall_direction` is the band value at the rounded
   average signed rank of the agreeing lenses; `overall_confidence`
   is `HIGH` only when at least 2 lenses agree AND each individually
   reports at least `MODERATE` confidence, otherwise the minimum of
   the agreeing lenses' own confidence.

**Every `LensOpinion` — including disagreeing and `UNKNOWN` ones — is
always preserved in full inside `participating_lenses`.** Nothing is
ever discarded or summarized away, proven by
`test_mixed_when_directly_constructed_opposing_lens_opinions` and
`test_supporting_assessment_ids_reference_real_inputs`.

### `NEUTRAL` vs `MIXED` vs `UNKNOWN` — proven distinct, not just described

- `NEUTRAL`: lenses genuinely **agree** there is no bias (e.g. a lens
  reporting balanced, two-sided price action).
- `MIXED`: lenses genuinely **disagree** in direction.
- `UNKNOWN`: **no lens could form any opinion at all** — absence of
  signal, not agreement or disagreement.

`test_unknown_and_neutral_are_provably_distinct_outcomes` and
`test_mixed_when_directly_constructed_opposing_lens_opinions` prove
these are three different, real outcomes of the same function, not
three names for one behavior.

## 4. Extensibility — future lenses

`taxonomy.KNOWN_LENS_NAMES` already reserves canonical string names for
future lenses this sprint does not implement: `VOLATILITY_DIRECTION`,
`OPTIONS_POSITIONING_DIRECTION`, `FUTURES_POSITIONING_DIRECTION`,
`LIQUIDITY_DIRECTION`, `CROSS_ASSET_DIRECTION`. Adding a real future
lens requires: (1) a `derive_<domain>_lens(...)` function producing a
`LensOpinion`, (2) registering its name, (3) passing its `LensOpinion`
into `reconcile_lenses`'s existing tuple — **zero changes to
`reconcile_lenses`, `detect_conflicts`, or `build_explanation`
themselves**, since none of them branch on lens count or identity.

## 5. Relationship to Consensus (81) and Decision Synthesis (77)

**Not wired in this sprint, deliberately, per Deliverable 9's explicit
constraint** ("MDI must prove itself independently first"). A future
wiring would most naturally treat `MarketDirectionAssessment` as one
more `DomainSignal`-adaptable input to Decision Synthesis, or as a
dedicated new input dimension to Consensus's own agreement measurement
— either is legitimate future work, neither is implemented here.

## 6. Replay/live parity

Since MDI's real input shape is a single, already-complete
`(PriceStructureAssessment, MarketStructureAssessment)` pair (not a
growing sequence), the meaningful parity property — mirroring Series
82/83's own resolved framing for the same shape — is: the batch
entrypoint (`runner.assess_market_direction`) and the streaming
entrypoint (`runner.MarketDirectionStream.process`) produce
byte-identical results for the same pair, since both delegate to the
identical `engine.determine_market_direction` function. Proven by
`test_batch_vs_streaming_parity`.

## 7. Deliverable 10 — Readiness Report (honest assessment)

**Future directional lenses remaining unimplemented:** Volatility
Direction, Options Positioning Direction, Futures Positioning
Direction, Liquidity Direction, Cross-Asset Direction — all five
reserved as taxonomy names, none implemented.

**Existing intelligence modules that could later be bridged:**
- `bujji/intelligence/greeks_brain.py`'s real, signed `NET_LONG`/
  `NET_SHORT` position-delta classification (per Series 84's
  investigation) is a directly reusable *technique* for a future
  Options Positioning lens — though it's currently scoped to the
  live hardcoded position's own delta, not a market-wide options-chain
  read, so bridging it would require generalizing beyond a single
  position.
- `bujji/intelligence/structure_brain.py`'s real `put_call_oi_ratio` is
  directionally-adjacent raw material (a real skew number) but is
  never thresholded into a bullish/bearish read today — a future
  Options Positioning lens would need to add that thresholding logic,
  not merely read the existing number.
- `bujji/intelligence/regime_brain.py` independently exhibits the exact
  same sign-discard pattern found in Series 78 (a real `net_move` is
  computed, then thrown away for an unsigned classification) — the
  same additive-field fix pattern used here for Series 78 would apply
  there too, if `regime_brain` is ever bridged into this arc.

**What MDI still genuinely lacks:** any real options/futures OI
directional signal, any real volatility-regime directional signal, and
any cross-asset confirmation — all five reserved lenses are currently
empty slots, not approximations.

**Honest assessment of sufficiency for Strategy Selection v1:** **this
2-lens version is a real, genuine start — not a placeholder — but it is
thin.** Both lenses currently derive from price action alone (one
brain's trend read, one brain's structural-level read); they are
correlated in what they observe (the same underlying price series),
even though they are computed independently and can genuinely disagree
(as `MIXED` proves). A real Strategy Selector consuming only this
2-lens `MarketDirectionAssessment` would be making a directional call
with no options-market or volatility-market corroboration at all —
exactly the kind of single-domain fragility Consensus (81) was built
to detect and flag when consuming multiple *reasoning* domains. Adding
at least one lens genuinely independent of price action (most
plausibly Options Positioning, since Series 84 found `put_call_oi_ratio`
already computed and just unthresholded) before treating this engine's
`overall_direction` as trustworthy for real Strategy Selection is the
honest recommendation, not a reassurance that 2 lenses are enough.

## 8. Tests

`tests/test_msi_market_direction_intelligence.py` — **19 tests**, all
passing: deterministic IDs, batch/streaming parity, evidence lineage,
contradiction preservation (both a real price-walk scenario and a
direct, unambiguous constructed-opinion test), the NEUTRAL/MIXED/UNKNOWN
three-way distinction (proven, not merely asserted), real-data lens
derivation from genuine Series 78/79 output, genuinely-computed
Explanation, serialization round-trip, append-only journal, query
helpers, the full Deliverable 9 three-assessment byte-identical
double-run, and AST isolation (no `mic_v2`/Runtime/Trading Brain/
Strategy Selector/FYERS-SDK/sibling-MSI-package imports beyond the two
explicitly-permitted downstream ones, no `uuid4`, no unseeded
randomness, no strategy/strike/execution identifiers).

Full BUJJI suite after this sprint (including the Series 78 additive
change): **2392 passed, zero regressions** (2373 baseline + 19 new).
