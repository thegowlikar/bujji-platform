# MSI Trade Intent — BUJJI Engineering Series 83

## Philosophy

Trade Intent converts a permissible strategy family (Series 82's
`StrategyEligibilityAssessment`) into a concrete description of what a
trade should EXPRESS: its delta lean, its vega posture, whether it
collects or pays premium, its risk shape, and what would invalidate it
before any construction work begins. It is purely descriptive of
INTENT. It never selects strikes, expiry, or size, and it performs no
scoring, optimization, or P&L prediction of any kind.

**Trade Intent describes what a trade should express. It never
constructs or executes the trade.**

## Check 1 — "Strategy Selection" does not exist (full finding)

The task specification's pipeline diagram and Deliverable 4 both
reference "Strategy Selection (83)" as an already-existing prior stage
that this sprint should consume via a `StrategySelectionAssessment`
type. This was verified, directly against the live repository at
`/opt/bujji/app`, to be **incorrect** — the same kind of gap as the
previously-confirmed-absent Series 80 (Volatility Structure):

```
$ ls bujji/ | grep -iE 'strategy_selection|strategy.selector'
(no output matching "strategy_selection")
$ find bujji -iname '*StrategySelectionAssessment*' -o -iname '*msi_strategy_selection*'
(no output)
```

`bujji/trading_brain/strategy_selector/` DOES exist on disk, but it is
a dormant, unrelated legacy module from an earlier, different arc of
this project — not part of the MSI 77-83 series (confirmed: nothing in
`bujji.trading_brain` is imported by the live app or by any MSI-series
package). The most recently completed real series is **Series 82 —
Strategy Eligibility** (`bujji/msi_strategy_eligibility/`), which
produces `StrategyEligibilityAssessment` with
`eligible_strategy_families`/`ineligible_strategy_families` — it
narrows the solution space down to a SET of eligible families; it does
NOT select ONE.

**Resolution:** Series 83 consumes Series 82's REAL
`StrategyEligibilityAssessment` directly (the actual, most-recent, real
upstream output) — the same "downstream consumer, not sibling
isolation" exception 82 itself used for consuming 77 and 81. Since
Deliverable 4 explicitly requires converting "a selected strategy
family" (singular) into intent, and no real selector exists to produce
one, `bujji/msi_trade_intent/engine.py` implements a MINIMAL, clearly
disclosed, deterministic placeholder —
`_placeholder_select_one_eligible_family` — that applies a fixed,
alphabetical priority order over the eligible set
(`config.FAMILY_SELECTION_PRIORITY_ORDER`) to pick exactly one family.
It is NOT a real strategy selector: no scoring, no optimization, no
preference for expected quality. It exists purely so Trade Intent has
something concrete to build intent around. A real Strategy Selector
remains genuine future work (Series 84 or later). Trade Intent itself
stays scope-limited to Deliverables 2-6 — this sprint does not become a
hidden strategy-selection sprint.

## Why Trade Intent consumes BOTH `StrategyEligibilityAssessment` and `MarketOpportunityAssessment`

Deliverable 4's literal text says "consume only
StrategySelectionAssessment." Once resolved (above) to consume 82's
real `StrategyEligibilityAssessment` instead, a family name alone
(e.g. `DEFINED_RISK_DIRECTIONAL`) does not carry enough real,
checkable context: Deliverable 5's invalidation conditions must be
able to reference "the opportunity_state changing away from the one
that produced this intent" and cite the real
`MarketOpportunityAssessment.assessment_id` — information
`StrategyEligibilityAssessment` only references by ID string, not by
live field value. `determine_trade_intent()` therefore accepts BOTH
real types directly, mirroring the exact "downstream consumer, not
peer" exception Series 82 established for consuming 77 and 81's public
output types — no adapter, no test-only translation layer.

## Check 1b — a second, deeper honest gap discovered during Step 0 study

While studying whether `market_bias` (LONG_DELTA/SHORT_DELTA/
DELTA_NEUTRAL) could be derived from real opportunity context (a
directional family like `DEFINED_RISK_DIRECTIONAL` doesn't itself say
WHICH direction), a direct reading of
`bujji/msi_decision_synthesis/models.py` and
`bujji/msi_consensus/models.py` showed that **neither real upstream
type stores an aggregate bullish/bearish directional-lean field at
all**:

- `MarketOpportunityAssessment` has `opportunity_state` (a TYPE of
  opportunity, e.g. `"DIRECTIONAL_OPPORTUNITY"`, never a direction),
  `confidence_level`, `opportunity_quality`, `supporting_domains`/
  `conflicting_domains` (domain NAMES), and
  `compatible_strategy_families`/`incompatible_strategy_families`
  (family NAMES). No bullish/bearish field anywhere.
- `ConsensusAssessment` has `agreeing_domains`/`conflicting_domains`
  (domain NAMES) and no stored aggregate `lean` value at all — the
  per-domain `lean` values that feed `compute_consensus()` are
  transient, CALLER-supplied inputs (`DomainAssessmentView.lean`) and
  are never retained on the output `ConsensusAssessment` object.

**Resolution:** `market_bias` cannot be honestly derived as
LONG_DELTA/SHORT_DELTA from real upstream evidence today — doing so
would fabricate a directional read neither real upstream type
provides. `engine.derive_market_bias()` deterministically returns
`MARKET_BIAS_DELTA_NEUTRAL` for every strategy family, disclosed
prominently in `taxonomy.py`'s and `engine.py`'s module docstrings and
proven by
`tests/test_msi_trade_intent.py::test_market_bias_defaults_deterministically_absent_real_directional_signal`,
which replaces the originally-specified but unbuildable "opposite-
direction opportunity" test (that test would have required constructing
two real `MarketOpportunityAssessment` objects differing in a
directional field that does not exist on the real type). This is a
genuine, disclosed limitation of Series 83, not a hardcoded shortcut
hidden from the reader — future work is a real upstream series
publishing an aggregate directional-lean field, at which point
`derive_market_bias` can be revised to consult it.

## The intent model

Each dimension of `TradeIntentAssessment` and how it's derived:

- **`selected_strategy_family`** — the one family
  `_placeholder_select_one_eligible_family` picked from
  `eligibility.eligible_strategy_families`, per the disclosed
  alphabetical priority order.
- **`intent_state`** — one of `FORMED`/`AWAITING_CONSTRUCTION`/
  `INVALIDATED`. `determine_trade_intent()` only ever returns `FORMED`
  (or `None`). `AWAITING_CONSTRUCTION` and `INVALIDATED` are part of
  the disclosed state VOCABULARY (Deliverable 2's requirement) for a
  future revalidation/transition process (Series 84+) that would
  re-check a formed intent's invalidation conditions against fresh
  upstream evidence over time — genuinely out of this sprint's scope.
- **`market_bias`** — see Check 1b above: always `DELTA_NEUTRAL` today,
  honestly disclosed.
- **`volatility_bias`** / **`premium_exposure`** /
  **`directional_exposure`** / **`risk_profile`** — a fixed, disclosed,
  deterministic mapping (`config.FAMILY_INTENT_PROFILE`) from
  conventional options-structure domain knowledge for each of the 8
  strategy families (e.g. `SHORT_VOLATILITY` → collects premium, short
  vega, undefined risk; `CALENDAR`/`DIAGONAL` → pays premium, net long
  vega, defined risk). Never fit or learned — a reviewed, disclosed
  table.
- **`invalidation_conditions`** — three genuinely mechanical, checkable
  `InvalidationCondition` records per formed intent, referencing real
  upstream fields by name: `eligibility_confidence` dropping below
  MODERATE, `eligible_strategy_families` no longer containing the
  selected family, and `opportunity_state` changing away from the
  state that produced this intent. Never empty for a formed intent.
- **`supporting_assessment_ids`** — the real
  `StrategyEligibilityAssessment.assessment_id` and
  `MarketOpportunityAssessment.assessment_id`, referenced (never
  copied).

### `market_bias` vs. `directional_exposure`

Deliverable 2 lists both as separate fields. Resolved as two genuinely
different dimensions: `market_bias` is the qualitative delta LEAN (see
Check 1b — currently always `DELTA_NEUTRAL`); `directional_exposure`
is a STRUCTURAL characterization of the position's exposure SHAPE
(`DEFINED`/`UNDEFINED`/`HEDGED`), mirroring the DEFINED_RISK/
UNDEFINED_RISK/HEDGED_DIRECTIONAL family concepts from 77/82 but
reframed as an exposure-shape property of the resulting intent, not a
family label. Forcing these to mean the same thing would collapse a
real distinction: two `DEFINED_RISK_DIRECTIONAL` intents could, in
principle, differ in `market_bias` (once a real directional-lean field
exists upstream) while always sharing `directional_exposure=DEFINED`.

### `volatility_bias` naming collision across packages

Series 82's taxonomy has `FAMILY_LONG_VOLATILITY`/
`FAMILY_SHORT_VOLATILITY` — STRATEGY FAMILY names. This package's
`VOLATILITY_BIAS_LONG`/`VOLATILITY_BIAS_SHORT` use the identical
string spelling (`"LONG_VOLATILITY"`/`"SHORT_VOLATILITY"`) for an
INTENT DIMENSION — a genuinely different concept (this trade's net
vega posture, not a family label). Proof the concepts diverge: the
`CALENDAR` family (not a "volatility" family in 82's taxonomy at all)
maps to `VOLATILITY_BIAS_LONG` in this package's
`FAMILY_INTENT_PROFILE`. This is a real, disclosed naming-overlap risk
across packages — acceptable only because no caller in this codebase
directly compares or unions `StrategyEligibilityAssessment.
eligible_strategy_families` with `TradeIntentAssessment.
volatility_bias`; a future caller must not assume string equality
implies conceptual equality.

## Relationship to Strategy Eligibility (82) and future Strike Selection

Strategy Eligibility (82) answers "which strategy families are
permissible, given the opportunity and the coherence of the
understanding behind it?" Trade Intent (83) answers "given ONE
(placeholder-selected) permissible family, what should this trade
EXPRESS — what exposures, what risk shape, what would invalidate it?"
Neither stage touches concrete strikes, expiry dates, contract
quantities, or order construction. A future Strike Selection stage
would still need, beyond Trade Intent's output: the concrete option
chain/contract universe, strike and expiry selection logic, position
sizing, and order construction/execution — none of which exist yet and
none of which this package fabricates or approximates.

## Replay/live equivalence

Mirrors Series 82's own resolved precedent exactly (see `runner.py`'s
module docstring in full): `engine.determine_trade_intent()` is a
PURE, STATELESS function of exactly one `(eligibility, opportunity)`
pair — it has no `previous_assessment` parameter, because
`TradeIntentAssessment` carries no `what_changed`-style field and there
is no principled definition of "the previous trade intent" independent
of "the previous eligibility/opportunity pair." The parity property
tested is therefore determinism-under-repetition: the same pair, fed
through the batch entrypoint
(`determine_trade_intent_for_cycles`) and the incremental/streaming
entrypoint (`TradeIntentStream.handle_pair`), at two different
wall-clock timestamps, produces byte-identical results for every field
except `timestamp`. Both entrypoints delegate to the exact same pure
`engine.determine_trade_intent` function per pair, so parity holds by
construction — proven by
`tests/test_msi_trade_intent.py::test_batch_vs_incremental_parity` and
`test_same_pair_fed_twice_is_byte_identical_regardless_of_entrypoint`.

## Known limitations

1. **Series 80 (Volatility Structure) does not exist.** Reconfirmed:
   `ls bujji/ | grep -i volatility` finds nothing under the MSI series.
   Only the unrelated legacy `bujji.intelligence.volatility_brain`
   module exists. Every demonstration involving "volatility" in this
   sprint's own Deliverable 10 uses the same disclosed
   `MOCK_VOLATILITY_DOMAIN_VIEW`/`MOCK_VOLATILITY_DSE_SIGNAL` pattern
   Series 81/82 established.
2. **No real "Strategy Selection" stage / `StrategySelectionAssessment`
   type exists.** See Check 1 above in full — this sprint uses a
   disclosed, non-scoring placeholder selection function instead.
3. **No real upstream aggregate directional-lean field exists.** See
   Check 1b above in full — `market_bias` is honestly always
   `DELTA_NEUTRAL` today, not fabricated LONG_DELTA/SHORT_DELTA.
4. **`FAMILY_INTENT_PROFILE`'s per-family volatility/premium/
   directional/risk mapping is a disclosed, reviewed table built from
   conventional options-structure domain knowledge** (e.g. "a short
   straddle/strangle collects premium, is short vega, and carries
   undefined risk") -- it is not fit, tuned, scored, or learned, and it
   is deliberately coarse (one profile per family, not per specific
   structure within a family).
5. **The Deliverable 10 pipeline demonstration in this sprint's own
   fixed price walk produces `trade_intent = None`** (the REDUCED
   coherence gate + `DIRECTIONAL_OPPORTUNITY` base-eligible set yields
   an empty eligible family set for that specific scenario) — this is
   a real, honestly-observed result of the real upstream engines, not
   a bug; the dedicated unit tests
   (`test_assessment_id_deterministic_same_input_same_id`,
   `test_invalidation_conditions_never_empty_and_reference_real_fields`,
   etc.) separately demonstrate real intent FORMATION with a
   non-empty, real eligible family set.

## Trade intent vs. trade implementation

**A `TradeIntentAssessment` is a description of what a trade should
express — its delta lean, vega posture, premium direction, risk
shape, and invalidation conditions. It is never a trade. It contains
no strike price, no expiry date, no contract quantity, no lot size, no
order ID, and no execution plan of any kind — those all belong to
future, not-yet-built stages (Strike Selection, Position Sizing, Order
Construction, Execution).** This boundary is enforced by
`tests/test_msi_trade_intent.py`'s
`test_no_concrete_strike_expiry_or_execution_identifiers_in_package`
and `test_ast_isolation_no_uuid4_no_unseeded_randomness_no_forbidden_terms`.
