"""Phase 17H.3 — Historical FYERS spot access certification script.

Loaded by file path. `main()`'s live-broker path requires real
credentials; what's tested here is the pure candle-integrity checker
and the access-method distinctness this whole certification exists to
guarantee.
"""
import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "certify_fyers_historical_spot_access.py"
_spec = importlib.util.spec_from_file_location(
    "certify_fyers_historical_spot_access", _SCRIPT_PATH
)
certify_script = importlib.util.module_from_spec(_spec)
sys.modules["certify_fyers_historical_spot_access"] = certify_script
_spec.loader.exec_module(certify_script)


# --- The single most important structural fact this script exists for --------
def test_access_method_is_distinct_from_the_live_quote_access_method():
    """PHASE_17H3 Part 1.1: reusing the live access-method value would
    make CertificationGate.status_for() return CERTIFIED_AVAILABLE for
    historical access that was never certified -- the exact collision
    class already found and fixed once (17G.0 REST-vs-websocket)."""
    assert certify_script.ACCESS_METHOD == "direct_sdk_fyers_historical_rest"
    assert certify_script.ACCESS_METHOD != "direct_sdk_fyers_broker_py"


def test_not_market_hours_gated():
    assert not hasattr(certify_script, "within_market_hours")
    assert not hasattr(certify_script, "MARKET_OPEN")
    assert not hasattr(certify_script, "MARKET_CLOSE")


# --- Candle integrity checker --------------------------------------------------
def test_validate_candles_accepts_real_ascending_series():
    candles = [
        [894240000, 1159.8, 1185.75, 1159.8, 1185.15, 0],
        [894326400, 1185.15, 1200.0, 1180.0, 1190.0, 0],
    ]
    result = certify_script._validate_candles(candles)
    assert result["ok"] is True
    assert result["issues"] == []


def test_validate_candles_rejects_impossible_ohlc():
    candles = [[894240000, 100.0, 50.0, 40.0, 90.0, 0]]  # high < open
    result = certify_script._validate_candles(candles)
    assert result["ok"] is False


def test_validate_candles_rejects_duplicate_epochs():
    candles = [
        [894240000, 100.0, 110.0, 90.0, 105.0, 0],
        [894240000, 100.0, 110.0, 90.0, 105.0, 0],
    ]
    result = certify_script._validate_candles(candles)
    assert result["ok"] is False
    assert any("duplicate" in issue.lower() for issue in result["issues"])


def test_validate_candles_rejects_non_ascending_order():
    candles = [
        [894326400, 100.0, 110.0, 90.0, 105.0, 0],
        [894240000, 100.0, 110.0, 90.0, 105.0, 0],
    ]
    result = certify_script._validate_candles(candles)
    assert result["ok"] is False


def test_validate_candles_handles_empty_list():
    result = certify_script._validate_candles([])
    assert result["ok"] is None
    assert "no candles" in result["issues"][0]


# --- Certification-key mapping reused unchanged --------------------------------
def test_instrument_matches_existing_cert_key_mapping():
    from bujji.market_reality.certification import INSTRUMENT_TYPE_TO_CERT_KEY
    from bujji.market_reality import taxonomy

    assert certify_script.INSTRUMENT == INSTRUMENT_TYPE_TO_CERT_KEY[taxonomy.INSTRUMENT_SPOT]
