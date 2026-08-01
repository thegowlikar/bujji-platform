"""Tests — Numeric Risk Governor Gate C.4 (margin certification
harness). Zero network access anywhere in this file."""
from __future__ import annotations

import ast
import inspect
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bujji.trading_brain.risk_governor import margin_certification_engine
from bujji.trading_brain.risk_governor.margin_certification_engine import (
    CERTIFICATION_CERTIFIED,
    CERTIFICATION_FAILED,
    CERTIFICATION_INSUFFICIENT_DATA,
    CERTIFICATION_WARNING,
    MarginCertificationCase,
    MarginCertificationEngine,
    build_certification_case,
    format_certification_report,
)
from bujji.trading_brain.risk_governor.margin_comparison_engine import (
    COMPARISON_FAIL,
    COMPARISON_PASS,
    COMPARISON_STALE,
    COMPARISON_UNAVAILABLE,
    COMPARISON_WARNING,
    MarginComparisonReport,
    compare_margin,
)
from bujji.trading_brain.risk_governor.broker_margin_reality_adapter import BrokerMarginSnapshot
from bujji.trading_brain.risk_governor.whole_book_margin_provider import MarginSnapshot


def _clock(iso="2026-08-02T09:15:00+00:00"):
    dt = datetime.fromisoformat(iso)
    return lambda: dt


def _report(status, deviation_fraction=None, reason=None, simulated=400000.0, broker=None):
    return MarginComparisonReport(
        simulated_margin=simulated, broker_margin=broker,
        difference=None if broker is None else broker - simulated,
        deviation_fraction=deviation_fraction, status=status, reason=reason,
    )


def _case(case_id, strategy_type, status, deviation_fraction=None, reason=None, ts="2026-08-02T09:15:00+00:00"):
    return MarginCertificationCase(
        case_id=case_id, strategy_type=strategy_type,
        simulated_margin_snapshot=MarginSnapshot(required_margin=400000.0, margin_verified=True,
                                                  margin_source="SIM", as_of=datetime.fromisoformat(ts), quote=None),
        broker_margin_snapshot=None,
        comparison_report=_report(status, deviation_fraction, reason),
        timestamp=datetime.fromisoformat(ts), metadata={},
    )


# --------------------------------------------------------------------- #
# Basic 1 -- perfect match
# --------------------------------------------------------------------- #

def test_perfect_match_is_certified():
    engine = MarginCertificationEngine()
    cases = [_case(f"c{i}", "STRADDLE", COMPARISON_PASS, deviation_fraction=0.0) for i in range(10)]
    result = engine.certify(cases)
    assert result.certification_status == CERTIFICATION_CERTIFIED
    assert result.average_deviation == 0.0
    assert result.failed_cases == 0


# --------------------------------------------------------------------- #
# Basic 2 -- small deviations
# --------------------------------------------------------------------- #

def test_small_deviations_certified():
    """Matches the spec's own worked example almost exactly: mostly
    PASS, a few WARNING, zero FAIL, low average -> still CERTIFIED."""
    engine = MarginCertificationEngine()
    cases = (
        [_case(f"p{i}", "STRADDLE", COMPARISON_PASS, deviation_fraction=0.02) for i in range(95)]
        + [_case(f"w{i}", "STRADDLE", COMPARISON_WARNING, deviation_fraction=0.08) for i in range(5)]
    )
    result = engine.certify(cases)
    assert result.certification_status == CERTIFICATION_CERTIFIED
    assert result.passed_cases == 95
    assert result.warning_cases == 5
    assert result.failed_cases == 0
    assert result.average_deviation < 0.05


# --------------------------------------------------------------------- #
# Basic 3 -- large deviations
# --------------------------------------------------------------------- #

def test_large_deviations_failed():
    engine = MarginCertificationEngine()
    cases = [_case(f"f{i}", "STRADDLE", COMPARISON_FAIL, deviation_fraction=0.18) for i in range(10)]
    result = engine.certify(cases)
    assert result.certification_status == CERTIFICATION_FAILED
    assert result.average_deviation == pytest.approx(0.18)


# --------------------------------------------------------------------- #
# Edge 4 -- no certification cases
# --------------------------------------------------------------------- #

def test_no_cases_insufficient_data():
    engine = MarginCertificationEngine()
    result = engine.certify([])
    assert result.certification_status == CERTIFICATION_INSUFFICIENT_DATA
    assert result.total_cases == 0
    assert "no certification cases" in " ".join(result.failure_reasons).lower()


# --------------------------------------------------------------------- #
# Edge 5 -- missing broker snapshot (documented choice: excluded, not FAIL)
# --------------------------------------------------------------------- #

def test_all_missing_broker_data_is_insufficient_data_not_failed():
    """Documented design choice: a batch entirely composed of
    unavailable broker comparisons must NEVER read as FAILED (that
    would conflate 'we don't know' with 'the model is wrong') -- must
    be INSUFFICIENT_DATA instead."""
    engine = MarginCertificationEngine()
    cases = [_case(f"u{i}", "STRADDLE", COMPARISON_UNAVAILABLE, reason="broker outage") for i in range(5)]
    result = engine.certify(cases)
    assert result.certification_status == CERTIFICATION_INSUFFICIENT_DATA
    assert result.total_cases == 5
    assert result.comparable_cases == 0
    assert result.excluded_cases == 5


def test_partial_missing_broker_data_excluded_not_counted_as_fail():
    """A minority of unavailable cases mixed with good ones must be
    excluded from stats/status, never silently counted as failures,
    but must still be visible in failure_reasons for transparency."""
    engine = MarginCertificationEngine()
    cases = (
        [_case(f"p{i}", "STRADDLE", COMPARISON_PASS, deviation_fraction=0.01) for i in range(9)]
        + [_case("u1", "STRADDLE", COMPARISON_UNAVAILABLE, reason="broker outage")]
    )
    result = engine.certify(cases)
    assert result.certification_status == CERTIFICATION_CERTIFIED
    assert result.total_cases == 10
    assert result.comparable_cases == 9
    assert result.excluded_cases == 1
    assert any("u1" in r for r in result.failure_reasons)


def test_stale_data_also_excluded_like_unavailable():
    engine = MarginCertificationEngine()
    cases = [_case("s1", "STRADDLE", COMPARISON_STALE, reason="180s old")]
    result = engine.certify(cases)
    assert result.certification_status == CERTIFICATION_INSUFFICIENT_DATA
    assert result.excluded_cases == 1


# --------------------------------------------------------------------- #
# Edge 6 -- mixed strategy results, correct segmentation
# --------------------------------------------------------------------- #

def test_mixed_strategy_segmentation():
    engine = MarginCertificationEngine()
    cases = (
        [_case(f"straddle{i}", "NIFTY_SHORT_STRADDLE", COMPARISON_PASS, deviation_fraction=0.01) for i in range(5)]
        + [_case(f"spread{i}", "BULL_CALL_SPREAD", COMPARISON_PASS, deviation_fraction=0.02) for i in range(5)]
        + [_case(f"multi{i}", "MULTI_POSITION_BOOK", COMPARISON_WARNING, deviation_fraction=0.10) for i in range(5)]
    )
    result = engine.certify(cases)
    breakdown_by_strategy = {s.strategy_type: s for s in result.strategy_breakdown}
    assert breakdown_by_strategy["NIFTY_SHORT_STRADDLE"].status == CERTIFICATION_CERTIFIED
    assert breakdown_by_strategy["BULL_CALL_SPREAD"].status == CERTIFICATION_CERTIFIED
    assert breakdown_by_strategy["MULTI_POSITION_BOOK"].status == CERTIFICATION_WARNING
    assert len(result.strategy_breakdown) == 3


def test_one_strategy_failing_does_not_silently_pass_overall():
    """If one strategy has a real failure, the OVERALL status must
    also reflect it, even though other strategies look fine --
    matches audit item #6's spirit at the whole-portfolio level."""
    engine = MarginCertificationEngine()
    cases = (
        [_case(f"good{i}", "STRADDLE", COMPARISON_PASS, deviation_fraction=0.01) for i in range(20)]
        + [_case("bad1", "EXOTIC_SPREAD", COMPARISON_FAIL, deviation_fraction=0.30)]
    )
    result = engine.certify(cases)
    assert result.certification_status == CERTIFICATION_FAILED
    breakdown_by_strategy = {s.strategy_type: s for s in result.strategy_breakdown}
    assert breakdown_by_strategy["EXOTIC_SPREAD"].status == CERTIFICATION_FAILED
    assert breakdown_by_strategy["STRADDLE"].status == CERTIFICATION_CERTIFIED


# --------------------------------------------------------------------- #
# Edge 7 -- boundary thresholds exactly 5% and 15%
# --------------------------------------------------------------------- #

def test_exactly_five_percent_average_is_warning_not_certified():
    engine = MarginCertificationEngine()
    cases = [_case("c1", "STRADDLE", COMPARISON_WARNING, deviation_fraction=0.05)]
    result = engine.certify(cases)
    assert result.certification_status == CERTIFICATION_WARNING


def test_exactly_fifteen_percent_average_is_warning_not_failed():
    engine = MarginCertificationEngine()
    cases = [_case("c1", "STRADDLE", COMPARISON_WARNING, deviation_fraction=0.15)]
    result = engine.certify(cases)
    assert result.certification_status == CERTIFICATION_WARNING


def test_just_above_fifteen_percent_average_is_failed():
    engine = MarginCertificationEngine()
    cases = [_case("c1", "STRADDLE", COMPARISON_FAIL, deviation_fraction=0.151)]
    result = engine.certify(cases)
    assert result.certification_status == CERTIFICATION_FAILED


# --------------------------------------------------------------------- #
# Safety 8/9 -- no capital_check, no execution/order capability
# --------------------------------------------------------------------- #

def test_certification_module_never_imports_capital_check():
    source_path = Path(inspect.getfile(margin_certification_engine))
    tree = ast.parse(source_path.read_text())
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    offenders = [m for m in imported if "capital_check" in m]
    assert offenders == [], f"certification engine must never import capital_check: {offenders}"


def test_certification_module_never_imports_broker_or_execution_code():
    source_path = Path(inspect.getfile(margin_certification_engine))
    tree = ast.parse(source_path.read_text())
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    forbidden_prefixes = ("bujji.broker.fyers", "bujji.broker.hybrid", "bujji.runtime_execution")
    offenders = [m for m in imported if any(m == p or m.startswith(p + ".") for p in forbidden_prefixes)]
    assert offenders == [], f"forbidden imports found: {offenders}"


def test_certification_module_never_references_order_methods():
    source_path = Path(inspect.getfile(margin_certification_engine))
    tree = ast.parse(source_path.read_text())
    forbidden_names = {"place_order", "cancel_order", "modify_order", "get_open_positions"}
    referenced_attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    referenced_names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    offenders = forbidden_names & (referenced_attrs | referenced_names)
    assert offenders == set(), f"forbidden order-method references found: {offenders}"


def test_certification_result_has_no_decision_or_verdict_field():
    """Structural proof the output is a REPORT, not a trading
    permission -- MarginCertificationResult carries no ALLOW/VETO/
    decision-shaped field of any kind."""
    engine = MarginCertificationEngine()
    result = engine.certify([_case("c1", "STRADDLE", COMPARISON_PASS, deviation_fraction=0.01)])
    forbidden_field_names = {"decision", "verdict", "allow", "veto", "approved", "can_trade"}
    actual_fields = set(result.__dataclass_fields__.keys())
    assert forbidden_field_names.isdisjoint(actual_fields)


# --------------------------------------------------------------------- #
# build_certification_case() -- convenience constructor reuses compare_margin
# --------------------------------------------------------------------- #

def test_build_certification_case_reuses_compare_margin_not_reimplemented():
    sim = MarginSnapshot(required_margin=400000.0, margin_verified=True, margin_source="SIM",
                          as_of=_clock()(), quote=None)
    broker = BrokerMarginSnapshot(available_margin=1000000.0, used_margin=100000.0, required_margin=406500.0,
                                   timestamp=_clock()(), source="FYERS_READ_ONLY", available=True)
    case = build_certification_case("c1", "STRADDLE", sim, broker, clock=_clock())
    independent_report = compare_margin(sim, broker, clock=_clock())
    assert case.comparison_report.status == independent_report.status
    assert case.comparison_report.deviation_fraction == independent_report.deviation_fraction


# --------------------------------------------------------------------- #
# format_certification_report() -- pure formatting
# --------------------------------------------------------------------- #

def test_format_certification_report_contains_expected_sections():
    engine = MarginCertificationEngine()
    cases = [_case("c1", "NIFTY_SHORT_STRADDLE", COMPARISON_PASS, deviation_fraction=0.03)]
    result = engine.certify(cases)
    text = format_certification_report(result, period="2026-08-01")
    assert "Bujji Margin Certification Report" in text
    assert "2026-08-01" in text
    assert "CERTIFIED" in text
    assert "NIFTY_SHORT_STRADDLE" in text


# --------------------------------------------------------------------- #
# Configurable thresholds / min_comparable_cases
# --------------------------------------------------------------------- #

def test_thresholds_are_configurable():
    tight_engine = MarginCertificationEngine(pass_threshold=0.01, fail_threshold=0.02)
    cases = [_case("c1", "STRADDLE", COMPARISON_WARNING, deviation_fraction=0.015)]
    result = tight_engine.certify(cases)
    assert result.certification_status == CERTIFICATION_WARNING  # would be CERTIFIED under default 5% threshold


def test_min_comparable_cases_is_configurable():
    strict_engine = MarginCertificationEngine(min_comparable_cases=10)
    cases = [_case(f"c{i}", "STRADDLE", COMPARISON_PASS, deviation_fraction=0.01) for i in range(5)]
    result = strict_engine.certify(cases)
    assert result.certification_status == CERTIFICATION_INSUFFICIENT_DATA  # only 5 comparable, need 10


def test_inverted_thresholds_raise():
    with pytest.raises(ValueError):
        MarginCertificationEngine(pass_threshold=0.5, fail_threshold=0.1)
