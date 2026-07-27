# Multi-Domain Consensus Intelligence (MDCI v1)
## Engineering Series 81 — Implementation

**Status:** Implemented. Describes `bujji/msi_consensus/`. No changes
were made to `mic_v2`, `bujji.mic_replay`, `bujji.production_runtime`,
`bujji.trading_brain`, `bujji.strategy_selector`, `bujji.msi_price_structure`,
`bujji.msi_market_structure`, or `bujji.msi_decision_synthesis` — MDCI
is a new, isolated package that only consumes a generic internal shape
(`engine.DomainAssessmentView`) that callers populate by translating
real (or, this sprint, one mocked) brain assessments outside this
package.

---

## 0. CRITICAL DISCLOSURE — Series 80 ("Volatility Structure") does not exist

Before any design work in this sprint, direct on-disk verification was
performed:

```
$ ssh root@139.59.76.137 "ls /opt/bujji/app/bujji/ | grep -i volatility"
(no output)

$ ssh root@139.59.76.137 "find /opt/bujji/app -iname '*volatility*'"
/opt/bujji/app/bujji/intelligence/volatility_brain.py
/opt/bujji/app/tests/test_volatility_brain.py
(+ their .pyc caches)
```

There is **no** `bujji/msi_volatility*` package, and no MSI-series
"Volatility Structure" brain of any kind. The only volatility-named
code in the repository (`bujji/intelligence/volatility_brain.py`) is a
pre-existing, architecturally unrelated legacy module — it is not part
of the `msi_price_structure`/`msi_market_structure`/
`msi_decision_synthesis` series, was not touched by this sprint, and is
not referenced anywhere in `bujji/msi_consensus/`.

The engineering series in this arc run 78 (Price Structure) → 79
(Market Structure) → **81** (this sprint, Multi-Domain Consensus) with
no Series 80 ever having been implemented. This confirms the task's
own premise gap.

**Consequence for this sprint:** `bujji/msi_consensus/engine.py` is
built to be genuinely domain-count/domain-type agnostic (its own
design mandate, independent of this gap — see Section 2). The
Deliverable 9 integration demonstration
(`tests/test_msi_consensus_intelligence.py::
test_deliverable_9_four_way_byte_identical_integration_demonstration`)
uses a clearly-named, clearly-commented
`MOCK_VOLATILITY_DOMAIN_VIEW`/`MOCK_VOLATILITY_DSE_SIGNAL` — a minimal
synthetic object shaped like what a real Volatility Structure
assessment's translation would look like, never imported from or
presented as a real brain. **Real volatility-domain participation
should be re-verified once/if a genuine Series 80 is built.**

---

## 1. Philosophy

MDCI sits **between** the MSI brains (Series 78, 79, and any future
brain — real or, today, mocked) and Series 77 Decision Synthesis. It
does **not** reinterpret any brain's conclusions. It measures how
coherent/agreeing/well-evidenced the **collection** of brain outputs
is, as a distinct quality signal — orthogonal to, and computed
independently of, any single brain's own read.

This sprint does **not** modify `bujji.msi_decision_synthesis` to
consume a `ConsensusAssessment`. `engine.synthesize()`'s real,
unmodified signature —

```
synthesize(domain_signals, previous_assessment, episode_ids, *,
           timestamp, schema_version=..., provenance=...)
```

— was read directly (`bujji/msi_decision_synthesis/engine.py:229`) and
confirmed to have **no slot** for a distinct "consensus" input; it
accepts a tuple of `DomainSignal`s and nothing else. Forcing a fake
parameter into that call, or fabricating a code path that doesn't
exist, would misrepresent how these two packages actually connect.
The Deliverable 9 demonstration therefore runs `synthesize()` **as-is**
on the domain signals, and reports the independently-computed
`ConsensusAssessment` **alongside** the resulting
`MarketOpportunityAssessment`, as two parallel outputs of the same
underlying scenario — not threaded through `synthesize()`'s parameters.
Wiring `ConsensusAssessment` into `synthesize()` (e.g. as a future
optional keyword) is left as explicit future work.

---

## 2. The lean-mapping design — MDCI's own responsibility

Series 78 (Price Structure) and Series 79 (Market Structure) publish
in fundamentally different vocabularies (`trend_state` vs.
`support_state`, `compression_state` vs. `breakout_state`, ...) — they
cannot be compared for "agreement" by string equality; there is no
shared field.

Series 77 (Decision Synthesis) solved an *analogous-looking* problem
with `config.STATE_LEAN_MAP`, reducing each domain's free-text `state`
to one of `OPPORTUNITY_FORMING` / `NEUTRAL_FORMING` / `AMBIGUOUS`
before voting (confirmed by reading
`bujji/msi_decision_synthesis/config.py` directly). **That concept —
"reduce heterogeneous domain output to a small, shared, coarser lean
before cross-domain comparison" — is directly reusable here, as a
CONCEPT.** It is not reusable as code, and was not imported as code
(this package's own isolation mandate forbids importing
`bujji.msi_decision_synthesis` at all — see `__init__.py` and the AST
isolation test): Series 77's lean vocabulary is specifically about
*opportunity formation* (is a tradeable setup emerging), a different
question from what MDCI asks (do the brains' reads, whatever they
substantively mean, point the same direction or not, and how much of
the collection is even represented).

MDCI therefore defines its **own** small, purpose-built lean
vocabulary (`bujji/msi_consensus/taxonomy.py`):

```
LEAN_BULLISH, LEAN_BEARISH, LEAN_NEUTRAL   — the three CONSIDERED leans (vote)
LEAN_AMBIGUOUS                              — never votes (mirrors Series 77's
                                               PRECONDITION_DOMAINS/AMBIGUOUS
                                               exclusion concept)
```

The reduction from a real brain's real assessment into one of these
leans is done entirely by the **caller** (a translation layer outside
`bujji.msi_consensus`, exactly like Series 79's own translation into
`DomainSignal`) — `engine.DomainAssessmentView` already expects a
pre-computed `lean`, never a raw brain assessment.

---

## 3. Agreement / conflict computation

1. Every `DomainAssessmentView` supplies a `lean`. Views whose lean is
   `AMBIGUOUS_LEANING` are excluded from the vote entirely — neither
   agreeing nor conflicting (but still count toward
   `participating_domains` and evidence/coverage accounting).
2. Among the remaining ("considered") views, the lean with strictly
   more votes wins. A tie (including an all-different split, e.g.
   1 bullish / 1 bearish / 1 neutral) resolves to `NEUTRAL_LEANING` —
   the conservative default always wins ties, mirroring Series 77's
   "never overclaim from a tie" precedent. If `NEUTRAL_LEANING` itself
   was not one of the tied candidates, this means `agreeing_domains`
   can legitimately be empty even with considered domains present
   (verified by `test_consensus_level_monotonic_with_agreement_ratio`'s
   documented mid-sweep dip — see that test's comment for why a naive
   full sweep across a flipping majority is NOT monotonic in the
   *input* variable, only in the pure banding function itself).
3. `agreeing_domains` = considered domains whose lean equals the
   winner. `conflicting_domains` = considered domains whose lean
   differs. Whenever any considered domain disagrees, it is
   unconditionally placed in `conflicting_domains` — there is no
   averaging/discarding/"drop the minority" branch anywhere in
   `engine.py`.

`ConsensusLevel` is a **pure function of `agreement_ratio`
(agreeing_count / considered_count) alone** (`config.py`'s disclosed
thresholds: `>=1.0` UNANIMOUS, `>=0.75` STRONG, `>=0.50` MODERATE,
`>0` WEAK, `<=0` or undefined NO_CONSENSUS). Monotonicity is proven
directly against this pure function over a real ratio sweep
(`test_consensus_level_monotonic_with_agreement_ratio`), plus a
second, full-engine sweep constructed so the majority never flips
mid-sweep (avoiding the tie-driven, expected non-monotonicity of a
naive `n_agree`-indexed sweep across a flipping majority).

`contradiction_density = conflicting_domain_count / considered_domain_count`
(0.0 when there are zero considered domains) — the exact disclosed
formula, complementary to `agreement_ratio` (they sum to 1.0 whenever
considered domains exist).

---

## 4. Evidence sufficiency — independent of agreement

`EvidenceSufficiency` is a **separate dimension** from `ConsensusLevel`
(Deliverable 3's explicit requirement) — two brains can agree
completely (UNANIMOUS) while the collection is still evidentially thin
(few of the expected domains participated, each citing little
evidence), or vice versa. Neither formula reads the other's inputs.

```
coverage_ratio   = participating_domain_count / expected_domain_count
evidence_density = mean( min(1.0, len(evidence_ids) / EVIDENCE_DENSITY_TARGET) )
                   over every participating domain view
sufficiency_score = coverage_ratio * evidence_density
```

Banded (`config.py`, fixed/disclosed): `>=0.75` ROBUST, `>=0.50`
ADEQUATE, `>0` LIMITED, `0.0` or zero participants INSUFFICIENT.
`EVIDENCE_DENSITY_TARGET = 3` (citing 3+ evidence ids per domain counts
as "full" density for that domain).

`missing_domains` = the configured expected-domain registry
(`config.DEFAULT_EXPECTED_DOMAINS`, reusing Series 77's disclosed
9-domain-name vocabulary **by name only**, no import) minus the
domains that actually participated this cycle — this is Deliverable
4's "incomplete domain coverage" check, feeding directly into the
`coverage_ratio` term above.

---

## 5. Confidence calibration

Measures whether a domain's **own self-reported confidence** is
internally consistent with the evidence it cites:

- `evidence_ids` count `<= 1` (thin) **and** confidence `>= 0.70`
  (claims high) → that domain's individual read is `OVERCONFIDENT`.
- `evidence_ids` count `>= 3` (dense) **and** confidence `<= 0.40`
  (claims low) → `UNDERCONFIDENT`.
- Zero cited evidence → that domain **cannot be judged** at all
  (excluded from the vote, never defaulted to `WELL_CALIBRATED`).
- Otherwise → `WELL_CALIBRATED`.

The overall `confidence_calibration` field is a majority vote across
every **judged** domain view; a tie, or zero judged domains, resolves
to `UNKNOWN` — the conservative default when there isn't enough basis
to assert a specific verdict (never fabricated).

---

## 6. Conflict topology — never resolved, only surfaced

`detect_cross_domain_contradictions` emits one `Contradiction` record
per pair of considered domain views whose leans differ — `dimension_a`/
`dimension_b` are the two conflicting **domain names** (not sub-fields
of one brain's own assessment, since MDCI measures agreement *across*
brains, not within one). There is no code path anywhere in `engine.py`
that resolves, averages, or drops a genuine conflict — proven by
`test_contradiction_preservation_real_conflict_scenario` (a genuinely
conflicting 2-domain scenario: `conflicting_domains != ()`,
`consensus_level == NO_CONSENSUS`, exactly one `Contradiction`
surfaced) and `test_contradiction_density_formula` (a 3-domain,
1-conflict scenario asserting the exact `1/3` density value).

---

## 7. Explainability — the 5 mandatory questions

Every `ConsensusAssessment` is paired with a mandatory `Explanation`
(`engine.build_explanation`), genuinely computed, never templated:

1. **`which_domains_agree`** — = `agreeing_domains`, first-class.
2. **`which_domains_disagree`** — = `conflicting_domains`, first-class.
3. **`which_evidence_is_missing`** — `missing_domains` **plus** any
   participating domain that cited zero `evidence_ids` at all.
4. **`why_consensus_is_high_or_low`** — a real computed sentence citing
   the actual agreement_ratio, considered/agreeing/conflicting counts,
   and missing-domain count.
5. **`what_additional_domains_would_increase_confidence`** —
   deterministic, mechanical statements only (missing-domain
   participation, conflicting-domain resolution, additional evidence
   for zero-evidence domains) — never speculative.

`what_changed` diffs `consensus_level`/`evidence_sufficiency`/
`confidence_calibration` against the previous assessment by id; `None`
if there was no previous assessment.

---

## 8. Replay / live equivalence

`runner.compute_consensus_for_cycles` (batch) and `runner.ConsensusStream`
(incremental) both delegate every computation to the same
`engine.compute_consensus_with_explanation`, threading
`previous_assessment` forward identically. Parity holds by
construction — proven by `test_replay_live_parity`, which runs an
identical 2-cycle sequence through both entrypoints and asserts full
equality of every field (`provenance` legitimately differs by design,
labeling which entrypoint produced the record, mirroring
`bujji.msi_market_structure.runner`'s own precedent — every other
field, including `assessment_id` and the full `Explanation`, is
asserted identical).

---

## 9. `assessment_id` determinism

`hashlib.md5` over: the sorted tuple of participating domain views'
content (`domain_name`, `lean`, `confidence` rounded to 6 places,
`evidence_ids`, `source_assessment_id`), plus the resulting
`consensus_level`/`evidence_sufficiency`/`confidence_calibration`
values, plus `schema_version` — **never** `timestamp`, **never**
`uuid4()`. Sorting by `domain_name` means the same views supplied in a
different order still produce the same id. Proven by
`test_assessment_id_deterministic_same_input_same_id` (same views, two
different wall-clock timestamps, identical id) and
`test_assessment_id_order_independent`.

---

## 10. Deliverable 9 — the integration demonstration (real measured output)

A real, deterministic `_BREAKOUT_THEN_FAILED_RETEST_PRICES` scenario
(reusing Series 79's own price sequence) produces a real, unmodified
`PriceStructureAssessment` (78) and `MarketStructureAssessment` (79)
from the same underlying episode/event/observation chain, **plus** a
clearly-marked `MOCK_VOLATILITY_DOMAIN_VIEW` / `MOCK_VOLATILITY_DSE_SIGNAL`
(see Section 0). All three are fed into MDCI's `engine` to get a real
`ConsensusAssessment`; the same two real assessments (+ mock),
separately adapted into `DomainSignal`s per Series 79's established
translation pattern, are fed into Series 77's real, unmodified
`synthesize()`. The entire pipeline is run **twice**, independently:

```
psi.assessment_id        == PSA-56edef1c3ae015491f35909c   (identical both runs)
mssi.assessment_id       == MSA-69c77da7ef6dd171cab2af79   (identical both runs)
consensus.assessment_id  == a2fdb59231038eb397475562ad84cf98  (identical both runs)
opportunity.assessment_id == MOA-2fe7a1b876b32cb9c2bcb858  (identical both runs)

consensus.consensus_level         == UNANIMOUS_CONSENSUS
consensus.evidence_sufficiency    == LIMITED
consensus.confidence_calibration  == WELL_CALIBRATED
consensus.agreeing_domains        == ('PRICE_STRUCTURE', 'SUPPORT_RESISTANCE', 'VOLATILITY_STRUCTURE')
consensus.conflicting_domains     == ()

opportunity.opportunity_state     == DIRECTIONAL_OPPORTUNITY

run1 == run2 for ALL FOUR outputs -> True
```

Test: `tests/test_msi_consensus_intelligence.py::
test_deliverable_9_four_way_byte_identical_integration_demonstration`.
This is a stronger proof than Series 79's own Deliverable-10-equivalent
demonstration (which did not compute a separate consensus object) —
all **four** independently-produced outputs are asserted byte-identical
across two runs, not just two.

---

## 11. Decision boundaries (Deliverable 10) — Consensus vs. Synthesis vs. future Strategy Selection

| Layer | Owns | Does NOT own |
|---|---|---|
| **Consensus Intelligence** (this sprint, Series 81) | Measuring agreement/conflict/evidence-sufficiency/confidence-calibration **across independent brains**, as a first-class, separately-recorded, explainable object | Fusing signals into an opportunity read; strategy-family compatibility; concrete strategy/strike selection |
| **Decision Synthesis** (Series 77) | Fusing domain signals into ONE opportunity read (`opportunity_state`) + that read's own trustworthiness (`confidence_level`) + that read's own evidentiary quality (`opportunity_quality`) + strategy-family compatibility | Measuring cross-domain agreement as its own first-class object (it only implicitly aggregates agreement into `confidence_level`, then discards the per-domain agreement detail); concrete strategy/strike selection |
| **Future Strategy Selection** | Concrete strategy/strike choice, execution-plan construction | Any of the above measurement/fusion responsibilities |

**Honest overlap-risk reasoning** (worked through, not merely
asserted): Series 77's `confidence_level` is computed from
`agreement_count`/`conflict_count` too (Section 5 of
`docs/MSI_DECISION_SYNTHESIS_ENGINE.md`) — at first glance this looks
identical to what `ConsensusLevel` measures. The actual difference: 77
computes agreement/conflict **as an internal step toward one specific
opportunity_state read**, and neither records nor exposes the
agreement computation as its own object — a caller cannot ask 77 "how
much did the brains agree" independent of "what opportunity did they
imply." 77's `confidence_level` also answers "how sure am I THIS
particular `opportunity_state` is right," a question that only makes
sense in the context of a resolved opportunity type; it collapses once
`opportunity_state` is `NO_ACTION`/`WAIT`/`MONITOR`. MDCI's
`consensus_level`/`evidence_sufficiency`, by contrast, are meaningful
and recorded **independent of any opportunity read at all** — they
describe the health of the input collection itself, and remain
well-defined (and useful for diagnosing why an opportunity read might
be unreliable) even when there is no opportunity to speak of. Put
differently: 77 could theoretically be fed by brains that already
internally reduced multi-domain input into one signal-set — but it
still would not itself expose *how coherent that reduction was* as a
separate, queryable, explainable record; that is squarely what this
sprint's `ConsensusAssessment` adds, without duplicating or
reinterpreting 77's own `opportunity_state`/`confidence_level`/
`opportunity_quality` fields (verified: neither package imports the
other's models; neither formula reads the other's output).
`opportunity_quality` (77, coverage × self-confidence, independent of
agreement) is closer in spirit to MDCI's `evidence_sufficiency`
(coverage × evidence density) than `confidence_level` is to
`consensus_level` — both pairs are related-but-distinct, computed from
overlapping raw inputs (domain count, self-reported confidence,
evidence citation counts) but for genuinely different questions (one
opportunity's own trustworthiness vs. the input collection's own
coherence as a first-class object). No code or vocabulary is shared
between the two packages beyond plain-string domain names.

---

## 12. Known limitations

- **Series 80 (Volatility Structure) does not exist.** See Section 0.
  The Deliverable 9 demonstration's third domain view is a mock,
  clearly named `MOCK_VOLATILITY_DOMAIN_VIEW`/
  `MOCK_VOLATILITY_DSE_SIGNAL`. Real volatility-domain participation
  in consensus computation should be re-verified once/if a genuine
  Series 80 brain is built and a real translation layer is written for
  it.
- `engine.py` is NOT wired into `bujji.msi_decision_synthesis` —
  `synthesize()` remains completely unmodified; `ConsensusAssessment`
  is computed and reported alongside it, never consumed by it. Future
  work, if pursued, would need an explicit design decision about
  whether/how 77 should read a `ConsensusAssessment`.
- The lean-mapping translation from a real brain's assessment into a
  `DomainAssessmentView.lean` lives entirely in caller code (tests,
  future runtime wiring) — this package intentionally has no opinion
  on how any specific brain's states map to `BULLISH_LEANING`/
  `BEARISH_LEANING`/`NEUTRAL_LEANING`/`AMBIGUOUS_LEANING`; a poorly
  designed translation layer could produce a misleading consensus read
  even though the underlying engine logic is correct.
- The full-engine monotonicity sweep found (and documents, in
  `test_consensus_level_monotonic_with_agreement_ratio`) a real,
  expected non-monotonicity artifact when a naive sweep is indexed by
  `n_agree` across a majority that flips mid-sweep (a 2-2 tie among 4
  considered domains resolves to `NEUTRAL_LEANING`, which nothing
  voted for, producing `agreement_ratio == 0`). This is not a bug — it
  is the same "ties resolve conservatively" design also present in
  Series 77 — but it means monotonicity only holds against the true
  independent variable (`agreement_ratio` itself, or a sweep
  constructed so the majority never flips), not against every possible
  parameterization of a test scenario.
