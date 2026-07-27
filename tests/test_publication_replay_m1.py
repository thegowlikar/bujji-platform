"""Tests — BUJJI-side wiring for MIC v2 Engineering Series M1."""
from unittest.mock import patch

from bujji.mic_replay.observation_adapter import build_observation_payload
from bujji.mic_replay.publication_replay import _BRIDGE_SCRIPT, replay_published_state_for_session
from bujji.replay.historical_session import HistoricalSessionRecord, OptionLiquiditySnapshot


def _record_with_evidence():
    liquidity = (
        OptionLiquiditySnapshot(strike=24000, option_type="CE", expiry="2026-07-28", contract_symbol="X", open_interest=1234),
    )
    return HistoricalSessionRecord(
        session_id="NIFTY-2026-07-22", trading_date="2026-07-22", timestamp="2026-07-22T15:30:00",
        spot=23996.25, spot_as_of="2026-07-22T15:30:00",
        option_chain_entries=((24000.0, "CE", "2026-07-28", "NIFTY26JUL24000CE"),),
        option_chain_expiries=("2026-07-28",), option_chain_liquidity=liquidity,
        vix=13.29, vix_as_of="2026-07-22T15:30:00",
    )


def test_bridge_script_builds_option_chain_and_vix_maps():
    assert "option_chain_by_timestamp" in _BRIDGE_SCRIPT
    assert "vix_by_timestamp" in _BRIDGE_SCRIPT
    assert "OptionChainLevel" in _BRIDGE_SCRIPT


def test_bridge_script_passes_maps_to_contract_and_opinion_runners():
    assert "run_replay_with_contract(" in _BRIDGE_SCRIPT
    assert "option_chain_by_timestamp=option_chain_by_timestamp" in _BRIDGE_SCRIPT
    assert "run_replay_with_opinions(" in _BRIDGE_SCRIPT


def test_observation_payload_still_feeds_the_bridge_unchanged():
    record = _record_with_evidence()
    payload = build_observation_payload(record)
    assert payload["vix"] == 13.29
    assert payload["option_chain"][0]["ce_oi"] == 1234


class _FakeCompletedProcess:
    def __init__(self, stdout_obj, returncode=0, stderr=""):
        import json

        self.returncode = returncode
        self.stdout = json.dumps(stdout_obj)
        self.stderr = stderr


def test_replay_still_works_end_to_end_mocked():
    import json

    fake = {
        "market_context": "TRENDING_UP", "context_id": "CTX-1",
        "market_opinion": "BULLISH", "opinion_id": "OPN-1",
        "context_stability": "STABLE", "stability_id": "STB-1",
        "calibration": "CALIBRATED", "calibration_id": "CAL-1",
        "governance": "APPROVED", "governance_id": "GOV-1",
        "lifecycle": "ACTIVE", "lifecycle_id": "LFC-1",
        "contract": "COMPLETE", "contract_record_id": "CTR-1",
        "candle_count": 1,
    }
    with patch(
        "bujji.mic_replay.publication_replay.subprocess.run",
        return_value=_FakeCompletedProcess(fake),
    ):
        state = replay_published_state_for_session([build_observation_payload(_record_with_evidence())])
    assert state.market_context == "TRENDING_UP"
