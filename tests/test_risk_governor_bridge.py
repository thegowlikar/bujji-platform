"""Tests -- Phase 20.17 Risk Governor Bridge.
Zero network access, zero broker/execution coupling anywhere in this file."""
from __future__ import annotations

import ast
import inspect
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bujji.decision_orchestration import BLOCKED, EXECUTABLE_CANDIDATE, NO_OPPORTUNITY, WATCH, FinalDecision
from bujji.risk_governor_bridge import (
    NOTIONAL_PROBE_DESIRED_QUANTITY, NOTIONAL_PROBE_MARGIN, NOTIONAL_PROBE_MAX_LOSS,
    STATUS_ADMITTED, STATUS_BLOCKED, STATUS_NOT_EVALUATED,
    build_capital_snapshot_from_real_funds, evaluate_risk_governor, explain_risk_governor_assessment,
)
from bujji.trading_brain.risk_governor.capital_safety_governor import CapitalSafetySnapshot


def _clock(iso="2026-08-14T09:15:00+00:00"):
    dt = datetime.fromisoformat(iso)
    return lambda: dt


def _final_decision(decision_state, strategy_name="TrendFollowing"):
    return FinalDecision(
        strategy_name=strategy_name, decision_state=decision_state,
        positive=("real_evidence_supports_review",), negative=(), unknown=(),
        allocation=None, portfolio_decision=None,
    )


def _healthy_snapshot():
    """A fully-populated snapshot -- used only to prove the ADMITTED path
    is reachable when all governor inputs are present. Real, builder-
    produced snapshots leave daily_pnl/daily_loss_limit/peak_capital/
    max_allowed_drawdown honestly None (see test below), which the real
    D.1 governor conservatively treats as a block -- this is real
    governor behavior, not a bridge bug."""
    return CapitalSafetySnapshot(
        total_capital=1_000_000.0, available_capital=800_000.0, used_margin=200_000.0,
        open_risk=0.0, reserved_risk=0.0, daily_pnl=0.0, daily_loss_limit=50_000.0,
        peak_capital=1_000_000.0, max_allowed_drawdown=200_000.0, consecutive_losses=0,
        timestamp=datetime.fromisoformat("2026-08-14T09:15:00+00:00"),
    )


def _exhausted_snapshot():
    """available_capital far below the notional probe's own requirement --
    real D.1 rejection, not a fabricated block."""
    return CapitalSafetySnapshot(
        total_capital=1000.0, available_capital=0.0, used_margin=1000.0,
        open_risk=0.0, reserved_risk=0.0, daily_pnl=None, daily_loss_limit=None,
        peak_capital=None, max_allowed_drawdown=None, consecutive_losses=None,
        timestamp=datetime.fromisoformat("2026-08-14T09:15:00+00:00"),
    )


# --------------------------------------------------------------------- #
# 1. Not-evaluated honesty
# --------------------------------------------------------------------- #

@pytest.mark.parametrize("state", [NO_OPPORTUNITY, BLOCKED])
def test_decisions_not_worth_review_are_never_sent_to_governor(state):
    decision = _final_decision(state)
    assessment = evaluate_risk_governor(decision, _healthy_snapshot(), clock=_clock())
    assert assessment.final_status == STATUS_NOT_EVALUATED
    assert assessment.capital_status is None
    assert assessment.blocking_stage is None


# --------------------------------------------------------------------- #
# 2. Real D.1 admission and rejection
# --------------------------------------------------------------------- #

def test_executable_candidate_clears_d1_and_is_blocked_honestly_at_d2():
    """Genuine architecture finding, not a bridge defect: D.2's real
    aggregator (aggregate_portfolio_risk) marks a flat/zero-position
    book RISK_INVALID (INSUFFICIENT_PORTFOLIO_RISK_DATA) because
    total_margin_required/total_max_loss are None without a real
    MarginSnapshot -- and a real MarginSnapshot itself requires a real
    broker margin query against at least one real leg, which does not
    exist for a flat book. Fabricating a MarginSnapshot to force
    ADMITTED would be dishonest, so this bridge reports the real,
    structural D.2 block instead. STATUS_ADMITTED only becomes
    reachable once real position/margin data exists (future phase)."""
    decision = _final_decision(EXECUTABLE_CANDIDATE)
    assessment = evaluate_risk_governor(decision, _healthy_snapshot(), clock=_clock())
    assert assessment.capital_status == "SAFE"
    assert assessment.capital_allowed is True
    assert assessment.final_status == STATUS_BLOCKED
    assert assessment.blocking_stage == "D.2_PORTFOLIO_RISK"
    assert assessment.portfolio_status == "INVALID"


def test_builder_produced_snapshot_is_conservatively_blocked_on_missing_risk_data():
    """Honest disclosure, not a bug: a snapshot built purely from
    get_funds() (daily_pnl/daily_loss_limit/peak_capital/
    max_allowed_drawdown left None, since Cycle 1 has no real trading
    history) is BLOCKED by the real D.1 governor's own conservative
    ACCOUNT_ALREADY_BLOCKED:INSUFFICIENT_CAPITAL_DATA rule. This is
    real governor behavior operating on real data, not a bridge defect."""
    funds = {"account_equity": 1_000_000.0, "available_funds": 800_000.0, "used_margin": 200_000.0}
    snapshot = build_capital_snapshot_from_real_funds(
        funds, as_of=datetime.fromisoformat("2026-08-14T09:15:00+00:00"),
    )
    decision = _final_decision(EXECUTABLE_CANDIDATE)
    assessment = evaluate_risk_governor(decision, snapshot, clock=_clock())
    assert assessment.final_status == STATUS_BLOCKED
    assert assessment.blocking_stage == "D.1_CAPITAL_SAFETY"
    assert "INSUFFICIENT_CAPITAL_DATA" in assessment.capital_explanation


def test_watch_with_exhausted_capital_is_blocked_at_d1():
    decision = _final_decision(WATCH)
    assessment = evaluate_risk_governor(decision, _exhausted_snapshot(), clock=_clock())
    assert assessment.final_status == STATUS_BLOCKED
    assert assessment.blocking_stage == "D.1_CAPITAL_SAFETY"
    assert assessment.capital_allowed is False
    # Blocked at D.1 -- D.2/D.3 never ran.
    assert assessment.portfolio_status is None
    assert assessment.budget_status is None


# --------------------------------------------------------------------- #
# 3. D.4/D.5 honestly skipped, never fabricated
# --------------------------------------------------------------------- #

def test_d4_and_d5_are_always_disclosed_as_skipped():
    decision = _final_decision(EXECUTABLE_CANDIDATE)
    assessment = evaluate_risk_governor(decision, _healthy_snapshot(), clock=_clock())
    assert assessment.skipped_stages == ("D.4_POSITION_LIFECYCLE", "D.5_ADAPTIVE_EXPERIENCE")
    assert "fabricat" in assessment.skip_reason.lower()


# --------------------------------------------------------------------- #
# 4. Notional probe is never a real order
# --------------------------------------------------------------------- #

def test_probe_values_are_trivial_and_disclosed():
    decision = _final_decision(EXECUTABLE_CANDIDATE)
    assessment = evaluate_risk_governor(decision, _healthy_snapshot(), clock=_clock())
    assert NOTIONAL_PROBE_MARGIN == 1.0
    assert NOTIONAL_PROBE_MAX_LOSS == 1.0
    assert NOTIONAL_PROBE_DESIRED_QUANTITY == 1
    assert "never" in assessment.probe_disclosure.lower()
    assert "order" in assessment.probe_disclosure.lower()


# --------------------------------------------------------------------- #
# 5. Builder honesty -- no fabricated capital fields
# --------------------------------------------------------------------- #

def test_builder_maps_real_funds_and_leaves_unavailable_fields_none():
    funds = {"account_equity": 500_000.0, "available_funds": 400_000.0, "used_margin": 100_000.0,
              "cash_balance": 500_000.0, "collateral": 0.0}
    snapshot = build_capital_snapshot_from_real_funds(funds, as_of=datetime.now(timezone.utc))
    assert snapshot.total_capital == 500_000.0
    assert snapshot.available_capital == 400_000.0
    assert snapshot.used_margin == 100_000.0
    assert snapshot.open_risk == 0.0
    assert snapshot.reserved_risk == 0.0
    assert snapshot.daily_pnl is None
    assert snapshot.peak_capital is None
    assert snapshot.consecutive_losses is None


# --------------------------------------------------------------------- #
# 6. Explainability
# --------------------------------------------------------------------- #

def test_explanation_mentions_strategy_and_status():
    decision = _final_decision(EXECUTABLE_CANDIDATE)
    assessment = evaluate_risk_governor(decision, _healthy_snapshot(), clock=_clock())
    text = explain_risk_governor_assessment(assessment)
    assert "TrendFollowing" in text
    assert STATUS_BLOCKED in text


# --------------------------------------------------------------------- #
# 7. Safety boundary
# --------------------------------------------------------------------- #

_PKG_ROOT = Path(__file__).resolve().parent.parent / "bujji" / "risk_governor_bridge"

_FORBIDDEN_CALL_PATTERNS = (
    ".place_order(", ".modify_order(", ".cancel_order(", ".get_open_positions(", ".get_order(",
)


def test_no_forbidden_broker_calls_anywhere_in_package():
    for path in _PKG_ROOT.glob("*.py"):
        source = path.read_text()
        for pattern in _FORBIDDEN_CALL_PATTERNS:
            assert pattern not in source, f"{pattern!r} found in {path.name}"


def test_no_d4_d5_governor_calls_anywhere_in_package():
    forbidden_calls = ("recommend_risk_action(", "summarize_strategy_experience(",
                         "recommend_adaptive_risk_adjustment(", "evaluate_adaptive_governor_decision(")
    for path in _PKG_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text())
        call_sources = [ast.dump(n) for n in ast.walk(tree) if isinstance(n, ast.Call)]
        source = path.read_text()
        for pattern in forbidden_calls:
            assert pattern not in source, f"{pattern!r} found in {path.name}"


def test_package_never_recomputes_evidence_or_qualification():
    forbidden = ("evidence_score", "effective_score", "qualification_status")
    for path in _PKG_ROOT.glob("*.py"):
        source = path.read_text()
        for term in forbidden:
            assert term not in source, f"{term!r} found in {path.name}"
