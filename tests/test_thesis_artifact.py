"""D-5: the reasoning behind a decision must survive the session.

Before this, a session persisted "trend_regime=UNKNOWN -> no strategy" and
nothing about the evidence that produced it. These tests pin the record that
now answers "why": the understanding layer's own cycle record, the thesis,
the full 13-family verdict, and the two-string regime the selector was
actually handed -- with absence recorded as absence, never as zero.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.shadow_observatory.thesis_artifact import build_thesis_artifact


@dataclass(frozen=True)
class _Thesis:
    assessment_id: str = "MT-1"
    timestamp: str = "2026-08-20T09:27:30+05:30"
    market_regime: str = "RANGE_BOUND"
    directional_bias: str = "NEUTRAL"
    volatility_environment: str = "LOW_VOL"
    premium_environment: str = "RICH"
    expected_move_environment: str = "CONTAINED"
    positioning_environment: str = "BALANCED"
    liquidity_environment: str = "TIGHT"
    confidence: str = "MODERATE"
    preferred_strategy_families: Tuple[str, ...] = ("SHORT_STRANGLE", "IRON_CONDOR")
    rejected_strategy_families: Tuple[str, ...] = ("LONG_STRADDLE",)
    insufficient_evidence_families: Tuple[str, ...] = ("CALENDAR", "RATIO_SPREAD")
    reasons: Tuple[str, ...] = ("PSI range-bound", "VSB contained")
    supporting_assessment_ids: Tuple[str, ...] = ("PSI-9", "VSB-4")


CYCLE_RECORD = {"psi": {"structure_state": "RANGING"}, "mssi": None, "spot": 24010.5}


class TestTheRecordAnswersWhy:
    def test_the_thirteen_family_verdict_is_persisted_not_discarded(self):
        """The thesis computes a full family verdict that the two-input
        lookup selector never sees. Discarding it was the D-5 finding."""
        a = build_thesis_artifact(thesis=_Thesis(), cycle_record=CYCLE_RECORD)
        v = a["family_verdict"]
        assert v["preferred"] == ["SHORT_STRANGLE", "IRON_CONDOR"]
        assert v["rejected"] == ["LONG_STRADDLE"]
        assert v["insufficient_evidence"] == ["CALENDAR", "RATIO_SPREAD"]
        assert v["families_assessed"] == 5

    def test_insufficient_evidence_is_never_folded_into_rejected(self):
        """'We don't know' and 'we know it doesn't fit' are different facts;
        upstream keeps them apart and so must the artifact."""
        a = build_thesis_artifact(thesis=_Thesis(), cycle_record=CYCLE_RECORD)
        assert set(a["family_verdict"]["rejected"]).isdisjoint(
            a["family_verdict"]["insufficient_evidence"])

    def test_the_regime_handed_to_the_selector_is_recorded(self):
        """Both selectors' inputs in one record: the rich thesis, and the two
        strings the lookup actually got. This is what makes their divergence
        measurable across sessions."""
        a = build_thesis_artifact(thesis=_Thesis(), cycle_record=CYCLE_RECORD,
                                  trend_regime="SIDEWAYS", volatility_regime="LOW_VOL")
        assert a["regime_handed_to_selector"] == {
            "trend_regime": "SIDEWAYS", "volatility_regime": "LOW_VOL"}
        assert a["thesis"]["market_regime"] == "RANGE_BOUND"

    def test_the_cycle_record_is_persisted_verbatim(self):
        a = build_thesis_artifact(thesis=_Thesis(), cycle_record=CYCLE_RECORD)
        assert a["cycle_record"] == CYCLE_RECORD
        assert a["cycle_record_present"] is True

    def test_stability_verdict_and_cycle_number_travel_with_the_record(self):
        stability = {"is_stable": True, "reason": "strides agree", "spots_in_window": 16}
        a = build_thesis_artifact(thesis=_Thesis(), cycle_record=CYCLE_RECORD,
                                  stability=stability, cycle=7)
        assert a["stability"] == stability and a["cycle"] == 7


class TestAbsenceIsRecordedAsAbsence:
    def test_a_missing_field_is_null_and_named_not_zeroed(self):
        @dataclass(frozen=True)
        class _Thin:
            market_regime: str = "UNKNOWN"

        a = build_thesis_artifact(thesis=_Thin(), cycle_record=None)
        assert a["thesis"]["confidence"] is None
        assert "confidence" in a["absent_fields"]
        assert "preferred_strategy_families" in a["absent_fields"]

    def test_no_family_verdict_at_all_counts_none_not_zero(self):
        """Zero families assessed and 'we never assessed' must not read the
        same -- the difference is the whole point of the record."""
        @dataclass(frozen=True)
        class _Thin:
            market_regime: str = "UNKNOWN"

        a = build_thesis_artifact(thesis=_Thin(), cycle_record=None)
        assert a["family_verdict"]["families_assessed"] is None

    def test_an_empty_but_present_verdict_counts_zero(self):
        @dataclass(frozen=True)
        class _Empty:
            market_regime: str = "UNKNOWN"
            preferred_strategy_families: Tuple[str, ...] = ()
            rejected_strategy_families: Tuple[str, ...] = ()
            insufficient_evidence_families: Tuple[str, ...] = ()

        a = build_thesis_artifact(thesis=_Empty(), cycle_record=None)
        assert a["family_verdict"]["families_assessed"] == 0

    def test_a_missing_cycle_record_is_flagged_not_faked(self):
        a = build_thesis_artifact(thesis=_Thesis(), cycle_record=None)
        assert a["cycle_record"] is None and a["cycle_record_present"] is False

    def test_the_artifact_is_json_serialisable(self):
        import json

        a = build_thesis_artifact(thesis=_Thesis(), cycle_record=CYCLE_RECORD,
                                  trend_regime="SIDEWAYS", volatility_regime="LOW_VOL")
        assert json.loads(json.dumps(a)) == a


class TestTheRecorderNeverKillsTheSession:
    def test_a_store_failure_is_captured_not_raised(self):
        """A lost audit record is bad; a killed live session holding an open
        position is worse. Same contract as every other recorder method."""
        from bujji.shadow_observatory.recorder import ShadowObservatoryRecorder

        class _Exploding:
            session_id = "X"

            def append(self, *_a, **_k):
                raise OSError("disk full")

        rec = ShadowObservatoryRecorder(_Exploding())
        rec.record_market_thesis({"record_type": "MARKET_THESIS_DERIVATION"})
        assert rec.internal_errors and "OSError" in rec.internal_errors[0]

    def test_a_good_store_receives_the_record_on_its_own_file(self, tmp_path):
        from bujji.shadow_observatory.recorder import ShadowObservatoryRecorder
        from bujji.shadow_observatory.session_store import SessionStore

        store = SessionStore(tmp_path, "D5-TEST")
        rec = ShadowObservatoryRecorder(store)
        a = build_thesis_artifact(thesis=_Thesis(), cycle_record=CYCLE_RECORD,
                                  trend_regime="SIDEWAYS", volatility_regime="LOW_VOL")
        rec.record_market_thesis(a)
        assert rec.internal_errors == []
        rows = store.read_jsonl("market_thesis.jsonl")
        assert len(rows) == 1
        assert rows[0]["family_verdict"]["preferred"] == ["SHORT_STRANGLE", "IRON_CONDOR"]


class TestTheRunnerActuallyWritesIt:
    """Wiring proof: the record is only real if the runner persists it. A
    pure builder nobody calls is exactly the write-only-memory anti-pattern
    this phase exists to fix."""

    def test_persist_thesis_writes_the_pending_record_with_the_mapped_regime(self, tmp_path):
        import importlib.util
        import logging

        from bujji.shadow_observatory.recorder import ShadowObservatoryRecorder
        from bujji.shadow_observatory.session_store import SessionStore

        spec = importlib.util.spec_from_file_location(
            "runner_d5", REPO_ROOT / "bujji_options_os_runner.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        store = SessionStore(tmp_path, "D5-WIRE")

        class _Stub:
            pass

        stub = _Stub()
        stub._recorder = ShadowObservatoryRecorder(store)
        stub._logger = logging.getLogger("d5")
        stub._pending_thesis_artifact = build_thesis_artifact(
            thesis=_Thesis(), cycle_record=CYCLE_RECORD)

        mod.OptionsOSRunner._persist_thesis(stub, "SIDEWAYS", "LOW_VOL")

        rows = store.read_jsonl("market_thesis.jsonl")
        assert len(rows) == 1
        assert rows[0]["regime_handed_to_selector"]["trend_regime"] == "SIDEWAYS"
        # And it does not double-write on a second call.
        mod.OptionsOSRunner._persist_thesis(stub, "SIDEWAYS", "LOW_VOL")
        assert len(store.read_jsonl("market_thesis.jsonl")) == 1

    def test_nothing_pending_is_a_no_op_not_a_crash(self, tmp_path):
        import importlib.util
        import logging

        spec = importlib.util.spec_from_file_location(
            "runner_d5b", REPO_ROOT / "bujji_options_os_runner.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        class _Stub:
            _logger = logging.getLogger("d5")

        mod.OptionsOSRunner._persist_thesis(_Stub(), "SIDEWAYS", "LOW_VOL")
