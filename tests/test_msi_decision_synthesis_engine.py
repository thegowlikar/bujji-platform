"""Tests for the MSI Decision Synthesis Engine (Series 77, DSE v1)."""
from __future__ import annotations

import ast
import glob
import os
import tempfile

import pytest

from bujji.msi_decision_synthesis import config as dse_config
from bujji.msi_decision_synthesis import engine as dse_engine
from bujji.msi_decision_synthesis import journal as dse_journal
from bujji.msi_decision_synthesis import query as dse_query
from bujji.msi_decision_synthesis import runner as dse_runner
from bujji.msi_decision_synthesis import serialization as dse_serialization
from bujji.msi_decision_synthesis import taxonomy as dse_taxonomy
from bujji.msi_decision_synthesis.models import DomainSignal, MarketOpportunityAssessment


def _sig(domain, state, confidence, evidence_ids=()):
    return DomainSignal(domain_name=domain, state=state, confidence=confidence, evidence_ids=tuple(evidence_ids))


# ---------------------------------------------------------------------------
# Synthesis determinism
# ---------------------------------------------------------------------------
def test_synthesis_determinism():
    signals = (
        _sig(dse_taxonomy.DOMAIN_PRICE_STRUCTURE, "Trending", 0.8, ("ev-1",)),
        _sig(dse_taxonomy.DOMAIN_VOLATILITY_STRUCTURE, "Expanding", 0.75, ("ev-2",)),
        _sig(dse_taxonomy.DOMAIN_LIQUIDITY, "Strong", 0.7, ("ev-3",)),
        _sig(dse_taxonomy.DOMAIN_OPTIONS_MARKET_STRUCTURE, "Neutral", 0.6, ("ev-4",)),
    )
    a1 = dse_engine.synthesize(signals, None, (), timestamp="2026-07-24T09:00:00")
    a2 = dse_engine.synthesize(signals, None, (), timestamp="2026-07-25T15:30:00")  # different wall clock
    assert a1.assessment_id == a2.assessment_id
    assert a1.opportunity_state == a2.opportunity_state
    assert a1.confidence_level == a2.confidence_level

    # Order independence: shuffled signal order still produces the same id.
    shuffled = (signals[3], signals[1], signals[0], signals[2])
    a3 = dse_engine.synthesize(shuffled, None, (), timestamp="2026-07-24T09:00:00")
    assert a1.assessment_id == a3.assessment_id


# ---------------------------------------------------------------------------
# Contradiction preservation + comparative confidence
# ---------------------------------------------------------------------------
def test_contradiction_preservation_and_lower_confidence():
    agreeing_signals = (
        _sig(dse_taxonomy.DOMAIN_PRICE_STRUCTURE, "Trending", 0.8, ("ev-1",)),
        _sig(dse_taxonomy.DOMAIN_VOLATILITY_STRUCTURE, "Expanding", 0.8, ("ev-2",)),
        _sig(dse_taxonomy.DOMAIN_FUTURES_STRUCTURE, "Trending", 0.8, ("ev-3",)),
    )
    # A genuine 2-vs-2 split: the winning lean's supporting count no
    # longer clears the "agreement_count >= 2 at net==1" HIGH carve-out
    # once conflict_count also rises to 2 (net drops to 0), producing a
    # real, measurable confidence drop relative to the all-agreeing case.
    conflicting_signals = (
        _sig(dse_taxonomy.DOMAIN_PRICE_STRUCTURE, "Trending", 0.8, ("ev-1",)),
        _sig(dse_taxonomy.DOMAIN_FUTURES_STRUCTURE, "Trending", 0.8, ("ev-2",)),
        _sig(dse_taxonomy.DOMAIN_VOLATILITY_STRUCTURE, "Range", 0.8, ("ev-3",)),
        _sig(dse_taxonomy.DOMAIN_CROSS_ASSET, "Range", 0.8, ("ev-4",)),
    )

    all_agree = dse_engine.synthesize(agreeing_signals, None, (), timestamp="2026-07-24T09:00:00")
    genuinely_conflicting = dse_engine.synthesize(conflicting_signals, None, (), timestamp="2026-07-24T09:00:00")

    # Contradiction is never suppressed: conflicting_domains is non-empty
    # whenever a genuine disagreement exists.
    assert all_agree.conflicting_domains == ()
    assert genuinely_conflicting.conflicting_domains != ()
    assert dse_taxonomy.DOMAIN_PRICE_STRUCTURE in genuinely_conflicting.conflicting_domains

    # Confidence is measurably lower under contradiction.
    assert (
        dse_taxonomy.CONFIDENCE_RANK[genuinely_conflicting.confidence_level]
        < dse_taxonomy.CONFIDENCE_RANK[all_agree.confidence_level]
    )


# ---------------------------------------------------------------------------
# Evidence lineage
# ---------------------------------------------------------------------------
def test_evidence_lineage_traces_to_input_signals():
    signals = (
        _sig(dse_taxonomy.DOMAIN_PRICE_STRUCTURE, "Trending", 0.8, ("ev-1", "ev-2")),
        _sig(dse_taxonomy.DOMAIN_VOLATILITY_STRUCTURE, "Expanding", 0.7, ("ev-3",)),
    )
    assessment = dse_engine.synthesize(signals, None, (), timestamp="2026-07-24T09:00:00")
    all_input_evidence = {eid for s in signals for eid in s.evidence_ids}
    assert set(assessment.evidence_ids) <= all_input_evidence
    assert set(assessment.evidence_ids) == all_input_evidence  # nothing fabricated, nothing dropped.


# ---------------------------------------------------------------------------
# Confidence-decreases-with-contradiction: explicit monotonic property
# ---------------------------------------------------------------------------
def test_confidence_monotonic_with_conflict():
    agreement_count = 3
    total_considered_prior = None
    ranks = []
    for conflict_count in range(0, 4):
        level = dse_engine._confidence_level(agreement_count, conflict_count, agreement_count + conflict_count)
        ranks.append(dse_taxonomy.CONFIDENCE_RANK[level])
    # Non-increasing as conflict_count increases, for fixed agreement_count.
    for i in range(1, len(ranks)):
        assert ranks[i] <= ranks[i - 1], f"confidence rank increased at conflict_count={i}: {ranks}"
    # And it strictly decreases somewhere across this sweep (real effect, not a no-op).
    assert ranks[-1] < ranks[0]


# ---------------------------------------------------------------------------
# Explanation — all 7 fields genuinely populated for a realistic scenario
# ---------------------------------------------------------------------------
def test_explanation_fields_all_populated():
    signals = (
        _sig(dse_taxonomy.DOMAIN_PRICE_STRUCTURE, "Trending", 0.8, ("ev-1",)),
        _sig(dse_taxonomy.DOMAIN_VOLATILITY_STRUCTURE, "Expanding", 0.75, ("ev-2",)),
        _sig(dse_taxonomy.DOMAIN_LIQUIDITY, "Strong", 0.7, ("ev-3",)),
        _sig(dse_taxonomy.DOMAIN_OPTIONS_MARKET_STRUCTURE, "Neutral", 0.6, ("ev-4",)),
    )
    assessment = dse_engine.synthesize(signals, None, (), timestamp="2026-07-24T09:00:00")
    explanation = dse_engine.build_explanation(signals, assessment, None)

    assert explanation.why and isinstance(explanation.why, str)
    assert len(explanation.why_not) == len(dse_taxonomy.ALL_OPPORTUNITY_STATES) - 1
    assert all(entry for entry in explanation.why_not)
    assert explanation.what_changed is None  # no previous assessment.
    assert explanation.domains_agreeing != ()
    assert explanation.domains_disagreeing != ()
    assert len(explanation.missing_evidence) == len(dse_taxonomy.ALL_MSI_DOMAINS) - 4
    assert explanation.evidence_that_would_increase_confidence != ()

    # what_changed populated on a second cycle with a real previous assessment.
    signals2 = signals + (_sig(dse_taxonomy.DOMAIN_CROSS_ASSET, "Trending", 0.65, ("ev-5",)),)
    assessment2 = dse_engine.synthesize(signals2, assessment, (), timestamp="2026-07-24T09:05:00")
    explanation2 = dse_engine.build_explanation(signals2, assessment2, assessment)
    assert explanation2.what_changed is not None
    assert assessment.assessment_id in explanation2.what_changed


# ---------------------------------------------------------------------------
# Replay / live parity
# ---------------------------------------------------------------------------
def test_replay_live_parity():
    cycles = [
        (
            (_sig(dse_taxonomy.DOMAIN_PRICE_STRUCTURE, "Trending", 0.8, ("ev-1",)),
             _sig(dse_taxonomy.DOMAIN_VOLATILITY_STRUCTURE, "Expanding", 0.75, ("ev-2",))),
            "2026-07-24T09:00:00",
            ("EPS-abc",),
        ),
        (
            (_sig(dse_taxonomy.DOMAIN_PRICE_STRUCTURE, "Range", 0.6, ("ev-3",)),
             _sig(dse_taxonomy.DOMAIN_OPTIONS_MARKET_STRUCTURE, "Neutral", 0.55, ("ev-4",))),
            "2026-07-24T09:05:00",
            ("EPS-abc",),
        ),
    ]

    batch_assessments, batch_explanations = dse_runner.generate_assessments_for_cycles(tuple(cycles))

    stream = dse_runner.DecisionSynthesisStream()
    live_assessments = []
    live_explanations = []
    for domain_signals, timestamp, episode_ids in cycles:
        a, e = stream.handle_cycle(domain_signals, timestamp=timestamp, episode_ids=episode_ids)
        live_assessments.append(a)
        live_explanations.append(e)

    assert len(batch_assessments) == len(live_assessments) == 2
    for b, l in zip(batch_assessments, live_assessments):
        assert b == l
    for b, l in zip(batch_explanations, live_explanations):
        assert b == l


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------
def test_serialization_round_trip():
    signals = (_sig(dse_taxonomy.DOMAIN_PRICE_STRUCTURE, "Trending", 0.8, ("ev-1",)),)
    assessment = dse_engine.synthesize(signals, None, ("EPS-1",), timestamp="2026-07-24T09:00:00")
    explanation = dse_engine.build_explanation(signals, assessment, None)

    a_json = dse_serialization.assessment_to_json(assessment)
    a_back = dse_serialization.assessment_from_json(a_json)
    assert a_back == assessment

    e_json = dse_serialization.explanation_to_json(explanation)
    e_back = dse_serialization.explanation_from_json(e_json)
    assert e_back == explanation


# ---------------------------------------------------------------------------
# Journal — append-only, structurally proven
# ---------------------------------------------------------------------------
def test_journal_append_only_structurally():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "assessments.jsonl")
        j = dse_journal.DecisionSynthesisJournal(path)

        signals1 = (_sig(dse_taxonomy.DOMAIN_PRICE_STRUCTURE, "Trending", 0.8, ("ev-1",)),)
        a1 = dse_engine.synthesize(signals1, None, (), timestamp="2026-07-24T09:00:00")
        e1 = dse_engine.build_explanation(signals1, a1, None)
        j.record_assessment(a1, e1)
        with open(path, "rb") as fh:
            snapshot_1 = fh.read()

        signals2 = signals1 + (_sig(dse_taxonomy.DOMAIN_VOLATILITY_STRUCTURE, "Range", 0.6, ("ev-2",)),)
        a2 = dse_engine.synthesize(signals2, a1, (), timestamp="2026-07-24T09:05:00")
        e2 = dse_engine.build_explanation(signals2, a2, a1)
        j.record_assessment(a2, e2)
        with open(path, "rb") as fh:
            snapshot_2 = fh.read()

        assert snapshot_2.startswith(snapshot_1)
        assert len(snapshot_2) > len(snapshot_1)

        records = j.read_assessments()
        assert len(records) == 2
        assert records[0] == a1
        assert records[1] == a2


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------
def test_query_helpers():
    signals1 = (_sig(dse_taxonomy.DOMAIN_PRICE_STRUCTURE, "Trending", 0.8, ("ev-1",)),)
    a1 = dse_engine.synthesize(signals1, None, ("EPS-1",), timestamp="2026-07-24T09:00:00")
    signals2 = (_sig(dse_taxonomy.DOMAIN_PRICE_STRUCTURE, "Range", 0.6, ("ev-2",)),)
    a2 = dse_engine.synthesize(signals2, a1, ("EPS-2",), timestamp="2026-07-24T09:05:00")

    assessments = (a1, a2)
    assert dse_query.assessment_by_id(assessments, a1.assessment_id) == a1
    assert dse_query.assessments_by_opportunity_state(assessments, a1.opportunity_state) != ()
    assert a1 in dse_query.assessments_in_time_range(assessments, "2026-07-24T08:00:00", "2026-07-24T09:02:00")
    assert dse_query.assessments_for_episode(assessments, "EPS-2") == (a2,)
    assert dse_query.latest_assessment(assessments) == a2
    assert dse_query.latest_assessment(()) is None


# ---------------------------------------------------------------------------
# Deliverable 10 demonstration — synthetic scenario, run twice, byte-identical
# ---------------------------------------------------------------------------
def _deliverable_10_signals():
    return (
        _sig(dse_taxonomy.DOMAIN_PRICE_STRUCTURE, "Trending", 0.8, ("ev-price-1",)),
        _sig(dse_taxonomy.DOMAIN_VOLATILITY_STRUCTURE, "Expanding", 0.75, ("ev-vol-1",)),
        _sig(dse_taxonomy.DOMAIN_LIQUIDITY, "Strong", 0.7, ("ev-liq-1",)),
        _sig(dse_taxonomy.DOMAIN_OPTIONS_MARKET_STRUCTURE, "Neutral", 0.6, ("ev-opt-1",)),
    )


def test_deliverable_10_demonstration_runs_twice_identically():
    signals = _deliverable_10_signals()

    run1 = dse_engine.synthesize(signals, None, (), timestamp="2026-07-24T09:00:00")
    run2 = dse_engine.synthesize(signals, None, (), timestamp="2026-07-25T14:22:00")

    assert run1.assessment_id == run2.assessment_id
    # Full content identical except (deliberately) timestamp, which is
    # never part of assessment_id's hash input (see models.py).
    d1 = dse_serialization.assessment_to_dict(run1)
    d2 = dse_serialization.assessment_to_dict(run2)
    d1.pop("timestamp")
    d2.pop("timestamp")
    assert d1 == d2

    assert run1.opportunity_state == dse_taxonomy.OPPORTUNITY_STATE_DIRECTIONAL
    assert run1.confidence_level == dse_taxonomy.CONFIDENCE_HIGH
    compatible, incompatible = dse_engine.compatible_strategy_families(run1.opportunity_state)
    assert dse_taxonomy.STRATEGY_FAMILY_DEFINED_RISK_DIRECTIONAL in compatible
    assert dse_taxonomy.STRATEGY_FAMILY_UNDEFINED_RISK_PREMIUM in incompatible
    # Options Structure -> Neutral genuinely conflicts with the winning
    # (OPPORTUNITY_FORMING/directional) read in this engine's design.
    assert dse_taxonomy.DOMAIN_OPTIONS_MARKET_STRUCTURE in run1.conflicting_domains


# ---------------------------------------------------------------------------
# AST isolation
# ---------------------------------------------------------------------------
_FORBIDDEN_MODULE_PREFIXES = (
    "mic_v2",
    "bujji.mic_replay",
    "bujji.production_runtime",
    "bujji.trading_brain",
    "bujji.strategy_selector",
    "fyers_apiv3",
)

_FORBIDDEN_MEANING_TERMS = (
    "iron condor",
    "iron fly",
    "straddle",
    "strangle",
    "butterfly",
    "covered call",
    "jade lizard",
    "calendar spread",
    "diagonal spread",
    "strike",
    "expiry",
    "quantity",
    "p&l",
    "pnl",
    "probability of profit",
    "optimi",
)


def _dse_source_files():
    import bujji.msi_decision_synthesis as pkg

    package_dir = os.path.dirname(pkg.__file__)
    return sorted(glob.glob(os.path.join(package_dir, "*.py")))


def test_ast_isolation_no_forbidden_imports():
    for path in _dse_source_files():
        with open(path, "r") as fh:
            source = fh.read()
        tree = ast.parse(source, filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for forbidden in _FORBIDDEN_MODULE_PREFIXES:
                        assert not alias.name.startswith(forbidden), f"{path} imports forbidden module {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                for forbidden in _FORBIDDEN_MODULE_PREFIXES:
                    assert not mod.startswith(forbidden), f"{path} imports from forbidden module {mod}"


def test_ast_isolation_no_uuid4_no_unseeded_randomness():
    for path in _dse_source_files():
        with open(path, "r") as fh:
            source = fh.read()
        tree = ast.parse(source, filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                func_name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                assert func_name != "uuid4", f"{path} calls uuid4()"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in ("random", "uuid"), f"{path} imports {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                assert node.module not in ("random", "uuid"), f"{path} imports from {node.module}"


def test_ast_isolation_no_forbidden_strategy_or_trade_terms():
    for path in _dse_source_files():
        with open(path, "r") as fh:
            source = fh.read().lower()
        for term in _FORBIDDEN_MEANING_TERMS:
            assert term not in source, f"{path} contains forbidden concrete-trade/strategy term {term!r}"
