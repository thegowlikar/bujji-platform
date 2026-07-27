"""Tests — Engineering Series 68: OI/VIX shadow investigation
(bujji-side transport completion only)."""
from bujji.mic_replay.observation_adapter import (
    build_observation_payload,
    build_option_chain_payload,
)
from bujji.replay.historical_session import HistoricalSessionRecord, OptionLiquiditySnapshot


def _record(**kwargs):
    base = dict(
        session_id="S1",
        trading_date="2026-07-22",
        timestamp="2026-07-22T15:30:00",
        spot=23996.25,
        option_chain_entries=(
            (24000.0, "CE", "2026-07-28", "NIFTY26JUL24000CE"),
            (24000.0, "PE", "2026-07-28", "NIFTY26JUL24000PE"),
        ),
        option_chain_expiries=("2026-07-28",),
    )
    base.update(kwargs)
    return HistoricalSessionRecord(**base)


# ---------------------------------------------------------------------------
# OI transport completion
# ---------------------------------------------------------------------------


def test_ce_pe_oi_populated_from_liquidity_snapshots():
    liquidity = (
        OptionLiquiditySnapshot(strike=24000, option_type="CE", expiry="2026-07-28", contract_symbol="X", open_interest=1234),
        OptionLiquiditySnapshot(strike=24000, option_type="PE", expiry="2026-07-28", contract_symbol="X", open_interest=5678),
    )
    record = _record(option_chain_liquidity=liquidity)
    levels = build_option_chain_payload(record)
    assert len(levels) == 1
    assert levels[0]["ce_oi"] == 1234
    assert levels[0]["pe_oi"] == 5678


def test_oi_defaults_to_zero_never_fabricated_when_no_liquidity_supplied():
    record = _record()
    levels = build_option_chain_payload(record)
    assert levels[0]["ce_oi"] == 0
    assert levels[0]["pe_oi"] == 0


def test_oi_defaults_to_zero_when_liquidity_present_but_open_interest_none():
    liquidity = (
        OptionLiquiditySnapshot(strike=24000, option_type="CE", expiry="2026-07-28", contract_symbol="X", open_interest=None),
    )
    record = _record(option_chain_liquidity=liquidity)
    levels = build_option_chain_payload(record)
    assert levels[0]["ce_oi"] == 0


def test_bid_ask_still_never_populated():
    liquidity = (
        OptionLiquiditySnapshot(strike=24000, option_type="CE", expiry="2026-07-28", contract_symbol="X", open_interest=1234, bid=10.0, ask=11.0),
    )
    record = _record(option_chain_liquidity=liquidity)
    levels = build_option_chain_payload(record)
    assert levels[0]["ce_bid"] is None
    assert levels[0]["ce_ask"] is None


# ---------------------------------------------------------------------------
# VIX transport completion
# ---------------------------------------------------------------------------


def test_vix_included_in_payload_when_present():
    record = _record(vix=13.29, vix_as_of="2026-07-22T15:30:00")
    payload = build_observation_payload(record)
    assert payload["vix"] == 13.29


def test_vix_omitted_entirely_when_absent_never_fabricated_as_zero():
    record = _record()
    payload = build_observation_payload(record)
    assert "vix" not in payload


# ---------------------------------------------------------------------------
# No behavioral change / determinism preserved
# ---------------------------------------------------------------------------


def test_payload_still_deterministic():
    liquidity = (
        OptionLiquiditySnapshot(strike=24000, option_type="CE", expiry="2026-07-28", contract_symbol="X", open_interest=1234),
    )
    record = _record(option_chain_liquidity=liquidity, vix=13.29)
    a = build_observation_payload(record)
    b = build_observation_payload(record)
    assert a == b


def test_candle_shape_unaffected_by_series_68():
    record = _record(vix=13.29)
    payload = build_observation_payload(record)
    assert set(payload["candle"].keys()) == {"timestamp", "open", "high", "low", "close", "volume"}
