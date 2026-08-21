"""Phase 17I.6.2 — Live FYERS Raw Response Discovery.

`scripts/discover_fyers_raw_responses.py` is an operational script loaded
directly from its file path, same posture as every other discovery
script in this project. Its live-broker path (`run()`) requires real
credentials and market hours, so what's tested here is the one part that
doesn't: the pure key-scanning logic (`_all_keys`/
`_candidate_timestamp_keys`) that determines whether a real timestamp
field would actually be surfaced in the report -- a bug here would
silently hide a real finding, which is the one failure mode this script
exists to prevent.
"""
import datetime
import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "discover_fyers_raw_responses.py"
_spec = importlib.util.spec_from_file_location(
    "discover_fyers_raw_responses", _SCRIPT_PATH
)
discover_script = importlib.util.module_from_spec(_spec)
sys.modules["discover_fyers_raw_responses"] = discover_script
_spec.loader.exec_module(discover_script)


# --- Market-hours gate (same pattern as every other discovery script) -----
def test_within_market_hours_true_during_session():
    weekday = datetime.datetime(2026, 8, 12, 10, 0, tzinfo=discover_script.IST)
    assert discover_script.within_market_hours(weekday) is True


def test_within_market_hours_false_before_open():
    early = datetime.datetime(2026, 8, 12, 9, 0, tzinfo=discover_script.IST)
    assert discover_script.within_market_hours(early) is False


def test_within_market_hours_false_on_weekend():
    saturday = datetime.datetime(2026, 8, 15, 10, 0, tzinfo=discover_script.IST)
    assert discover_script.within_market_hours(saturday) is False


# --- _all_keys() -------------------------------------------------------------
def test_all_keys_flattens_nested_dict_paths():
    obj = {"s": "ok", "d": [{"n": "NSE:NIFTY50-INDEX", "v": {"lp": 24270.85}}]}
    keys = discover_script._all_keys(obj)
    assert "s" in keys
    assert "d" in keys
    assert "d[0].n" in keys
    assert "d[0].v" in keys
    assert "d[0].v.lp" in keys


def test_all_keys_handles_dict_shaped_lists_like_depth_response():
    obj = {"d": {"NSE:NIFTY26AUGFUT": {"oi": 12803765, "pdoi": 12645685}}}
    keys = discover_script._all_keys(obj)
    assert "d.NSE:NIFTY26AUGFUT.oi" in keys
    assert "d.NSE:NIFTY26AUGFUT.pdoi" in keys


def test_all_keys_on_empty_structures():
    assert discover_script._all_keys({}) == []
    assert discover_script._all_keys([]) == []
    assert discover_script._all_keys("not a dict or list") == []


# --- _candidate_timestamp_keys() --------------------------------------------
def test_candidate_timestamp_keys_finds_known_keyword_matches():
    obj = {"d": [{"v": {
        "lp": 24270.85, "exch_feed_time": 1755071233, "last_traded_time": 1755071230,
    }}]}
    candidates = discover_script._candidate_timestamp_keys(obj)
    assert "d[0].v.exch_feed_time" in candidates
    assert "d[0].v.last_traded_time" in candidates
    assert "d[0].v.lp" not in candidates  # Not a timestamp-shaped key name.


def test_candidate_timestamp_keys_matches_short_keywords_ts_and_date():
    obj = {"ts": 123, "trade_date": "2026-08-13", "epoch_val": 456}
    candidates = discover_script._candidate_timestamp_keys(obj)
    assert "ts" in candidates
    assert "trade_date" in candidates
    assert "epoch_val" in candidates


def test_candidate_timestamp_keys_finds_the_real_tt_and_ltt_fields():
    """Regression test for a real gap the first live run (2026-08-13)
    exposed: the original keyword list missed FYERS's real `tt` (on
    every plain 'ltp' response) and `ltt` (on 'depth' responses) fields
    entirely, because neither contains "time"/"ts"/"date"/"epoch"/"feed"
    as a substring. Both are real, present-in-production field names --
    this test pins the fix so it can't silently regress."""
    quote_obj = {"d": [{"v": {"lp": 24393.45, "tt": "1786579200"}}]}
    depth_obj = {"d": {"NSE:NIFTY26AUGFUT": {"ltp": 24483.4, "ltt": 1786603984}}}
    assert "d[0].v.tt" in discover_script._candidate_timestamp_keys(quote_obj)
    assert "d.NSE:NIFTY26AUGFUT.ltt" in discover_script._candidate_timestamp_keys(depth_obj)


def test_candidate_timestamp_keys_is_empty_when_nothing_matches():
    obj = {"lp": 24270.85, "volume": 700440, "oi": 12803765}
    assert discover_script._candidate_timestamp_keys(obj) == []


def test_candidate_timestamp_keys_matches_are_case_insensitive():
    obj = {"ExchFeedTIME": 123}
    assert discover_script._candidate_timestamp_keys(obj) == ["ExchFeedTIME"]


# --- report section construction --------------------------------------------
def test_report_section_bundles_keys_candidates_and_raw_response():
    response = {"lp": 24270.85, "exch_feed_time": 1755071233}
    section = discover_script._report_section("SPOT", response)
    assert section["title"] == "SPOT"
    assert "lp" in section["all_keys"]
    assert "exch_feed_time" in section["candidate_timestamp_keys"]
    assert section["raw_response"] == response
