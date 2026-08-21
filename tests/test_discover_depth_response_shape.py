"""Phase 17F.1.2.2 — depth response shape discovery script.

Same posture as `test_futures_depth_poller.py`: loaded from its file
path (operational script, not a package). Tests the pure logic only --
market-hours gating, the structural-description helper (never
normalizes/renames), and the snapshot-vs-incremental diff signal.
"""
import datetime
import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "discover_depth_response_shape.py"
_spec = importlib.util.spec_from_file_location("discover_depth_response_shape", _SCRIPT_PATH)
discover = importlib.util.module_from_spec(_spec)
sys.modules["discover_depth_response_shape"] = discover
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


def test_describe_never_renames_or_normalizes_keys():
    """The structural description must preserve the original key names
    exactly -- normalizing here would defeat the entire point of this
    script."""
    raw = {"oi": 100, "pdoi": 90, "weirdFyersKey123": [{"nested": True}]}
    described = discover._describe(raw)
    assert set(described.keys()) == {"oi", "pdoi", "weirdFyersKey123"}
    assert described["oi"] == "int"


def test_describe_handles_empty_list_without_fabricating_shape():
    described = discover._describe({"bids": []})
    assert described["bids"] == "<empty list>"


def test_describe_handles_none_row():
    report = discover._structural_report("futures_poll_1", None)
    assert report["row_present"] is False


def test_describe_reports_real_row_shape():
    report = discover._structural_report("futures_poll_1", {"oi": 100, "ltp": 24650.0})
    assert report["row_present"] is True
    assert report["top_level_keys"] == ["ltp", "oi"]
    assert report["example"] == {"oi": 100, "ltp": 24650.0}


def test_diff_signal_flags_missing_keys_between_polls():
    first = {"oi": 100, "bids": [{"p": 1}, {"p": 2}]}
    second = {"oi": 105}
    diff = discover._diff_poll_pair(first, second)
    assert diff["comparable"] is True
    assert diff["keys_only_in_first"] == ["bids"]
    assert diff["keys_only_in_second"] == []


def test_diff_signal_reports_list_length_deltas():
    first = {"bids": [{"p": 1}, {"p": 2}, {"p": 3}]}
    second = {"bids": [{"p": 1}]}
    diff = discover._diff_poll_pair(first, second)
    assert diff["shared_list_field_length_deltas"]["bids"] == {"first_len": 3, "second_len": 1}


def test_diff_signal_not_comparable_when_either_poll_missing():
    assert discover._diff_poll_pair(None, {"oi": 1})["comparable"] is False
    assert discover._diff_poll_pair({"oi": 1}, None)["comparable"] is False


@pytest.mark.asyncio
async def test_run_aborts_outside_market_hours_without_importing_broker(monkeypatch):
    monkeypatch.setattr(discover, "within_market_hours", lambda now: False)
    result = await discover.run(option_symbol=None)
    assert result == 1
