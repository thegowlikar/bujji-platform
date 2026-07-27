# Market Intelligence Core (MIC)

Observation layer sitting ABOVE production. No module here ever places an
order, sizes a trade, or calls into the Capital Management Engine,
Execution Engine, Orchestrator, or Trade Manager. See
`docs/AUDIT_LOG.md` Pass 11 for the full design rationale (the MIC
research report from this session's chat history).

## Status

| Brain | Status | Data source |
|---|---|---|
| Regime Brain | **Built, tested, real-data validated** | Real NIFTY spot candles (already live) |
| Premium, Capital | Designed, not yet built (buildable on existing data) | Existing production data |
| Volatility, Greeks (1st-order), Event (gap-risk only) | Designed, not yet built | Existing production data + Black-Scholes tooling |
| OI (standalone brain, not built) | Designed, **data access unverified** | Requires a live-certification pass, same discipline as span_margin |
| Event | **Built (2026-07-20), narrowed scope** -- expiry-proximity + India VIX verified live; economic-calendar half (FOMC/RBI/Budget) has NO verified data source and remains unbuilt | See Event Brain section below |
| Structure | **Built (2026-07-20)** -- optionchain OI verified live | See Structure Brain section below |
| Liquidity | **Built (2026-07-20)** -- bid/ask/spread verified live | See Liquidity Brain section below |
| Volatility (IV-rank) | Designed, blocked on history | Needs months of real accumulated data |
| Behaviour | **Built (2026-07-20), inert by design** -- confirmed zero real trades on file; hard-gated at 30 trades | See Behaviour Brain section below |

## Regime Brain

**Purpose:** classify today's session as TRENDING / RANGING / VOLATILE /
COMPRESSED / TRANSITIONING / UNKNOWN, from real spot candles alone.

**Method:** Kaufman's Efficiency Ratio (trend vs. noise) + realized
volatility (absolute level, and first-half-vs-second-half-of-session
ratio for compression/expansion detection). Deliberately not ML — no
real multi-week history exists yet to train or honestly validate one.
Every reading carries its raw evidence (`efficiency_ratio`, `net_move`,
`path_length`, `realized_vol`, `compression_ratio`) so a human can
independently recompute and check it.

**Data-quality gate:** fewer than 6 candles (30 minutes) → `UNKNOWN`,
confidence 0 — never a guess from too little data.

### Real-data validation (2026-07-19, all 13 real NIFTY trading days on file)

12 of 13 genuinely quiet real days (day-range 0.45%-1.11%) correctly
classified **RANGING** with confidence 58%-98%. The one day with a real,
notably larger move (07-08: 2.04% day-range, -1.60% net move — visibly
different from every other day in the sample) was correctly **excluded**
from the RANGING bucket, landing on TRANSITIONING rather than a false
"ranging" call. For a brain feeding a premium-selling risk decision,
correctly excluding the one anomalous day from "safe to sell premium" is
the behaviorally important result — full readings for all 13 days are
reproducible via `tests/test_regime_brain.py`'s real-data fixture pattern.

### Calibration note (honest, not hidden)

07-08's efficiency ratio was 0.391 — below the current `TRENDING`
threshold (0.60) despite being the sample's clearest directional day.
This suggests the threshold may be tuned conservatively for 5-minute
NIFTY data specifically. **Not adjusted** — the four threshold constants
in `regime_brain.py` are documented as a first pass, not a proven-optimal
set, and 13 days is not a sample to recalibrate a threshold from. Revisit
once real multi-week history accumulates (same constraint flagged
throughout the MIC research and strategy-roadmap reports).

### Files

- `bujji/intelligence/models.py` — `RegimeType`, `DataQuality`, `RegimeReading`
- `bujji/intelligence/regime_brain.py` — `RegimeBrain.analyze(candles) -> RegimeReading`
- `tests/test_regime_brain.py` — 11 tests: data-quality gate, 5 synthetic
  unambiguous cases (one per regime, realistic NIFTY price scale), evidence
  completeness, order-independence, rendering, and a real-data regression
  fixture locking in the validated 2026-07-01 result.

### Explicitly NOT done in this pass

Not wired into the dashboard, journal, or any production decision path —
this is a standalone, observation-only module per the MIC's first
principle ("a strategy should never decide whether to trade; the MIC
decides, strategies execute" — and even the MIC itself doesn't decide
yet, it only observes). Wiring it into a visible Market State object and
the Decision Layer is future work, not assumed complete here.


## Volatility Brain

**Purpose:** is implied volatility rich or cheap relative to what the
underlying is actually realizing -- the core "does premium-selling have
an edge today" question -- and what move does the market's own option
pricing imply between now and expiry.

**Method:** solves implied volatility separately for the CE and PE legs
from their real market premiums via Newton-Raphson on the Black-Scholes
price (bisection fallback if Newton-Raphson fails to converge -- a single
bad premium must never crash the brain). Realized volatility is computed
from real spot candles using the same log-return primitives as the
Regime Brain, annualized since IV is quoted annualized. Richness =
IV / realized vol, the textbook volatility risk premium framing.
Expected move (1-sigma, to expiry) = spot * IV * sqrt(time-to-expiry).

**Data-quality gate:** fewer than 6 candles -> `UNKNOWN`, confidence 0.
**Both-legs discipline:** `iv_average` (and therefore `richness`) is only
computed when BOTH the CE and PE legs successfully solve -- a straddle's
implied volatility is not honestly represented by one leg's number alone.
If either leg fails to solve (e.g. a stale/below-intrinsic quote), the
whole reading degrades to `richness=UNKNOWN`, `data_quality=INSUFFICIENT`.

**IV rank / IV percentile are always `None`, by design.** Both require
weeks/months of historical implied volatility. FYERS serves no historical
data for expired option contracts (verified live, same finding as the
backtesting work earlier this session) -- until real IV history
accumulates week over week or an external vendor is integrated, this
brain reports `None` explicitly rather than fabricating a number from an
assumed "typical" range.

**Solver correctness:** validated with a synthetic round-trip check --
feed the solver a Black-Scholes price generated from a known sigma
(0.08 to 0.50), confirm it recovers that exact sigma (abs tolerance
1e-4). All 6 tested sigmas round-tripped exactly.

### Real-data validation (2026-07-13 to 2026-07-17, real CE/PE premiums + real spot)

All 5 real trading days produced **IV_RICH** at 09:20 entry, IV in the
10.5%-15.4% range against realized vol implying ratios of 1.16-1.70.
This is consistent with the well-documented volatility risk premium
(options routinely price in more movement than materializes) and matches
the direction a premium-selling strategy needs to be structurally
profitable. Checked again at 10:30 to satisfy the MIN_CANDLES=6
data-quality gate honestly (09:20 correctly returns UNKNOWN -- entry is
literally the first candle of the session, there is no real history yet
to compute a realized vol from).

### Bugs found via testing, not by inspection (documented per this project's discipline)

1. **Partial-leg IV averaging (real bug, fixed).** The first implementation
   computed iv_average from whichever of iv_ce/iv_pe solved, even if
   only one did -- silently treating a half-failed solve as a complete
   reading. Caught by test_unsolvable_premium_produces_unknown_not_a_crash,
   which deliberately feeds an unsolvable CE premium. Fixed to require
   both legs before computing iv_average/richness at all.
2. **Test-data mismatch (test bug, not a brain bug).** The real-data
   regression test for 2026-07-13 initially used a hand-picked,
   down-sampled approximation of the real spot closes rather than the
   exact candle sequence, and produced the wrong classification as a
   result. Fixed by re-fetching and embedding the exact real 16-candle
   close sequence from /tmp/nifty_real_spot_20260701_20260719.json.

### Files

- `bujji/intelligence/models.py` -- Richness, VolatilityReading
- `bujji/intelligence/volatility_brain.py` -- Black-Scholes pricer/vega,
  solve_implied_volatility(), VolatilityBrain.analyze(...)
- `tests/test_volatility_brain.py` -- 17 tests: solver round-trip
  correctness (6 sigmas + puts), below-intrinsic/non-positive-input
  refusal, data-quality gate, partial-leg-solve degradation, richness
  classification (synthetic rich/cheap control cases), expected-move
  sanity, rendering, and a real-data regression fixture for 2026-07-13.

### Explicitly NOT done in this pass

Not wired into the dashboard, journal, or any production decision path --
same standalone, observation-only status as the Regime Brain. IV
rank/percentile remain structurally unavailable until real IV history
accumulates or an external vendor is integrated (tracked as an open item
elsewhere in this codebase's research, not solved here).


## Premium Brain

**Purpose:** is the combined straddle premium decaying the way a seller
wants -- driven by time (theta) -- or is something else (spot movement,
IV expansion/contraction) fighting or accelerating that decay? A raw P&L
number alone doesn't answer this; a premium that has barely moved could
mean "theta is working exactly as expected" or "theta decay is being
completely offset by an adverse spot move" -- very different situations
that look identical on a P&L line.

**Method:** hold spot and IV FIXED at their real values at entry, and
reprice the straddle at the CURRENT point in time via Black-Scholes --
a pure counterfactual: "what would the combined premium be right now if
literally nothing had changed except time passing?" Compare that
theoretical, time-decay-only baseline against the REAL current combined
premium. `behavior_ratio = actual / theoretical`. Reuses the exact same
`_bs_price` pricer already validated by the Volatility Brain's solver
round-trip test -- no new untested pricing math.

- ratio <= 0.85 -> `DECAYING_FASTER_THAN_THETA` (favorable: something
  beyond time decay, e.g. IV compression, is helping the seller)
- ratio >= 1.15 -> `RISING_AGAINST_THETA` (unfavorable: something beyond
  time decay, e.g. spot running away from the strike or IV expanding, is
  fighting the seller)
- otherwise -> `DECAYING_AS_EXPECTED`

**Data-quality gates:** requires a known entry IV (from the Volatility
Brain or another reliable source) -- without one there's no legitimate
theta-only baseline to compare against, so this brain refuses to guess
one and reports `UNKNOWN`/`INSUFFICIENT`. Also gated on non-positive
premiums, `now` before `entry_time`, and at/past-expiry (no meaningful
theta-only baseline once time-to-expiry hits zero).

**Synthetic correctness check:** if the "current" premium fed to the
brain is itself computed from the exact same Black-Scholes formula with
only time moved forward (spot and IV literally unchanged), the ratio
must come back exactly 1.0 (`DECAYING_AS_EXPECTED`) -- verified exactly
in `tests/test_premium_brain.py`.

### Real-data validation (2026-07-13, 09:20 entry vs 10:30, real CE/PE premiums)

Combined premium (24000 strike) rose from a real 378.20 to 418.35 as
spot rallied from 24027.45 to 24154.75 (+127 points) in just over an
hour. The theta-only baseline barely moved (378.20 -> 377.05) since so
little time had passed relative to ~8 days to expiry. `behavior_ratio`
came out to 1.11 -- just under the `RISING_AGAINST_THETA` threshold --
correctly attributing almost the entire premium rise to the real spot
move rather than to decay-defying premium behavior, exactly the
distinction this brain exists to surface.

### Files

- `bujji/intelligence/models.py` -- `PremiumBehavior`, `PremiumReading`
- `bujji/intelligence/premium_brain.py` -- `PremiumBrain.analyze(...)`,
  reuses `_bs_price` from `volatility_brain.py`
- `tests/test_premium_brain.py` -- 11 tests: data-quality gates (missing
  entry IV, non-positive premium, invalid time window, at/past expiry),
  an exact synthetic correctness check on the theta-only baseline,
  classification edge cases (far-faster / far-slower than theta),
  derived-field sanity, rendering, and a real-data regression fixture
  for 2026-07-13.

### Explicitly NOT done in this pass

Not wired into the dashboard, journal, or any production decision path
-- same standalone, observation-only status as the Regime and Volatility
Brains.


## Greeks Brain

**Purpose:** right now, how sensitive is the SHORT straddle position to
a move in spot (delta), a change in that sensitivity itself (gamma), the
passage of time (theta), and a change in implied volatility (vega)?

**Method:** standard closed-form Black-Scholes delta/gamma/theta,
computed per leg (CE, PE) for a LONG position -- textbook convention --
combined into POSITION Greeks for the actual SHORT straddle BUJJI holds
(`position_x = -(leg_x_ce + leg_x_pe)`). Reuses `_norm_cdf`, `_norm_pdf`,
`_bs_price`, and `_bs_vega` from the Volatility Brain (already validated
there); only delta/gamma/theta formulas are new, each cross-checked
against finite-difference derivatives of the already-trusted pricer
before being trusted. Theta reported per calendar day (annualized/365),
vega per 1% IV change (annualized/100) -- the units a trader actually
reasons in.

**Exposure classification:** `position_delta` past +-0.15 ->
`NET_LONG_EXPOSURE` / `NET_SHORT_EXPOSURE` (position now benefits from
further spot movement in that direction); otherwise `DELTA_NEUTRAL`.

**Data-quality gates:** requires a known IV for BOTH legs and a positive
time-to-expiry -- without either, no legitimate Greeks calculation
exists, so this brain refuses to guess and returns
`UNKNOWN`/`INSUFFICIENT`.

### Formula correctness (finite-difference cross-checks, not just "looks right")

- Delta: matches the finite difference of `_bs_price` w.r.t. spot to
  1e-3, for both CE and PE.
- Gamma: matches the finite difference of delta itself to 1e-5.
- Theta: a naive 1-day finite-difference step showed real discretization
  error (~5%) against the analytic instantaneous rate -- caught and
  cross-checked with a much smaller time step (1e-5 years) instead,
  which converges to the analytic value to within 0.05 (per calendar
  day). Not a brain bug; a coarse-vs-instantaneous-derivative
  discrepancy, resolved by using the right-sized finite-difference step.
- Vega: matches the finite difference of `_bs_price` w.r.t. sigma to
  1e-2 (per 1% IV change).
- Known-value sanity: ATM CE delta ~+0.52, ATM PE delta ~-0.48 (both
  near textbook +-0.5), gamma positive and equal for CE/PE at equal IV,
  per-leg theta negative (long options decay), **position theta
  positive** -- the seller structurally earns from time passing, which
  is the entire premise of this strategy.

### Real-data validation (2026-07-13, real 09:20 entry)

Real 24000-strike ATM straddle at the real 09:20 spot (24027.45) with
the real solved entry IVs for each leg (CE 11.68%, PE 14.39%) came back
`DELTA_NEUTRAL` with positive position theta -- exactly the expected
shape for an ATM straddle sold right at the money.

### Files

- `bujji/intelligence/models.py` -- `GreeksExposure`, `GreeksReading`
- `bujji/intelligence/greeks_brain.py` -- `_bs_delta`, `_bs_gamma`,
  `_bs_theta` (new), `GreeksBrain.analyze(...)` (reuses `_bs_vega` from
  `volatility_brain.py`)
- `tests/test_greeks_brain.py` -- 19 tests: finite-difference formula
  correctness (delta/gamma/theta/vega), ATM known-value sanity, exposure
  classification (neutral / net-long / net-short), data-quality gates,
  rendering, and a real-data regression fixture for 2026-07-13.

### Explicitly NOT done in this pass

Not wired into the dashboard, journal, or any production decision path
-- same standalone, observation-only status as the Regime, Volatility,
and Premium Brains.


## Liquidity Brain

**Purpose:** is the combined straddle's top-of-book bid/ask spread tight
enough to enter or exit right now without meaningful slippage?

**Data access verified live before writing any brain logic** (this
brain's data source was previously listed as "designed, data access
unverified" in the MIC architecture -- closed out here the same way
span_margin was: a live call before trusting anything). The real FYERS
`quotes` response for an option symbol includes per-symbol `bid`, `ask`,
and `spread` fields:

```
NSE:NIFTY2672124250CE -> bid=82.4  ask=82.6  spread=0.2
NSE:NIFTY2672124250PE -> bid=68.0  ask=68.05 spread=0.05
```

`spread == ask - bid` held exactly on both legs -- confirmation these
are genuine top-of-book quotes, not placeholders. **Deliberately not
used:** the same response's `volume` field, which returned 400M+ for a
single option symbol -- not a plausible per-symbol traded quantity and
not independently corroborated. Rather than guess what it represents,
this brain does not consume it. Full multi-level market depth (beyond
top-of-book bid/ask) was not observed in the raw response either, and is
not assumed available.

**Method:** combined spread = `(ce_ask + pe_ask) - (ce_bid + pe_bid)` --
the real cost of buying back both legs at the current ask (the relevant
side when exiting a short straddle) versus the mid. Reported both as an
absolute (points) and as a percentage of the combined mid, so it's
comparable across very different premium levels. Per-leg spread % also
reported individually.

- combined spread % <= 0.50% -> `TIGHT`
- combined spread % >= 2.00% -> `WIDE`
- otherwise -> `NORMAL`

(First-pass thresholds, same calibration-note discipline as every other
brain -- not statistically tuned, will need real spread history across
varied conditions -- opening volatility, illiquid strikes, expiry day --
to validate properly.)

**Data-quality gates:** any non-positive bid/ask on either leg, or a
crossed market (ask below bid on either leg -- a stale/bad quote) ->
`UNKNOWN`/`INSUFFICIENT`. The raw quote values are still reported even
on a bad reading, so a human can see what was actually fed in.

**Integration note:** no current BUJJI broker method exposes bid/ask --
only `get_ltp()` (last price only). This brain is data-source agnostic
and takes bid/ask as plain arguments, exactly like the
Volatility/Premium/Greeks Brains take premiums and IV as plain
arguments. Wiring real bid/ask into production (extending the broker
interface with a method that returns the full quote, not just `lp`) is a
separate, not-yet-done integration step.

### Real-data validation (2026-07-20, live NIFTY 24250-strike CE/PE quotes)

The exact live-captured bid/ask above (CE 82.4/82.6, PE 68.0/68.05)
produced combined spread 0.25 points (0.166% of mid) -- correctly
classified `TIGHT`, consistent with genuine liquidity in a NIFTY weekly
ATM strike.

### Files

- `bujji/intelligence/models.py` -- `SpreadTightness`, `LiquidityReading`
- `bujji/intelligence/liquidity_brain.py` -- `LiquidityBrain.analyze(...)`
- `tests/test_liquidity_brain.py` -- 16 tests: spread-math correctness
  (exact arithmetic control), classification edge cases, data-quality
  gates (non-positive quotes, crossed market on either leg), raw-value
  visibility on a bad reading, rendering, and a real-data regression
  fixture for the live 2026-07-20 quote capture.

### Explicitly NOT done in this pass

Not wired into the dashboard, journal, or any production decision path
-- same standalone, observation-only status as the other four brains.
Bid/ask is not yet plumbed into the broker interface for live production
use (see Integration note above). Multi-level depth and the `volume`
field's true meaning remain open items requiring their own live
verification, not solved here.


## Structure Brain

**Purpose:** is spot sitting right at a real open-interest wall -- a
strike where heavy call or put writing tends to act as resistance or
support -- or comfortably between walls? This is real market
positioning, a genuinely different signal from the Regime Brain's pure
price-action view.

**Data access verified live before writing any brain logic.** This
brain uses a DIFFERENT endpoint from every other brain's data source:
FYERS `optionchain` (`client.optionchain({"symbol": ..., "strikecount": N})`),
not the plain `quotes` call. Confirmed live against real NIFTY strikes
that it returns per-strike `oi`, `prev_oi`, and `oich` (OI change) for
both CE and PE, with `oich == oi - prev_oi` holding exactly on every
strike checked (e.g. 24100 PE: oi=17,299,295, prev_oi=10,241,300,
oich=7,057,995 -- exact) -- genuine, internally consistent open interest,
not a placeholder. Closes out the "data access unverified" status this
brain previously carried in the MIC status table.

**Method:** resistance = the strike ABOVE spot with the highest CE open
interest (the classic "call wall" -- writers betting price won't clear
that level). Support = the strike BELOW spot with the highest PE open
interest ("put wall"). Put/Call OI ratio (total PE OI / total CE OI) is
reported as evidence only -- informational, not yet used for
classification; there isn't enough real history to calibrate a PCR
signal honestly yet.

- distance to the nearer wall <= 0.25% of spot (~one NIFTY strike-width
  at ~24000) -> `NEAR_RESISTANCE_WALL` or `NEAR_SUPPORT_WALL`, whichever
  is strictly closer
- otherwise -> `MID_RANGE`

(First-pass threshold, same calibration-note discipline as every other
brain.)

**Data-quality gates:** non-positive spot, no strikes with usable
non-negative CE/PE OI, or no strikes on either side of spot ->
`UNKNOWN`/`INSUFFICIENT`. If only one side has data (e.g. only strikes
above spot), the brain reports what it can compute (resistance) and
leaves the other side honestly `None` rather than fabricating a support
level.

### Real-data validation (2026-07-20, live NIFTY option chain, strikes 24100-24250)

Real spot (24243.1) sat almost exactly under the 24250 call wall (CE OI
12,541,490) -- distance 0.0285%, well inside the threshold -- correctly
classified `NEAR_RESISTANCE_WALL`. The 24200 strike held the largest PE
OI of the strikes captured (25,869,350) and was correctly selected as
support even though it was numerically closer to spot than 24250,
because the classification logic compares actual distances and picks
whichever wall is nearer (24250 at 0.0285% beat 24200's 0.178%).
Put/Call OI ratio came out to 2.16 (informational).

### Files

- `bujji/intelligence/models.py` -- `StructureProximity`, `StructureReading`
- `bujji/intelligence/structure_brain.py` -- `StructureBrain.analyze(...)`
- `tests/test_structure_brain.py` -- 16 tests: wall-selection correctness
  (highest OI on the correct side of spot, opposite-side OI must never
  leak in), proximity classification (including the nearer-wall-wins
  case when both are within threshold), data-quality gates (invalid
  spot, no valid strikes, negative OI exclusion, one-sided data),
  rendering, and a real-data regression fixture for the live 2026-07-20
  option chain capture.

### Explicitly NOT done in this pass

Not wired into the dashboard, journal, or any production decision path
-- same standalone, observation-only status as the other five brains.
Put/Call OI ratio is reported but not classified -- calibrating a PCR
signal is a separate future step. The live check used only 4 strikes
around spot (`strikecount=3`); production use would need more strikes
to reliably find true walls further out, which is an integration detail
for whoever wires this brain into the live data path, not solved here.


## Event Brain

**Scope stated up front, not discovered later:** this is deliberately
narrower than the MIC's original "Event Brain" concept. The calendar
half (FOMC, RBI policy, Union Budget, and similar scheduled macro
events) needs an external economic-calendar data source. BUJJI has no
such integration, and none of the FYERS endpoints already used elsewhere
in this codebase (`quotes`, `historical`, `optionchain`) provide one.
Rather than fabricate event dates from general knowledge -- which would
silently go stale and could simply be wrong for any given contract
cycle -- that half is **not built**, and remains an explicit open item
(same status as the standalone OI brain).

**What IS built, on two genuinely verifiable sources:**

1. **Expiry proximity** -- pure date arithmetic between today and the
   straddle's own real expiry date. Needs no external data at all;
   always available. `EXPIRY_DAY` (0 days) / `EXPIRY_EVE` (1 day) /
   `NORMAL` (2+ days). `today > expiry_date` is treated as an
   inconsistent/stale input and refused (`UNKNOWN`), not silently
   coerced into a nonsensical negative days-to-expiry.
2. **VIX regime** -- India VIX, verified live via the same `quotes`
   endpoint the Liquidity Brain's check used: `NSE:INDIAVIX-INDEX`
   returned `lp=13.02, prev_close_price=13.15, chp=-0.99` at capture
   time -- a real, plausible level. Classifies `LOW` (<=13) /
   `MODERATE` (13-20) / `ELEVATED` (>=20) -- first-pass bands from
   India VIX's well-known historical behavior, not yet statistically
   calibrated from BUJJI's own accumulated history (same calibration-
   note discipline as every other brain). `vix_change_pct` is FYERS's
   own real day-over-day change (`chp`), not derived or guessed here.

**Deliberately not combined into one synthesized "risk score".** Expiry
proximity and VIX regime are reported as two independent dimensions --
inventing a formula to merge them would be exactly the kind of
unvalidated synthesis this codebase's discipline avoids.

**Data-quality handling:** the two dimensions are independent, so a
missing/invalid VIX must not block the (always-computable) expiry
reading, and vice versa -- same "partial but honest" pattern as the
Structure Brain's one-sided wall data. `UNKNOWN`/`INSUFFICIENT` only
when BOTH dimensions fail to resolve; confidence is 1.0 when both
resolve, 0.5 when only one does.

### Real-data validation (2026-07-20, live India VIX + real 2026-07-21 expiry)

Real VIX (13.02, prev close 13.15) correctly classified `MODERATE`
(just above the LOW threshold) with `vix_change_pct` = -0.989%, FYERS's
own real number. Today (2026-07-20) against the real 2026-07-21 expiry
correctly classified `EXPIRY_EVE` (1 day to expiry).

### Files

- `bujji/intelligence/models.py` -- `ExpiryProximity`, `VixRegime`, `EventReading`
- `bujji/intelligence/event_brain.py` -- `EventBrain.analyze(...)`
- `tests/test_event_brain.py` -- 16 tests: expiry-date arithmetic
  (same-day, eve, several-days-out, inconsistent-dates refusal), VIX
  regime classification and change-%, independent-dimension partial-data
  honesty, data-quality gates, rendering (including the explicit
  "calendar events not covered" note), and a real-data regression
  fixture for the live 2026-07-20 VIX capture.

### Explicitly NOT done in this pass

Not wired into the dashboard, journal, or any production decision path
-- same standalone, observation-only status as the other six brains.
Economic-calendar events (FOMC/RBI/Budget/etc.) remain entirely
unbuilt -- there is no verified data source for them in this codebase,
and adding one (an external calendar vendor integration) is a separate,
much larger undertaking than this pass, not attempted here.


## Behaviour Brain

**Built inert by design -- this is the point, not a limitation to
apologize for.** Before writing any logic, checked the real trade
journal (`bujji/journal/journal.py`, backed by `data/bujji.db`): **zero
completed live trades**. Even counting this session's real-data
backtest runs, only ~5 correlated real trading days exist on file
(2026-07-13 to 2026-07-17, the same 5 days re-run several times) --
nowhere near independent or numerous enough to detect a genuine
behavioural pattern rather than noise. This matches (and is worse than)
the MIC status table's earlier note that Behaviour was "blocked on
history."

**Decision, made with the user rather than silently:** build the full
interface now -- models, arithmetic, tests -- but gate every single
output behind a hard trade-count floor
(`MIN_TRADES_REQUIRED = 30`, a documented first-pass rule-of-thumb
floor, not a rigorous statistical derivation for this specific
strategy). Below that floor the brain reports `UNKNOWN` across the
board -- not a partial reading, not a cautious guess with a low
confidence score. Reporting a "pattern" from 5 correlated data points
would be exactly the kind of fabrication this codebase's discipline
exists to prevent.

**Method (once the floor is met):**
- `win_rate`, `avg_pnl` -- plain arithmetic over real trade PnLs.
- `current_streak` -- signed count of the most recent consecutive
  same-direction outcomes (positive = win streak, negative = loss
  streak), classified `WINNING_STREAK` / `LOSING_STREAK` at
  `|streak| >= STREAK_ALERT_THRESHOLD` (first pass, 3), else `NORMAL`.
- `exit_reason_breakdown` -- count/win-rate/avg-pnl grouped by each
  trade's real recorded exit reason, to surface whether a specific exit
  rule is quietly underperforming.

**No real-data validation section here, unlike every other brain in the
MIC.** There is no real data to validate against yet -- that section
gets written honestly once real trades accumulate, not filled with
placeholder numbers now.

### Files

- `bujji/intelligence/models.py` -- `StreakSignal`, `BehaviourReading`
- `bujji/intelligence/behaviour_brain.py` -- `BehaviourBrain.analyze(...)`
- `tests/test_behaviour_brain.py` -- 13 tests: the hard data-quantity
  gate (zero trades, the actual ~5-day real count, one-below-threshold,
  exactly-at-threshold), arithmetic correctness on synthetic sequences
  sized above the floor (win rate, avg PnL, streak counting in both
  directions, streak classification, exit-reason grouping), and
  rendering.

### Explicitly NOT done in this pass

Not wired into the dashboard, journal, or any production decision path
-- same standalone, observation-only status as the other seven brains.
No real-data validation exists (see above) -- this is the one brain in
the MIC where that's an honest gap rather than an oversight, and it
should stay a gap until BUJJI has actually traded live for a while. The
30-trade floor itself is a first-pass placeholder, not calibrated to
this specific strategy's variance -- revisit once real data exists to
calibrate it properly.


## Dashboard Wiring (read-only view of all eight brains)

**What changed:** all eight MIC brains are now computed every candle
cycle and shown on the live dashboard, strictly as a read-only
observation panel -- no brain output feeds back into any trading
decision, order, or sizing. New files/edits:

- `bujji/intelligence/runner.py` (new) -- `run_intelligence(...)`, the
  ONLY place production code touches `bujji.intelligence`. Takes
  whatever real data is available on a cycle and returns a
  dashboard-ready dict, one entry per brain. A brain's key is simply
  ABSENT from the dict when there is no real data for it at all --
  never a fabricated placeholder reading.
- `bujji/core/runtime_status.py` -- added `RuntimeStatus.intelligence:
  dict`, populated by the orchestrator, read by the dashboard. Same
  read-only pattern as `capital_health`/`market_data_health`.
- `bujji/core/orchestrator.py` -- added a bounded (`maxlen=80`) real
  spot-candle history purely for the Regime Brain (never read by any
  trading logic); captures per-leg CE/PE premiums opportunistically in
  `_handle_in_position` (only used while a position is genuinely open --
  gated in the runner call, not by clearing stale state on exit, to keep
  the change minimal); calls `_update_intelligence()` at the very end of
  `on_candle()`, after the VWAP audit, wrapped in try/except so an
  intelligence-computation failure can NEVER affect the state machine or
  an open position.
- `bujji/dashboard/server.py` -- new "Market Intelligence Core" section:
  one card per brain, showing its most decision-relevant fields plus
  `data_quality`. A brain with no data this cycle shows "NOT AVAILABLE"
  with an honest reason (no open position / not yet wired into
  production / insufficient trade history), not a blank or fabricated
  card.

**Coverage, stated honestly:**

| Brain | Runs when |
|---|---|
| Regime | Always (needs only real spot candle history) |
| Behaviour | Always (needs only the real trade journal) |
| Event (expiry half) | Always (pure date arithmetic) |
| Event (VIX half) | **Wired (2026-07-20)** -- real India VIX fetched every cycle |
| Volatility, Premium, Greeks | Only while a real straddle position is open, with real current CE/PE premiums |
| Liquidity | **Wired (2026-07-20)** -- real bid/ask fetched for the open straddle's legs each cycle |
| Structure | **Wired (2026-07-20)** -- real option-chain OI fetched around spot each cycle |

Liquidity, Structure, and Event's VIX half were live-verified as real,
usable data sources earlier this session (Pass 15-17) but wiring the
actual broker calls into the live trading loop's data path was
explicitly deferred as a separate integration step in each of those
passes -- this dashboard-wiring pass does not change that. Their cards
will show "NOT AVAILABLE" on the live dashboard until that follow-up
work happens; this is documented, not silently broken.

**A real bug caught and fixed before this shipped:** the Premium Brain
needs an `entry_iv` to build its theta-only baseline, and the natural
temptation was to approximate one by splitting `Position.entry_price`
(the only entry premium the journal actually records -- COMBINED CE+PE,
never per-leg) 50/50 across the two legs. Caught during review and
rejected -- CE and PE premiums are rarely close to equal, so that split
would silently feed a wrong number into Greeks/theta math. Fixed to pass
`entry_iv=None` honestly; the Premium Brain's own gate reports `UNKNOWN`
with reason `no_entry_iv` instead. Capturing real separate CE/PE entry
premiums in the journal is a genuine, separate future improvement, not
attempted here.

**A second bug caught and fixed:** the first draft of `_position_context`
used `position.entry_spot` as "current spot" when computing live Greeks
and current IV -- silently stale the moment spot moved after entry.
Fixed to use the real current spot (the latest real candle close) instead,
with a regression test (`test_greeks_use_current_spot_not_entry_spot`)
that fails if this regresses.

**A third bug caught and fixed:** the dashboard's generated JavaScript had
a nested-single-quote syntax error (`'<span class='warn'>...'`) that would
have broken the whole page's script tag in a real browser. Caught by
extracting and syntax-checking the generated JS with `node --check`
before considering this done, not by visual inspection alone. Full
render was then verified end-to-end with a real DOM (`jsdom`) fed the
actual live API response -- confirmed all eight brain cards render
correctly, including the three "NOT AVAILABLE" position-dependent cards
and the "(INSUFFICIENT)" Behaviour card, with zero runtime JS errors.

**Tests:** `tests/test_intelligence_runner.py` -- 13 tests: no-crash on
totally empty input, position-dependent brains correctly absent without
a real position/real premiums/an expired contract, all three populate
correctly with a real position, the entry-IV-never-fabricated
regression, the current-spot-not-entry-spot regression, trade-row
reordering for the Behaviour Brain's streak logic, and the three
not-yet-wired brains staying null-safe. Full suite: 382/382 passing, no
regressions.

### Explicitly NOT done in this pass

Liquidity/Structure/Event-VIX are still not fed real production data --
that requires extending the broker interface with real quote/optionchain/
VIX calls in the live trading loop, a separate piece of work. The
journal does not record separate CE/PE entry premiums, so Premium Brain
will show `UNKNOWN` for every real position until that's added. No
dashboard control lets a human override or interact with any brain --
by design, this is observation only.


## Liquidity and Structure wired into the live data path

**What changed:** the broker interface (`bujji/broker/base.py`) gained
two new read-only methods -- `get_quote(contract)` (real bid/ask/spread)
and `get_option_chain(underlying, spot, strike_count)` (real per-strike
OI) -- implemented for real in `FyersBroker` using the exact endpoints
verified live in Pass 15 (Liquidity) and Pass 16 (Structure). Delegated
through `HybridPaperBroker` (fyers_paper mode, what the live service
actually runs) exactly like `get_ltp`/`get_funds` already are, and
wrapped in `ExecutionEngine.get_quote`/`get_option_chain` as
best-effort, single-attempt, never-raising calls (deliberately NOT run
through the retry/backoff path used for critical trading calls --
observational data going stale for one cycle is harmless; burning the
retry schedule on it is not).

`Orchestrator._update_intelligence` now fetches the open straddle's real
CE/PE bid/ask (when a position exists) and a real 5-strike option chain
around current spot (every cycle, position or not) before calling
`run_intelligence(...)`. Liquidity and Structure now produce genuine
`SUFFICIENT` readings on the dashboard instead of `UNKNOWN`.

**A real bug caught before this shipped:** the `optionchain` endpoint's
payload is nested under a top-level `"data"` key -- the same pattern
already known from `get_funds`/`get_order_margin` earlier this session,
but missed on the first pass here (the earlier Pass-16 live check had
been read loosely enough not to catch it). A live re-check the moment
`get_option_chain` returned an empty list on real data (rather than
trusting "0 strikes" as a plausible answer) surfaced it immediately --
confirmed with a fresh, direct inspection of the raw response, fixed,
and locked in with a regression test
(`test_get_option_chain_parses_the_nested_data_key`) using the exact
real shape.

**End-to-end proof, not just unit tests:** ran the full real pipeline
live -- `FyersBroker.get_quote`/`get_option_chain` against the real
account, straight into `run_intelligence(...)` -- and confirmed both
brains return `SUFFICIENT` with real numbers (e.g. Structure: spot
24238.5, resistance wall at 24500 (OI 11.7M), support wall at 24200
(OI 13.5M), correctly `NEAR_SUPPORT_WALL` at 0.159% distance).

**Tests added:**
- `tests/test_fyers_transport_mapping.py` -- 5 tests: `get_quote`
  parsing (verified shape, missing symbol, crossed/zero quote),
  `get_option_chain` parsing (the nested-`"data"`-key regression fixture
  above, and the empty-real-strikes case).
- `tests/test_hybrid_paper_broker.py` -- 1 test confirming both new
  methods delegate through the live leg in fyers_paper mode.
- `tests/test_execution_and_e2e.py` -- 3 tests confirming
  `ExecutionEngine`'s wrappers never raise even when the underlying
  broker call fails, and pass through cleanly on success/no-op.

Full suite: 391/391 passing, no regressions.

### Explicitly NOT done in this pass

Event's VIX half is still not fetched anywhere in the trading loop --
`get_vix()`-equivalent wiring was not part of this request and remains
a separate, explicitly deferred step. The option-chain fetch uses a
fixed `strike_count=5` around spot; widening that (or making it
configurable) is a future refinement, not attempted here.


## Event Brain's VIX half wired into the live data path

**What changed:** `Broker.get_vix()` added to the interface (default
`None`, same pattern as `get_quote`/`get_option_chain`), implemented for
real in `FyersBroker` using the exact `NSE:INDIAVIX-INDEX` quote
endpoint verified live in Pass 17. Delegated through `HybridPaperBroker`
and wrapped in `ExecutionEngine.get_vix` as a best-effort,
single-attempt, never-raising call -- identical discipline to
`get_quote`/`get_option_chain`. `Orchestrator._update_intelligence`
fetches real VIX every cycle (market-wide signal, not tied to whether a
position is open) and passes `vix_level`/`vix_change_pct` into
`run_intelligence(...)`.

Event's VIX half now produces genuine `SUFFICIENT` readings on the
dashboard. Its expiry-proximity half remains `UNKNOWN` without an open
position (it needs a resolved contract's real expiry date, which only
exists once a straddle is actually sold) -- unrelated to this change,
same pre-existing, documented behavior as before.

**No new bugs found this time** -- `get_vix` reused the exact
`_call("ltp", ...)` shape already verified and exercised by `get_quote`,
so there was no new parsing surface to get wrong.

**End-to-end proof against the real account:** `FyersBroker.get_vix()`
returned the real current India VIX (12.98, down from a real prior
close of 13.15, -1.29%) straight into `run_intelligence(...)`, correctly
classified `LOW` (just at the threshold) with `data_quality=SUFFICIENT`.

**Tests added:**
- `tests/test_fyers_transport_mapping.py` -- 4 tests: verified-shape
  parsing, missing-symbol handling, non-positive-level refusal, and
  `prev_close` omitted when missing/non-positive.
- `tests/test_hybrid_paper_broker.py` -- extended the existing
  delegation test to also cover `get_vix`.
- `tests/test_execution_and_e2e.py` -- 1 test confirming the
  never-raises discipline holds for `get_vix` too.

Full suite: 396/396 passing, no regressions.

### Coverage note

All eight brains now have their live production data feed wired in.
Every data source flagged in earlier passes as "not yet wired into
production" (Liquidity's bid/ask, Structure's option-chain OI, Event's
VIX) is now live. The only remaining structural gaps are: Behaviour
(genuinely blocked on real trade history, by design), the economic
calendar half of Event (no data source exists at all, not attempted),
and the standalone OI brain concept (never built as a separate brain --
its function is covered by Structure).
