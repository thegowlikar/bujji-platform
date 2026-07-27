# MSI Brain 1: Price Structure Intelligence (PSI v1) — BUJJI Engineering Series 78

## Philosophy

`bujji/msi_price_structure/` is the first genuine **reasoning** brain in
the project. Series 73A–77 built transport and synthesis-framework
infrastructure (`Observation -> MarketEvent -> Episode -> generic
DomainSignal fusion`) — none of that layer *interpreted* price into a
first-principles market concept. PSI does: it consumes Series 76
`Episode` objects (plus the Series 75 `MarketEvent` objects they
reference) and produces a `PriceStructureAssessment` — a genuine
interpretation of price structure using the first-principles vocabulary
from `docs/MSI_V1_FOUNDATION.md` Deliverable 1 (Trend, Swing,
Compression, Expansion, Balance/Imbalance), never indicators-as-
intelligence (Guiding Principle 1).

Still purely descriptive: no strategy, strike, direction prediction, or
probability-of-profit vocabulary anywhere in this package — enforced by
AST isolation tests, not just prose.

## Step 0.7 findings — dormant `trading_brain` inspection

`bujji/trading_brain/market_state/models.py`'s `MarketStateAssessment`
carries `market_state, market_phase, market_character, market_conviction,
confidence, supporting_evidence, contradicting_evidence, reasoning_trace,
interpretation_id`. `bujji/trading_brain/evidence_interpreter/` was also
inspected. **Vocabulary reused (concept only, not code or field names
verbatim):** the idea of a composite/overall state field derived from
finer per-dimension reads, and mandatory `contradicting_evidence`/
`reasoning_trace`-style transparency, both echoed in
`PriceStructureAssessment.structure_state`/`contradictions`/
`Explanation`. **Not reused:** the specific field names
`market_phase`/`market_character`/`market_conviction` were deliberately
NOT copied — PSI's own Deliverable-2-mandated field list
(`structure_state, trend_state, swing_state, compression_state,
expansion_state, balance_state, structure_integrity`) is more granular
and domain-specific, and copying trading_brain's flatter 4-field shape
would have discarded that granularity. **Zero code import**: confirmed
by `test_ast_isolation_no_forbidden_imports`, which fails the whole
suite if any `bujji.msi_price_structure/*.py` file imports
`bujji.trading_brain` (or `mic_v2`, `bujji.mic_replay`,
`bujji.production_runtime`, `bujji.strategy_selector`, `fyers_apiv3`).

## The single-vs-multi-dimension `structure_state` resolution

`docs/MSI_V1_FOUNDATION.md` Deliverable 3 gives ONE flat `StructureState`
enum: `{IMPULSE, CORRECTION, COMPRESSION, EXPANSION, BALANCE,
TRANSITIONING, UNKNOWN}` — read literally, a single linear state
machine. Deliverable 2's own Price Structure field list, however,
calls for `structure_state, trend_state, swing_state, compression_state,
expansion_state, balance_state` as SEPARATE fields on one assessment.
Building real reasoning against the flat enum surfaced exactly the
problem Deliverable 2's field list already anticipates: **Compression
and Expansion are sibling, opposite "potential energy" conditions
(Deliverable 1) that can coexist with a Trend or Correction read** — a
trend losing range/momentum before its next leg ("compressing inside a
trend") is a common, real pattern that a single mutually-exclusive
enum cannot represent without silently discarding one of the two true
conclusions every time it occurs.

**Resolution:** `structure_state` (`taxonomy.ALL_STRUCTURE_STATES` =
`UNKNOWN, BALANCE, TRENDING, CORRECTING, TRANSITIONING`) is kept as a
real, top-level composite/overall read — a single trader-facing
headline classification is still genuinely useful — but it
**deliberately excludes COMPRESSION/EXPANSION as values**. Those live
only in their own independent `compression_state`/`expansion_state`
fields (each `NOT_DETECTED/EARLY/CONFIRMED`), which can carry any value
regardless of what `structure_state` currently reads. `structure_state`
is deterministically derived from `trend_state` + `balance_state` only
(`engine.derive_structure_state`):

| trend_state | balance_state | -> structure_state |
|---|---|---|
| ESTABLISHED_TREND | (any) | TRENDING |
| WEAKENING_TREND | (any) | CORRECTING |
| (not established/weakening) | IN_BALANCE | BALANCE |
| EMERGING_TREND | (not IN_BALANCE) | TRANSITIONING |
| NO_TREND | IMBALANCED / TRANSITIONING | TRANSITIONING |
| NO_TREND | UNKNOWN | UNKNOWN |

This is a **disclosed deviation** from the literal Deliverable 3 enum
text (which lists IMPULSE/COMPRESSION/EXPANSION as `structure_state`
values) — the deviation exists precisely because the literal single
enum cannot represent trend+compression coexistence, and Deliverable
2's own separate-field list is the stronger, more specific spec text
this design honors instead. `IMPULSE`/`ACCEPTANCE`/`REJECTION`
(Deliverable 1 concepts) are not modeled at all in PSI v1 — they need
multi-swing/level structure this v1's single-instrument, event-delta-
only evidence stream cannot yet support; noted as a known limitation
below.

## Per-dimension derivation logic

All derivation functions are pure, in `engine.py`, one concern per
function, operating over the ordered sequence of price-structure
`MarketEvent`s referenced (transitively) by the input `Episode`s
(`PRICE_CHANGED`, `PRICE_GAP_DETECTED`, `NEW_SESSION_HIGH`,
`NEW_SESSION_LOW` — the only Series 75 event types carrying a
`detail.delta`; see `taxonomy.PRICE_STRUCTURE_EVENT_TYPES`).

- **`derive_swing_state`** (Deliverable 1: "a local extreme confirmed
  by subsequent OPPOSING structure") — `CONFIRMED` the instant the
  trailing two deltas' signs differ (a reversal just occurred);
  `FORMING` if fewer than 2 events or no reversal yet; `NO_SWING_DATA`
  if fewer than 2 price events exist at all.
- **`derive_trend_state`** (Deliverable 1: "a directional sequence of
  higher/lower swing structure the market is currently extending") —
  the trailing run length of same-signed deltas: run >= 3 ->
  `ESTABLISHED_TREND`, run == 2 -> `EMERGING_TREND`. If the latest
  delta just broke a prior run that was itself >= 2 long, that reads
  as `WEAKENING_TREND` (momentum loss, Deliverable 1's Momentum/
  Exhaustion concepts) rather than `NO_TREND`.
- **`derive_compression_state` / `derive_expansion_state`** — trailing
  monotonic run of `|delta|` magnitudes (shrinking -> compression,
  growing -> expansion); run >= 3 -> `CONFIRMED`, run == 2 -> `EARLY`.
  **Necessarily approximate** — see Known Limitations.
- **`derive_balance_state`** (Deliverable 1: Balance/Imbalance/Auction)
  — `balance_ratio = |sum(deltas)| / sum(|deltas|)`. Near 0 means price
  oscillated and net-cancelled (`IN_BALANCE`); near 1 means every move
  was the same direction (`IMBALANCED`); the fixed cut points
  (`config.BALANCE_RATIO_LOW_THRESHOLD = 0.35`,
  `BALANCE_RATIO_HIGH_THRESHOLD = 0.65`) are disclosed, not tuned.
- **`derive_structure_state`** — see the table above.
- **`detect_contradictions`** — three genuine structural checks:
  ESTABLISHED_TREND vs. IN_BALANCE; a trend read with `NO_SWING_DATA`
  underneath it; COMPRESSION_CONFIRMED vs. EXPANSION_CONFIRMED
  simultaneously. Surfaced as `Contradiction` records, never hidden.
- **`derive_structure_integrity`** — `COHERENT` (0 contradictions),
  `PARTIALLY_COHERENT` (1), `CONFLICTED` (2+).
- **`compute_confidence`** — evidence-count base level
  (`config.CONFIDENCE_EVIDENCE_THRESHOLDS`) minus one rank per
  contradiction, floored at `NONE`. Proven monotonic non-increasing in
  contradiction count by
  `tests/test_msi_price_structure_intelligence.py::test_confidence_decreases_monotonically_with_contradictions`,
  mirroring Series 77's own confidence-decreases-with-contradiction
  property.
- **`build_explanation`** — every field genuinely computed from the
  real trace: `why` is a per-dimension reasoning sentence (not one
  opaque string), `what_changed` diffs against `previous_assessment`
  field-by-field, `missing_evidence` and `would_increase_confidence`
  are deterministic, mechanical statements derived from the actual
  evidence count/contradiction count — never templated filler.

## Evidence lineage

`PriceStructureAssessment.supporting_episode_ids` /
`supporting_event_ids` / `supporting_observation_ids` are populated by
walking the input `Episode`s' own `originating_event_ids` /
`originating_observation_ids` (Series 76's transitive collection from
Series 75's `MarketEvent.originating_observation_ids`, itself from
Series 73A `Observation.identity.observation_id`) — reference by id
only, never a payload copy, exactly like every prior series.
`test_evidence_lineage_traces_to_real_observations` builds a real
`Observation -> MarketEvent -> Episode` chain via the actual Series
73A/75/76 constructors (`market_observation.engine.build_observation`,
`live_market_events.engine.detect_price_change`,
`market_episode.engine.process_event`/`advance_time`) and asserts every
id PSI reports is a genuine subset of what the underlying Episodes
actually reference.

## The evidence-driven, never time-driven property

Unlike `bujji.market_episode.runner`, this package has **no
time-advance entrypoint at all** — no `advance_time`, no
`handle_time_advance`. `assess_price_structure`'s only external inputs
are `episodes`/`events` (evidence) plus a `timestamp` argument that is
metadata-only (recorded on the assessment, excluded from
`assessment_id`). `test_evidence_driven_not_time_driven` proves that
re-invoking `assess_price_structure` on an unchanged `(episodes,
events)` pair, with only `timestamp` advanced by a month, produces an
identical `structure_state` and `assessment_id` — and additionally
asserts neither `engine` nor `runner` even exposes an `advance_time`
attribute, so the "never time-driven" property is enforced structurally,
not merely by convention.

## Replay / live parity

`runner.assess_price_structure_for_episodes` (batch) and
`runner.PriceStructureStream` (incremental) both delegate every
assessment to the single `engine.assess_price_structure` pure function
— there is exactly one reasoning implementation in this package.
Parity holds by construction and is proven by
`test_replay_live_parity`: identical `assessment_id` and all six
dimension states (plus `contradictions`) between the two entrypoints
for an identical input walk.

## `assessment_id` determinism

`assessment_id = "PSA-" + md5(sorted(episode_ids) + all six dimension
values + structure_integrity + confidence + schema_version)[:24]` —
never `timestamp`, never `uuid4()`. Proven by
`test_assessment_determinism_same_input_same_id` (same input, two
different `timestamp` args, byte-identical id) and the Deliverable 10
demonstration (`test_deliverable_10_demonstration_deterministic_and_synthesizes`,
identical `prices` walked through two independent
`Observation->MarketEvent->Episode->PriceStructureAssessment` chains,
every produced assessment's full `assessment_to_dict()` byte-identical).

## Genuine Series 77 integration (Deliverable 10, Step 3)

The Deliverable 10 test also adapts the final `PriceStructureAssessment`
into a `bujji.msi_decision_synthesis.models.DomainSignal`
(`domain_name=DOMAIN_PRICE_STRUCTURE`), constructs two mock
`DomainSignal`s standing in for Volatility Structure / Liquidity, and
feeds all three through Series 77's REAL, unmodified
`bujji.msi_decision_synthesis.engine.synthesize()`, asserting a real
`MarketOpportunityAssessment` comes back with `PRICE_STRUCTURE` counted
in either `supporting_domains` or `conflicting_domains`. **One
adaptation nuance surfaced by actually running this integration**: PSI's
own `structure_state` vocabulary (`UNKNOWN/BALANCE/TRENDING/
CORRECTING/TRANSITIONING`) is not the same closed vocabulary DSE's
`config.STATE_LEAN_MAP` recognizes (Title-case: `"Trending"`,
`"Balanced"`, `"Range"`, `"Neutral"`); a small translation dict lives
**only in the test**, never inside `bujji.msi_price_structure` — this
is exactly the "thin translation layer outside this package" Series
77's own `models.py` docstring already anticipated future brains would
need.

## Known limitations (honest disclosure)

1. **Compression/Expansion detection is necessarily approximate.**
   Real range/volatility data (the Volatility Structure brain, MSI
   Brain 3 per Deliverable 8) does not exist yet. This engine uses
   `|price-delta|` magnitude of consecutive `PRICE_CHANGED`-family
   events as the only available price-only proxy for range
   contraction/expansion — genuinely informative, but not the same
   thing as true range/IV-based compression detection. Disclosed in
   `config.py`, `engine.py`, and in every assessment's
   `Explanation.missing_evidence`.
2. **Impulse/Correction/Acceptance/Rejection (Deliverable 1) are not
   modeled in PSI v1.** `trend_state`'s `WEAKENING_TREND` value is the
   closest proxy to "correction," but true Impulse/Acceptance/Rejection
   reasoning needs multi-swing and level-revisit structure (Support &
   Resistance's job, MSI Brain 2) this v1 does not attempt.
3. **Single-instrument scope, structural not stored** — inherited
   directly from Series 75/76's own INV-8 precedent: `episodes`/
   `events` passed into one `assess_price_structure` call are assumed
   to be one instrument's stream; there is no stored `instrument`
   field to check.
4. **No support/resistance, options, futures, or cross-asset evidence**
   — by design (Deliverable 2, domain 1: "does not consume options,
   futures, or volatility data ... so its conclusions are independently
   falsifiable against every other domain").

## File tree

```
bujji/msi_price_structure/
├── __init__.py
├── taxonomy.py        # StructureState/TrendState/SwingState/... + VALID_STRUCTURE_TRANSITIONS
├── models.py           # Contradiction, Explanation, PriceStructureAssessment (frozen dataclasses)
├── config.py            # schema version, disclosed fixed thresholds
├── engine.py            # derive_*, detect_contradictions, compute_confidence, build_explanation, assess_price_structure
├── serialization.py     # deterministic JSON round-trip
├── query.py              # read-only lookups by id/structure_state/time-range/episode
├── runner.py             # assess_price_structure_for_episodes (batch), PriceStructureStream (incremental)
└── journal.py            # PriceStructureJournal — append-only JSONL recorder

tests/test_msi_price_structure_intelligence.py
```

---

## Series 85 addendum — `trend_direction_signal` (purely additive)

Series 84's investigation (`docs/DIRECTIONAL_OWNERSHIP_INVESTIGATION.md`) found that
`derive_trend_state` computes a signed value on every price delta internally
(the trailing run's sign, via `_sign()`), but never exposed it — `trend_state`
(NO_TREND/EMERGING_TREND/ESTABLISHED_TREND/WEAKENING_TREND) only encodes
run-length/persistence, so TRENDING_UP and TRENDING_DOWN were indistinguishable
from the public field alone.

Series 85 (Market Direction Intelligence) added a new function,
`derive_trend_direction_signal(price_events, trend_state)`, which re-reads the
same deterministic delta-sign sequence `derive_trend_state` already computes
and exposes the trailing run's sign as `taxonomy.DIRECTION_UP` /
`taxonomy.DIRECTION_DOWN`, or `None` when `trend_state == TREND_NONE` (no run
exists to have a sign at all).

This is a **purely additive** change:
- `derive_trend_state`'s own logic, return values, and taxonomy are byte-for-byte
  unchanged.
- `PriceStructureAssessment` gained one new field, `trend_direction_signal:
  Optional[str] = None`, defaulting to `None` so no existing caller/test needed
  to change.
- `schema_version` was bumped `1.0.0 -> 1.1.0`; `RECOGNIZED_SCHEMA_VERSIONS` still
  includes `1.0.0` for backward compatibility.
- `assessment_id`'s content-hash now includes `trend_direction_signal` (empty
  string when `None`) — this is a deliberate, disclosed hash-input change, not
  an accident; it remains fully deterministic (same inputs -> same id, always).

Full Series 78 test suite re-run after this change: **16/16 passed**, zero
regressions. Full BUJJI suite re-run: **2373/2373 passed**, zero regressions.

This field exists to serve `bujji.msi_market_direction` (Series 85)'s Price
Structure lens — see `docs/MARKET_DIRECTION_INTELLIGENCE.md` for how it's
consumed. `bujji.msi_market_structure` (Series 79) needed **no equivalent
change**: its `breakout_state`/`breakdown_state` fields were already genuinely
directional (confirmed breaks of resistance/support), just not yet consumed
anywhere for direction — see that sprint's own addendum-free status in Series
85's documentation.
