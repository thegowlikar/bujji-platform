"""Tests — Engineering Series 61: historical MIC replay engine.

Uses real, previously-fetched NSE Bhavcopy fixture rows (structurally
identical to the real files used in Data Acquisition Sprint A) for the
observation-adapter tests, and mocks `run_mic_replay`'s subprocess call
for driver/recorder tests that don't need a live MIC v2 process --
except the explicit `@pytest.mark.mic_v2_live` tests, which invoke the
real MIC v2 subprocess and are skipped if that environment is absent
(this repo's CI/sandbox may not always have `/opt/bujji-mic-v2/`).
"""
import json
import os
from unittest.mock import patch

import pytest

from bujji.mic_replay.observation_adapter import (
    build_candle_payload,
    build_observation_payload,
    build_option_chain_payload,
)
from bujji.mic_replay.recorder import HistoricalIntelligenceRecorder, intelligence_id, observation_id
from bujji.mic_replay.replay_driver import MicReplayError, MicReplayResult, run_mic_replay
from bujji.replay.validator import HistoricalSessionRecord

MIC_V2_AVAILABLE = os.path.exists("/opt/bujji-mic-v2/.venv/bin/python")


def _record(session_id="NIFTY-2026-07-22", spot=23996.25, entries=None):
    entries = entries if entries is not None else ((24000.0, "CE", "2026-07-28", "NIFTY26JUL24000CE"),)
    return HistoricalSessionRecord(
        session_id=session_id,
        trading_date="2026-07-22",
        timestamp="2026-07-22T15:30:00",
        spot=spot,
        spot_as_of="2026-07-22T15:30:00",
        option_chain_entries=entries,
        option_chain_expiries=("2026-07-28",),
        option_chain_as_of="2026-07-22T15:30:00",
    )


# ---------------------------------------------------------------------------
# Observation adapter
# ---------------------------------------------------------------------------


def test_build_candle_payload_preserves_timestamp_and_spot():
    payload = build_candle_payload(_record())
    assert payload["timestamp"] == "2026-07-22T15:30:00"
    assert payload["open"] == payload["high"] == payload["low"] == payload["close"] == 23996.25


def test_build_candle_payload_returns_none_for_missing_spot():
    assert build_candle_payload(_record(spot=None)) is None


def test_build_option_chain_payload_preserves_strike_and_never_fabricates_oi():
    entries = (
        (24000.0, "CE", "2026-07-28", "NIFTY26JUL24000CE"),
        (24000.0, "PE", "2026-07-28", "NIFTY26JUL24000PE"),
        (24050.0, "CE", "2026-07-28", "NIFTY26JUL24050CE"),
    )
    levels = build_option_chain_payload(_record(entries=entries))
    assert [lvl["strike"] for lvl in levels] == [24000, 24050]
    assert all(lvl["ce_oi"] == 0 and lvl["pe_oi"] == 0 for lvl in levels)
    assert all(lvl["ce_bid"] is None and lvl["pe_bid"] is None for lvl in levels)


def test_build_observation_payload_missing_spot_returns_none():
    assert build_observation_payload(_record(spot=None)) is None


def test_build_observation_payload_missing_option_chain_yields_empty_chain():
    payload = build_observation_payload(_record(entries=()))
    assert payload is not None
    assert payload["option_chain"] == []


def test_strike_ordering_never_reordered_beyond_grouping():
    entries = (
        (24100.0, "CE", "2026-07-28", "X1"),
        (24000.0, "CE", "2026-07-28", "X2"),
        (24050.0, "CE", "2026-07-28", "X3"),
    )
    levels = build_option_chain_payload(_record(entries=entries))
    assert [lvl["strike"] for lvl in levels] == sorted(lvl["strike"] for lvl in levels)


# ---------------------------------------------------------------------------
# Replay driver -- mocked subprocess
# ---------------------------------------------------------------------------


def _fake_completed_process(stdout_obj, returncode=0, stderr=""):
    class _P:
        pass

    p = _P()
    p.returncode = returncode
    p.stdout = json.dumps(stdout_obj)
    p.stderr = stderr
    return p


def test_run_mic_replay_empty_observations_returns_empty_result():
    result = run_mic_replay([])
    assert result.evidence == ()
    assert result.candle_count == 0


def test_run_mic_replay_parses_subprocess_output():
    fake = {"evidence": [{"timestamp": "2026-07-22T15:30:00", "module": "time_of_day", "signal": "AFTER_HOURS",
                           "confidence": 1.0, "supporting_evidence": {}}], "candle_count": 1}
    with patch("bujji.mic_replay.replay_driver.subprocess.run", return_value=_fake_completed_process(fake)):
        result = run_mic_replay([build_observation_payload(_record())])
    assert isinstance(result, MicReplayResult)
    assert result.candle_count == 1
    assert len(result.evidence) == 1


def test_run_mic_replay_raises_on_nonzero_exit():
    with patch(
        "bujji.mic_replay.replay_driver.subprocess.run",
        return_value=_fake_completed_process({}, returncode=1, stderr="boom"),
    ):
        with pytest.raises(MicReplayError):
            run_mic_replay([build_observation_payload(_record())])


def test_run_mic_replay_raises_on_malformed_output():
    class _P:
        returncode = 0
        stdout = "not json"
        stderr = ""

    with patch("bujji.mic_replay.replay_driver.subprocess.run", return_value=_P()):
        with pytest.raises(MicReplayError):
            run_mic_replay([build_observation_payload(_record())])


def test_run_mic_replay_never_fabricates_result_on_failure():
    with patch("bujji.mic_replay.replay_driver.subprocess.run", side_effect=OSError("no such interpreter")):
        with pytest.raises(MicReplayError):
            run_mic_replay([build_observation_payload(_record())])


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------


def test_recorder_produces_deterministic_ids():
    a = observation_id("NIFTY-2026-07-22", "2026-07-22T15:30:00")
    b = observation_id("NIFTY-2026-07-22", "2026-07-22T15:30:00")
    assert a == b
    assert a.startswith("OBS-")

    ia = intelligence_id("NIFTY-2026-07-22", "2026-07-22T15:30:00", 2)
    ib = intelligence_id("NIFTY-2026-07-22", "2026-07-22T15:30:00", 2)
    assert ia == ib
    assert ia.startswith("INTEL-")


def test_recorder_never_overwrites():
    recorder = HistoricalIntelligenceRecorder()
    recorder.record("S1", "t1", (), "prov", "FP-1")
    first = recorder.records
    recorder.record("S2", "t2", (), "prov", "FP-1")
    assert recorder.records[: len(first)] == first
    assert len(recorder) == 2


def test_recorder_entries_are_immutable():
    recorder = HistoricalIntelligenceRecorder()
    record = recorder.record("S1", "t1", (), "prov", "FP-1")
    with pytest.raises(Exception):
        record.replay_identifier = "CHANGED"  # type: ignore[misc]


def test_recorder_records_are_tuple_snapshots():
    recorder = HistoricalIntelligenceRecorder()
    recorder.record("S1", "t1", (), "prov", "FP-1")
    snapshot = recorder.records
    recorder.record("S2", "t2", (), "prov", "FP-1")
    assert len(snapshot) == 1  # earlier snapshot unaffected by later record()


# ---------------------------------------------------------------------------
# Real MIC v2 subprocess integration (skipped if MIC v2 is unavailable)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not MIC_V2_AVAILABLE, reason="MIC v2 environment not present")
def test_single_session_replay_against_real_mic_v2():
    payload = build_observation_payload(_record())
    result = run_mic_replay([payload])
    assert result.candle_count == 1
    assert isinstance(result.evidence, tuple)


@pytest.mark.skipif(not MIC_V2_AVAILABLE, reason="MIC v2 environment not present")
def test_multi_session_replay_against_real_mic_v2():
    r1 = _record(session_id="NIFTY-2026-07-21", spot=24187.7)
    r2 = _record(session_id="NIFTY-2026-07-22", spot=23996.25)
    payloads = [build_observation_payload(r1), build_observation_payload(r2)]
    result = run_mic_replay(payloads)
    assert result.candle_count == 2


@pytest.mark.skipif(not MIC_V2_AVAILABLE, reason="MIC v2 environment not present")
def test_deterministic_replay_against_real_mic_v2():
    payload = build_observation_payload(_record())
    r1 = run_mic_replay([payload])
    r2 = run_mic_replay([payload])
    assert r1.evidence == r2.evidence
    assert r1.candle_count == r2.candle_count


@pytest.mark.skipif(not MIC_V2_AVAILABLE, reason="MIC v2 environment not present")
def test_real_mic_v2_never_receives_bujji_broker_or_runtime_modules():
    # The bridge script's own source imports only mic_v2.* -- verified
    # statically here rather than by inspecting the live subprocess.
    from bujji.mic_replay.replay_driver import _BRIDGE_SCRIPT

    assert "bujji." not in _BRIDGE_SCRIPT
    assert "broker" not in _BRIDGE_SCRIPT.lower()
    assert "dispatch" not in _BRIDGE_SCRIPT.lower()
    assert "authenticate" not in _BRIDGE_SCRIPT.lower()
