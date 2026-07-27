# MSI Strategy Eligibility Intelligence (SEI) — BUJJI Engineering Series 82

## Philosophy

Strategy Eligibility defines the permissible solution space. It never
selects a trade.

Given a synthesized opportunity read (Series 77, `MarketOpportunityAssessment`)
and a measurement of how coherent the underlying multi-domain
understanding backing that read is (Series 81, `ConsensusAssessment`),
SEI determines WHICH strategy families are eligible to be considered
at all this cycle, and which are not. It performs no scoring, no
optimization, no probability-of-profit estimation, and names no
concrete strategy, strike, expiry, or size. A future Strategy Selector
still has to: pick ONE specific family from the eligible set, then a
concrete strategy within that family, then strikes/expiry/sizing —
none of which SEI does.

## Step 0 findings (Checks 1–3)

### Check 1 — Series 80 status (reconfirmed)

```
$ ssh root@139.59.76.137 "ls /opt/bujji/app/bujji/ | grep -i volatility"
(no output)
$ ssh root@139.59.76.137 "find /opt/bujji/app/bujji -iname '*msi_volatility*'"
(no output)
$ ssh root@139.59.76.137 "find /opt/bujji/app/bujji -iname '*volatility_brain*'"
bujji/intelligence/volatility_brain.py
```

Series 80 ("Volatility Structure") does not exist. Only the unrelated,
pre-existing legacy `bujji/intelligence/volatility_brain.py` module is
present. Every demonstration in this sprint that needs a "volatility"
domain view uses the SAME disclosed mock pattern Series 81 established:
`MOCK_VOLATILITY_DOMAIN_VIEW` / `MOCK_VOLATILITY_DSE_SIGNAL`, clearly
named, clearly commented, never imported from any real brain.

### Check 2 — overlap with Series 77's `compatible_strategy_families`

Series 77's `MarketOpportunityAssessment.compatible_strategy_families`/
`incompatible_strategy_families` are a SINGLE synthesis-time judgment,
computed as a byproduct of fusing domain signals into ONE opportunity
conclusion. Confirmed by reading `bujji/msi_decision_synthesis/engine.py`'s
`synthesize()` signature directly:

```python
def synthesize(
    domain_signals: Tuple[DomainSignal, ...],
    previous_assessment: Optional[MarketOpportunityAssessment],
    episode_ids: Tuple[str, ...],
    *, timestamp: str, schema_version=..., provenance=...,
) -> MarketOpportunityAssessment:
```

There is no `ConsensusAssessment` parameter, and per Series 81's own
documented design, 81 was built to sit ALONGSIDE 77, not feed into it
— this sprint does not change that.

**Resolution:** SEI's `StrategyEligibilityAssessment.eligible_strategy_families`/
`ineligible_strategy_families` is a DEDICATED, SEPARATELY EXPLAINED
layer that additionally factors in Consensus's coherence-of-
understanding signal (`consensus_level`, `evidence_sufficiency`) —
information 77 structurally cannot see. SEI is therefore strictly
downstream of, and richer than, 77's own family fields — not a
duplicate.

**Strongest evidence, measured directly** (`tests/
test_msi_strategy_eligibility.py::
test_low_coherence_overrides_confident_opportunity`): a HIGH-confidence
`DIRECTIONAL_OPPORTUNITY` read, paired with `WEAK_CONSENSUS` +
`INSUFFICIENT` evidence sufficiency, produces:

```
eligible_strategy_families == ()
eligibility_confidence == NONE
contradictions: 1+ (opportunity confidence vs. consensus level)
```

Series 77 alone, fed only the same opportunity-side domain signals,
would still report `HIGH` confidence and a non-empty
`compatible_strategy_families` — it has no way to see that the
underlying multi-domain agreement is weak. This is the case that
proves the two fields answer genuinely different questions.

Also observed directly in the Deliverable 10 run (see below): a real
`DIRECTIONAL_OPPORTUNITY`/`HIGH`-confidence read paired with real
`UNANIMOUS_CONSENSUS` but only `LIMITED` evidence_sufficiency produced
`eligible_strategy_families == ()` and `eligibility_confidence ==
LOW` — SEI's REDUCED-coherence gate correctly withheld eligibility
even though consensus itself was unanimous, because evidence
sufficiency was thin. (See "Known limitations" below for the specific
consequence of this interaction for DIRECTIONAL opportunities, which
is a real, disclosed configuration choice, not a bug.)

### Check 3 — "no adapters" vs. AST isolation

Every prior brain (78/79/81) enforces mutual PEER isolation: none may
import another peer's real model type, because each independently
reads the same underlying market data and none should leak internal
representation into another's reasoning.

**Resolution:** SEI is not a peer of 77 or 81 — it is STRICTLY
DOWNSTREAM of both. Series 77's `MarketOpportunityAssessment` and
Series 81's `ConsensusAssessment` already encapsulate/summarize Price
Structure (78)/Market Structure (79)/Volatility (80, mocked)
information. Consuming 77's and 81's PUBLIC output types directly is
therefore an explicit, deliberate, ONE-DIRECTION downstream dependency
— not the "never import a sibling's code" rule. `engine.py`'s real
entrypoint, `determine_eligibility(opportunity: MarketOpportunityAssessment,
consensus: ConsensusAssessment) -> StrategyEligibilityAssessment`,
accepts the real, unmodified types directly — no test-only adapter is
needed to go from real 77/81 outputs into 82, satisfying Deliverable
10's literal "without adapters" requirement.

SEI still does NOT import `bujji.msi_price_structure` or
`bujji.msi_market_structure` directly — those are already summarized
two levels up by 77/81's own outputs, and reaching past them would
duplicate reasoning 77/81 already did, with no principled benefit.

This makes SEI's own AST isolation test meaningfully different from
every prior brain's: it must explicitly ALLOW
`bujji.msi_decision_synthesis` and `bujji.msi_consensus` imports (and
prove `engine.py` genuinely uses them) while still forbidding
`bujji.msi_price_structure`/`bujji.msi_market_structure` and every
`mic_v2`/`trading_brain`/`strategy_selector`/`fyers_apiv3` term every
prior brain forbids.

## Family-taxonomy mapping (77 ↔ 82)

Series 77's `ALL_STRATEGY_FAMILIES` (`bujji/msi_decision_synthesis/taxonomy.py`)
has a single `DEFINED_RISK_VOLATILITY` bucket. This sprint's
Deliverable 3 spec calls for a finer split, `LONG_VOLATILITY`/
`SHORT_VOLATILITY`. SEI uses ITS OWN taxonomy exactly as specified,
documented against 77's via an explicit mapping (never silent
divergence):

| SEI family (`msi_strategy_eligibility.taxonomy`) | Nearest 77 family (`msi_decision_synthesis.taxonomy`) | Relationship |
|---|---|---|
| `DEFINED_RISK_DIRECTIONAL` | `DEFINED_RISK_DIRECTIONAL` | identical concept |
| `DEFINED_RISK_NEUTRAL` | `DEFINED_RISK_NEUTRAL` | identical concept |
| `UNDEFINED_RISK_PREMIUM` | `UNDEFINED_RISK_PREMIUM` | identical concept |
| `LONG_VOLATILITY` | `DEFINED_RISK_VOLATILITY` | refinement — "buy vol" half of 77's single bucket |
| `SHORT_VOLATILITY` | `DEFINED_RISK_VOLATILITY` | refinement — "sell vol" half of 77's single bucket |
| `CALENDAR` | `CALENDAR` | identical concept |
| `DIAGONAL` | `DIAGONAL` | identical concept |
| `HEDGED_DIRECTIONAL` | `HEDGED_DIRECTIONAL` | identical concept |

Encoded programmatically as `taxonomy.FAMILY_TO_DSE_FAMILY` — a
documentation/tooling mapping, never consulted by `engine.py`'s
eligibility logic itself (eligibility is computed from
`opportunity_state`/`confidence_level`/consensus fields, never by
copying or re-deriving 77's own family fields).

## Eligibility model (real rules, `engine.py`)

1. **Base-eligible family set per `opportunity_state`** — a fixed
   mapping (`_BASE_ELIGIBLE_BY_STATE`) from each of 77's five real
   opportunity types (`DIRECTIONAL_OPPORTUNITY`, `BREAKOUT_OPPORTUNITY`,
   `VOLATILITY_OPPORTUNITY`, `NEUTRAL_OPPORTUNITY`,
   `MEAN_REVERSION_OPPORTUNITY`) plus the three non-opportunity states
   (`NO_ACTION`/`WAIT`/`MONITOR`, always empty) to the families that
   make sense given the KIND of opportunity.

2. **Coherence gate** (`_coherence_gate`) — a deterministic function of
   `consensus.consensus_level` rank, `consensus.evidence_sufficiency`
   rank, and `opportunity.confidence_level` rank (all against fixed,
   disclosed thresholds in `config.py`):
   - `NONE`: opportunity confidence below `LOW`, OR consensus below
     `WEAK_CONSENSUS`, OR sufficiency below `LIMITED` → zero eligible
     families, `eligibility_confidence = NONE`.
   - `REDUCED`: passes the `NONE` floor but doesn't clear
     `STRONG_CONSENSUS` + `ADEQUATE` sufficiency → only the
     conservative fallback family (`DEFINED_RISK_NEUTRAL`) survives,
     and only if it was part of the opportunity type's own
     base-eligible set; `eligibility_confidence = LOW`.
   - `NORMAL`: clears both floors → the full base-eligible set for
     that `opportunity_state`; `eligibility_confidence` scales with
     `min(opportunity_rank, consensus_rank)`, reaching `HIGH` only when
     opportunity confidence is `HIGH` AND consensus is `STRONG` or
     `UNANIMOUS`.

3. **Contradiction detection** (`detect_contradictions`) — surfaces,
   never resolves: (a) confident opportunity (`MODERATE`/`HIGH`) atop
   `WEAK_CONSENSUS`/`NO_CONSENSUS`; (b) `HIGH`-confidence opportunity
   with non-empty `consensus.conflicting_domains`; (c)
   `HIGH`-confidence opportunity atop `INSUFFICIENT` evidence
   sufficiency.

### Worked example — low coherence overrides confident opportunity

Real measured test output
(`test_low_coherence_overrides_confident_opportunity`):

- Input: `opportunity_state=DIRECTIONAL_OPPORTUNITY`,
  `confidence_level=HIGH`; `consensus_level=WEAK_CONSENSUS`,
  `evidence_sufficiency=INSUFFICIENT`.
- Output: `eligible_strategy_families=()`,
  `eligibility_confidence=NONE`, 1+ contradiction recorded.

This is the exact scenario Check 2 predicted would prove SEI does
something 77 alone cannot: 77's own `confidence_level` for this
opportunity would remain `HIGH` (77 never sees the `ConsensusAssessment`
at all), but SEI correctly withholds all eligibility because the
independently-measured cross-domain coherence backing that read is too
weak to license confident family selection.

## Replay/live equivalence — parity framing (resolved)

Series 78/79/81's runners thread a `previous_assessment` through a
SEQUENCE of cycles, so their batch/incremental parity means "replaying
a sequence at once == feeding the same cycles one at a time live,"
meaningful because each cycle's output depends on the prior cycle's.
`engine.determine_eligibility()` is different in kind — it is a PURE,
STATELESS function of exactly one `(opportunity, consensus)` pair, with
no `previous_assessment` parameter at all (there is no principled
"previous eligibility assessment" independent of "the previous
opportunity/consensus pair," unlike 77/81's own genuinely-threaded
state).

**Resolution:** the meaningful parity property for SEI is "the same
`(opportunity, consensus)` pair, fed via the batch entrypoint
(`runner.determine_eligibility_for_cycles`) and via the
streaming/incremental entrypoint (`runner.StrategyEligibilityStream`),
at different wall-clock timestamps, produces byte-identical results
for every field except `timestamp`" — determinism-under-repetition, not
sequence-threading parity. Both entrypoints exist because a real
caller does need both operational shapes (offline replay over many
cycles vs. a live per-cycle call), but both delegate to the exact same
pure `engine.determine_eligibility` function per pair, so parity holds
by construction. Proven by
`test_batch_vs_incremental_parity`/`test_same_pair_fed_twice_is_byte_identical_regardless_of_entrypoint`.

## Known limitations

- **Series 80 (Volatility Structure) does not exist.** Every
  demonstration involving "volatility" in this sprint (and in Series
  81 before it) uses a disclosed mock domain view/signal, never a real
  brain. This is disclosed prominently here and in the test file's
  module docstring.
- SEI's `REDUCED`-gate conservative fallback set is currently just
  `DEFINED_RISK_NEUTRAL`. For a `DIRECTIONAL_OPPORTUNITY` (whose
  base-eligible set does not include `DEFINED_RISK_NEUTRAL`), a
  `REDUCED` gate therefore yields ZERO eligible families rather than a
  reduced-but-non-empty directional set — observed directly in the
  Deliverable 10 run (`UNANIMOUS_CONSENSUS` + `LIMITED` sufficiency →
  `eligible_strategy_families == ()`). This is a deliberate,
  conservative default (when in doubt under reduced evidence,
  eligibility defaults to nothing rather than to a directional
  family), disclosed here rather than silently accepted.
- SEI performs no scoring, ranking, or preference among eligible
  families — it treats the eligible set as a flat, unordered
  permissible-solution-space boundary. A future Strategy Selector must
  still: pick ONE specific family from that set, then a concrete
  strategy within that family, then strikes/expiry/sizing.
- SEI does not currently consume Price Structure (78)/Market Structure
  (79) directly, even though they are technically accessible — by
  design, per Check 3's scoping decision, since 77/81's outputs already
  summarize them.

Strategy Eligibility defines the permissible solution space. It never
selects a trade.
