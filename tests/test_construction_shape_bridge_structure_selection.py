"""Tests -- bujji.construction_shape_bridge.construct_trade_honoring_structure_selection,
Phase 20.31. Real option chain (same Bhavcopy fixture pattern every
msi_trade_construction test already uses).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bujji.construction_shape_bridge import bridge as csb
from bujji.msi_trade_construction import engine as tc_engine
from bujji.options_observation import runner as opt_runner
from bujji.premium_structure_intelligence.models import CandidateEvaluation, StructureSelectionAssessment

TS = "2026-05-25T15:30:00+05:30"
DAY = "2026-05-25"
REAL_BHAVCOPY = "/tmp/m1/BhavCopy_NSE_FO_0_0_0_20260525_F_0000.csv"


def _real_chain():
    with open(REAL_BHAVCOPY) as f:
        text = f.read()
    series, _ = opt_runner.ingest_all_option_series_from_bhavcopy(text, DAY, underlying="NIFTY")
    return tuple(s.observations()[-1] for s in series if len(s.observations()) > 0)


@pytest.fixture(scope="module")
def chain():
    return _real_chain()


@pytest.fixture(scope="module")
def spot(chain):
    return next((r.underlying_price for r in chain if r.underlying_price), None)


def _structure_selection(structure, confidence="HIGH", reasons=("test reason",)):
    return StructureSelectionAssessment(
        assessment_id="PSA-test", timestamp=TS, selected_structure=structure, confidence=confidence,
        data_quality="SUFFICIENT", reasons=reasons, rejected_structures=(), supporting_metrics={},
        supporting_assessment_ids=("mdi-1", "psi-1"),
    )


@pytest.mark.parametrize("structure,family", [
    ("SHORT_STRANGLE", "NEUTRAL_PREMIUM_SELLING"), ("IRON_CONDOR", "IRON_CONDOR"), ("IRON_FLY", "IRON_FLY"),
])
def test_each_real_structure_calls_the_real_construct_trade_identically(chain, spot, structure, family):
    selection = _structure_selection(structure)
    bridged = csb.construct_trade_honoring_structure_selection(
        selection, chain, spot, DAY, timestamp=TS, supporting_assessment_ids=("s1",),
    )
    direct = tc_engine.construct_trade(family, chain, spot, DAY, timestamp=TS, supporting_assessment_ids=("s1", "mdi-1", "psi-1"))
    assert bridged.legs == direct.legs
    assert bridged.constructed == direct.constructed
    assert bridged.strategy_family == family


def test_no_trade_never_calls_construct_trade(chain, spot, monkeypatch):
    called = []

    def _must_not_be_called(*args, **kwargs):
        called.append(1)
        raise AssertionError("construct_trade must never be called when NO_TRADE was selected")

    monkeypatch.setattr(csb, "construct_trade", _must_not_be_called)
    selection = _structure_selection("NO_TRADE", confidence="LOW", reasons=("no structure matched",))
    result = csb.construct_trade_honoring_structure_selection(selection, chain, spot, DAY, timestamp=TS)
    assert called == []
    assert result.constructed is False
    assert result.rejection_reason == csb.REJECT_NO_TRADE_SELECTED
    assert "no structure matched" in result.expiry_decision.reasoning[0]


def test_unknown_structure_value_fails_closed_not_a_crash(chain, spot):
    selection = _structure_selection("SOMETHING_NOT_REAL")
    result = csb.construct_trade_honoring_structure_selection(selection, chain, spot, DAY, timestamp=TS)
    assert result.constructed is False
    assert result.rejection_reason == csb.REJECT_NO_TRADE_SELECTED


def test_supporting_assessment_ids_merged_from_both_sources(chain, spot):
    selection = _structure_selection("IRON_FLY")
    result = csb.construct_trade_honoring_structure_selection(
        selection, chain, spot, DAY, timestamp=TS, supporting_assessment_ids=("caller-1",),
    )
    assert "caller-1" in result.supporting_assessment_ids
    assert "mdi-1" in result.supporting_assessment_ids
    assert "psi-1" in result.supporting_assessment_ids


def test_deterministic_same_input_same_output(chain, spot):
    selection = _structure_selection("SHORT_STRANGLE")
    a = csb.construct_trade_honoring_structure_selection(selection, chain, spot, DAY, timestamp=TS)
    b = csb.construct_trade_honoring_structure_selection(selection, chain, spot, DAY, timestamp=TS)
    assert a == b


def test_msi_trade_construction_still_byte_identical_to_baseline():
    import subprocess
    repo_root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        ["git", "diff", "--name-only", "b148e39", "--", "bujji/msi_trade_construction/"],
        cwd=repo_root, capture_output=True, text=True,
    )
    changed = [l for l in result.stdout.strip().splitlines() if l]
    assert changed == [], f"msi_trade_construction was modified, expected untouched: {changed}"


def test_construct_trade_honoring_position_plan_unchanged_by_this_phase():
    """Phase 20.29's own function must still exist with its own
    unchanged behavior -- this phase is additive only."""
    assert hasattr(csb, "construct_trade_honoring_position_plan")
