# MSI Decision Synthesis Engine (DSE v1)
## Engineering Series 77 — Implementation

**Status:** Implemented. This document describes `bujji/msi_decision_synthesis/`,
the fusion layer that synthesizes future MSI intelligence-domain
outputs (Series 71) into one `MarketOpportunityAssessment`. No changes
were made to `mic_v2`, `bujji.mic_replay`, `bujji.production_runtime`,
`bujji.trading_brain`, `bujji.strategy_selector`, `bujji.market_observation`,
`bujji.live_market_events`, or `bujji.market_episode` — DSE v1 is a
new, isolated package that only references Series 76 `Episode` ids by
plain string, never by import.

---

## 1. Philosophy

`docs/MSI_V1_FOUNDATION.md` (Series 71) defines nine intelligence
domains and an Evidence Graph (Observation → Derived Evidence →
Reasoning Object → Published Intelligence). Domain 9, Regime
Intelligence, is the only domain in that document permitted to consume
other domains' Published Intelligence — it is a synthesis layer.

DSE is architecturally analogous to Regime Intelligence's synthesis
role, but is explicitly **not identical to it**: Regime Intelligence
produces a composite *structural regime* classification; DSE produces
a `MarketOpportunityAssessment` that additionally folds in
**strategy-family compatibility** — something Regime Intelligence does
not do (MSI_V1_FOUNDATION.md Deliverable 2, Domain 9). DSE sits one
layer above where Regime Intelligence's own output would eventually
feed in, consuming a generic placeholder for **any** MSI domain's
Published Intelligence (including a future Regime Intelligence brain's
own output), not just the eight "read" domains.

No real MSI brain exists yet (Series 71's own Gap Analysis: only
architecture, no implementation). DSE is therefore built and tested
entirely against a synthetic, domain-neutral input contract
(`DomainSignal`) — see Section 2. The engine itself never hardcodes
which brains exist; it only knows the 9 domain *names* as a disclosed
vocabulary (`taxonomy.ALL_MSI_DOMAINS`), for labeling and coverage
accounting, never for control flow branching per named domain.

**The Decision Synthesis Engine recommends opportunity classes, never
concrete trades.**

---

## 2. `DomainSignal` — the interim, domain-neutral input contract

```
DomainSignal(domain_name, state, confidence, evidence_ids)
```

This is deliberately generic: no `PriceStructureSignal`/
`VolatilitySignal` subclasses. `state` is the domain's own free-text
published classification string (e.g. `"Trending"`, `"Expanding"`,
`"Neutral"`). `confidence` is the domain's own self-reported confidence
in that classification — distinct from DSE's fused `confidence_level`.
`evidence_ids` references (never copies) the domain's own Derived
Evidence ids.

This is an **interim** shape. Once real MSI brains are built, each
will need to either publish directly in this shape or be adapted to it
by a thin translation layer outside this package — `engine.synthesize`
itself never needs to change to accommodate a new domain's arrival,
only genuinely new domain names added to `taxonomy.ALL_MSI_DOMAINS`.

---

## 3. The three-way taxonomy split: `opportunity_state` vs.
`confidence_level` vs. `opportunity_quality`

The spec's own Deliverable 2 lists ONE flat set of 10 example values
(NO_ACTION, WAIT, MONITOR, LOW_CONVICTION, HIGH_CONVICTION,
DIRECTIONAL_OPPORTUNITY, NEUTRAL_OPPORTUNITY, VOLATILITY_OPPORTUNITY,
MEAN_REVERSION_OPPORTUNITY, BREAKOUT_OPPORTUNITY) while ALSO requiring
`MarketOpportunityAssessment` to carry `opportunity_state`,
`confidence_level`, **and** `opportunity_quality` as three separate
fields. Reusing "HIGH_CONVICTION"/"LOW_CONVICTION" as `opportunity_state`
values while also needing a distinct `confidence_level` field would be
a genuine naming collision. Resolved as follows:

| Field | Question it answers | Values | Computed from |
|---|---|---|---|
| `opportunity_state` | **WHAT KIND** of opportunity, if any | `NO_ACTION, WAIT, MONITOR` (non-opportunity) + `DIRECTIONAL_OPPORTUNITY, BREAKOUT_OPPORTUNITY, VOLATILITY_OPPORTUNITY, NEUTRAL_OPPORTUNITY, MEAN_REVERSION_OPPORTUNITY` (real types) | The winning lean + type-vote tie-break (Section 4) |
| `confidence_level` | **HOW SURE** DSE is that `opportunity_state` is correct | `NONE, LOW, MODERATE, HIGH` | Agreement count vs. conflict count (Section 5) |
| `opportunity_quality` | **HOW GOOD/well-evidenced** the opportunity is, if real | `POOR, FAIR, GOOD, EXCELLENT` | Domain coverage × average domain self-confidence (Section 6) — deliberately independent of agreement/conflict |

Conflating quality and confidence would silently discard a real
distinction the spec itself calls out by listing them separately: a
single domain reporting with total conviction is not the same evidence
base as many independent domains agreeing at only moderate confidence
each — the former may have *higher* confidence (little disagreement to
speak of) but *lower* quality (thin evidentiary base). `opportunity_quality`
is computed strictly from coverage/self-confidence; `confidence_level`
is computed strictly from agreement/conflict. Neither formula reads
the other's inputs.

---

## 4. Agreement / conflict / synthesis rule (Deliverable 5)

1. Every `DomainSignal` maps to a **lean** via `config.STATE_LEAN_MAP`:
   `OPPORTUNITY_FORMING`, `NEUTRAL_FORMING`, or `AMBIGUOUS`. Domains in
   `taxonomy.PRECONDITION_DOMAINS` (`LIQUIDITY`, `TIME_STRUCTURE`) are
   **always** `AMBIGUOUS` regardless of their reported `state`, because
   MSI_V1_FOUNDATION.md Deliverable 2 explicitly defines their
   reasoning responsibility as a structural precondition/weighting
   concern, "not a signal about direction" — forcing them into the
   directional vote would fabricate a read they are not designed to
   produce. An unmapped `state` string also maps to `AMBIGUOUS` —
   never silently coerced into agreement or conflict.
2. `AMBIGUOUS` signals vote in **neither** agreement nor conflict.
   Among the remaining ("considered") signals, count
   `OPPORTUNITY_FORMING` vs. `NEUTRAL_FORMING`. The lean with strictly
   more votes wins; a tie (including 0-0) resolves to
   `NEUTRAL_FORMING` — the conservative default always wins ties, per
   MSI_V1_FOUNDATION.md's "no silent data gaps / never overclaim"
   principle.
3. `supporting_domains` = every considered domain whose lean equals the
   winning lean. `conflicting_domains` = every considered domain whose
   lean is the *other* lean. Whenever any considered domain disagrees
   with the winning lean, it is unconditionally placed in
   `conflicting_domains` — there is no code path that empties this
   list while a genuine conflict exists (contradiction is never
   suppressed).
4. If the winning lean has type-tagged supporting domains
   (`config.STATE_LEAN_MAP`'s second tuple element, e.g. `"Trending"` →
   `DIRECTIONAL_OPPORTUNITY`), `opportunity_state` is the type with the
   most votes among supporting domains, ties broken by
   `config.TYPE_TIEBREAK_ORDER` (declaration order of
   `taxonomy.OPPORTUNITY_TYPES`: DIRECTIONAL, BREAKOUT, VOLATILITY,
   NEUTRAL, MEAN_REVERSION). If there are zero considered signals,
   `opportunity_state` is `MONITOR` (or `WAIT` if there are zero
   signals at all).

---

## 5. Confidence computation (Deliverable 5)

`net = agreement_count - conflict_count`, over the *considered*
(non-ambiguous) signals only:

| Condition | `confidence_level` |
|---|---|
| zero considered signals | `NONE` |
| `net >= 2` | `HIGH` |
| `net >= 1` and `agreement_count >= 2` | `HIGH` |
| `net >= 1` | `MODERATE` |
| `net == 0` and `agreement_count >= 1` | `MODERATE` |
| otherwise | `LOW` |

**Monotonicity property (tested explicitly):** for any fixed
`agreement_count`, `confidence_level`'s rank is non-increasing as
`conflict_count` increases, and strictly decreases across a
sufficiently wide sweep — proven by
`tests/test_msi_decision_synthesis_engine.py::test_confidence_monotonic_with_conflict`.
The comparative test
`test_contradiction_preservation_and_lower_confidence` additionally
proves a real, measurable drop between an all-agreeing scenario
(`HIGH`) and a genuinely 2-vs-2 conflicting scenario (`MODERATE`) built
from the same signal count.

All thresholds above are fixed, disclosed constants in `config.py`,
never tuned against outcomes (per this project's "measure before
tuning" discipline and this sprint's explicit no-tuning-against-results
constraint).

---

## 6. Opportunity quality computation

```
coverage = len(domain_signals) / 9   # 9 = total MSI domains
score = coverage * average(domain_signal.confidence for domain_signal in domain_signals)
```

| `score` | `opportunity_quality` |
|---|---|
| `>= 0.60` | `EXCELLENT` |
| `>= 0.35` | `GOOD` |
| `>= 0.15` | `FAIR` |
| otherwise | `POOR` |

Deliberately independent of agreement/conflict (see Section 3).

---

## 7. Contradiction handling — explicit statement

**Disagreement between domains is never suppressed.** Whenever a
considered domain's lean differs from the winning lean,
`conflicting_domains` contains it — unconditionally, every synthesis
run. This is enforced structurally (there is no averaging, no
discarding, no "if conflicts, drop the minority" branch anywhere in
`engine.py`), and verified by
`tests/test_msi_decision_synthesis_engine.py::test_contradiction_preservation_and_lower_confidence`,
which constructs a genuinely disagreeing scenario and asserts
`conflicting_domains != ()`.

---

## 8. Compatibility model (Deliverable 4)

A fixed, disclosed `opportunity_state → strategy_family` table
(`engine._COMPATIBILITY_TABLE`):

| `opportunity_state` | Compatible families | Incompatible families |
|---|---|---|
| `DIRECTIONAL_OPPORTUNITY` | `DEFINED_RISK_DIRECTIONAL`, `HEDGED_DIRECTIONAL`, `DIAGONAL` | `UNDEFINED_RISK_PREMIUM`, `DEFINED_RISK_NEUTRAL` |
| `BREAKOUT_OPPORTUNITY` | `DEFINED_RISK_DIRECTIONAL`, `HEDGED_DIRECTIONAL` | `UNDEFINED_RISK_PREMIUM`, `DEFINED_RISK_NEUTRAL`, `CALENDAR` |
| `VOLATILITY_OPPORTUNITY` | `DEFINED_RISK_VOLATILITY`, `HEDGED_DIRECTIONAL` | `UNDEFINED_RISK_PREMIUM`, `CALENDAR`, `DEFINED_RISK_NEUTRAL` |
| `NEUTRAL_OPPORTUNITY` | `DEFINED_RISK_NEUTRAL`, `UNDEFINED_RISK_PREMIUM`, `CALENDAR` | `DEFINED_RISK_DIRECTIONAL`, `HEDGED_DIRECTIONAL` |
| `MEAN_REVERSION_OPPORTUNITY` | `DEFINED_RISK_NEUTRAL`, `CALENDAR`, `DIAGONAL` | `DEFINED_RISK_DIRECTIONAL`, `HEDGED_DIRECTIONAL`, `UNDEFINED_RISK_PREMIUM` |
| `NO_ACTION` / `WAIT` / `MONITOR` | *(none)* | *all 7 families* |

No concrete strategy names appear anywhere (no named option
structures) — those belong to a future Strategy Selector.

---

## 9. Explainability (Deliverable 6) — the 7 mandatory questions

Every `MarketOpportunityAssessment` is paired with a mandatory
`Explanation`, computed by `engine.build_explanation`:

1. **`why`** — which `supporting_domains` + which evidence ids produced
   `opportunity_state`, stated with real domain names and evidence ids,
   not templated prose.
2. **`why_not`** — one entry per every OTHER `opportunity_state` value,
   stating it was rejected because it was not the winning read, with
   the actual supporting/conflicting counts.
3. **`what_changed`** — `None` if there is no previous assessment;
   otherwise a real diff (`opportunity_state`/`confidence_level`/
   `opportunity_quality` changes) referencing the previous
   `assessment_id`.
4. **`domains_agreeing`** / 5. **`domains_disagreeing`** — first-class
   fields, directly populated by the same computation that produced
   `supporting_domains`/`conflicting_domains`, never left to be
   re-derived by a downstream caller.
5. **`missing_evidence`** — which of the 9 MSI domains did not
   contribute a signal this cycle at all (set difference against
   `taxonomy.ALL_MSI_DOMAINS`) — honestly absent, never fabricated.
6. **`evidence_that_would_increase_confidence`** — deterministic,
   mechanical statements only: "resolution of conflicting domain X",
   "one more agreeing domain from: [missing domains]" — never a
   speculative/ML-style suggestion.

Tested for genuine (non-empty, non-placeholder) population by
`tests/test_msi_decision_synthesis_engine.py::test_explanation_fields_all_populated`.

---

## 10. Replay / live equivalence

`runner.generate_assessments_for_cycles` (batch) and
`runner.DecisionSynthesisStream` (incremental) both delegate every
synthesis call to the same `engine.synthesize`/`engine.build_explanation`
functions, threading `previous_assessment` forward identically. Parity
holds by construction, proven by
`tests/test_msi_decision_synthesis_engine.py::test_replay_live_parity`,
which runs an identical 2-cycle sequence through both entrypoints and
asserts full equality of every produced assessment and explanation.

---

## 11. `assessment_id` determinism

`assessment_id` is a `hashlib.md5` hash over the **sorted** tuple of
input `DomainSignal` contents (`domain_name`, `state`, `confidence`,
`evidence_ids`) plus `schema_version` — never over `timestamp`, never
`uuid4()`. Sorting by `domain_name` means supplying the same signals in
a different order still produces the same id. Two identical sets of
domain signals fed through `synthesize()` at two different wall-clock
times always produce the identical `assessment_id` — proven by
`test_synthesis_determinism` and the Deliverable 10 demonstration
below.

`episode_ids` references Series 76 `Episode.episode_id` values as
**plain strings only** — no import of `bujji.market_episode`, even at
the type level. This keeps the AST isolation boundary as simple as
every prior series' (reference by id, never import the producing
package).

---

## 12. Deliverable 10 demonstration

Synthetic scenario (no real MSI brains exist yet):

```
PRICE_STRUCTURE           -> "Trending"   (confidence 0.80)
VOLATILITY_STRUCTURE      -> "Expanding"  (confidence 0.75)
LIQUIDITY                 -> "Strong"     (confidence 0.70)
OPTIONS_MARKET_STRUCTURE  -> "Neutral"    (confidence 0.60)
```

- `LIQUIDITY`'s `"Strong"` maps to `AMBIGUOUS` (a precondition domain,
  per Section 4) — it does not vote.
- Considered signals: `PRICE_STRUCTURE` (OPPORTUNITY_FORMING/DIRECTIONAL),
  `VOLATILITY_STRUCTURE` (OPPORTUNITY_FORMING/VOLATILITY),
  `OPTIONS_MARKET_STRUCTURE` (NEUTRAL_FORMING/NEUTRAL).
- **Honest resolution of "Options Structure → Neutral":** in this
  engine's design it genuinely **conflicts** with the winning read. 2
  of 3 considered domains (Price, Volatility) lean `OPPORTUNITY_FORMING`;
  Options Structure's `"Neutral"` leans `NEUTRAL_FORMING` — the
  minority read. It is placed in `conflicting_domains`, not silently
  dropped.
- Winning lean: `OPPORTUNITY_FORMING` (2 vs. 1). `supporting_domains =
  (OPTIONS_MARKET_STRUCTURE is NOT here) (PRICE_STRUCTURE,
  VOLATILITY_STRUCTURE)`. `conflicting_domains = (OPTIONS_MARKET_STRUCTURE,)`.
- Type vote: `DIRECTIONAL_OPPORTUNITY` (1, Price) vs.
  `VOLATILITY_OPPORTUNITY` (1, Volatility) — tied; tie-break order
  favors `DIRECTIONAL_OPPORTUNITY` → **`opportunity_state =
  DIRECTIONAL_OPPORTUNITY`** (directional-flavored, as expected).
- Confidence: `agreement_count=2`, `conflict_count=1`,
  `net=1`, `agreement_count>=2` → **`confidence_level = HIGH`**.
- `compatible_strategy_families` includes `DEFINED_RISK_DIRECTIONAL`
  (directional-flavored); `incompatible_strategy_families` includes
  `UNDEFINED_RISK_PREMIUM` (premium-neutral-flavored).

Run twice independently (different `timestamp` each run), verified
byte-identical `assessment_id` and full content (excluding
`timestamp`, which is deliberately not part of the hash) —
`tests/test_msi_decision_synthesis_engine.py::test_deliverable_10_demonstration_runs_twice_identically`.

Real measured output from this session:
```
run1.assessment_id == run2.assessment_id  -> True
run1.opportunity_state                    -> DIRECTIONAL_OPPORTUNITY
run1.confidence_level                     -> HIGH
run1.conflicting_domains                  -> ('OPTIONS_MARKET_STRUCTURE',)
compatible strategy families              -> includes DEFINED_RISK_DIRECTIONAL
incompatible strategy families            -> includes UNDEFINED_RISK_PREMIUM
```

---

## 13. Step 0.4 findings — `bujji.trading_brain` vocabulary reuse

Read directly (no code imported, confirmed by this package's AST
isolation test):

- `bujji/trading_brain/strategy_selector/registry.py`'s
  `StrategyDefinition.risk_profile` uses exactly `DEFINED_RISK` /
  `UNDEFINED_RISK`. **Reused** as a naming prefix for DSE's strategy
  family constants (`DEFINED_RISK_DIRECTIONAL`, `UNDEFINED_RISK_PREMIUM`,
  ...) because it is the right, already-proven distinction. No
  concrete strategy name from that registry (no named option
  structures) is referenced anywhere in DSE.
- `bujji/trading_brain/ontology/models.py`'s `TradingOntologySnapshot`
  already has an `opportunity_state` field, and
  `bujji/trading_brain/ontology/taxonomy.py` defines
  `ALL_OPPORTUNITY_STATES = (UNKNOWN, AVOID, WATCH, LOW_EDGE, MEDIUM_EDGE,
  HIGH_EDGE)`. This is an **edge-strength scale**, closer in spirit to
  what this package calls `confidence_level`/`opportunity_quality` than
  to what this package calls `opportunity_state` (a TYPE taxonomy).
  **Deliberately not reused verbatim** — reusing `HIGH_EDGE`/`LOW_EDGE`
  as `opportunity_state` values here would recreate the exact naming
  collision this document's Section 3 resolves for
  LOW_CONVICTION/HIGH_CONVICTION.
- `risk_state`'s vocabulary (`UNKNOWN, LOW, NORMAL, HIGH, EXTREME`) was
  considered and **deliberately not reused** for `confidence_level`
  either — it describes portfolio/market risk level, a different
  concept than "how much do domains agree," despite both using
  LOW/HIGH-shaped words.

**Confirmation of zero code import:** enforced by
`tests/test_msi_decision_synthesis_engine.py::test_ast_isolation_no_forbidden_imports`,
which AST-walks every file in `bujji/msi_decision_synthesis/` and
asserts no `import`/`from` statement starts with `bujji.trading_brain`
(or `mic_v2`, `bujji.mic_replay`, `bujji.production_runtime`,
`bujji.strategy_selector`, `fyers_apiv3`). Passing, verified this
session.

---

## 14. Test results (this session)

- Baseline full suite before any change: **2280 passed** (confirmed by
  running `pytest -q` from a clean checkout before writing any DSE
  code).
- New file alone (`tests/test_msi_decision_synthesis_engine.py`):
  **13 passed**.
- Full suite after adding DSE: **2293 passed** (2280 + 13, no
  regressions).
- Contradiction-preservation + comparative confidence test: passing —
  all-agreeing scenario reads `HIGH`, a genuinely 2-vs-2 conflicting
  scenario built from the same domain count reads `MODERATE`.
- Confidence-monotonicity-with-conflict test: passing — confidence
  rank is non-increasing across an increasing-conflict sweep at fixed
  agreement count, and strictly lower at the sweep's end than its
  start.
- Replay/live parity test: passing — a 2-cycle sequence run through
  `generate_assessments_for_cycles` (batch) and `DecisionSynthesisStream`
  (incremental) produces fully equal assessments and explanations.
- AST isolation (forbidden imports, no `uuid4()`/`random`/`uuid`, no
  named-option-structure or contract-parameter identifiers): passing.
- Deliverable 10 demonstration (run twice, byte-identical): passing.
