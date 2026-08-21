# BUJJI Learning Architecture Codex

**Status: FROZEN.** This document is the canonical reference for the
learning architecture (Series 99–106). It was produced by Series 107,
which created no new engines, models, journals, packages, or logic —
only documentation, verified programmatically against the real,
already-committed code.

Every fact below was checked against the real repository at commit
`57fde0d` on `v1.0-shadow`, not asserted from memory. Full regression:
**3032/3032 passing.** All 35 isolation tests across the 7 learning
packages: **passing.**

---

## 1. The Learning Stack (frozen)

| Series | Package | Question it answers |
|---|---|---|
| 99 | `bujji.msi_decision_auditor` | What happened? |
| 100 | `bujji.msi_market_learning` | What observations accumulate? |
| 101 | `bujji.msi_evidence_packet` | What immutable evidence exists? |
| 102 | `bujji.msi_counterfactual_replay` | What legal alternatives existed? |
| 103 | `bujji.msi_market_phenomena` | What objectively happened? |
| 104 | `bujji.msi_opportunity_assessment` | Was the decision justified? |
| 105 | `bujji.msi_knowledge_validation` | Has enough evidence accumulated? |
| 106 | `bujji.msi_engineering_evidence_board` | Is there sufficient evidence to justify engineering investigation? |

This stack is now **complete**. No further learning infrastructure shall
be added without an explicit architectural review (per this codex's own
Future Rules, §6).

---

## 2. Architectural Laws (permanent)

1. **Production is deterministic.** Every decision function in `bujji/`'s
   Production packages is a pure function of real, disclosed inputs — no
   `uuid4()`, no wall-clock reads, no randomness anywhere in a decision
   path. Verified continuously by this project's regression suite since
   Series 1.
2. **Learning never changes Production.** Verified structurally, not by
   convention: every one of the 7 learning packages has a dedicated
   isolation test (`tests/test_*_isolation.py`) confirming no Production
   module imports it. Re-verified fresh for this codex (§5).
3. **Production changes only through explicit engineering
   implementation.** No learning package contains a code-generation
   capability (`compile`/`exec`/`eval` structurally forbidden in
   `msi_engineering_evidence_board`, the package closest to that risk)
   and no package's output type can hold executable code or a
   parameter-change instruction.
4. **Every engineering change must be traceable to replay-supported
   evidence.** The `TraceabilityEdge` graph (Series 101) and the
   `referenced_*` fields threaded through every downstream package's
   output (Series 102–106) implement this literally: a
   `READY_FOR_ENGINEERING_REVIEW` decision always carries real
   `referenced_knowledge_validation_reports`/`referenced_evidence_
   packets`/`referenced_opportunity_assessments` ids back to their real
   source.
5. **Every replay must be causal.** Enforced by a dedicated,
   independently-tested validator in every package that touches
   timestamps: `msi_counterfactual_replay.engine.validate_causality`,
   `msi_market_phenomena.engine.validate_causal_order`,
   `msi_opportunity_assessment.engine.validate_causality`,
   `msi_knowledge_validation.engine.validate_causal_order`. A causality
   violation is never silently discarded — it produces a real, disclosed,
   conservative result (never an inflated one).
6. **Evidence is immutable.** `EvidencePacket` (Series 101) and
   `EngineeringEvidenceReport` (Series 106) both use content-hash
   identity (`packet_id`/`report_id` are `hashlib.md5` of real content) —
   a correction can only ever produce a *new* id, never overwrite an old
   one. `EvidencePacketJournal` additionally defends against a same-id/
   different-content write with a hard `ImmutabilityViolation`.
7. **Knowledge must be earned.** Series 105's seven-state ladder
   (`NOT_OBSERVED` → ... → `VALIDATED`) requires real, measured
   diversity, consistency, and replay support — not occurrence count
   alone (test-verified: 25 same-classification occurrences stay
   `EMERGING`, never `VALIDATED`).
8. **Governance never implies implementation.**
   `READY_FOR_ENGINEERING_REVIEW` (Series 106) is structurally incapable
   of meaning "implement this" — no field on `EngineeringEvidenceReport`
   could hold code, a parameter value, or an instruction, and the
   disclosed reasoning explicitly denies it every time.
9. **Human engineering remains the final authority.** `ARCHIVED` and
   `SUPERSEDED` (Series 106) are the only two decisions that exist
   outside the automatic evidence-review path — both require an explicit
   human call (`archive()`/`supersede()`), each demanding a real,
   non-empty, disclosed reason.
10. **Every production rule must have ancestry.** The full real chain —
    Decision Record → Evidence Packet → Counterfactual Session → Market
    Phenomena → Opportunity Assessment → Knowledge Validation →
    Engineering Evidence Board — is real, id-referenced, and queryable at
    every layer (§4).

---

## 3. Package Ownership Matrix

| Package | Owns | Does NOT own |
|---|---|---|
| Series 99 | Recording what Production actually decided and its realized outcome | Interpretation, pattern detection |
| Series 100 | Knowledge Candidate lifecycle, evidence-tier accumulation | Immutable fact storage, causal replay, market vocabulary |
| Series 101 | Immutable fact/measurement storage, the traceability graph's edge mechanics | Interpretation, pattern detection, replay execution |
| Series 102 | Causal replay of alternative decision paths (the only package that invokes real decision functions, isolated to `replay.py`) | Interpretation, market classification, evidence storage |
| Series 103 | Objective market-behavior classification from real Intelligence-layer fields (isolated to `translate.py`) | Decision quality, strategy evaluation |
| Series 104 | Per-day decision-quality classification (5 exhaustive states) | Pattern accumulation across days, evidence storage |
| Series 105 | Cross-day pattern validation (7 exhaustive states) | Per-day judgment, governance decisions |
| Series 106 | Engineering-readiness governance (5 exhaustive states) | Pattern validation math, evidence storage, implementation |

**No overlapping ownership, verified**: each package's own `taxonomy.py`
declares a disjoint state vocabulary (5, 5, —, —, 10 phenomena, 5, 7, 5
respectively across the packages that classify anything), and no two
packages implement the same causal-ordering or decay-detection logic by
import — where the same *kind* of check is needed twice (e.g. decay
detection in both Series 100 and 105), it is deliberately
**reimplemented locally** rather than shared, specifically because
sharing would require a cross-package import that isolation forbids.
This is a real, disclosed tradeoff (duplication of ~10 lines of
declarative math) accepted in exchange for zero coupling — documented
here explicitly so it is never mistaken for accidental drift.

---

## 4. Architecture Dependency Graph (real, verified)

```
                          Live Market
                               |
                               v
                        Production BUJJI  (deterministic, Series 1-98)
                               |
                               v
                  Series 99 — Decision Auditor
                    (DecisionRecord, OutcomeRecord)
                               |
              +----------------+----------------+
              |                |                |
              v                v                v
   Series 100 (MLE)   Series 101 (EPS)   Series 102 (CRE)
   [zero bujji         [zero bujji        [imports Production
    imports]            imports]           via replay.py ONLY]
              |                |                |
              +--------+-------+-------+--------+
                       |               |
                       v               v
              Series 103 (MPC)   (all four above feed into:)
              [imports Production
               via translate.py ONLY]
                       |
                       v
              Series 104 (OAE)
              [zero bujji imports --
               consumes 99/102/103 via
               local View translations]
                       |
                       v
              Series 105 (KVE)
              [zero bujji imports --
               consumes 101-104 via
               local View translations]
                       |
                       v
              Series 106 (EEB)
              [zero bujji imports --
               consumes 101/104/105 via
               local View translations]
                       |
                       v
          [human] Engineering Proposal
                       |
                       v
              Replay Validation (Sprint 114A discipline)
                       |
                       v
                 Production BUJJI
```

**Real, verified facts** (checked fresh for this codex, not carried over
from memory):
- **Zero cross-package imports among the 7 learning packages themselves**
  — grepped `from bujji.` / `import bujji.` across all 7 packages' source:
  empty result. Every artefact flow shown above is a *conceptual*
  dependency (caller-supplied data), never a *code* dependency.
- **Exactly two, disclosed exceptions** import real Production types:
  `msi_counterfactual_replay/replay.py` (imports `SessionDriver`,
  `run_full_cadence`, and the observation-construction chain — the one
  package whose entire purpose requires invoking real decision
  functions) and `msi_market_phenomena/translate.py` (imports only real
  Intelligence-layer **model** types, never an engine module). Both are
  single-file, allowlist-tested exceptions (§5).
- **Zero Production modules import any of the 7 learning packages** —
  grepped every `.py` file outside the 7 packages for any reference:
  empty result.
- **No cyclic dependency exists** — the graph above is a DAG; information
  flows strictly upward from Series 99 through Series 106, then out to a
  human, never back down into any Series 99-106 package, and never into
  Production.

### Forbidden imports (enforced by tests, re-verified for this codex)

| From | Into | Status |
|---|---|---|
| Any Production module | Any of the 7 learning packages | Forbidden, tested, verified clean |
| Series 100, 101 | Any other bujji package | Forbidden, tested, verified clean |
| Series 103, 104, 105, 106 | Any other bujji package | Forbidden, tested, verified clean |
| Series 102 (outside `replay.py`) | Any Production package | Forbidden, tested, verified clean |
| Series 103 (outside `translate.py`) | Any Production **engine** module | Forbidden, tested (`test_translate_py_never_imports_a_decision_function_only_model_types`) |
| Any of the 7 | Order-placing functions (`place_order`/`submit_and_confirm`/`modify_order`/`cancel_order`) | Forbidden, tested in all 7 packages |

### Replay boundaries

Only two files in the entire learning stack ever invoke real Production
computation: `msi_counterfactual_replay/replay.py` (invokes real decision
functions to replay an alternative path) and
`msi_market_phenomena/translate.py` (reads real, already-computed
Intelligence field values, never invokes a decision function). Every
other replay-adjacent operation in Series 99–106 operates on
already-materialized real data — a `DecisionRecord`, an `EvidencePacket`,
a `CounterfactualSession` — never on a live or re-executed Production
call.

### Governance boundaries

Series 106 is the only package with the authority to assign
`READY_FOR_ENGINEERING_REVIEW`, `ARCHIVED`, or `SUPERSEDED`. No other
package in the stack computes or references these states. Series 106
itself cannot act on its own `READY_FOR_ENGINEERING_REVIEW` decision —
there is no code path from Series 106 to Production, by the same
isolation contract as every other layer.

### Production boundaries

Production (Series 1–98, `bujji/live_pipeline_bridge.py`,
`bujji/live_shadow_validation.py`, `run_live_shadow.py`, and every
`msi_*` package that is not one of the 7 learning packages) never
imports any learning package. This is the single permanently-forbidden
edge in the whole graph, and it is the one edge every isolation test in
Series 100–106 checks first.

---

## 5. Import Boundary Verification (this codex's own re-check)

Run fresh against the real, current repository for this document (not
copied from any prior sprint's report):

```
$ grep -rn '^from bujji\.\|^import bujji\.' bujji/{msi_market_learning,msi_evidence_packet,
    msi_counterfactual_replay,msi_market_phenomena,msi_opportunity_assessment,
    msi_knowledge_validation,msi_engineering_evidence_board}/*.py
    | grep -v <own-package>
(empty, for six of the seven packages -- see exceptions below)

$ grep -n '^from bujji\.' bujji/msi_counterfactual_replay/replay.py
bujji.live_pipeline_bridge, bujji.live_shadow_validation, bujji.market_episode,
bujji.market_observation, bujji.live_market_events, bujji.msi_portfolio_construction.models
(the one disclosed exception, allowlist-tested)

$ grep -n '^from bujji\.' bujji/msi_market_phenomena/translate.py
bujji.msi_market_direction.models, bujji.msi_market_structure.models,
bujji.msi_price_structure.models, bujji.msi_volatility_structure.models
(the other disclosed exception, allowlist-tested, models only, no engine)

$ [search every Production .py file outside the 7 packages for any reference
   to any of the 7 package names]
(empty -- zero Production module imports any learning package)
```

Full regression at time of freeze: **3032/3032 passing.** All 35
isolation tests across the 7 packages: **passing.**

---

## 6. Future Extension Rules

Any future learning subsystem (Series 107+, if ever authorized) MUST:

1. **Consume existing artefacts, never recreate them.** If the data a new
   package needs already exists as a real Series 99–106 output, the new
   package imports nothing from that package — it defines its own local
   `*View` translation type, exactly as Series 104/105/106 did.
2. **Never bypass governance.** No new package may assign
   `READY_FOR_ENGINEERING_REVIEW`/`ARCHIVED`/`SUPERSEDED` — that
   authority belongs exclusively to Series 106.
3. **Never modify Production.** A dedicated `test_*_isolation.py`
   verifying zero Production imports (or, if the new package genuinely
   needs to read Production state, a single disclosed, allowlist-tested
   exception file exactly like `replay.py`/`translate.py`) is a hard
   prerequisite before any such package may be committed.
4. **Preserve replay determinism.** Same discipline as every package in
   this codex: no `uuid4()`, no wall-clock reads, content-hash ids,
   golden replay tests against the real corpus, and a full 41-day
   replay-parity proof (0 diffs) before commit.
5. **Preserve causal reasoning.** Any function that orders or compares
   real timestamps must validate non-decreasing chronological order and
   reject (or honestly flag) any violation — never silently proceed.

Any new package violating any of the above is **not aligned with this
codex** and must not be merged without a fresh, explicit architectural
review overriding this freeze.

---

## 7. Migration Guidance

- **No retroactive integration was built.** Series 100–106 do not
  currently call each other in any real, wired pipeline — every
  cross-series "consumption" demonstrated in this stack's golden replay
  tests is a caller (a test, or a future orchestration script)
  constructing the appropriate `*View` translation by hand. Wiring a real
  end-to-end daily pipeline (Series 99 → 100 → ... → 106, automatically,
  post-close) is explicitly **not** part of this freeze and would itself
  be a new, reviewable piece of work — likely orchestration code outside
  any of the 7 packages, not a new package.
- **Existing reports remain valid.** Nothing in Series 107 changes any
  prior sprint's stored journal format or invalidates any earlier
  finding (Sprints 115–122's investigation reports remain accurate
  descriptions of the codebase state at the time they were written).
- **The first real Engineering Proposal**, whenever evidence and a human
  decision justify one, is a new, ordinary engineering sprint — full
  Sprint 114A-style discipline (root cause, deterministic fix, replay
  parity, regression, commit) — not a new learning-stack package.

---

## 8. Architecture Freeze Notice

**Effective as of this document, the BUJJI Learning Architecture (Series
99–106) is FROZEN.**

- No further learning infrastructure (new engine, new package, new
  journal, new replay logic) shall be added without an explicit,
  separate architectural review that revisits this codex.
- All future work under this initiative shifts from **building
  infrastructure** to **collecting real-world evidence** — running
  Live Shadow Sessions, letting Series 99 accumulate real
  `DecisionRecord`/`OutcomeRecord` pairs, and only then constructing the
  real `*View` translations this stack's golden tests already prove work
  correctly.
- This codex, `docs/LEARNING_ARCHITECTURE_CODEX.md`, is the canonical
  reference for the frozen architecture. Any future proposal that
  contradicts a Law in §2 or a boundary in §4/§5 must explicitly amend
  this document, not silently diverge from it.

No code was written in this sprint. No new engines, models, journals,
packages, or learning logic were created. This document only observes
and freezes what Series 99–106 already, verifiably, are.
