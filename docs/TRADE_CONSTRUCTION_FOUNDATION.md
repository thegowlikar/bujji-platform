# Trade Construction Foundation v1 (Strike Selection v1)
## BUJJI Engineering Series 90

**Status:** Real, implemented, tested, replayed against the real 41-day
corpus, driven by Series 89's own real `selected_strategy_family`
output. Given a strategy family, this package produces a fully
specified options position -- expiry, strikes, entry reference
prices, expected credit/debit, risk profile -- and never places an
order.

---

## 1. Deliverable 1 — Capability Audit

Investigated before writing any new code:

| Capability | What exists | Reused as |
|---|---|---|
| Option chain handling | `bujji.options_observation` (real Bhavcopy ingestion; one real chain snapshot has 18 distinct expiries, 144 strikes at the nearest expiry, real settlement/OI/volume per contract) | Consumed directly -- this package never re-parses Bhavcopy. |
| Implied volatility | `bujji.intelligence.volatility_brain.solve_implied_volatility` (real Newton-Raphson + bisection solver, already bridged into `msi_volatility_structure`) | Called directly, per strike, to derive delta. |
| Greeks / delta | `bujji.intelligence.greeks_brain._bs_delta` (pure Black-Scholes delta) | Called directly, per strike. |
| Expected move | `bujji.intelligence.volatility_brain.compute_expected_move`, already surfaced via `msi_volatility_structure`'s `expected_move_pct` | Used as the preferred wing-width source for Iron Condor/Iron Fly/Butterfly. |
| Expiry handling | `bujji.broker.instrument_master.InstrumentMaster.resolve_atm` -- **LIVE ONLY** (async, network, wall-clock `datetime.now()`) | **Not reusable for deterministic replay** -- confirms real per-underlying expiry structure exists (weeklies + monthlies), but this package derives expiry candidates from the real chain's own `expiry` field instead, which is replay-safe. |
| Liquidity | `bujji.intelligence.liquidity_brain` -- real, but requires live bid/ask | **Real gap, disclosed, not silently worked around**: Bhavcopy's bid/ask columns are 100% `None` (verified: sampled a real 1,876-contract chain, zero non-None values) -- the spread-based check is unavailable in historical replay. Open interest (100% populated, real range 0 to ~13M in the sampled day) is used instead, as a disclosed, WEAKER proxy -- a raw activity floor, not a spread-tightness measure. |
| Margin | `bujji.capital.providers.FyersSpanMarginProvider` -- **LIVE ONLY** (calls FYERS's `span_margin` HTTP endpoint) | **Real gap, disclosed, not guessed**: no deterministic/replay-safe margin formula exists anywhere in this codebase. `required_margin` is always `None` on every `TradeConstructionAssessment`, with `margin_unavailable_reason` stating exactly why. This is a genuine, structural limitation of this milestone, not an oversight. |
| Pricing | Real Bhavcopy `settlement` premiums, per contract | Used directly for entry reference prices and expected credit/debit -- never re-derived from a model price. |

## 2. Deliverable 2 — Expiry Selection

`select_expiry(chain, as_of_date, min_dte, max_dte)`: every expiry actually present in the real chain is a candidate; each is either chosen (nearest DTE within the configured `[min_dte, max_dte]` window, default `[1, 45]`) or rejected with an explicit reason (`BELOW_MIN_DTE` / `ABOVE_MAX_DTE`). `chosen_expiry=None` is a real, honest outcome when the window excludes every candidate -- never guessed. `CALENDAR` uses `select_calendar_expiries`, which additionally selects the next later expiry (also within `max_dte`) as the far leg; `NO_FAR_EXPIRY_AVAILABLE` is reported, not silently substituted, when none exists.

## 3. Deliverable 3 — Strike Selection, per family

All 13 Series 87 strategy families are supported. Every rule is delta-target, ATM-anchor, or expected-move based -- never a historical-return lookup:

- **LONG_DIRECTIONAL / SHORT_DIRECTIONAL**: single long/short option at a configured target delta (0.40), side (CE/PE) chosen from the real MDI `overall_direction` lean (bullish → CE for LONG_DIRECTIONAL, PE for SHORT_DIRECTIONAL's premium sale; reversed for a bearish lean). `direction` absent/`NEUTRAL`/`MIXED`/`UNKNOWN` → fails closed (`STRIKE_UNAVAILABLE`) rather than guessing a side.
- **NEUTRAL_PREMIUM_SELLING / _BUYING**: short/long strangle, both legs at a configured target delta (0.20 sell / 0.40 buy).
- **VOLATILITY_EXPANSION / _COMPRESSION**: long/short ATM straddle.
- **IRON_CONDOR**: short strikes at 0.20 delta each side; wings placed at a distance equal to the day's real VSB `expected_move_pct` (preferred) or a configured fallback (200 pts), always disclosed which source was used.
- **IRON_FLY**: ATM short straddle body; wings at the same expected-move/fallback distance.
- **BUTTERFLY**: single-option-type (CALL) butterfly -- ATM body (2x short), wings at the expected-move/fallback distance.
- **RATIO**: 1x long ATM call, 2x short call at 0.30 delta (a call ratio spread).
- **COVERED**: sells a single CE at a configured covered-call target delta (0.30), independent of directional lean (classic covered-call convention).
- **CALENDAR**: same ATM strike, near expiry sold / far expiry bought.
- **SYNTHETIC**: ATM CE+PE, long/short assignment driven by the real directional lean; fails closed without one.

Every leg's `reasoning` field states the real evidence used (delta value, distance from target, wing-width source) -- e.g. *"sold PE24000: delta 0.199 nearest to target 0.20, directional premium sale against a BULLISH lean"* -- never a performance claim.

## 4. Deliverable 4 — TradeConstructionAssessment

Immutable, frozen. Fields: `strategy_family`, `constructed` (bool), `rejection_reason` (`Optional`, set iff not constructed), `expiry`, `expiry_decision`, `legs` (each a `StrikeLeg` with role/option_type/strike/delta/premium/open_interest/side/ratio/reasoning), `entry_reference_prices`, `expected_credit_debit`, `risk_profile`, `required_margin` (always `None`, see Section 1), `margin_unavailable_reason`, `supporting_assessment_ids`, `explanation`, `provenance`, `assessment_id`, `schema_version`.

## 5. Deliverable 5 — Construction Validation (fail closed)

Rejected, with a specific `rejection_reason`, never guessed:
- `NO_SUITABLE_EXPIRY` -- no expiry survives the DTE window (or, for CALENDAR, no far leg).
- `STRIKE_UNAVAILABLE` -- no chain evidence at the required delta/strike (includes: no directional lean available for a family that needs one).
- `LIQUIDITY_INSUFFICIENT` -- any leg's open interest is below the configured floor (500).
- `IMPOSSIBLE_WING_WIDTH` -- no real strike exists at the computed wing distance.
- `INCONSISTENT_CHAIN` -- empty/missing chain or spot.
- `UNSUPPORTED_FAMILY` -- not one of the 13 (currently never triggered; all 13 are supported).

## 6. Deliverable 6 — Real 41-day corpus replay

Driven directly by Series 89's own real `selected_strategy_family` per day (not a synthetic family list):

- **35 of 41 days** had a family actually selected (the other 6 are Series 89's own honest no-selection days).
- **34 of 35 constructed successfully; 1 rejected** (`LIQUIDITY_INSUFFICIENT`, a real BUTTERFLY case where a required wing strike's open interest fell below the configured floor).
- **Risk profile split: UNDEFINED_RISK 20, DEFINED_RISK 14** -- a real, disclosed finding: roughly 59% of constructed trades in this corpus carry structurally undefined max loss (naked shorts: `SHORT_DIRECTIONAL`, `NEUTRAL_PREMIUM_SELLING`, `VOLATILITY_COMPRESSION`, `COVERED`, `RATIO`). This is a genuine input to the next milestone's risk discussion, not glossed over.
- **DTE distribution**: `{7: 9, 4: 7, 6: 6, 5: 6, 1: 6}` -- all within the configured `[1,45]` window, spread across real weekly expiries.
- **Leg-count distribution**: `{1: 17, 2: 13, 3: 4}` -- matches the families MSS actually selected in this corpus (single-leg directional/covered/short-directional, two-leg strangle/straddle/synthetic-shaped families, three-leg for the 4 successful `BUTTERFLY` days). `IRON_CONDOR`/`IRON_FLY`/`CALENDAR`/`SYNTHETIC`/`NEUTRAL_PREMIUM_BUYING`/`VOLATILITY_EXPANSION` were never selected by MSS in this corpus, so their construction logic exists and is unit-tested but has no real-corpus exercise yet -- disclosed, not hidden.
- **Per-family construction success**: 6 of 7 exercised families constructed 100% of the time; `BUTTERFLY` at 4/5 (the one liquidity rejection).

No optimization was performed against these numbers -- the delta targets, wing-width source, and OI floor were fixed before this replay ran.

## 7. Deliverable 7 — Explainability

Every `TradeConstructionAssessment.explanation` answers, from real evidence:
- **Why this expiry?** `why_this_expiry` -- DTE and window.
- **Why these strikes?** `why_these_strikes` -- delta value, distance from configured target, or ATM anchor, or wing-width source.
- **Why not neighbouring strikes?** `why_not_neighbouring_strikes` -- the next-2 nearest-by-delta candidates and their delta distance, whenever present.
- **Which constraints dominated?** `dominant_constraints` -- `delta_target` / `atm_anchor` / `expected_move` / `wing_width_fallback` / the specific rejection reason when construction failed.

## 8. Deliverable 8 — Determinism

`assessment_id` is a deterministic content hash over family + expiry + every leg's role/type/strike/side/ratio/expiry + rejection reason + schema version -- never timestamp, never `uuid4`. Proven: the full 41-day corpus replay's construction `assessment_id`s were byte-identical across two independent runs.

## 9. Known limitations

- **Margin is entirely unavailable in this milestone** (Section 1) -- every `required_margin` is `None`. This is the single largest gap standing between this package's output and a real, capital-aware position.
- **Liquidity is a proxy (open interest), not a real spread check** -- historical Bhavcopy data structurally cannot support the real `liquidity_brain` spread logic; a live cutover would need to wire real bid/ask in, exactly as `liquidity_brain`'s own docstring already anticipated.
- **Wing-width fallback (200 pts) is a structural default, not calibrated** -- used only on the (currently zero, in this corpus) days where VSB's expected move is unavailable.
- **`IRON_CONDOR`/`IRON_FLY`/`CALENDAR`/`SYNTHETIC`/`NEUTRAL_PREMIUM_BUYING`/`VOLATILITY_EXPANSION` have no real-corpus exercise yet** -- their logic is unit-tested against the real chain directly (Deliverable 3's test suite constructs all 13 on a real day), but MSS never selected them in the 41-day window, so their real-world behavior across many days is unverified.
- **Directional families depend on MDI's `overall_direction`**, whose predictive value remains the documented negative result from Series 86 -- this package does not resolve that; it only uses the lean to pick a side, not to imply the side will be profitable.

## 10. Future Position Construction interface

A future Position Construction module should consume a `TradeConstructionAssessment` (when `constructed=True`) plus a real, live margin figure (via `bujji.capital.providers.FyersSpanMarginProvider` or an equivalent certified source) and the existing `bujji.capital.engine` capital-policy machinery, to determine lot count and capital allocation. It should NOT need to re-derive strikes, expiry, or risk profile -- those are this module's job, already done. The `risk_profile` field (`DEFINED_RISK`/`UNDEFINED_RISK`) is designed to be read directly as a sizing-policy input (e.g. tighter capital caps on `UNDEFINED_RISK` constructions).

## 11. Deliverable 10 — Recommendation: **Series 91 — Position Construction**

Evidence-based:

1. **Margin is a hard, total gap** (Section 1, Section 9) -- every constructed trade in the real replay has `required_margin=None`. Without it, no responsible position size can be determined for ANY of the 34 real constructed trades.
2. **59% of constructed trades are UNDEFINED_RISK** (20/34) -- a real, measured finding showing capital-aware sizing is not a nice-to-have here; it is the single highest-leverage next step given how much of this corpus's real output carries unbounded structural loss potential.
3. **Liquidity Intelligence is a smaller, lower-frequency gap** -- only 1 of 35 real days failed on the OI proxy; upgrading to a real spread check would help marginally, not transformatively.
4. **Term Structure Intelligence** would only unlock `CALENDAR`, which MSS never selected once in this corpus -- low near-term payoff.
5. **Execution Planning / Trade Management remain premature** -- there is no sized position yet for either to act on.

**Recommended next step: Series 91 — Position Construction**, wiring real (live, certified) margin + `bujji.capital.engine`'s existing capital-policy machinery onto this package's real `TradeConstructionAssessment` output, per the interface in Section 10.
