# Evidence Packet System (EPS) — Series 101 Architecture

## What this is

EPS is the immutable, factual layer beneath the Market Learning Engine
(Series 100). Where a `KnowledgeCandidate` (MLE) is an *interpretation*
("this pattern looks repeatable"), an `EvidencePacket` (EPS) is the
*fact* it's built from — real decision/outcome ids, real context, real
measurements, never a conclusion. Per the mission's own first principle:

> A Knowledge Candidate is NOT evidence. It is an interpretation of
> evidence. Evidence must exist independently.

## Package structure

`bujji/msi_evidence_packet/`, same 9-file-family house convention as
Series 99/100:

| File | Responsibility |
|---|---|
| `taxonomy.py` | The traceability graph's node-type vocabulary (`EVIDENCE_PACKET`/`KNOWLEDGE_CANDIDATE`/`ENGINEERING_PROPOSAL`/`PRODUCTION_RULE`) and the disclosed set of allowed edge directions. Deliberately minimal — this package stores facts, not judgments, so it has no confidence scale, no scoring vocabulary. |
| `config.py` | Provenance/schema-version constants only — no tunable thresholds exist in this package. |
| `models.py` | Frozen dataclasses: `EvidencePacket`, `Metric`, `EngineeringProposal`, `TraceabilityEdge`. |
| `engine.py` | Pure functions: `build_evidence_packet`, `build_engineering_proposal`, `link`. Zero Production imports (isolation, verified). |
| `serialization.py` | Dict/JSON round-trip. |
| `journal.py` | `EvidencePacketJournal` — three append-only JSONL stores (packets, proposals, edges), plus `ImmutabilityViolation`, the concrete enforcement described below. |
| `query.py` | The real Traceability Graph: `referencing`/`referenced_by`/`trace_ancestry`, built by walking `TraceabilityEdge` records — never stored as a back-reference on the (immutable) packet itself. |

## Immutability (Deliverable 5) — how it's actually enforced, not just documented

Three independent, real layers:

1. **`frozen=True` on every dataclass** — a structural guarantee; Python
   raises `FrozenInstanceError` on any attempted attribute write
   (test-verified: `test_dataclass_mutation_raises_frozen_instance_error`).
2. **Content-hash identity** — `packet_id`/`proposal_id`/`edge_id` are
   `hashlib.md5` hashes of the object's real content. This means the
   *only* way to get a new id is to have genuinely different content —
   "corrections create a new Evidence Packet" isn't a rule engineers have
   to remember to follow; it's what the id computation already does
   (test-verified: `test_same_real_inputs_always_produce_same_packet_id`,
   `test_different_real_inputs_produce_different_packet_id`).
3. **`EvidencePacketJournal`'s own defence** — a genuine content collision
   under the same id should be structurally impossible given (2), but the
   journal checks for it anyway on every `record()` call and raises
   `ImmutabilityViolation` rather than silently overwriting (test-verified
   with a forged collision: `test_journal_refuses_to_silently_overwrite_
   conflicting_content`). Re-recording the *identical* real packet (e.g. a
   retried write) is explicitly allowed and idempotent — only a genuine
   conflict is rejected.

## Reproducibility (Deliverable 8)

`build_evidence_packet` is a pure function of its real inputs — the same
`decision_record_ids`/`outcome_record_ids`/`market_recorder_session`/
`replay_session`/`production_version`/`mle_version`/`replay_version`/
context fields/statistics always produce the byte-identical packet,
including the same `packet_id` (test-verified:
`test_regenerating_a_packet_from_the_same_real_inputs_is_byte_identical`).
This is the literal mechanism behind the mission's own reproducibility
requirement: given the same real Decision Journal, Replay Session, Market
Recorder, Production Version, and MLE Version, another engineer
regenerates the identical packet — because the function has no hidden
state, no wall-clock read, no randomness.

## Traceability graph (Deliverable 6)

Edges are directed, real, and immutable, following exactly the mission's
own diagram direction: `EVIDENCE_PACKET → KNOWLEDGE_CANDIDATE →
ENGINEERING_PROPOSAL → PRODUCTION_RULE` (plus one disclosed shortcut,
`EVIDENCE_PACKET → ENGINEERING_PROPOSAL`, since a proposal may cite
evidence directly per the mission's own linking description).
`taxonomy.is_allowed_edge` rejects any other direction — e.g. a
`PRODUCTION_RULE → EVIDENCE_PACKET` edge (which would let ancestry be
asserted backward instead of derived) is refused with `ValueError`
(test-verified: `test_link_rejects_illegal_edge_direction`).

Crucially, **an `EvidencePacket` never stores who references it** — that
would violate immutability (the packet would need to be edited every time
a new Knowledge Candidate cited it). Instead, `query.trace_ancestry` walks
the real, separately-journaled edge set backward from any node, at read
time — a full real ancestry chain (`Production Rule ← Engineering
Proposal ← Knowledge Candidate ← Evidence Packet`) is available without
ever mutating a packet (test-verified:
`test_full_traceability_chain_walkable_backward_from_production_rule`).

## Engineering Proposals

`EngineeringProposal` links real `KnowledgeCandidate`/`EvidencePacket` ids
to a disclosed, plain-language `proposed_change` string — never
implementation, never code. `build_engineering_proposal` refuses to
construct a proposal with zero real references (test-verified:
`test_engineering_proposal_requires_at_least_one_real_reference`) — an
opinion with nothing behind it is exactly what this whole system exists
to prevent, structurally, not by review discipline alone.

## Isolation (mirrors Series 100 exactly)

`tests/test_eps_isolation.py`: no Production module imports EPS (the
forbidden path), and EPS never imports any Production decision/execution/
broker package or references order-placing functions. Same AST-based
approach as `tests/test_mle_isolation.py`, run as part of the standing
pytest gate.

## Golden replay tests (Deliverable 7) / Replay compatibility

`test_golden_replay_eps_construction_does_not_touch_production_state`:
builds a real `DecisionRecord`, builds a real `EvidencePacket` citing its
real `decision_id`, re-derives the same `DecisionRecord` a second time —
byte-identical. Additionally, the full 41-day real corpus replay
(`/tmp/sprint120_replay.py`, same script used throughout Sprints
115–122 and Series 100) was re-run with `bujji.msi_evidence_packet`
present: **zero diffs** across thesis, conviction, selected family,
construction type, portfolio approval state, margin, and both real
assessment IDs, all 41 days.

## Migration notes (Deliverable 10)

- **No existing code changes.** EPS is purely additive — nothing under
  `bujji.msi_decision_auditor` or `bujji.msi_market_learning` was
  modified to accommodate it. A future integration (not built in this
  sprint) would have MLE's `KnowledgeCandidate.supporting_evidence`
  optionally cite a real `EvidencePacket.packet_id` in addition to raw
  Series 99 ids — additive, backward-compatible, exactly like every prior
  schema extension in this project (Sprint 116's `per_family_assessment`,
  Sprint 120's `expression_advisories`).
- **No retroactive packets.** Existing Series 99/100 journal entries are
  not backfilled into Evidence Packets by this sprint — EPS starts
  recording from whenever it is first wired into a real workflow, and
  historical entries remain valid, interpretable Series 99/100 records on
  their own.
- **What's NOT built yet, deliberately**: nothing yet *calls*
  `build_evidence_packet` from a real workflow — Series 101 is
  infrastructure only, same discipline as Series 100. Wiring EPS into an
  actual post-close pipeline (e.g. MLE's future analysis layer
  constructing a packet before building a `KnowledgeCandidate`) is a
  separate, future, reviewed sprint.

## Verification summary

- Regression: **2896/2896 passing** (2874 pre-existing + 22 new EPS
  tests), zero pre-existing tests modified.
- Replay parity: **0 diffs** across all 41 real corpus days.
- Isolation: verified both structurally (AST) and behaviourally (golden
  replay), same standing pytest gate as Series 100.

## Explicitly not implemented (per mission's own Non-Goals)

Counterfactual Engine, Missed Opportunity Detector, Knowledge scoring,
recommendations, adaptive logic, any Production change. Implementation
stops here — do not begin counterfactual analysis without review.
