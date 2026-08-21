"""Phase 17F.7.1 — option chain premium field discovery script.

Same posture as `test_discover_depth_response_shape.py`: loaded from its
file path (operational script, not a package). Tests the pure logic
only — market-hours gating, row extraction/description never renaming
fields, and the CE/PE key comparison.
"""
import datetime
import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "discover_option_chain_premium_fields.py"
)
_spec = importlib.util.spec_from_file_location("discover_option_chain_premium_fields", _SCRIPT_PATH)
discover = importlib.util.module_from_spec(_spec)
sys.modules["discover_option_chain_premium_fields"] = discover
_spec.loader.exec_module(discover)


def test_within_market_hours_true_during_session():
    weekday = datetime.datetime(2026, 8, 12, 10, 0, tzinfo=discover.IST)
    assert discover.within_market_hours(weekday) is True


def test_within_market_hours_false_outside_session():
    early = datetime.datetime(2026, 8, 12, 8, 0, tzinfo=discover.IST)
    assert discover.within_market_hours(early) is False


def test_within_market_hours_false_on_weekend():
    saturday = datetime.datetime(2026, 8, 15, 10, 0, tzinfo=discover.IST)
    assert discover.within_market_hours(saturday) is False


def test_extract_rows_navigates_without_filtering_or_renaming():
    raw = {
        "s": "ok",
        "data": {"optionsChain": [
            {"strike_price": -1, "option_type": "", "ltp": 24243.1},
            {"strike_price": 24100, "option_type": "CE", "oi": 100, "ltp": 187.35},
        ]},
    }
    rows = discover._extract_rows(raw)
    assert rows == raw["data"]["optionsChain"]  # identical, not reshaped.


def test_extract_rows_handles_none_response():
    assert discover._extract_rows(None) == []


def test_extract_rows_handles_missing_data_key():
    assert discover._extract_rows({"s": "ok"}) == []


def test_describe_never_renames_keys():
    described = discover._describe({"ltp": 187.35, "weirdFyersKey": [{"a": 1}]})
    assert set(described.keys()) == {"ltp", "weirdFyersKey"}


def test_describe_handles_empty_list():
    assert discover._describe({"optionsChain": []})["optionsChain"] == "<empty list>"


def test_field_presence_report_lists_real_raw_keys_only():
    rows = [
        {"strike_price": 24100, "option_type": "CE", "oi": 100, "ltp": 187.35},
        {"strike_price": 24100, "option_type": "PE", "oi": 200, "bid": 12.0},
    ]
    report = discover._field_presence_report(rows)
    assert set(report["all_raw_keys_seen_across_every_row"]) == {
        "strike_price", "option_type", "oi", "ltp", "bid",
    }
    # It must not claim any of these are present just because
    # OptionObservation needs them -- "close"/"settlement" were never in
    # the raw rows above, so they must not silently appear in the "seen" list.
    assert "close" not in report["all_raw_keys_seen_across_every_row"]
    assert "settlement" not in report["all_raw_keys_seen_across_every_row"]


def test_field_presence_report_lists_the_needed_fields_separately():
    report = discover._field_presence_report([])
    assert "close" in report["option_observation_fields_needed"]
    assert "open_interest" in report["option_observation_fields_needed"]


def test_ce_pe_comparison_separates_by_option_type():
    rows = [
        {"strike_price": 24100, "option_type": "CE", "ltp": 187.35, "greeks_delta": 0.5},
        {"strike_price": 24100, "option_type": "PE", "ltp": 91.2},
    ]
    comparison = discover._ce_pe_comparison(rows)
    assert "greeks_delta" in comparison["ce_keys"]
    assert "greeks_delta" not in comparison["pe_keys"]
    assert comparison["keys_only_in_ce"] == ["greeks_delta"]


def test_ce_pe_comparison_ignores_the_underlying_row():
    rows = [{"strike_price": -1, "option_type": "", "ltp": 24243.1}]
    comparison = discover._ce_pe_comparison(rows)
    assert comparison["ce_keys"] == []
    assert comparison["pe_keys"] == []


@pytest.mark.asyncio
async def test_run_aborts_outside_market_hours_without_importing_broker(monkeypatch):
    monkeypatch.setattr(discover, "within_market_hours", lambda now: False)
    result = await discover.run(strike_count=5)
    assert result == 1
