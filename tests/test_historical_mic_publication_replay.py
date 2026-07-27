"""Tests — Engineering Series 62: historical MIC publication replay."""
import json
import os
from unittest.mock import patch

import pytest

from bujji.mic_replay.compatibility_validator import (
    REQUIRED_FIELDS,
    CompatibilityResult,
    to_pipeline_input_kwargs,
    validate_compatibility,
)
from bujji.mic_replay.observation_adapter import build_observation_payload
from bujji.mic_replay.publication_recorder import PublishedStateRecorder
from bujji.mic_replay.publication_replay import (
    PublicationReplayError,
    PublishedState,
    replay_corpus_published_states,
    replay_published_state_for_session,
)
from bujji.production_runtime.runtime import PipelineInput
from bujji.replay.validator import HistoricalSessionRecord

MIC_V2_AVAILABLE = os.path.exists("/opt/bujji-mic-v2/.venv/bin/python")


def _record(session_id="NIFTY-2026-07-22", trading_date="2026-07-22", spot=23996.25):
    return HistoricalSessionRecord(
        session_id=session_id,
        trading_date=trading_date,
        timestamp=f"{trading_date}T15:30:00",
        spot=spot,
        spot_as_of=f"{trading_date}T15:30:00",
        option_chain_entries=((24000.0, "CE", "2026-07-28", "NIFTY26JUL24000CE"),),
        option_chain_expiries=("2026-07-28",),
        option_chain_as_of=f"{trading_date}T15:30:00",
    )


def _full_state(**overrides):
    base = dict(
        market_context="TRENDING_UP",
        market_opinion="BULLISH",
        context_stability="STABLE",
        calibration="CALIBRATED",
        governance="APPROVED",
        lifecycle="ACTIVE",
        contract="COMPLETE",
        publication_ids={f"{f}_id": f"ID-{f}" for f in REQUIRED_FIELDS},
        candle_count=1,
    )
    base.update(overrides)
    return PublishedState(**base)


def _fake_bridge_result(**overrides):
    base = {
        "market_context": "TRENDING_UP", "context_id": "CTX-1",
        "market_opinion": "BULLISH", "opinion_id": "OPN-1",
        "context_stability": "STABLE", "stability_id": "STB-1",
        "calibration": "CALIBRATED", "calibration_id": "CAL-1",
        "governance": "APPROVED", "governance_id": "GOV-1",
        "lifecycle": "ACTIVE", "lifecycle_id": "LFC-1",
        "contract": "COMPLETE", "contract_record_id": "CTR-1",
        "candle_count": 1,
    }
    base.update(overrides)
    return base


class _FakeCompletedProcess:
    def __init__(self, stdout_obj, returncode=0, stderr=""):
        self.returncode = returncode
        self.stdout = json.dumps(stdout_obj)
        self.stderr = stderr


# ---------------------------------------------------------------------------
# Publication replay driver -- mocked subprocess
# ---------------------------------------------------------------------------


def test_single_session_publication_replay_mocked():
    with patch(
        "bujji.mic_replay.publication_replay.subprocess.run",
        return_value=_FakeCompletedProcess(_fake_bridge_result()),
    ):
        state = replay_published_state_for_session([build_observation_payload(_record())])
    assert state.market_context == "TRENDING_UP"
    assert state.market_opinion == "BULLISH"
    assert state.candle_count == 1


def test_multi_session_publication_replay_mocked():
    r1 = _record(session_id="NIFTY-2026-07-21", trading_date="2026-07-21")
    r2 = _record(session_id="NIFTY-2026-07-22", trading_date="2026-07-22")
    obs = [build_observation_payload(r1), build_observation_payload(r2)]
    with patch(
        "bujji.mic_replay.publication_replay.subprocess.run",
        side_effect=[
            _FakeCompletedProcess(_fake_bridge_result(candle_count=1)),
            _FakeCompletedProcess(_fake_bridge_result(candle_count=2)),
        ],
    ):
        results = replay_corpus_published_states(obs)
    assert len(results) == 2
    assert results[0][1].candle_count == 1
    assert results[1][1].candle_count == 2


def test_empty_replay_corpus_returns_empty_list():
    assert replay_corpus_published_states([]) == []


def test_replay_raises_on_empty_observation_list_for_single_session():
    with pytest.raises(PublicationReplayError):
        replay_published_state_for_session([])


def test_deterministic_publication_output_mocked():
    fake = _fake_bridge_result()
    with patch("bujji.mic_replay.publication_replay.subprocess.run", return_value=_FakeCompletedProcess(fake)):
        a = replay_published_state_for_session([build_observation_payload(_record())])
        b = replay_published_state_for_session([build_observation_payload(_record())])
    assert a == b


def test_replay_never_fabricates_on_subprocess_failure():
    with patch("bujji.mic_replay.publication_replay.subprocess.run", side_effect=OSError("boom")):
        with pytest.raises(PublicationReplayError):
            replay_published_state_for_session([build_observation_payload(_record())])


def test_replay_raises_on_nonzero_exit():
    with patch(
        "bujji.mic_replay.publication_replay.subprocess.run",
        return_value=_FakeCompletedProcess({}, returncode=1, stderr="boom"),
    ):
        with pytest.raises(PublicationReplayError):
            replay_published_state_for_session([build_observation_payload(_record())])


# ---------------------------------------------------------------------------
# Compatibility validator
# ---------------------------------------------------------------------------


def test_compatibility_success_when_all_fields_present_and_recognized():
    result = validate_compatibility(_full_state())
    assert isinstance(result, CompatibilityResult)
    assert result.compatible is True
    assert result.missing_fields == ()
    assert result.unrecognized_fields == {}


def test_compatibility_failure_when_field_missing():
    result = validate_compatibility(_full_state(governance=None))
    assert result.compatible is False
    assert "governance" in result.missing_fields


def test_compatibility_failure_when_field_unrecognized():
    result = validate_compatibility(_full_state(lifecycle="NOT_A_REAL_STATUS"))
    assert result.compatible is False
    assert result.unrecognized_fields.get("lifecycle") == "NOT_A_REAL_STATUS"


def test_unknown_is_not_treated_as_missing():
    result = validate_compatibility(_full_state(market_context="UNKNOWN", market_opinion="INSUFFICIENT_EVIDENCE"))
    assert result.compatible is True
    assert result.missing_fields == ()


def test_to_pipeline_input_kwargs_builds_valid_pipeline_input():
    kwargs = to_pipeline_input_kwargs(_full_state())
    pi = PipelineInput(**kwargs)
    assert pi.market_context == "TRENDING_UP"
    assert pi.contract == "COMPLETE"


def test_to_pipeline_input_kwargs_never_fabricates_missing_field():
    kwargs = to_pipeline_input_kwargs(_full_state(governance=None))
    assert kwargs["governance"] is None


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------


def test_recorder_never_overwrites():
    recorder = PublishedStateRecorder()
    compat = validate_compatibility(_full_state())
    recorder.record("S1", "OBS-1", "t1", _full_state(), compat, "prov", "FP-1")
    first = recorder.records
    recorder.record("S2", "OBS-2", "t2", _full_state(), compat, "prov", "FP-1")
    assert recorder.records[: len(first)] == first
    assert len(recorder) == 2


def test_recorder_entries_are_immutable():
    recorder = PublishedStateRecorder()
    compat = validate_compatibility(_full_state())
    record = recorder.record("S1", "OBS-1", "t1", _full_state(), compat, "prov", "FP-1")
    with pytest.raises(Exception):
        record.replay_identifier = "CHANGED"  # type: ignore[misc]


def test_recorder_captures_all_seven_classifications():
    recorder = PublishedStateRecorder()
    compat = validate_compatibility(_full_state())
    record = recorder.record("S1", "OBS-1", "t1", _full_state(), compat, "prov", "FP-1")
    assert set(record.published_classifications.keys()) == set(REQUIRED_FIELDS)


# ---------------------------------------------------------------------------
# Real MIC v2 subprocess integration (skipped if MIC v2 is unavailable)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not MIC_V2_AVAILABLE, reason="MIC v2 environment not present")
def test_single_session_publication_replay_against_real_mic_v2():
    state = replay_published_state_for_session([build_observation_payload(_record())])
    result = validate_compatibility(state)
    assert result.compatible is True  # all 7 fields present and taxonomy-recognized, even if UNKNOWN/INSUFFICIENT_*


@pytest.mark.skipif(not MIC_V2_AVAILABLE, reason="MIC v2 environment not present")
def test_multi_session_publication_replay_against_real_mic_v2():
    r1 = _record(session_id="NIFTY-2026-07-21", trading_date="2026-07-21", spot=24187.7)
    r2 = _record(session_id="NIFTY-2026-07-22", trading_date="2026-07-22", spot=23996.25)
    results = replay_corpus_published_states([build_observation_payload(r1), build_observation_payload(r2)])
    assert len(results) == 2
    assert results[0][1].candle_count == 1
    assert results[1][1].candle_count == 2


@pytest.mark.skipif(not MIC_V2_AVAILABLE, reason="MIC v2 environment not present")
def test_deterministic_replay_against_real_mic_v2():
    """All seven classifications and six of seven publication IDs are
    fully deterministic. `lifecycle_id` alone is not: it is a
    discovered, disclosed MIC v2 integration finding (never fixed by
    this sprint, per the "do not modify MIC v2" rule) --
    `contract/runner.py::run_replay_with_contract` does not forward a
    `clock`/`timestamp` parameter down to
    `lifecycle/runner.py::run_replay_with_lifecycle`, even though
    `lifecycle/engine.py::derive_lifecycle` itself already supports
    injecting one. See docs/HISTORICAL_MIC_PUBLICATION_REPLAY.md.
    """
    obs = build_observation_payload(_record())
    a = replay_published_state_for_session([obs])
    b = replay_published_state_for_session([obs])

    assert a.market_context == b.market_context
    assert a.market_opinion == b.market_opinion
    assert a.context_stability == b.context_stability
    assert a.calibration == b.calibration
    assert a.governance == b.governance
    assert a.lifecycle == b.lifecycle
    assert a.contract == b.contract
    assert a.candle_count == b.candle_count

    deterministic_id_keys = {
        k: v for k, v in a.publication_ids.items() if k != "lifecycle_id"
    }
    deterministic_id_keys_b = {
        k: v for k, v in b.publication_ids.items() if k != "lifecycle_id"
    }
    assert deterministic_id_keys == deterministic_id_keys_b


@pytest.mark.skipif(not MIC_V2_AVAILABLE, reason="MIC v2 environment not present")
def test_real_published_state_feeds_trading_brain_pipeline_input_without_error():
    state = replay_published_state_for_session([build_observation_payload(_record())])
    kwargs = to_pipeline_input_kwargs(state)
    pi = PipelineInput(**kwargs)  # must not raise -- proves real taxonomy alignment
    assert pi.market_context == state.market_context


@pytest.mark.skipif(not MIC_V2_AVAILABLE, reason="MIC v2 environment not present")
def test_bridge_script_never_references_bujji_or_broker():
    from bujji.mic_replay.publication_replay import _BRIDGE_SCRIPT

    assert "bujji." not in _BRIDGE_SCRIPT
    assert "broker" not in _BRIDGE_SCRIPT.lower()
    assert "dispatch" not in _BRIDGE_SCRIPT.lower()
    assert "authenticate" not in _BRIDGE_SCRIPT.lower()


# ---------------------------------------------------------------------------
# Engineering Series 65: governance certification evidence
# ---------------------------------------------------------------------------


def test_governance_evidence_present_mocked():
    fake = _fake_bridge_result(
        governance="APPROVED_WITH_WARNINGS",
        certification_status="CERTIFIED",
        certification_id="CERT-1",
        certification_deterministic=True,
    )
    with patch("bujji.mic_replay.publication_replay.subprocess.run", return_value=_FakeCompletedProcess(fake)):
        state = replay_published_state_for_session([build_observation_payload(_record())])
    assert state.governance == "APPROVED_WITH_WARNINGS"
    assert state.certification_status == "CERTIFIED"
    assert state.certification_deterministic is True
    assert state.publication_ids["certification_id"] == "CERT-1"


def test_governance_evidence_absent_never_fabricated():
    # A subprocess response with no certification keys at all (e.g. an
    # older bridge run) must never be filled in with a guess -- both
    # fields stay None, exactly Series 64's "missing, never inferred"
    # contract.
    fake = _fake_bridge_result()
    with patch("bujji.mic_replay.publication_replay.subprocess.run", return_value=_FakeCompletedProcess(fake)):
        state = replay_published_state_for_session([build_observation_payload(_record())])
    assert state.certification_status is None
    assert state.certification_deterministic is None
    assert state.publication_ids["certification_id"] is None


def test_governance_deterministic_replay_mocked():
    fake = _fake_bridge_result(
        governance="APPROVED_WITH_WARNINGS", certification_status="CERTIFIED", certification_id="CERT-1",
    )
    with patch("bujji.mic_replay.publication_replay.subprocess.run", return_value=_FakeCompletedProcess(fake)):
        a = replay_published_state_for_session([build_observation_payload(_record())])
        b = replay_published_state_for_session([build_observation_payload(_record())])
    assert a.governance == b.governance
    assert a.certification_status == b.certification_status
    assert a.publication_ids["certification_id"] == b.publication_ids["certification_id"]


def test_governance_field_backward_compatible_default_construction():
    # PublishedState must remain constructible without certification
    # kwargs at all -- pre-Series-65 call sites and fixtures.
    state = _full_state()
    assert state.certification_status is None
    assert state.certification_deterministic is None


def test_governance_evidence_recorded_by_publication_recorder():
    from bujji.mic_replay.compatibility_validator import validate_compatibility

    state = _full_state(governance="APPROVED_WITH_WARNINGS")
    recorder = PublishedStateRecorder()
    compat = validate_compatibility(state)
    record = recorder.record("S1", "OBS-1", "t1", state, compat, "prov", "FP-1")
    assert record.published_classifications["governance"] == "APPROVED_WITH_WARNINGS"


@pytest.mark.skipif(not MIC_V2_AVAILABLE, reason="MIC v2 environment not present")
def test_real_governance_no_longer_defaults_to_rejected():
    """Regression check for the dominant blocker identified across
    Campaign v2/v2.1/Sprint B: governance must now be evidence-driven,
    not the unconditional REJECTED produced when certification was
    never supplied.
    """
    state = replay_published_state_for_session([build_observation_payload(_record())])
    assert state.certification_status is not None
    assert state.certification_status in ("CERTIFIED", "CERTIFIED_WITH_WARNINGS", "NOT_CERTIFIED", "UNKNOWN")
    # Real evidence was supplied to derive_governance() this time --
    # whether it approves or rejects is MIC v2's own real decision, but
    # it must no longer be the "no certification supplied at all" path.
    assert state.governance is not None
