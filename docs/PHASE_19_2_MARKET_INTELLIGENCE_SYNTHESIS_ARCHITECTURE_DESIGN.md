# Phase 19.2 — Market Intelligence Synthesis Layer Architecture Design

**Status: DESIGN ONLY. Zero code changes.** Every field below traces
to a real, already-verified source from Phases 19.0–19.1.2 — this
document assembles, it does not invent.

---

## 0. The Five-Layer Model

```
Real World Market
        |
        v
Reality Capture Layer        HistoricalObservationStore, certification, lineage
        |                     FROZEN (Phase 18.15)
        v
Market Reality Layer         MarketRealitySnapshot — "what happened"
        |                     FROZEN (Phase 18.15)
        v
Intelligence Layer           MarketIntelligenceSnapshot — "what Bujji understands"
        |                     THIS PHASE'S OWN DESIGN TARGET
        v
Decision Intelligence Layer  DecisionContext, strategy selection, risk interpretation
        |                     EXISTS (epistemics.identity.DecisionContext, Phase 16D)
        v
Execution Layer              Orders, positions, management
                              EXISTS (bujji.broker, bujji.runtime_execution) — out of scope
```

Each layer consumes only the layer directly above it. This is not a
new rule — it is exactly what Phase 18.15's own Backtester Dependency
Contract already established for Datasets, extended one layer further.

## 1. `MarketIntelligenceSnapshot` Model

```
MarketIntelligenceSnapshot
  # --- Identity (§7 defines the full taxonomy) ---
  intelligence_snapshot_id:      NEW — content hash, fingerprint_state()
                                  (Phase 18.3's mechanism, mil_next's own
                                  independently-converged pattern, §6)
  source_market_snapshot_fingerprint:  = MarketRealitySnapshot.fingerprint()
                                  (Phase 18.3) — mandatory, never absent
  source_dataset_artifact_id:     = DatasetArtifact.artifact_id (Phase 18.12) —
                                  OPTIONAL (Phase 19.1's own original finding:
                                  ad hoc reconstructions outside a published
                                  artifact are a real, honest state)

  # --- Temporal ---
  as_of_time:                      the HISTORICAL/live moment this reflects —
                                  INJECTED via the mandatory clock rule (§3),
                                  never `datetime.now()`
  computed_at:                      real wall-clock build time — distinct from
                                  `as_of_time`, allowed to be "now"

  # --- Evidence boundary (§2) ---
  evidence_refs:                    union of every real `HistoricalObservation.observation_id`
                                  that contributed, transitively via
                                  `MarketRealitySnapshot.source_observation_ids`
                                  (Phase 18.1/18.3, already real, already proven)

  # --- Brain outputs (Phase 19.0.1's own real field data, verbatim) ---
  regime:            {state: RegimeReading.regime, confidence: .confidence,
                       data_quality: .data_quality, evidence: .evidence}
  structure:           {market_shape: StructureReading.proximity,
                       resistance: {strike, oi, distance_pct},
                       support: {strike, oi, distance_pct},
                       put_call_oi_ratio: .put_call_oi_ratio (carried, NEVER
                       promoted to a classifier — Phase 19.1's own disclosed
                       nuance, unchanged), confidence: .confidence}
  volatility:            {iv_ce, iv_pe, iv_average, realized_vol, richness,
                       richness_ratio, expected_move_points, expected_move_pct,
                       iv_rank: None, iv_percentile: None (always None, honest
                       limitation, Phase 19.1's own confirmed finding),
                       confidence}
  liquidity:               {ce_bid, ce_ask, pe_bid, pe_ask,
                       spread_state: DERIVED at assembly time from real bid/ask
                       (Phase 19.1's own resolved open item — NOT fabricated,
                       NOT left as a placeholder; a real, small, disclosed
                       computation: e.g. (ask-bid)/mid, a legitimate Intelligence-
                       layer derivation from real facts, same category as
                       VolatilityBrain's own IV solve)}
  greeks:                    {delta_ce, delta_pe, gamma_ce, gamma_pe,
                       theta_ce_per_day, theta_pe_per_day, vega fields
                       (per Phase 19.0.1's own confirmed GreeksReading shape) —
                       computed from `volatility.iv_ce`/`.iv_pe` ABOVE, never
                       from a Reality-tier IV field (none exists, Phase 19.0.1's
                       own confirmed architectural finding) — this is why
                       `greeks` is listed AFTER `volatility` in this schema:
                       sequencing is a real dependency, not stylistic}
  event_context:               {days_to_expiry, expiry_proximity, vix_level,
                       vix_change_pct, vix_regime} (EventReading, requires the
                       prior-day cross-reference adapter, Phase 19.0.1 §6)

  # --- Options market intelligence (Phase 19.1's own explicit scope) ---
  options_context:
    strike_distribution:    derived directly from `MarketRealitySnapshot.options.contracts`
    oi_evidence:               = structure.resistance/.support OI fields (no duplication)
    # Explicitly excluded: PCR-as-classifier, max pain, writer/buyer inference —
    # unchanged from Phase 19.1's own confirmed absence in every brain

  # --- mil_next-derived synthesis (§6 defines exactly what is adopted) ---
  thesis:                       TradeThesis-shaped: {thesis_id, direction,
                              supporting_evidence: Tuple[observation_id, ...]
                              (FIXED from mil_next's own untyped strings,
                              Phase 19.1.2's own confirmed required fix),
                              conviction_rank, timeframe}
  competing_thesis:               Optional, same shape + invalidation_condition
  contradiction_score:              {supporting_domain_count, contradicting_domain_count,
                              evidence_freshness_penalty, regime_consistency_penalty,
                              overall} — mil_next's own real shape, adopted verbatim
  confidence:                        aggregate — NEVER a new arbitrary number (§ Confidence
                              Model, Phase 19.1's own already-established rule):
                              a function of (a) each contributing brain's own
                              real, evidence-derived confidence, (b) any
                              `DataQuality.INSUFFICIENT` propagating down
                              (never silently ignored), (c) `contradiction_score`
                              (mil_next's own real signal, now a real input to
                              this aggregate rather than a separate figure)
  posture:                          mil_next's own closed vocabulary
                              (NORMAL/REDUCED/DEFINED_RISK_ONLY/MANAGE_ONLY/
                              NO_TRADE) — adopted verbatim; still, per Phase
                              19.1.2 §5, NOT a trade instruction, an honest
                              constraint on what CAN be considered
  invalidation_conditions:            Tuple[str, ...] — mil_next's own field,
                              adopted verbatim

  # --- Revision lineage (mil_next-derived, §6) ---
  revision:
    parent_intelligence_snapshot_id:  Optional[str] — same pattern as
                                     DatasetArtifact.parent_artifact_id
                                     (Phase 18.14) but with mil_next's own
                                     richer shape (adopted, not
                                     Phase 18.14's narrower one):
    revision_reason:                   Optional[str] — WHY, not just a pointer
    superseding_fingerprint:             the new snapshot's own fingerprint,
                                     named consistently with this schema's
                                     own field name (mil_next's
                                     `superseding_content_hash`, renamed to
                                     match this schema's vocabulary)

  # --- Governance (Phase 19.0.1 §6 / Phase 19.1.2 §5, re-confirmed here) ---
  # Structurally absent, not merely policy: no trade-signal field, no
  # execution-command field, no PnL field, no broker-order field exists
  # anywhere on this object. A future consumer wanting to act on this
  # snapshot must do so in the Decision Intelligence layer, one hop away.
```

## 2. Evidence Boundary

**What can enter FROM Reality** (never derived, always a direct copy):
`MarketRealitySnapshot`'s own real fields — spot/futures/vix OHLC,
option `ltp`/`bid`/`ask`/`oi`, `source_observation_ids`,
`certification_refs`. These pass through the adapter (Phase 19.1's
own scoped work) unchanged in value, only reshaped.

**What must be DERIVED** (a real computation over real Reality facts,
never a Reality-tier fact itself): `volatility.iv_ce`/`.iv_pe`
(Black-Scholes inversion from real premiums, Phase 19.0.1's own
confirmed requirement), `greeks.*` (computed from the derived IV,
never from a stored one), `liquidity.spread_state` (a real ratio of
real bid/ask), `regime`/`structure`/`event_context`'s own
classifications (each brain's own real, evidence-based logic),
`contradiction_score`, `confidence`, `posture` (mil_next's own
composition logic, adapted).

**What must NEVER be fabricated**: PCR-as-signal, max pain,
writer/buyer intent inference, `iv_rank`/`iv_percentile` (structurally
`None` — Phase 19.0.1's own confirmed, honest limitation, carried
through unchanged, never backfilled), any field whose real source data
does not exist for the requested `as_of_time` (the same "no fake
completeness" rule every Phase 18.x layer already enforces, extended
here rather than reinvented).

## 3. Historical Replay Requirements — The Mandatory Clock Rule

**Adopted verbatim, made a hard project rule, not a suggestion**: every
function that computes any part of a `MarketIntelligenceSnapshot` must
accept an injectable clock or an explicit `as_of_time`/`observation_time`
parameter — modeled directly on `mil_next.snapshot_builder`'s own real
`Clock = Callable[[], datetime]` parameter (Phase 19.1.2's own
confirmed finding: this already exists, proven, in `mil_next`, and
already fixes the exact defect Phase 19.0.1 found in `RegimeBrain.analyze()`'s
hardcoded `now_ist()`). Concretely: **every brain module
(`regime_brain.py` through `event_brain.py`) must be individually
checked and, where needed, fixed to accept an injected `as_of` before
Phase 19.3 implementation begins** — not assumed clean by association
with `mil_next`'s own good example, exactly as Phase 19.0.1 originally
cautioned.

- **Deterministic**: every brain is already a pure function (Phase
  19.0.1's own confirmed finding, re-affirmed); given the clock fix,
  identical inputs produce identical outputs by construction.
- **No future leakage**: inherited from `MarketRealitySnapshot`'s own
  adversarially-proven `as_of_time` bound (Phase 18.1–18.10) — the
  Intelligence layer adds no new query surface of its own that could
  violate this, since it never queries `HistoricalObservationStore`
  directly (§ Relationship with Existing Modules, below).
- **Reproducible hash**: `intelligence_snapshot_id` reuses
  `fingerprint_state()` with the identical exclude-audit-fields
  discipline both `MarketRealitySnapshot.fingerprint()` (Phase 18.3)
  and `mil_next.canonical_hash.compute_content_hash()` (Phase 19.1.2)
  independently arrived at — `computed_at`, `intelligence_snapshot_id`
  itself, and any wall-clock-derived field are excluded from the hash
  payload, the same three-way-converged rule, not reinvented a third
  time.

## 4. Live Operation Requirements

**The same object must support historical replay, paper trading, and
live market — confirmed achievable by construction, not merely hoped
for**: every brain is a pure function over plain input models
(`Candle`, premium/strike tuples, bid/ask floats — Phase 19.0.1's own
confirmed signatures); none of them import a broker, a clock, or a
live connection. The ONLY thing that differs between historical
replay, paper trading, and live operation is **which adapter supplies
the inputs** — a historical adapter reading `MarketRealitySnapshot`
(Phase 19.1's own scoped work), or a live adapter reading whatever
paper/live market data pipeline already exists
(`bujji.market_perception`, confirmed to exist, Phase 19.0). The
`MarketIntelligenceSnapshot` object itself, and every brain that feeds
it, is unaware of which. This is the direct, structural reason the
mandatory clock rule (§3) matters: it is the ONE thing that must be
injected correctly for this unification to hold, in every mode.

## 5. Relationship With Existing Brain Modules

| Brain | Role in this design | Real dependency confirmed |
|---|---|---|
| `RegimeBrain` | Feeds `regime` | Candles from the Reality adapter |
| `StructureBrain` | Feeds `structure` | Strike/OI tuples from `MarketRealitySnapshot.options` |
| `VolatilityBrain` | Feeds `volatility` — **including the IV solve `GreeksBrain` depends on** | Spot candles + one selected strike's premiums (strike-selection rule remains Phase 19.0.1's own confirmed open adapter decision) |
| `LiquidityBrain` | Feeds `liquidity` (raw fields); `spread_state` derived at assembly | Bid/ask per selected contract |
| `GreeksBrain` | Feeds `greeks` — **sequenced strictly after `VolatilityBrain`**, consuming its `iv_ce`/`iv_pe`, never Reality directly (Phase 19.0.1's own corrected finding) | `VolatilityBrain`'s own output |
| `EventBrain` | Feeds `event_context` | VIX snapshot + prior-day VIX (cross-reference adapter, Phase 19.0.1 §6) |

**Excluded from this synthesis layer, confirmed correct exclusions,
unchanged from Phase 19.0.1**: `PremiumBrain` (position-tracking
shape) and `BehaviourBrain` (trade-outcome history) — neither answers
"what is the market's current state," both belong to Position/
Learning intelligence, a different, later layer.

## 6. Relationship With `mil_next`

**Adopted, per Phase 19.1.2's own Option C recommendation**:
- The thesis/competing-thesis/contradiction-score composition shape
  (fixed to reference real `observation_id`s, closing Phase 19.1.2's
  own confirmed evidence-typing gap)
- `posture`/`invalidation_conditions`' closed vocabulary
- The revision-lineage concept (richer than Phase 18.14's own
  `parent_artifact_id` alone — carries a reason, not just a pointer)
- The canonical-hash exclusion-list PATTERN (independently
  re-validated, not reused as literal code — `fingerprint_state()`
  remains the one real hashing implementation, Phase 18.3's own,
  reused a fourth time here)
- The mandatory clock-injection discipline (§3)

**Rejected, per Phase 19.1.2's own confirmed reasoning**:
- `MarketDataPoint`/`SourceHealth`/`DataQualityContext` as built — a
  live-streaming-shaped model (`arrival_age_ms`, `transport_latency_ms`,
  `clock_skew_detected`, multi-feed disagreement) with no real
  equivalent in historical, already-certified Reality data. Replaced
  entirely by `MarketRealitySnapshot`/`ResearchSessionReadiness`/
  `DatasetVersion`'s own real completeness/certification signals.
- `mil_next`'s own regime vocabulary (`TRANSITIONING`/`VOLATILITY_EXPANSION`/
  `LIQUIDITY_STRESS`/`SHOCK_LIKE`/`CALM`) — confirmed, by direct
  comparison (Phase 19.1.2 §2), to be a DIFFERENT vocabulary from
  `RegimeBrain`'s own (`TRENDING`/`RANGING`/`VOLATILE`/`COMPRESSED`/
  `TRANSITIONING`). This design keeps `RegimeBrain`'s own vocabulary
  (it is the one with real, live-verified NIFTY data behind it,
  confirmed Phase 19.0) rather than mil_next's untested one.
- `mil_next`'s own literal `MarketIntelligenceSnapshot` class and
  `snapshot_id` field — per Phase 19.1.1's own governance rule, not
  silently repurposed; this design's own `intelligence_snapshot_id`
  is a new, distinct field on a new, distinct class.

## 7. Identity Taxonomy — Final, Disambiguated

```
MarketRealitySnapshotIdentity     = MarketRealitySnapshot.fingerprint()        (Phase 18.3, a METHOD)
DatasetArtifactIdentity            = DatasetArtifact.artifact_id                (Phase 18.12, a stored field)
MarketIntelligenceSnapshotIdentity  = MarketIntelligenceSnapshot.intelligence_snapshot_id
                                     (THIS PHASE — new, distinct name,
                                     never "snapshot_id" alone, avoiding
                                     Phase 19.1.1's own confirmed three-way
                                     collision on that bare name)
DecisionContextIdentity              = epistemics.identity.DecisionContext's own
                                     `as_of` + `session_id` pairing (Phase 16D,
                                     confirmed real; DecisionContext itself has
                                     no single `_id` field of its own — a real,
                                     pre-existing minor inconsistency this
                                     design does not attempt to fix, out of
                                     scope for an Intelligence-layer design doc)
```

**Explicitly NOT reused, per Phase 19.1.1's own governance rule**:
`mic_adapter`'s `artifact_id`/`lineage_id`/`snapshot_id` (active, live,
untouched), `bujji.qualification`'s `artifact_ids` (orphaned,
untouched), `mil_next`'s own `snapshot_id`/`content_hash`/
`idempotency_key` (orphaned, untouched, its class not reused).

## Final Recommendation

**A) Ready for implementation.**

Every field in §1 traces to a real, already-verified source across
five prior phases (19.0, 19.0.1, 19.1, 19.1.1, 19.1.2) — this document
introduces no new unverified concept, only assembly. The two real
preconditions implementation must satisfy, both already fully
specified rather than left open: (1) the mandatory clock-injection fix
applied to every brain individually before Phase 19.3, per §3's own
explicit instruction not to assume it by association with `mil_next`'s
good example; (2) the evidence-typing fix to the adopted thesis
model (real `observation_id`s, not free strings), per §6. Neither is a
redesign — both are small, bounded, already-scoped engineering tasks
for the implementation phase itself.
