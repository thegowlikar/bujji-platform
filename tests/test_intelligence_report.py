"""Tests -- Shadow Intelligence Report, Phase 11 Upgrade 7. Pure
functions over plain dicts, no server, no broker, no live calls, no
action/trading control anywhere."""
from __future__ import annotations

from bujji.intelligence_report.engine import build_report_dict, render_report_text


def test_empty_inputs_never_raise_and_show_unknown():
    report = build_report_dict({})
    assert report["regime"] == "UNKNOWN"
    assert report["structure"] == "UNKNOWN"
    text = render_report_text(report)
    assert "UNKNOWN" in text


def test_report_never_fabricates_narrative_when_absent():
    report = build_report_dict({})
    assert "Insufficient evidence" in report["narrative"]


def test_report_reflects_real_record_values():
    record = {
        "market_structure": {"structure_location": "NEAR_RESISTANCE"},
        "volatility_structure": {"volatility_regime": "COMPRESSED"},
        "participant_positioning": {"positioning_bias": "BULLISH_POSITIONING"},
        "liquidity": {"tightness": "TIGHT"},
    }
    report = build_report_dict(record)
    assert report["structure"] == "NEAR_RESISTANCE"
    assert report["volatility"] == "COMPRESSED"
    assert report["participants"] == "BULLISH_POSITIONING"
    assert report["liquidity"] == "TIGHT"


def test_unknown_domains_listed_from_completeness_report():
    completeness = {
        "domains": {
            "price_structure": {"status": "COMPLETE"},
            "market_structure": {"status": "UNKNOWN"},
            "liquidity": {"status": "UNKNOWN"},
        }
    }
    report = build_report_dict({}, completeness=completeness)
    assert report["unknowns"] == ["liquidity", "market_structure"]


def test_no_unknowns_disclosed_when_completeness_absent():
    report = build_report_dict({})
    assert report["unknowns"] == []


def test_render_text_has_no_trading_control_language():
    """This is a read-only report -- must never contain action-shaped
    text (buy/sell/place/execute/order)."""
    report = build_report_dict({
        "market_structure": {"structure_location": "INSIDE_RANGE"},
    })
    text = render_report_text(report).lower()
    for forbidden in ("buy", "sell", "place order", "execute", "confirm trade"):
        assert forbidden not in text


def test_render_text_shows_completeness_percentage():
    completeness = {"completeness_score": 82.5, "domains": {}}
    report = build_report_dict({}, completeness=completeness)
    text = render_report_text(report)
    assert "82.5%" in text


def test_render_text_shows_none_disclosed_when_no_unknowns():
    report = build_report_dict({}, completeness={"completeness_score": 100.0, "domains": {}})
    text = render_report_text(report)
    assert "(none disclosed)" in text


def test_regime_duration_shown_when_present():
    regime_memory = {"current_regime": "RANGING", "duration_cycles": 15}
    report = build_report_dict({}, regime_memory=regime_memory)
    text = render_report_text(report)
    assert "running 15 cycles" in text
