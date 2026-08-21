"""Phase 17H.6 — VIX and Futures historical access certification scripts.

Mirrors test_certify_fyers_historical_spot_access.py's structure for
both new scripts.
"""
import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, _REPO_ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


vix_script = _load("certify_fyers_historical_vix_access", "certify_fyers_historical_vix_access.py")
fut_script = _load("certify_fyers_historical_futures_access", "certify_fyers_historical_futures_access.py")


# --- VIX -----------------------------------------------------------------------
def test_vix_access_method_is_distinct_from_live_quote_access_method():
    assert vix_script.ACCESS_METHOD == "direct_sdk_fyers_historical_rest"
    assert vix_script.ACCESS_METHOD != "direct_sdk_fyers_broker_py"


def test_vix_not_market_hours_gated():
    assert not hasattr(vix_script, "within_market_hours")
    assert not hasattr(vix_script, "MARKET_OPEN")


def test_vix_instrument_matches_existing_cert_key_mapping():
    from bujji.market_reality.certification import INSTRUMENT_TYPE_TO_CERT_KEY
    from bujji.market_reality import taxonomy
    assert vix_script.INSTRUMENT == INSTRUMENT_TYPE_TO_CERT_KEY[taxonomy.INSTRUMENT_INDEX]


def test_vix_validate_candles_rejects_impossible_ohlc():
    candles = [[894240000, 100.0, 50.0, 40.0, 90.0, 0]]
    assert vix_script._validate_candles(candles)["ok"] is False


# --- Futures ---------------------------------------------------------------------
def test_futures_access_method_is_distinct_from_live_quote_access_method():
    assert fut_script.ACCESS_METHOD == "direct_sdk_fyers_historical_rest"
    assert fut_script.ACCESS_METHOD != "direct_sdk_fyers_broker_py"


def test_futures_not_market_hours_gated():
    assert not hasattr(fut_script, "within_market_hours")
    assert not hasattr(fut_script, "MARKET_OPEN")


def test_futures_instrument_matches_existing_cert_key_mapping():
    from bujji.market_reality.certification import INSTRUMENT_TYPE_TO_CERT_KEY
    from bujji.market_reality import taxonomy
    assert fut_script.INSTRUMENT == INSTRUMENT_TYPE_TO_CERT_KEY[taxonomy.INSTRUMENT_FUTURE]


def test_futures_continuity_method_constant():
    assert fut_script.CONTINUITY_METHOD == "fyers_cont_flag_1"


def test_futures_validate_candles_rejects_impossible_ohlc():
    candles = [[894240000, 100.0, 50.0, 40.0, 90.0, 0]]
    assert fut_script._validate_candles(candles)["ok"] is False
