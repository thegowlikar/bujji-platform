"""Tests for bujji.msi_evidence_packet -- Series 101.

Covers: model correctness, immutability enforcement (Deliverable 5),
traceability graph (Deliverable 6), golden replay tests (Deliverable 7),
replay reconstruction / reproducibility tests (Deliverable 8)."""
from __future__ import annotations

import dataclasses

import pytest

from bujji.msi_evidence_packet import engine as eps_engine
from bujji.msi_evidence_packet import query as eps_query
from bujji.msi_evidence_packet import serialization as eps_serialization
from bujji.msi_evidence_packet import taxonomy as eps_taxonomy
from bujji.msi_evidence_packet.journal import EvidencePacketJournal, ImmutabilityViolation
from bujji.msi_evidence_packet.models import Metric

TS = "2026-07-20T15:30:00"


def _mk_packet(iv_rank="12.5", classification="RANGE"):
    return eps_engine.build_evidence_packet(
        decision_record_ids=["d1"], outcome_record_ids=["o1"], journal_references=["j1"],
        market_recorder_session="sess1", replay_session="replay1", production_version="551d494",
        mle_version="1.0.0", replay_version="1.0.0", market_classification=classification,
        trading_day_classification="RANGE", regime="LOW_VOL", expiry_context="WEEKLY",
        volatility_context="COMPRESSED", statistics=[Metric("iv_rank", iv_rank, "pct")],
        created_timestamp=TS,
    )


# --- Deliverable 1: EvidencePacket model, "No Opinions" ---

def test_evidence_packet_has_no_recommendation_or_confidence_field():
    """Structural check of the mission's own 'No Opinions' requirement --
    there is nowhere on this dataclass to put a recommendation."""
    field_names = {f.name for f in dataclasses.fields(_mk_packet())}
    forbidden = {"recommendation", "suggestion", "optimisation", "optimization",
                 "threshold_change", "strategy_preference", "confidence", "conclusion"}
    assert field_names.isdisjoint(forbidden)


def test_evidence_packet_contains_all_required_identity_fields():
    p = _mk_packet()
    assert p.packet_id and p.created_timestamp and p.mle_version and p.production_version and p.replay_version


def test_evidence_packet_contains_all_required_source_evidence_fields():
    p = _mk_packet()
    assert p.decision_record_ids == ("d1",)
    assert p.outcome_record_ids == ("o1",)
    assert p.journal_references == ("j1",)
    assert p.market_recorder_session == "sess1"
    assert p.replay_session == "replay1"


def test_evidence_packet_contains_all_required_context_fields():
    p = _mk_packet()
    assert p.market_classification and p.trading_day_classification and p.regime
    assert p.expiry_context and p.volatility_context


# --- Deliverable 5: Immutability ---

def test_dataclass_mutation_raises_frozen_instance_error():
    p = _mk_packet()
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.market_classification = "TREND"  # type: ignore[misc]


def test_same_real_inputs_always_produce_same_packet_id():
    """A genuine correction must produce a DIFFERENT id -- verified by
    the converse: identical real inputs NEVER produce a different id."""
    p1 = _mk_packet()
    p2 = _mk_packet()
    assert p1.packet_id == p2.packet_id
    assert p1 == p2


def test_different_real_inputs_produce_different_packet_id():
    p1 = _mk_packet(iv_rank="12.5")
    p2 = _mk_packet(iv_rank="99.9")  # a genuine correction
    assert p1.packet_id != p2.packet_id


def test_journal_refuses_to_silently_overwrite_conflicting_content(tmp_path):
    """The concrete enforcement: this should be structurally impossible
    (packet_id is a content hash), but the journal defends against it
    anyway as a hard error, never a silent overwrite."""
    journal = EvidencePacketJournal(tmp_path)
    p = _mk_packet()
    journal.record_packet(p)
    # Simulate a real bug that produced a colliding id with different
    # content (should never happen given content hashing, but the
    # journal's own defence must still fire).
    forged = dataclasses.replace(p, market_classification="TREND")
    object.__setattr__(forged, "packet_id", p.packet_id)  # force a real id collision to test the defence
    with pytest.raises(ImmutabilityViolation):
        journal.record_packet(forged)


def test_journal_allows_recording_the_same_real_packet_twice_idempotently(tmp_path):
    """Re-recording the identical real packet (e.g. a retried write) is
    NOT an immutability violation -- content matches exactly."""
    journal = EvidencePacketJournal(tmp_path)
    p = _mk_packet()
    journal.record_packet(p)
    journal.record_packet(p)  # must not raise
    assert journal.packet_by_id(p.packet_id) == p


def test_journal_failed_write_is_tracked(tmp_path):
    journal = EvidencePacketJournal(tmp_path)
    journal._packets._path = tmp_path / "nonexistent" / "packets.jsonl"
    journal.record_packet(_mk_packet())
    assert journal.failed_writes == 1


# --- Deliverable 8: Reproducibility / replay reconstruction ---

def test_regenerating_a_packet_from_the_same_real_inputs_is_byte_identical():
    """Given the same real decision journal / replay session / production
    version / mle version, another engineer must be able to regenerate
    the identical packet -- this is exactly that guarantee, tested."""
    p1 = _mk_packet()
    p2 = _mk_packet()
    assert eps_serialization.packet_to_dict(p1) == eps_serialization.packet_to_dict(p2)


def test_serialization_round_trip_preserves_full_state():
    p = _mk_packet()
    text = eps_serialization.packet_to_json(p)
    recovered = eps_serialization.packet_from_json(text)
    assert recovered == p


# --- Deliverable 6: Traceability graph ---

def test_engineering_proposal_requires_at_least_one_real_reference():
    with pytest.raises(ValueError):
        eps_engine.build_engineering_proposal(
            knowledge_candidate_ids=[], evidence_packet_ids=[],
            proposed_change="do something", reasoning=[], created_timestamp=TS,
        )


def test_link_rejects_illegal_edge_direction():
    p = _mk_packet()
    with pytest.raises(ValueError):
        eps_engine.link(eps_taxonomy.NODE_PRODUCTION_RULE, "rule1",
                         eps_taxonomy.NODE_EVIDENCE_PACKET, p.packet_id, timestamp=TS)


def test_full_traceability_chain_walkable_backward_from_production_rule():
    p = _mk_packet()
    prop = eps_engine.build_engineering_proposal(
        knowledge_candidate_ids=["KC-1"], evidence_packet_ids=[p.packet_id],
        proposed_change="wire DOMAIN_LIQUIDITY", reasoning=["Sprint 116 finding"], created_timestamp=TS,
    )
    edges = [
        eps_engine.link(eps_taxonomy.NODE_EVIDENCE_PACKET, p.packet_id,
                         eps_taxonomy.NODE_KNOWLEDGE_CANDIDATE, "KC-1", timestamp=TS),
        eps_engine.link(eps_taxonomy.NODE_KNOWLEDGE_CANDIDATE, "KC-1",
                         eps_taxonomy.NODE_ENGINEERING_PROPOSAL, prop.proposal_id, timestamp=TS),
        eps_engine.link(eps_taxonomy.NODE_ENGINEERING_PROPOSAL, prop.proposal_id,
                         eps_taxonomy.NODE_PRODUCTION_RULE, "RULE-1", timestamp=TS),
    ]
    ancestry = eps_query.trace_ancestry(edges, eps_taxonomy.NODE_PRODUCTION_RULE, "RULE-1")
    ancestry_pairs = {(e.from_type, e.from_id) for e in ancestry}
    assert (eps_taxonomy.NODE_ENGINEERING_PROPOSAL, prop.proposal_id) in ancestry_pairs
    assert (eps_taxonomy.NODE_KNOWLEDGE_CANDIDATE, "KC-1") in ancestry_pairs
    assert (eps_taxonomy.NODE_EVIDENCE_PACKET, p.packet_id) in ancestry_pairs


def test_node_with_no_incoming_edge_returns_empty_ancestry_honestly():
    ancestry = eps_query.trace_ancestry([], eps_taxonomy.NODE_PRODUCTION_RULE, "RULE-UNKNOWN")
    assert ancestry == ()


def test_referencing_finds_which_knowledge_candidates_cite_a_packet():
    p = _mk_packet()
    edges = [
        eps_engine.link(eps_taxonomy.NODE_EVIDENCE_PACKET, p.packet_id,
                         eps_taxonomy.NODE_KNOWLEDGE_CANDIDATE, "KC-1", timestamp=TS),
        eps_engine.link(eps_taxonomy.NODE_EVIDENCE_PACKET, p.packet_id,
                         eps_taxonomy.NODE_KNOWLEDGE_CANDIDATE, "KC-2", timestamp=TS),
    ]
    # Edge direction, per taxonomy.py's disclosed convention, mirrors the
    # mission's own ancestry diagram (Evidence -> Knowledge): the edge
    # points FROM the packet TO the citing candidate. "Which candidates
    # reference this packet" is therefore `referenced_by` (edges
    # originating at the packet), not `referencing` (edges pointing at it).
    refs = eps_query.referenced_by(edges, eps_taxonomy.NODE_EVIDENCE_PACKET, p.packet_id)
    assert {e.to_id for e in refs} == {"KC-1", "KC-2"}


# --- Deliverable 7: Golden replay tests ---

def test_golden_replay_eps_construction_does_not_touch_production_state():
    """Constructs a real DecisionRecord via bujji.msi_decision_auditor,
    then a real Evidence Packet citing it, then re-derives the same
    DecisionRecord a second time -- byte-identical, proving EPS
    construction has zero effect on Production's own record-building."""
    from bujji.msi_decision_auditor import engine as da_engine
    from bujji.msi_trade_thesis.models import Explanation as ThesisExplanation, TradeThesisAssessment

    thesis = TradeThesisAssessment(
        assessment_id="thesis-eps-golden-1", timestamp=TS, thesis_type="RANGE_PERSISTENCE",
        market_expectation="range-bound", expected_move=None, expected_time_horizon="INTRADAY",
        volatility_expectation="STABLE", directional_expectation="NEUTRAL", conviction="HIGH",
        invalidation_conditions=(), supporting_domains=(), conflicting_domains=(),
        explanation=ThesisExplanation(
            assessment_id="thesis-eps-golden-1", why_this_thesis=("real thesis",),
            supporting_evidence=(), conflicting_evidence=(), what_would_invalidate=(),
            schema_version="1.0.0",
        ),
        provenance="test", schema_version="1.0.0",
    )
    before = da_engine.build_decision_record(
        "2026-05-25", (), (), "NEUTRAL", "UNANIMOUS_CONSENSUS", "STABLE",
        thesis, None, None, None, None, None, None, timestamp=TS,
    )
    decision = da_engine.build_decision_record(
        "2026-05-25", (), (), "NEUTRAL", "UNANIMOUS_CONSENSUS", "STABLE",
        thesis, None, None, None, None, None, None, timestamp=TS,
    )
    packet = eps_engine.build_evidence_packet(
        decision_record_ids=[decision.decision_id], outcome_record_ids=[],
        journal_references=[], market_recorder_session="golden-session", replay_session="golden-replay",
        production_version="551d494", mle_version="1.0.0", replay_version="1.0.0",
        market_classification="RANGE", trading_day_classification="RANGE", regime="LOW_VOL",
        expiry_context="WEEKLY", volatility_context="COMPRESSED", created_timestamp=TS,
    )
    after = da_engine.build_decision_record(
        "2026-05-25", (), (), "NEUTRAL", "UNANIMOUS_CONSENSUS", "STABLE",
        thesis, None, None, None, None, None, None, timestamp=TS,
    )
    assert before == after
    assert packet.decision_record_ids == (decision.decision_id,)
