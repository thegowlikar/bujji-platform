# Phase 19.1.1 — Intelligence Identity Namespace Audit

**Status: AUDIT ONLY. Zero code changes.**

**The decisive finding of this audit, stated first**: this is not a
two-way naming collision (`mic_adapter` vs. `DatasetArtifact`) as
Phase 19.1 characterized it. It is at least a **four-way** collision,
and the most serious instance is not a name clash at all — it is that
**Phase 19.1's own proposed `MarketIntelligenceSnapshot` design
already exists in the codebase, more maturely built, under the exact
same name, in a completely orphaned package**: `bujji/mil_next/`.
Confirmed by direct grep: **zero files anywhere in the repository
import from `bujji.mil_next`.** Before any implementation proceeds,
this must be reconciled — building a new `MarketIntelligenceSnapshot`
without addressing this would create a FOURTH parallel, disconnected
version of the same concept, repeating the exact failure mode this
audit exists to catch.

---

## 1. Every Identity Identifier Found — Real Inventory

A repository-wide sweep for `*_id` field names (60+ distinct names
found, ranked by real usage count) surfaces an identity landscape far
larger than the two names named in this phase's own prompt. The
highest-volume names, with their real home:

| Identifier | Real usage count | Primary home(s) |
|---|---|---|
| `assessment_id` | 595 | Trading brain / MSI assessment pipeline |
| `session_id` | 277 | Shadow runtime, broker sessions, trading sessions |
| `position_id`/`position_group_id` | 458 combined | Position/portfolio intelligence |
| `decision_id` | 192 | Trading brain decision pipeline |
| `observation_id` | 144 | **Reality tier** (`HistoricalObservation`, `RawObservation`) — FROZEN, Phase 17H/18.15 |
| `artifact_id` | 76 | **Four distinct meanings** — see §3 |
| `event_id` | 140 | Layer 0 capture events |
| `cycle_id` | 53 | Intelligence cycle recorder |
| `episode_id` | 49 | Market episode / regime memory |
| `snapshot_id` | 40 | **At least three distinct meanings** — see §3 |
| `dataset_id` | 38 | **Historical Foundation only** (Phase 18.11/18.12) — confirmed single-meaning, no collision found |
| `ingestion_run_id` | 23 | Reality tier lineage (Phase 17H.2) — confirmed single-meaning |
| `replay_id` | 25 | Orphaned `mic_v2`/`mic_adapter` vocabulary only |

**Scope note**: this audit focused its full depth on the identifiers
this phase's own prompt named as critical (`artifact_id`, `lineage_id`,
`snapshot_id`) and on the new finding they led to (`mil_next`). The
other 55+ identifiers in the full sweep were not individually
traced to their own collision risk this phase — a real, disclosed
limitation, not a claim of completeness across all 60+.

## 2. Canonical Identity Taxonomy — Corrected

Mapped to the REAL, verified owner of each layer, not an assumed one:

```
Reality:
  ObservationIdentity          = HistoricalObservation.observation_id / RawObservation's own id
                                  FROZEN (Phase 18.15) -- confirmed single meaning, zero collisions

Reconstruction:
  MarketRealitySnapshotIdentity = MarketRealitySnapshot.fingerprint()  (a METHOD, not a
                                  stored field -- Phase 18.3's own deliberate design;
                                  no literal "snapshot_id" field exists here at all,
                                  which is itself worth confirming stays true)

Dataset:
  DatasetIdentity                = DatasetIdentity.dataset_id           (Phase 18.11, content hash)
  DatasetArtifactIdentity          = DatasetArtifact.artifact_id          (Phase 18.12, event id)
                                  -- confirmed single, consistent meaning across
                                  dataset_artifact.py/dataset_artifact_store.py/
                                  dataset_lifecycle_store.py -- NO internal collision

Intelligence (CONTESTED -- see §3):
  MarketIntelligenceSnapshotIdentity  = mil_next.models.MarketIntelligenceSnapshot.snapshot_id
                                        (REAL, EXISTING, ORPHANED) -- Phase 19.1's own
                                        proposed design must be reconciled against this,
                                        not built alongside it

Decision:
  DecisionContextIdentity          = epistemics.identity.DecisionContext (Phase 16D,
                                    real, `as_of` + `session_id`, no `_id` suffix field
                                    of its own -- a real, if minor, taxonomy inconsistency)
```

## 3. Naming Collisions — Full Evidence

### `artifact_id` — FOUR distinct meanings found, not two

| Meaning | File(s) | Active or orphaned? |
|---|---|---|
| MIC v2 Consumer API reference id | `bujji/intelligence/mic_adapter/{models,mapper}.py` | **Active** — imported by `core/orchestrator.py`, `trading_brain/evidence_interpreter/engine.py` (confirmed, re-verified this phase) |
| `DatasetArtifact`'s own event identity | `bujji/market_reality_snapshot/dataset_artifact.py`, `dataset_artifact_store.py`, `dataset_lifecycle_store.py` | **Active** — Phase 18.12–18.14, FROZEN per Phase 18.15 |
| Qualification replay run's per-source artifact map | `bujji/qualification/replay_models.py` (`ReplayRunResult.artifact_ids: Dict[str, Optional[str]]`), `replay_runner.py`, `replay_statistics.py`, `journal/replay_qualification_journal.py` | **Orphaned** — re-confirmed this phase, consistent with every prior 18.x finding: zero references from `trading_brain`/`shadow_runtime`/`production_runtime` |
| (implicitly, via `mil_next`) — no literal `artifact_id` field found in `mil_next` itself, but its own `content_hash`/`idempotency_key` occupy the same CONCEPTUAL role `DatasetArtifact.artifact_id`/`fingerprint` occupy for datasets | `bujji/mil_next/models.py`/`canonical_hash.py` | **Orphaned** |

### `lineage_id` — two distinct meanings

| Meaning | File(s) | Active or orphaned? |
|---|---|---|
| MIC v2 Consumer API reference id | `bujji/intelligence/mic_adapter/{models,mapper}.py` | **Active** (same live import sites as above) |
| (No second literal `lineage_id` field found elsewhere — Phase 18.x's own lineage concept uses `ingestion_run_references`/`certification_references`, never a field literally named `lineage_id`) | — | — |

**Correction to this phase's own prompt**: `lineage_id` is not
actually a second live collision — Historical Foundation code never
uses that literal field name. The prompt's own framing (implying a
`lineage_id` clash) was not confirmed by evidence; worth stating
plainly rather than inventing a collision to match the prompt's own
expectation.

### `snapshot_id` — at least THREE distinct meanings, the most contested name

| Meaning | File(s) | Active or orphaned? |
|---|---|---|
| MIC v2 Consumer API reference id | `mic_adapter/{models,mapper}.py`, and `evaluation/{engine,policy,serialization}.py` | **Active** |
| `mil_next`'s own `MarketIntelligenceSnapshot.snapshot_id` | `mil_next/{models,snapshot_builder,snapshot_journal,canonical_hash}.py` | **Orphaned** (zero importers, confirmed) |
| A live-trading-path concept | `core/orchestrator.py`, `journal/decision_journal.py`, `trading_brain/ontology/{models,query,runner,serialization}.py`, `trading_brain/evidence_interpreter/engine.py` | **Active** — this is the ontology/decision-journal system's own real, currently-used snapshot concept, distinct from both of the above |

**This is the real, load-bearing finding**: THREE live-or-recently-live
systems each independently invented a `snapshot_id` concept
(`mic_adapter`'s consumer-record reference, `mil_next`'s own richer
intelligence snapshot, and `trading_brain/ontology`'s live decision
snapshot), plus a fourth (Phase 19.1's own proposed design) about to be
added on top. Phase 19.0/19.0.1/19.1 audited the 8 `intelligence/*_brain.py`
modules thoroughly but never surfaced `mil_next` or
`trading_brain/ontology`'s own snapshot concepts — a real, disclosed
gap in those prior phases' own search scope, corrected here.

## 4. Active vs. Orphaned vs. Rename vs. Preserve

| System | Status | Recommendation |
|---|---|---|
| `mic_adapter` (`mic_v2` vocabulary) | **Active**, live-imported into `orchestrator.py`/`evidence_interpreter` | **Preserve, but rename its OWN internal fields** if it is ever touched again — `artifact_id`/`lineage_id`/`snapshot_id` here refer to a package (`mic_v2`) that no longer exists on disk; these names are load-bearing for live code today and must not be silently repurposed |
| `DatasetArtifact` (Phase 18.12) | **Active**, FROZEN (Phase 18.15) | **Preserve exactly as-is** — this audit found no internal inconsistency in its own usage |
| `bujji.qualification`/`bujji.replay` (Series 46-64) | **Orphaned**, re-confirmed 5th consecutive phase | **No action required by this audit** — already correctly excluded from every dependency contract since Phase 18.0; a future cleanup phase's decision, not this one's |
| `bujji.mil_next` | **Orphaned**, confirmed zero importers | **The critical open decision** — see Final Recommendation |
| `trading_brain.ontology` / `decision_journal` / `orchestrator`'s own `snapshot_id` | **Active**, live trading path | **Preserve** — out of this audit's own scope to touch (a DECISION-tier concept, not Market Intelligence), but its existence must inform Phase 19.x naming so a NEW `MarketIntelligenceSnapshot` does not silently collide with what THIS system means by "snapshot" either |

## 5. Governance Rule

**Proposed, directly addressing what this audit found**:

> No two layers may share the same identity vocabulary UNLESS one is
> formally confirmed orphaned (zero real importers, verified by grep,
> not assumed) — in which case its names are historical, not reserved,
> and may be reused only after this fact is explicitly stated in the
> reusing phase's own report, exactly as this document does now.

This is stricter than a blanket "no collisions ever" rule — it would
have permitted, e.g., Phase 18.12's real, deliberate, disclosed reuse
of the word "artifact" (a defensible English word, not a reserved
namespace) PROVIDED the orphaned status of the prior `qualification`
usage had been checked first (it was, in Phase 18.0, before
`DatasetArtifact` was ever named — confirmed by that phase's own
report). The rule this audit adds going forward: **`mil_next` must
receive the same explicit disposition decision before Phase 19.x names
anything `MarketIntelligenceSnapshot`** — it has not yet received one.

## Final Recommendation

**B) Identity cleanup required first — narrowly scoped to one
decision, not a broad cleanup program.**

Not (A): proceeding to implement Phase 19.1's own proposed
`MarketIntelligenceSnapshot` today would create a real, avoidable
fourth version of a concept that already has a more mature, real
implementation sitting unused. Not (C): this is not evidence of a
broader redesign need — every OTHER identity boundary checked this
phase (`ObservationIdentity`, `DatasetIdentity`, `DatasetArtifactIdentity`)
is clean, consistent, and correctly frozen; the issue is narrowly
`MarketIntelligenceSnapshot` naming and design provenance, not the
Historical Foundation's own identity model.

**The one decision required before Phase 19.2 proceeds**: examine
`bujji/mil_next/` in full (this audit read its models and hashing
approach but not its full `snapshot_builder.py`/`data_quality.py`/
`timeframe_fold.py` logic) and decide, explicitly:

1. **Revive and extend `mil_next`'s own `MarketIntelligenceSnapshot`**
   (it is more feature-complete than Phase 19.1's proposal — it
   already has `content_hash`, `idempotency_key`, and even a real
   revision/versioning concept, `MILSnapshotRevision`, more mature
   than Phase 18.14's own `parent_artifact_id`) as the real target,
   wiring it to the Historical Foundation instead of building fresh; or
2. **Formally deprecate `mil_next`** (document why it was abandoned —
   this audit found no report explaining its own orphaning, unlike
   `bujji.replay`'s well-documented supersession) and proceed with
   Phase 19.1's simpler design under a deliberately DIFFERENT name
   (e.g. `MarketUnderstandingSnapshot`) to avoid ever colliding with
   the orphaned original.

Either is defensible; proceeding without choosing is not.
