# Phase 16K — Canonical Market State Fingerprint Audit

**Audit only. No code, no storage, no ML/embeddings. Regression unchanged at 5,167 / 0 failed.**

---

## 0. THE HEADLINE

> **Every "fingerprint" in Bujji today is an EQUALITY hash. A similarity fingerprint requires the opposite property.**

Six content-hash implementations exist (`mil_next.canonical_hash`, `replay_engine.fingerprint_state`, `market_observation.series_fingerprint`, two `compute_fingerprint`s, MLE's `candidate_id`). All are SHA-256/MD5 over normalized content.

| | Content hash | Similarity fingerprint |
|---|---|---|
| Goal | *"is this the identical snapshot?"* | *"is this a similar market?"* |
| Required property | **avalanche** — 1 bit changes → different hash | **stability** — small change → same bucket |
| On IV 23.47 → 23.48 | different hash | **must be** same bucket |
| Match rate on real data | effectively 0 | usefully > 0 |

**Using any existing hash for similarity retrieval would return zero matches, always.** They are not a partial solution to be extended — they solve the opposite problem, correctly, and should stay as they are.

### But `canonical_hash` already contains the key insight

```
Excluded:  arrival_age_ms · transport_latency_ms · skew_ms ·
           feed_disagreement_bps · as_of · receipt_time
           (wall-clock/replay-dependent raw measurements)

Included:  every data-quality CONCLUSION (freshness, completeness,
           outlier_flag, event_time_synthetic, clock_skew_detected)
           + decision-content fields
```

> *"two runs over byte-identical market data at different real times must still hash identically, provided their DATA-QUALITY CONCLUSIONS agree"*

**That is discretisation-for-stability already applied** — raw jittery values excluded, their categorical conclusions kept. A similarity fingerprint is the same principle pushed further: exclude *all* continuous values, keep *only* categorical conclusions.

---

## 1. Current fingerprint capability

| Dimension | Field | Owner | Status |
|---|---|---|---|
| **Underlying regime** | `market_state.regime`, `RegimeAssessment.regime` | MSI / mil_next | ✅ **categorical, ready** |
| **Volatility regime** | `volatility_regime` ∈ {COMPRESSED, HIGH_VOLATILITY, STABLE, TRANSITIONING, UNKNOWN} | VSB | ✅ **categorical, ready** |
| **Option positioning** | `positioning_bias` ∈ {BULLISH/BEARISH/NEUTRAL/MIXED/UNKNOWN_POSITIONING} | MPPI | ✅ **categorical, ready** |
| **Futures structure** | `DOMAIN_FUTURES_STRUCTURE` | declared | ❌ **never populated** |
| **Liquidity** | `LiquidityReading` tightness | LiquidityBrain | ⚠️ computed, not in any state model |
| **DTE** | `expiry` present in 30 files | instrument master | ⚠️ **derivable, never a field** |
| **Time of day** | timestamps | — | ❌ **0 files** — no session-phase concept |
| **Event context** | `known_event_risk_state` ∈ {KNOWN_NO_EVENT, KNOWN_EVENT, COVERAGE_UNKNOWN} | mil_next | ✅ **categorical, ready** |
| **Market breadth** | — | — | ❌ **1 file, no model** |
| **Sentiment** | — | — | ❌ **0 files** |

**Four dimensions are already categorical and fingerprint-ready.** Two (DTE, time-of-day) are trivially derivable from data already captured. Four are genuinely absent.

`COVERAGE_UNKNOWN` in the event-risk vocabulary is notable — it distinguishes *"no event scheduled"* from *"we don't know whether an event was scheduled."* That distinction must survive into the fingerprint; collapsing them would make two very different markets look identical.

---

## 2. Fingerprint ownership

| Candidate layer | Verdict |
|---|---|
| Observation | ❌ observations are facts; a fingerprint is an interpretation |
| MSI | ❌ MSI *produces* the states — fingerprinting there couples decision to retrieval |
| Phenomena | ❌ phenomena are events, not a persistent state descriptor |
| **Memory** | ✅ **correct owner** |

**The fingerprint belongs to the memory layer**, for three reasons:

1. **It exists only to serve retrieval.** Nothing in the decision path needs it — and if the decision path ever consumed it, that would be memory→decision feedback, which the 15N boundary forbids.
2. **Its bucketing will change** as the population grows. A retrieval-tuning concern must not sit inside decision-making, where changing a bucket boundary would silently alter live behaviour.
3. **Memory already carries the raw material** — `outcome_memory` stores `lifecycle_snapshot` and `attribution_snapshot` verbatim, including `entry_greeks` and `entry_premium_behaviour`.

**Concretely:** a `state_fingerprint` field on `OutcomeMemoryRecord`, computed at record time from the already-captured snapshot, with its own `calc_version`.

**Critical constraint:** the fingerprint must be **recomputable** from the verbatim snapshot. When bucketing changes, every historical record gets a new fingerprint under a new `calc_version` — old and new never mix. That is only possible because 15N stores snapshots verbatim rather than lossily summarised.

---

## 3. Minimum viable fingerprint

**Six dimensions. All categorical. All already captured or trivially derivable.**

```
StateFingerprint  (v1)
    regime            TRENDING | RANGING | ...        ✅ exists
    volatility        COMPRESSED | STABLE | HIGH_VOL  ✅ exists
    positioning       BULLISH | BEARISH | NEUTRAL     ✅ exists
    dte_bucket        0DTE | 1-3 | 4-7 | 8-15 | 16+   ⚠️ derive
    session_phase     OPEN | MORNING | MIDDAY |
                      AFTERNOON | CLOSE               ⚠️ derive
    event_context     KNOWN_EVENT | KNOWN_NO_EVENT |
                      COVERAGE_UNKNOWN                ✅ exists
```

### Why six, and why these

- **Every dimension is already a categorical conclusion** produced by an existing engine — no new intelligence, no thresholds fit to data.
- **DTE and session_phase are the two additions**, and both are the highest-value conditioning variables for an options desk: a 0DTE morning setup and a 15DTE afternoon setup are genuinely different markets, and neither is currently expressible.
- **Excluded deliberately from v1:** liquidity (sparse — 1/174 real cycles had both ATM bid/ask), futures structure (never populated), breadth and sentiment (do not exist). Adding a dimension that is `UNKNOWN` in 99% of records adds cardinality without adding discrimination.

### Cardinality

```
5 regimes × 5 volatility × 5 positioning × 5 dte × 5 phase × 3 event ≈ 9,375 cells
```

With realistic hundreds of records, most cells are empty. **v1 must therefore support partial matching** — match on a declared subset, report which dimensions were relaxed:

```
match(regime, volatility, dte)          → broad cohort
match(regime, volatility, dte, phase)   → narrower
exact 6-dimension match                  → rare; treat as a bonus, never the goal
```

**The relaxation must be explicit and reported.** A cohort assembled by silently dropping dimensions is indistinguishable from a well-matched one, and that is how false confidence enters.

---

## 4. Anti-pattern audit

| # | Risk | Assessment | Mitigation |
|---|---|---|---|
| 1 | **Too many dimensions** | **HIGH** — the natural instinct is 10 dimensions; that yields ~2M cells and guarantees empty cohorts | Hard cap at 6 for v1. Adding a dimension requires evidence it discriminates. |
| 2 | **Sparse samples** | **CERTAIN** — zero records today; hundreds after months | `MIN_SAMPLE_SIZE=3` + `INSUFFICIENT_HISTORY` already exist and **must not be relaxed** for an attractive 2-member cohort |
| 3 | **Confidence leakage** | **HIGH** | A cohort's size must never raise a *decision's* confidence. Retrieval returns evidence with its own `Uncertainty`; `epistemics.compose`'s MIN rule prevents a strong prior from overriding weak current evidence. |
| 4 | **Mixed feature versions** | **HIGH** | Fingerprints from different `calc_version`s must **never** be compared. Bucket-boundary changes create a new version; old records are recomputed, not reinterpreted. |
| 5 | **Look-ahead contamination** | **MEDIUM** | Fingerprint computed strictly from `as_of`-scoped data. `mil_next`'s cutoff discipline + `epistemics.look_ahead_violation` already provide the mechanism. |
| 6 | **Outcome leakage into the fingerprint** | **HIGH** *(not on the original list)* | The fingerprint describes the market **at entry** — it must contain **no** outcome-derived field. Including realized P&L or final thesis status would make retrieval trivially "predictive" and completely circular. |
| 7 | **Survivorship in the fingerprint corpus** | **MEDIUM** *(not on the original list)* | Only *traded* setups become memory records. Retrieval over them answers "when I traded this state, what happened" — **not** "when this state occurred, what happened." Those are different questions and must not be conflated in reporting. |

**Risks 6 and 7 were not on the brief and are the two most likely to produce confident nonsense.** Risk 7 in particular is structural: the population is filtered by Bujji's own past decisions, so any conclusion drawn from it is conditioned on those decisions.

---

## 5. Final recommendation

### ALREADY EXISTS

| | |
|---|---|
| Regime / volatility / positioning as categorical states | MSI (VSB, MPPI, market_state) |
| Event-risk context with explicit `COVERAGE_UNKNOWN` | `mil_next` |
| Discretisation-for-stability principle | `mil_next.canonical_hash` |
| Verbatim snapshots (recomputation substrate) | `outcome_memory` |
| Sample-sufficiency gating | `MIN_SAMPLE_SIZE` / `INSUFFICIENT_HISTORY` |
| `calc_version` mechanism | `epistemics.identity` |
| Uncertainty composition (MIN rule) | `epistemics.uncertainty` |
| Look-ahead detection | `epistemics.lineage` + `mil_next` cutoff |
| Memory→decision isolation | 15N, AST-enforced |

### NEEDS EXTENSION

| # | Extension | Size |
|---|---|---|
| 1 | `dte_bucket` derivation | small — expiry already in 30 files |
| 2 | `session_phase` derivation | small — timestamps already captured |
| 3 | `state_fingerprint` + `calc_version` on `OutcomeMemoryRecord` | small |
| 4 | Partial-match with explicit relaxation reporting | medium |

### NEW CAPABILITY

| # | Capability | Prerequisite |
|---|---|---|
| 1 | `StateFingerprint` model + bucketing rules | — |
| 2 | Cohort retrieval by fingerprint | fingerprinted records |
| 3 | Liquidity state as a categorical dimension | real bid/ask (Gate 1) |
| 4 | Futures structure dimension | futures capture |
| 5 | Breadth / sentiment | do not exist at all — **defer indefinitely** |

---

## 6. Verdict

**The fingerprint is smaller and closer than expected: four of six dimensions already exist as categorical states; the other two are derivations from captured data.**

The one genuine conceptual gap is that **no existing hash can serve similarity** — they are equality hashes by design and should remain so. But `canonical_hash` has already demonstrated the exact principle a similarity fingerprint needs: exclude jittery continuous values, keep categorical conclusions.

**Do not build this yet.** Two prerequisites are unmet:

1. **Zero population.** Every retrieval returns `INSUFFICIENT_HISTORY` until real sessions run (Gate 1-gated).
2. **No `calc_version` on any persisted record.** Phase 16D built the mechanism; no producer stamps it. Fingerprints without version identity would silently mix bucketing schemes — anti-pattern #4, permanently.

**Recommended sequence:** producer stamping (16D's unfinished half) → population via shadow trading → *then* fingerprinting, sized to the population that actually exists rather than the one imagined.

---

**No code written. Frozen untouched: TickStore · storage backend · watermark value · ingestion topology · Stack A. Memory/MSI/mil_next diff: 0 lines. Regression 5,167 / 0 failed.**
