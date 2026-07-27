# MSI Brain 2: Market Structure Intelligence (MSSI v1) — BUJJI Engineering Series 79

## Philosophy

`bujji/msi_market_structure/` is the second genuine reasoning brain in
the project, built directly on the pattern Series 78
(`bujji/msi_price_structure/`) established: deterministic content-hash
ids, immutable frozen-dataclass records, evidence-by-reference-only
lineage, mandatory computed `Explanation`, typed (never free-text)
`Contradiction`s, and replay/live parity by construction (both
entrypoints delegate to one pure `engine.assess_market_structure`
function).

Series 78 answers **HOW price is behaving** — trend, swing,
compression, expansion, balance (Deliverable 1's Trend/Swing/
Compression/Expansion/Balance concepts, domain 1: Price Structure
Intelligence). MSSI answers a genuinely different question: **WHERE
price is located relative to structure** — support, resistance,
breakout, breakdown, retest, rejection, structural balance
(Deliverable 2 domain 2: Support & Resistance Intelligence, reasoned
against Deliverable 1's Acceptance/Rejection/Auction concepts). Per
`docs/MSI_V1_FOUNDATION.md`: "S/R is defined structurally in terms of
swings, which only Price Structure produces" — a hard dependency in
spirit, though this v1, like Series 78, only shares the same
underlying raw evidence stream (Episodes/MarketEvents), not each
other's published output (no code import between the two packages).

Purely descriptive. No strategy, strike, direction-prediction, or
probability-of-profit vocabulary anywhere in this package — enforced
by AST isolation tests.

## Step 0.6 overlap-check finding (disclosed)

`bujji/msi_price_structure/engine.py` was read in full before writing
any MSSI code. It tracks only:
- delta **sign** runs (trend_state, swing_state),
- delta **magnitude** runs (compression_state, expansion_state),
- a net-displacement **ratio** (balance_state).

It never identifies a price *level*, never counts a *test*/*touch* of
a level, and never asks whether price *broke* or *retested* a prior
level. **Confirmed: no overlap.** This package's level-registry
machinery (`identify_structural_levels` and everything derived from
it) is genuinely new territory, not a re-implementation of anything
Series 78 already does.

The one place a naming collision risk existed is `StructuralBalance`
(this package) vs. `BalanceState` (78). Resolved by deliberate scoping:
78's `balance_state` asks "is directional price *movement* two-sided"
— a ratio over deltas, trend-scale, with no concept of a level at all.
`StructuralBalance` here asks a different, level-scale question — "is
current price presently *contained inside* a recognized
support/resistance range" — which requires the level registry 78 does
not build and could not answer even in principle. The two fields can
disagree freely (e.g. `balance_state=IMBALANCED` while
`structural_balance=RANGE_BOUND` — a trending move that is nonetheless
still inside an old established range) without contradiction, because
they are answering different questions.

## Dimension taxonomy

All independent, non-mutually-exclusive dimensions (Deliverable 4):

| Field | Values | Basis |
|---|---|---|
| `support_state` | `NONE, WEAK, DEVELOPING, ESTABLISHED` | nearest live (unbroken) support level's test count |
| `resistance_state` | `NONE, WEAK, DEVELOPING, ESTABLISHED` | nearest live (unbroken) resistance level's test count |
| `breakout_state` | `NONE, DEVELOPING, CONFIRMED, FAILED` | most recent decisive break of a resistance level |
| `breakdown_state` | `NONE, DEVELOPING, CONFIRMED, FAILED` | most recent decisive break of a support level (mirror-image of breakout, kept as a SEPARATE field rather than one signed field, because one evidence window can in principle carry evidence of an old breakout of one level and a newer breakdown of a different level — collapsing them would force a false choice) |
| `retest_state` | `NONE, ACTIVE, CONFIRMED, FAILED` | whether price returned to, then held or crossed back through, the most recently broken level |
| `rejection_state` | `NONE, WEAK, STRONG` | see below — not given a value set by the spec, designed here |
| `structural_balance` | `UNKNOWN, RANGE_BOUND, UNBOUNDED` | is current price inside a range bounded by BOTH an established support and an established resistance — see below |
| `structure_location` | `UNKNOWN, INSIDE_RANGE, NEAR_SUPPORT, NEAR_RESISTANCE, ABOVE_RESISTANCE, BELOW_SUPPORT, AT_RETEST` | the composite/overall read, see below |
| `confidence` | `NONE, LOW, MODERATE, HIGH` | evidence-count base level minus one rank per contradiction |

### RejectionState — designed from first principles

Deliverable 4's examples did not give `RejectionState` a value set.
Designed directly from Deliverable 1's Rejection concept ("price
visiting a level and being quickly, forcefully returned from it ...
indicating disagreement rather than acceptance"):

- `NONE` — no unbroken level has ever been tested.
- `WEAK` — the strongest live (unbroken) level has been tested exactly
  once and held (one forceful reversal off it).
- `STRONG` — the strongest live level has been tested 2+ times and
  held every time (the level has never once been accepted through —
  repeated forceful reversals, the clearest Deliverable 1 Rejection
  signature).

### StructuralBalance — designed to avoid the 78 collision

`UNKNOWN` (no levels identified at all), `RANGE_BOUND` (current price
sits between a nearest-support and nearest-resistance level, BOTH of
which independently read `ESTABLISHED`), `UNBOUNDED` (no such
double-established containment exists). See the Step 0.6 section above
for why this is scoped differently from 78's `balance_state`.

### StructureLocation — the composite, mirroring 78's pattern

Deterministically derived (`engine.derive_structure_location`) from
the independent dimensions plus current price's proximity to the level
registry, in this priority order: an active retest in progress
(`AT_RETEST`) > a confirmed breakout/breakdown (`ABOVE_RESISTANCE` /
`BELOW_SUPPORT`) > range-bound containment (`INSIDE_RANGE`) > proximity
to the nearest live resistance/support (`NEAR_RESISTANCE` /
`NEAR_SUPPORT`) > `UNKNOWN`. Deliberately excludes trend/swing/
compression/expansion vocabulary entirely — those are Series 78's
job.

## Level identification approach

`engine.identify_structural_levels` — per
`docs/MSI_V1_FOUNDATION.md`'s Support & Resistance domain: "identify
where the market has previously demonstrated acceptance or rejection
... a ranked set of active levels, each tagged with strength/age/test
count." A **local price extreme** (strictly higher/lower than both
immediate neighbors in the ordered, deduplicated price sequence) is the
minimal, disclosed, price-only swing-point primitive available without
real volume/OI participation data (Options/Futures/Liquidity brains do
not exist yet) — the same "necessarily approximate, disclosed" posture
Series 78 takes for compression/expansion.

Two genuine plumbing subtleties surfaced by actually running this
against real Series 73A/75/76 constructors:

1. **Duplicate price points across event types.** A single underlying
   price move can emit MORE THAN ONE Series-75 `MarketEvent` (e.g.
   `PRICE_CHANGED` *and* `PRICE_GAP_DETECTED` for the same
   old→new transition — "a gap is a large change, not a different kind
   of change," per `live_market_events.taxonomy`). Naively walking
   every event duplicates the same price point consecutively, which
   breaks the strict-inequality local-extreme test. Fixed by
   `engine._dedup_consecutive_price_events`, which collapses
   consecutive duplicate prices to their first representative event.
2. **Two different key names for "current price."** `PRICE_CHANGED`/
   `PRICE_GAP_DETECTED` carry `detail.new_price`; `NEW_SESSION_HIGH`/
   `NEW_SESSION_LOW` carry `detail.new_value` — confirmed by reading
   `bujji.live_market_events.engine` directly, not assumed.
   `engine._current_price` reads both.
3. **The walk's true starting price.** A `PRICE_CHANGED`-family event
   only exists when there is a *previous* observation to compare
   against, so the very first raw price a caller observed is never
   itself represented as an event — it only appears as the first
   event's `old_price`/`old_value`. Without prepending it back
   (`engine._prices_with_baseline`), a genuine local extreme at the
   second observed price (e.g. a spike-then-reversal) could never be
   recognized (it would always be missing its left neighbor).

Test-count thresholds (fixed, disclosed, `config.py`):
`SUPPORT_WEAK_MIN_TESTS=1`, `SUPPORT_DEVELOPING_MIN_TESTS=2`,
`SUPPORT_ESTABLISHED_MIN_TESTS=3` (mirrored for resistance).
`LEVEL_PROXIMITY_FRACTION=0.005` (price within 0.5% of a level counts
as "at" it). `BREAK_DECISIVE_FRACTION=0.003` (a close beyond the level
by more than 0.3% counts as a decisive break, distinct from a mere
touch). `BREAK_CONFIRM_MIN_SUSTAIN_EVENTS=2` (a break must sustain for
2+ subsequent events, clearly beyond the proximity band, to read
`CONFIRMED`).

## Derivation logic per dimension

- **`derive_support_state`/`derive_resistance_state`** — the nearest
  *live* (unbroken) level of the respective type; its own test count
  maps directly to the four-value scale.
- **`derive_breakout_state`/`derive_breakdown_state`** — the most
  recent break (by formation order) of a resistance/support level;
  `FAILED` if price crossed clearly back to the pre-break side without
  ever confirming, `CONFIRMED` if the break sustained 2+ events beyond
  the proximity band, `DEVELOPING` otherwise.
- **`derive_retest_state`** — the most recently broken level's own
  retest read: `ACTIVE` the moment price returns within the proximity
  band of the broken level from the new side, `CONFIRMED` if it then
  moves back away sustaining beyond, `FAILED` if it instead crosses
  clearly back through to the pre-break side.
- **`derive_rejection_state`** — see RejectionState section above.
- **`derive_structural_balance`** — see StructuralBalance section
  above.
- **`derive_structure_location`** — the composite, see StructureLocation
  section above.
- **`detect_contradictions`** — see next section.
- **`compute_confidence`** — evidence-count base level
  (`config.CONFIDENCE_EVIDENCE_THRESHOLDS`, keyed on identified level
  count) minus one rank per contradiction, floored at `NONE`. Proven
  monotonic non-increasing in contradiction count by
  `tests/test_msi_market_structure_intelligence.py::test_confidence_decreases_monotonically_with_contradictions`.
- **`build_explanation`** — every field genuinely computed: `why` is a
  per-dimension reasoning sentence, `what_changed` diffs field-by-field
  against `previous_assessment`, `missing_evidence` and
  `would_increase_confidence` are deterministic, mechanical statements
  derived from actual level/contradiction counts.

## Contradiction scenario (real, structurally-sound)

The spec asked us to think through whether "simultaneous
`BREAKOUT_CONFIRMED` and `RETEST_FAILED`" is a genuine contradiction or
a coherent sequence, and to design a real case, not a forced one.
**Resolution: it is a genuine contradiction.** A `CONFIRMED` breakout
asserts the broken resistance level should now hold as *support* on any
retest (Acceptance, in Deliverable 1's vocabulary — the market has
"agreed" the old resistance is now fair value from above). A `FAILED`
retest is direct, same-window evidence that it did *not* hold — price
crossed back through the identical level. The two reads disagree about
the structural status of the same level over the same evidence window;
surfacing this as a `Contradiction` (never hiding it) is exactly what
Deliverable 5 requires. The mirror case
(`BREAKDOWN_CONFIRMED`+`RETEST_FAILED`) is implemented identically.
Two further contradictions are detected: `RESISTANCE_ESTABLISHED`
simultaneous with `BREAKOUT_CONFIRMED` (a decisively broken level
cannot simultaneously be reported as the nearest live, established
one), mirrored for `SUPPORT_ESTABLISHED`/`BREAKDOWN_CONFIRMED`.

Proven with a real, deterministic synthetic price walk
(`_BREAKOUT_THEN_FAILED_RETEST_PRICES = [100, 105, 100, 110, 105, 115,
120, 125, 110, 105]`) built through the real Series 73A/75/76
constructors: this sequence genuinely establishes a resistance level at
110, breaks it decisively upward, sustains for 2+ events (confirming
the breakout), then crosses back through 110 (failing the retest) —
`test_contradiction_breakout_confirmed_vs_retest_failed_real_scenario`
asserts `breakout_state == BREAKOUT_CONFIRMED`,
`retest_state == RETEST_FAILED`, and the contradiction is present.

## Evidence lineage

Identical convention to Series 78:
`supporting_episode_ids`/`supporting_event_ids`/
`supporting_observation_ids` are populated by walking the input
`Episode`s' own `originating_event_ids`/`originating_observation_ids`
— reference by id only, never a payload copy.
`test_evidence_lineage_traces_to_real_observations` builds a real
`Observation -> MarketEvent -> Episode` chain via the actual Series
73A/75/76 constructors and asserts every id MSSI reports is a genuine
subset of what the underlying Episodes actually reference.

## Replay / live equivalence

`runner.assess_market_structure_for_episodes` (batch) and
`runner.MarketStructureStream` (incremental) both delegate every
assessment to the single `engine.assess_market_structure` pure
function. Parity holds by construction and is proven by
`test_replay_live_parity`.

## Evidence-driven, never time-driven

No time-advance entrypoint exists anywhere in this package (no
`advance_time`, no `handle_time_advance`) — mirrors Series 78 exactly.
`test_evidence_driven_not_time_driven` proves re-invoking
`assess_market_structure` on an unchanged `(episodes, events)` pair,
with only `timestamp` advanced by a month, produces an identical
`structure_location` and `assessment_id`, and additionally asserts
neither `engine` nor `runner` exposes an `advance_time` attribute.

## `assessment_id` determinism

`assessment_id = "MSA-" + md5(sorted(episode_ids) + all eight dimension
values + confidence + schema_version)[:24]` — never `timestamp`, never
`uuid4()`. Proven by `test_assessment_determinism_same_input_same_id`.

## Deliverable 10 — genuine three-brain composition (78 + 79 + 77)

`test_deliverable_10_three_brain_demonstration_deterministic_and_synthesizes`
builds ONE real, deterministic synthetic price walk through the real
Series 73A/75/76 constructors, produces BOTH a real
`PriceStructureAssessment` (Series 78's real, unmodified
`assess_price_structure_for_episodes`) AND a real
`MarketStructureAssessment` (this sprint's engine) from the SAME
underlying episode/event/observation chain, adapts BOTH into
`DomainSignal`s via thin translation layers living ONLY in the test
(never inside either brain package — exactly the pattern Series 78's
own demonstration established, and exactly what
`bujji.msi_decision_synthesis.models`' docstring anticipates future
brains would need), plus a third mock `DomainSignal` standing in for
Liquidity, and feeds all three into Series 77's real, unmodified
`synthesize()`. The whole three-brain pipeline is run TWICE
independently: both brains' final `assessment_id`s are byte-identical
across runs, AND the resulting `MarketOpportunityAssessment` is fully
byte-identical (`opp1 == opp2`) — the stronger composition proof this
sprint requires, beyond Series 78's own (78+77 only) integration test.

## Known limitations (honest disclosure)

1. **Level identification is necessarily approximate.** No real
   volume/OI participation evidence exists yet (Options Market
   Structure/Futures Structure/Liquidity brains are not built), so
   local price extremes are the only available proxy for
   acceptance/rejection at a level — a real Support & Resistance
   engine would weight levels by participation (volume/OI), not price
   shape alone. Disclosed in `config.py`, `engine.py`, and every
   assessment's `Explanation.missing_evidence`.
2. **Single-instrument scope, structural not stored** — inherited
   directly from Series 75/76/78's own precedent (INV-8): one call
   processes one instrument's stream by construction, never by a
   stored/compared field.
3. **No options, futures, or cross-asset evidence** — by design
   (Deliverable 2 domain 2's inputs are Price Structure's swing
   sequence plus participation evidence where available; this v1 has
   no participation evidence source yet).
4. **Level age/decay is not modeled.** A level formed very early in a
   long walk is treated identically to one formed recently — no
   "staleness" discount exists yet, a genuine simplification the real
   domain (per Deliverable 2: "tagged with strength/age/test count")
   calls for but this v1 does not implement.

## Avoiding duplication of Series 78 (summary)

See the Step 0.6 section above for the full disclosure: confirmed, by
reading Series 78's real `engine.py`, that it tracks zero level/
support/resistance/breakout/retest concepts. The one deliberate,
disclosed near-collision (`StructuralBalance` vs. `balance_state`) is
resolved by scoping StructuralBalance strictly to level-scale
containment, never movement-direction two-sidedness.

## File tree

```
bujji/msi_market_structure/
├── __init__.py
├── taxonomy.py        # SupportState/ResistanceState/BreakoutState/... + VALID_STRUCTURE_LOCATION_TRANSITIONS
├── models.py           # Contradiction, Explanation, MarketStructureAssessment (frozen dataclasses)
├── config.py            # schema version, disclosed fixed thresholds
├── engine.py            # identify_structural_levels, derive_*, detect_contradictions, compute_confidence, build_explanation, assess_market_structure
├── serialization.py     # deterministic JSON round-trip
├── query.py              # read-only lookups by id/structure_location/time-range/episode
├── runner.py             # assess_market_structure_for_episodes (batch), MarketStructureStream (incremental)
└── journal.py            # MarketStructureJournal — append-only JSONL recorder

tests/test_msi_market_structure_intelligence.py
```
