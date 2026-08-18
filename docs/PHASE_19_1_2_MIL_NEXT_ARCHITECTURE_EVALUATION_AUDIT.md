# Phase 19.1.2 — MIL Next Architecture Evaluation Audit

**Status: AUDIT ONLY. Zero code changes.**

**The decisive finding of this audit**: `mil_next` is not a
competing, unpromoted implementation of the same thing Phase 19.1
designed. It is a **deliberately self-contained LIVE-STREAMING data-
quality laboratory**, explicitly scoped away from the real ingestion
chain by its own documented design, that never passed its own
stated promotion gate — not an abandoned duplicate, a **prototype
that stopped short of the finish line it named for itself.** Its
`MarketIntelligenceSnapshot` OUTPUT vocabulary (thesis, contradiction,
posture, regime composition) is genuinely more mature than anything in
the 6 brains. Its INPUT model (transport latency, clock skew between
live feeds, sequence-gap detection) does not apply to historical,
already-certified, immutable Reality data at all — because those are
inherently live-streaming concerns, not reconstruction concerns.

---

## 1. `mil_next` Data Model — Read in Full

**Fields**: `MarketIntelligenceSnapshot` (confirmed, Phase 19.1.1)
carries `snapshot_id`, `cadence_id`, `session_id`, `schema_version`,
`decision_cutoff_event_time`, `idempotency_key`, `content_hash`,
`active_config_versions`, `timeframe_config_version`, `data_quality`,
`event_time_provenance`/`receipt_time_provenance`, `timeframe_states`,
`timeframe_agreement`, `composed_regime`, `primary_thesis`/
`competing_thesis`, `contradiction_score`, `oi_context`,
`volatility_posture`/`liquidity_posture`, `structural_conflict`,
`known_event_risk_state`, `invalidation_conditions`, `posture`/
`posture_reasons`, `version`.

**Identity**: `_snapshot_id()` = `"MILS-" + sha256(session_id|cadence_id|
decision_cutoff_event_time)[:20]` — deterministic, content-seeded, real.
`_idempotency_key()` = a plain, human-readable composite string of the
same three inputs — a second, distinct identity concept from
`snapshot_id` itself (both derived from the same three inputs, serving
different consumers: a hash for storage keys, a readable string for
dedup logic).

**Hashing**: `canonical_hash.compute_content_hash()` — SHA-256 over a
`json.dumps(..., sort_keys=True)` payload, with an explicit exclusion
list (`content_hash`, `snapshot_id`, `idempotency_key`,
`event_time_provenance`, `receipt_time_provenance`, plus five
wall-clock/audit-only `data_quality` sub-fields). **This is,
independently, the exact same design philosophy as
`MarketRealitySnapshot.fingerprint()`** (Phase 18.3): hash the DECISION
content, exclude wall-clock/identity/version metadata, document every
exclusion with a stated reason. Two engineers, at two different points
in this project's history, converged on the identical correct answer —
strong, convergent validation that the approach itself is right,
independent of which implementation is kept.

**Lineage**: `event_time_provenance`/`receipt_time_provenance` dicts —
a live-streaming concept (when did the exchange say this happened vs.
when did Bujji's process receive it) with **no equivalent need** in
historical reconstruction, where `HistoricalObservation`'s own
`retrieved_at`/lineage fields (Phase 17H.2) already answer the
analogous "when did Bujji learn this" question for batch-ingested data.

**Revision model**: `MILSnapshotRevision` — `original_idempotency_key`,
`revision_id`, `revision_reason`, `revised_at`, `superseding_content_hash`.
**Genuinely more complete than Phase 18.14's own `parent_artifact_id`**
— it names WHY a revision happened (`revision_reason`) and points
explicitly at the superseding CONTENT hash, not just a parent pointer.
A real, reusable design worth adopting regardless of the final
disposition of `mil_next` itself.

**Evidence handling**: `TradeThesis.supporting_evidence: Tuple[str, ...]`
— a real evidence-reference field, but confirmed (by reading `models.py`
in full) to be **untyped strings**, not `HistoricalObservation.observation_id`
references or any Reality-tier pointer. `mil_next` predates the
Historical Foundation's own evidence-boundary discipline (Phase 18.1's
`source_observation_ids`) — its evidence strings are a real gap
relative to what Phase 19.1's own contract design already requires.

## 2. Existing Brain Outputs vs. `mil_next` — Mapped

| Brain (Phase 19.0.1's own real field data) | Nearest `mil_next` concept | Fit |
|---|---|---|
| `RegimeReading.regime`/`.confidence` | `RegimeAssessment.regime`/`.direction`, folded into `composed_regime` | Close — `mil_next`'s regime vocabulary (`TRANSITIONING`/`VOLATILITY_EXPANSION`/`LIQUIDITY_STRESS`/`SHOCK_LIKE`/`CALM`) is DIFFERENT from `RegimeBrain`'s own (`TRENDING`/`RANGING`/`VOLATILE`/`COMPRESSED`/`TRANSITIONING`) — confirmed by reading both taxonomies; these are NOT the same vocabulary and would need explicit reconciliation, not a 1:1 field copy |
| `StructureReading` | No direct equivalent — `mil_next` has no options-chain/OI-structure concept beyond the coarser `OIContext.bias` | `StructureReading` is richer for options-specific structure; `mil_next` is richer for regime composition — genuinely complementary, not overlapping |
| `LiquidityReading` (ce/pe bid-ask) | `liquidity_posture: str` (a single enum: `LIQUIDITY_NORMAL`/`LIQUIDITY_STRESSED`) | `mil_next`'s liquidity concept is a coarser CLASSIFICATION; `LiquidityBrain`'s is raw bid/ask facts — `LiquidityBrain`'s output could be the INPUT that derives `mil_next`'s own `liquidity_posture`, a real, clean composition relationship |
| `VolatilityReading` (iv_ce/iv_pe/realized_vol) | `volatility_posture: RegimeAssessment` | Same complementary relationship — `VolatilityBrain`'s real numbers could feed `mil_next`'s own coarser posture classification |
| `EventReading` | `known_event_risk_state` + `EventCalendarView` | `mil_next`'s own event-calendar concept is MORE ambitious (`event_calendar.py`, a real scheduled-macro-event resolver) but its own `models.py` docstring for `EventReading`'s Bujji counterpart (Phase 19.0.1) already confirmed the brain deliberately does NOT attempt calendar events (no external data source) — `mil_next`'s calendar module was not verified this phase to have a real data source either; both may share the same real gap |
| `GreeksReading` | No equivalent field anywhere in `mil_next` | Genuine gap on `mil_next`'s side |

**Conclusion**: this is not a case where one system's outputs are a
strict subset or superset of the other's. **They are complementary,
covering different dimensions of "market understanding"** — the 6
brains are closer to Reality (raw, per-instrument, real-data-verified
numbers with disclosed limitations); `mil_next` is closer to Decision
(composed regime, thesis, contradiction, posture) but was built and
tested as a live-streaming laboratory, never fed real historical data.

## 3. Historical Replay Compatibility

**Can `mil_next` answer "what did Bujji understand at 10:30 on
2026-08-14"?** Evaluated against the three stated requirements:

- **Deterministic**: **Yes, and provably better than the brains
  audited in 19.0.1.** `snapshot_builder.build_snapshot()` takes an
  **injectable `Clock = Callable[[], datetime]`** parameter, with
  `_real_clock()` as only the DEFAULT — confirmed by direct read.
  This is EXACTLY the fix Phase 19.0.1 found missing from
  `RegimeBrain.analyze()`'s own hardcoded `now_ist()` call. `mil_next`
  already solved the wall-clock-injection problem the newer brains
  have not yet fixed.
- **No future leakage**: Structurally plausible (every input is
  passed in via `MarketDataInputs`, nothing fetches its own data) but
  **NOT independently proven** the way `MarketRealitySnapshot`'s own
  no-look-ahead guarantee was (adversarially tested across Phases
  18.1–18.10). `mil_next` has never been run against real historical
  data at all — this is an untested claim, not a verified one.
- **Evidence linked**: **Weaker than what Phase 19.1 already
  requires** — per §1, `TradeThesis.supporting_evidence` is untyped
  strings, not real `observation_id` references. Wiring `mil_next` to
  the Historical Foundation would require extending this, not merely
  adapting input shapes.

**The deeper incompatibility**: `mil_next`'s own `DataQualityContext`
(§1) is built entirely around LIVE-STREAMING quality signals —
`arrival_age_ms`, `transport_latency_ms`, `clock_skew_detected`,
`feed_disagreement_state` (multiple real-time feeds disagreeing),
sequence-gap detection. **None of these concepts have a real
equivalent in `MarketRealitySnapshot`** — a reconstructed historical
snapshot has no "arrival age" (it was retrieved once, long ago, and
is now an immutable fact) and no "feed disagreement" (Bujji has one
certified source per instrument, not multiple competing live feeds).
Feeding `mil_next`'s `build_snapshot()` from historical data would
require either fabricating plausible-looking values for fields that
are structurally meaningless in a historical context (a real
"fake observation" risk this project's own discipline forbids), or
substantially rewriting `data_quality.py` to use a different
classification scheme entirely — closer to
`ResearchSessionReadiness`/`DatasetVersion.ready_dates` (Phase 18.5/18.7)
than to anything `mil_next` currently computes.

## 4. Reality Compatibility

**Can `mil_next` consume `MarketRealitySnapshot`/`DatasetArtifact`/a
research dataset without violating immutability, evidence boundary, or
certification rules?**

- **Immutability**: no risk found — `mil_next` has zero write paths to
  any store (confirmed: no store import of any kind anywhere in the
  package).
- **Evidence boundary**: **currently violated by omission**, not by
  design — `mil_next`'s evidence fields are untyped strings, meaning a
  future consumer could not trace a `TradeThesis` back to a real
  `HistoricalObservation.observation_id` without extending the model
  first. Not unsafe today (it produces no output anyone consumes), but
  would need to be fixed before any real use.
- **Certification**: `mil_next` has zero awareness of `CertificationGate`,
  `DatasetArtifact`, or eligibility checking (Phase 18.12–18.14) —
  entirely unsurprising, since it predates all of that by a
  significant margin (its own `MIL_NEXT_VERSION = "0.1.0"` and
  "Gate 2 promotion" language suggest an early-stage internal
  milestone system, distinct from and unrelated to Phase 18.14's own
  lifecycle `CERTIFIED`/`PUBLISHED` states — a fifth, unrelated use of
  the word "certif­y"-adjacent vocabulary in this codebase, worth
  noting as a minor naming echo, not a real collision since `mil_next`
  never uses the literal word "certification").

## 5. Decision Boundary

Confirmed, directly, by re-reading `taxonomy.py` and `models.py` in
full: **no broker import, no order-placement field, no PnL field, no
execution-command field anywhere in `mil_next`.** `posture`
(`NORMAL`/`REDUCED`/`DEFINED_RISK_ONLY`/`MANAGE_ONLY`/`NO_TRADE`) is a
**closed-vocabulary risk-POSTURE classification**, not a trade
instruction — structurally the same category as `DataQuality.INSUFFICIENT`
on the newer brains: an honest constraint on what CAN be considered,
never a command to DO something. **Confirmed: `mil_next` is not a
trading signal generator, execution engine, or risk override** — the
same boundary Phase 19.0.1 §2 already confirmed for the 6 brains now
extends cleanly to this system too.

## Comparison: Option A vs. B vs. C

**Option A — Revive `mil_next` as canonical**: Rejected. Its INPUT
model is fundamentally live-streaming-shaped and does not map onto
historical batch reconstruction without a substantial rewrite of
`data_quality.py` specifically (§3) — "revive as-is" is not actually
possible; anything calling itself "reviving `mil_next`" would in
practice mean rewriting roughly half of it (the input/data-quality
half), which is not meaningfully different from building fresh with
its output vocabulary borrowed.

**Option B — Deprecate `mil_next`, build fresh**: Rejected as
too wasteful. Its OUTPUT vocabulary and composition logic
(`composed_regime`, `TradeThesis`/`CompetingThesis`, `ContradictionScore`,
`OIContext`, `posture`, the revision model) are genuinely more mature
than anything Phase 19.1 designed from the 6 brains alone, and its
clock-injection discipline is a real, already-solved fix for a bug
Phase 19.0.1 found elsewhere. Discarding all of it would mean
re-discovering the same design decisions later.

**Option C — Merge concepts. Recommended.** Precisely scoped:

- **Adopt from `mil_next`**: the OUTPUT vocabulary and composition
  shape (`composed_regime`, thesis/competing-thesis/contradiction,
  `posture`/`posture_reasons`/`invalidation_conditions`, the
  `MILSnapshotRevision` design), the canonical-hash exclusion-list
  PATTERN (already independently validated by `MarketRealitySnapshot.fingerprint()`'s
  own convergent design), and the mandatory clock-injection discipline
  (`Clock` parameter, never a bare `now_ist()` call).
- **Do NOT adopt from `mil_next`**: `MarketDataPoint`/`SourceHealth`/
  `DataQualityContext` as currently built — these are live-streaming
  concepts with no real equivalent in historical Reality. Replace them
  with data-quality signals `MarketRealitySnapshot`/`ResearchSessionReadiness`/
  `DatasetVersion` already provide (`completeness`, `ready_dates`/
  `incomplete_dates`, `certified_lineage_available`) — a real, better
  fit for the actual data source Phase 19.x is building against.
- **Fix before reuse**: `mil_next`'s evidence fields must be upgraded
  from untyped strings to real `observation_id` references before any
  `TradeThesis`-shaped output is trusted — required regardless of
  which option was chosen, since Phase 19.1's own contract already
  demands this.
- **Rename to avoid the Phase 19.1.1 collision**: keep `mil_next`'s
  own package and its historical, orphaned `snapshot_id`/`content_hash`
  fields untouched and undisturbed (do not repurpose them in place —
  Phase 19.1.1's own governance rule requires an explicit disposition
  decision, not a silent rewrite); build the new, historically-fed
  object under its own distinct name, informed by `mil_next`'s design
  but not literally reusing its `MarketIntelligenceSnapshot` class.

## Final Recommendation

**C) Merge concepts — precisely scoped as above.**

The evidence does not support wholesale revival (the input model is
the wrong shape for the actual problem Phase 19.x is solving) or
wholesale deprecation (the output vocabulary and several real
engineering fixes — clock injection chief among them — are worth too
much to discard). The right outcome is a genuinely new object, built
with full knowledge of `mil_next`'s own design decisions, adopting the
ones proven right (independently, twice, by two different engineers
at two different points in this project) and correctly declining the
ones that do not fit historical, already-certified, immutable Reality
data. This keeps Phase 19.1.1's own governance rule intact:
`mil_next`'s own package is neither silently repurposed nor thrown
away — it remains exactly what this audit found it to be, an honest,
disclosed, un-promoted laboratory, and the new work is built
alongside it, informed by it, under its own name.
