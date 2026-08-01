"""Tests — Numeric Risk Governor Gate C.3 (margin comparison engine).
Zero network access anywhere in this file."""
from __future__ import annotations

import ast
import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bujji.trading_brain.risk_governor import margin_comparison_engine
from bujji.trading_brain.risk_governor.broker_margin_reality_adapter import BrokerMarginSnapshot
from bujji.trading_brain.risk_governor.margin_comparison_engine import (
    COMPARISON_FAIL,
    COMPARISON_PASS,
    COMPARISON_STALE,
    COMPARISON_UNAVAILABLE,
    COMPARISON_WARNING,
    MarginComparisonEngine,
    compare_margin,
)
from bujji.trading_brain.risk_governor.whole_book_margin_provider import MarginSnapshot


def _clock(iso="2026-08-02T09:15:00+00:00"):
    dt = datetime.fromisoformat(iso)
    return lambda: dt


def _sim_snapshot(required_margin, verified=True, as_of="2026-08-02T09:15:00+00:00"):
    return MarginSnapshot(
        required_margin=required_margin, margin_verified=verified,
        margin_source="SIMULATED_WHOLE_BOOK", as_of=datetime.fromisoformat(as_of), quote=None,
    )


def _broker_snapshot(required_margin, available=True, source="FYERS_READ_ONLY",
                      timestamp="2026-08-02T09:15:00+00:00"):
    return BrokerMarginSnapshot(
        available_margin=500000.0, used_margin=100000.0, required_margin=required_margin,
        timestamp=datetime.fromisoformat(timestamp), source=source, available=available,
    )


# --------------------------------------------------------------------- #
# Exact match / small deviation -> PASS
# --------------------------------------------------------------------- #

def test_exact_match_is_pass():
    report = compare_margin(_sim_snapshot(400000.0), _broker_snapshot(400000.0), clock=_clock())
    assert report.status == COMPARISON_PASS
    assert report.difference == 0.0
    assert report.deviation_fraction == 0.0


def test_small_deviation_is_pass():
    # 6500 / 424500 = ~1.53%, well under 5%
    report = compare_margin(_sim_snapshot(418000.0), _broker_snapshot(424500.0), clock=_clock())
    assert report.status == COMPARISON_PASS
    assert report.difference == pytest.approx(6500.0)
    assert report.deviation_fraction == pytest.approx(6500.0 / 424500.0)


# --------------------------------------------------------------------- #
# Warning deviation
# --------------------------------------------------------------------- #

def test_warning_deviation():
    # 40000 / 400000 = 10%, within 5-15%
    report = compare_margin(_sim_snapshot(360000.0), _broker_snapshot(400000.0), clock=_clock())
    assert report.status == COMPARISON_WARNING


def test_exactly_at_pass_boundary_is_warning_not_pass():
    report = compare_margin(_sim_snapshot(380000.0), _broker_snapshot(400000.0), clock=_clock())  # exactly 5%
    assert report.status == COMPARISON_WARNING


def test_exactly_at_warning_boundary_is_still_warning_not_fail():
    report = compare_margin(_sim_snapshot(340000.0), _broker_snapshot(400000.0), clock=_clock())  # exactly 15%
    assert report.status == COMPARISON_WARNING


# --------------------------------------------------------------------- #
# Failure deviation
# --------------------------------------------------------------------- #

def test_failure_deviation():
    # 100000 / 400000 = 25%, over 15%
    report = compare_margin(_sim_snapshot(300000.0), _broker_snapshot(400000.0), clock=_clock())
    assert report.status == COMPARISON_FAIL


def test_configurable_thresholds():
    engine = MarginComparisonEngine(pass_threshold=0.01, warning_threshold=0.02)
    report = engine.compare(_sim_snapshot(395000.0), _broker_snapshot(400000.0), clock=_clock())
    # 5000/400000 = 1.25% -- would be PASS under defaults, WARNING under tighter thresholds
    assert report.status == COMPARISON_WARNING


# --------------------------------------------------------------------- #
# Stale broker data
# --------------------------------------------------------------------- #

def test_stale_broker_data_flagged_not_compared():
    old_broker_snapshot = _broker_snapshot(400000.0, timestamp="2026-08-02T09:00:00+00:00")  # 15 min before clock
    report = compare_margin(_sim_snapshot(400000.0), old_broker_snapshot, clock=_clock())  # clock is 09:15
    assert report.status == COMPARISON_STALE
    assert report.deviation_fraction is None


def test_fresh_broker_data_within_staleness_threshold_compares_normally():
    fresh_snapshot = _broker_snapshot(400000.0, timestamp="2026-08-02T09:13:00+00:00")  # 2 min old
    report = compare_margin(_sim_snapshot(400000.0), fresh_snapshot, clock=_clock())
    assert report.status == COMPARISON_PASS


def test_staleness_threshold_is_configurable():
    snapshot_2min_old = _broker_snapshot(400000.0, timestamp="2026-08-02T09:13:00+00:00")
    report = compare_margin(_sim_snapshot(400000.0), snapshot_2min_old, clock=_clock(), staleness_seconds=60.0)
    assert report.status == COMPARISON_STALE  # 2 minutes exceeds a 60s threshold


# --------------------------------------------------------------------- #
# Broker unavailable -- never falls back to zero
# --------------------------------------------------------------------- #

def test_broker_unavailable_is_unavailable_not_pass_or_zero_margin():
    unavailable = _broker_snapshot(None, available=False, source="AUTH_FAILED")
    report = compare_margin(_sim_snapshot(400000.0), unavailable, clock=_clock())
    assert report.status == COMPARISON_UNAVAILABLE
    assert report.broker_margin is None
    assert report.deviation_fraction is None
    assert report.reason is not None


def test_none_broker_snapshot_is_unavailable():
    report = compare_margin(_sim_snapshot(400000.0), None, clock=_clock())
    assert report.status == COMPARISON_UNAVAILABLE


def test_unverified_simulated_snapshot_is_unavailable():
    unverified = _sim_snapshot(None, verified=False)
    report = compare_margin(unverified, _broker_snapshot(400000.0), clock=_clock())
    assert report.status == COMPARISON_UNAVAILABLE
    assert report.simulated_margin is None


# --------------------------------------------------------------------- #
# Safety: comparison engine never touches capital_check
# --------------------------------------------------------------------- #

def test_comparison_engine_never_imports_capital_check():
    source_path = Path(inspect.getfile(margin_comparison_engine))
    tree = ast.parse(source_path.read_text())
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    offenders = [m for m in imported if "capital_check" in m]
    assert offenders == [], f"comparison engine must never import capital_check: {offenders}"


def test_inverted_thresholds_raise():
    with pytest.raises(ValueError):
        compare_margin(_sim_snapshot(400000.0), _broker_snapshot(400000.0), clock=_clock(),
                        pass_threshold=0.5, warning_threshold=0.1)
