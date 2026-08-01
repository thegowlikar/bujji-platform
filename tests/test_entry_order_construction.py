"""Tests — MSI-to-PaperBroker entry-order-construction bridge."""
from __future__ import annotations

import ast
import inspect
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bujji.core.models import OrderResult
from bujji.core.enums import OrderStatus
from bujji.broker.paper import PaperBroker
from bujji.journal.position_group_journal import PositionGroupJournal
from bujji.trading_brain.risk_governor import msi_entry_bridge
from bujji.trading_brain.risk_governor.msi_entry_bridge import (
    construct_and_gate_entry,
    dispatch_via_paper_broker,
)
from bujji.msi_trade_construction.models import Explanation, ExpiryDecision, StrikeLeg, TradeConstructionAssessment
from bujji.trading_brain.risk_governor.portfolio_limits import PortfolioLimits


def _clock(iso="2026-08-03T09:15:00+00:00"):
    dt = datetime.fromisoformat(iso)
    return lambda: dt


def _journal(tmp_path):
    return PositionGroupJournal(tmp_path / "pg.db")


def _leg(role, option_type, strike, side, premium=100.0, ratio=1):
    return StrikeLeg(
        role=role, option_type=option_type, strike=strike, expiry="2026-08-27",
        delta=0.3, premium=premium, open_interest=1000.0, side=side, ratio=ratio,
        reasoning=("test",),
    )


def _explanation():
    return Explanation(
        assessment_id="TCA-1", why_this_expiry=("test",), why_these_strikes=("test",),
        why_not_neighbouring_strikes=("test",), dominant_constraints=("test",), schema_version="1.0.0",
    )


def _expiry_decision(chosen="2026-08-27", dte=24):
    return ExpiryDecision(
        chosen_expiry=chosen, dte=dte, candidate_expiries=(chosen,) if chosen else (),
        rejected_expiries=(), reasoning=("test",),
    )


def _constructed_trade(strategy_family="NEUTRAL_PREMIUM_SELLING"):
    return TradeConstructionAssessment(
        assessment_id="TCA-1", timestamp="2026-08-03T09:15:00+00:00",
        strategy_family=strategy_family, constructed=True, rejection_reason=None,
        expiry="2026-08-27", expiry_decision=_expiry_decision(),
        legs=(
            _leg("SHORT", "CE", 24800, "SELL", premium=100.0),
            _leg("LONG", "CE", 24900, "BUY", premium=40.0),
        ),
        entry_reference_prices={"CE_24800": 100.0, "CE_24900": 40.0},
        expected_credit_debit=60.0, risk_profile="DEFINED",
        required_margin=None, margin_unavailable_reason="no live margin source",
        supporting_assessment_ids=("SSF-1",),
        explanation=_explanation(), provenance="test", schema_version="1.0.0",
    )


def _unconstructed_trade():
    return TradeConstructionAssessment(
        assessment_id="TCA-2", timestamp="2026-08-03T09:15:00+00:00",
        strategy_family="NONE", constructed=False, rejection_reason="NO_VALID_STRIKES",
        expiry=None, expiry_decision=_expiry_decision(chosen=None, dte=None),
        legs=(), entry_reference_prices={}, expected_credit_debit=None, risk_profile="UNKNOWN",
        required_margin=None, margin_unavailable_reason=None, supporting_assessment_ids=(),
        explanation=_explanation(), provenance="test", schema_version="1.0.0",
    )


_LIMITS = PortfolioLimits(max_simultaneous_positions=5, max_concentration_per_underlying=1.0)


# --------------------------------------------------------------------- #
# Structural: no live-broker-capable import anywhere in this module
# --------------------------------------------------------------------- #

def test_no_live_broker_imports():
    source_path = Path(inspect.getfile(msi_entry_bridge))
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


# --------------------------------------------------------------------- #
# construct_and_gate_entry
# --------------------------------------------------------------------- #

def test_unconstructed_trade_produces_no_trade_result(tmp_path):
    journal = _journal(tmp_path)
    result = construct_and_gate_entry(
        "DEC-1", _unconstructed_trade(), "NIFTY", 75, journal, [], {}, _LIMITS, 100000.0, clock=_clock(),
    )
    assert result.position_group_id is None
    assert result.verdict is None
    assert result.order_requests == ()
    assert result.decision_trace.startswith("NO_TRADE")
    assert journal.read_all_group_ids() == []  # nothing minted


def test_constructed_trade_mints_and_vetoes_today_honestly(tmp_path):
    """The central, honest disclosure: every real call today VETOes,
    because no MSI strategy has a reviewed defined-risk formula yet AND
    no certified margin provider exists. Both are documented gaps, not
    silent failures."""
    journal = _journal(tmp_path)
    result = construct_and_gate_entry(
        "DEC-2", _constructed_trade(), "NIFTY", 75, journal, [], {}, _LIMITS, 100000.0, clock=_clock(),
    )
    assert result.position_group_id is not None
    assert result.verdict.decision == "VETO"
    assert result.order_requests == ()
    assert "DEFINED_RISK_UNDEFINED_RISK_NO_STRESS_MODEL" in result.verdict.failed_checks
    assert "CAPITAL_MARGIN_NOT_CERTIFIED" in result.verdict.failed_checks


def test_constructed_trade_mints_a_real_gate_a_group(tmp_path):
    journal = _journal(tmp_path)
    result = construct_and_gate_entry(
        "DEC-3", _constructed_trade(), "NIFTY", 75, journal, [], {}, _LIMITS, 100000.0, clock=_clock(),
    )
    events = journal.read_events(result.position_group_id)
    event_types = [e.event_type for e in events]
    assert event_types == ["MINTED", "CONSTRUCTED"]


def test_market_order_never_reached_since_msi_bridge_uses_limit_shaped_orders(tmp_path):
    """Regression proof: the internal stand-in orders this bridge builds
    for defined_risk.py's market-order check are always LIMIT-shaped,
    so a real veto reason is UNDEFINED_RISK, never the market-order one
    -- confirming the bridge doesn't accidentally trip a different veto
    path than the one it's honestly disclosing."""
    journal = _journal(tmp_path)
    result = construct_and_gate_entry(
        "DEC-4", _constructed_trade(), "NIFTY", 75, journal, [], {}, _LIMITS, 100000.0, clock=_clock(),
    )
    assert "NO_ADVERSE_FILL_BOUND_FOR_MARKET_ORDER" not in " ".join(result.verdict.failed_checks)


# --------------------------------------------------------------------- #
# dispatch_via_paper_broker -- structurally PaperBroker-only
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_dispatch_rejects_non_paper_broker_object():
    class FakeLiveBroker:
        async def place_order(self, request):
            raise AssertionError("must never be called")

    with pytest.raises(TypeError):
        await dispatch_via_paper_broker((), FakeLiveBroker())


@pytest.mark.asyncio
async def test_dispatch_via_real_paper_broker_succeeds_when_given_orders(tmp_path):
    """Adversarial: even feeding a hand-forced ALLOW's worth of real
    order_requests through dispatch, only PaperBroker is ever touched --
    proving the guard is structural (isinstance-checked), not merely
    documented, by using the REAL PaperBroker class, not a mock."""
    journal = _journal(tmp_path)
    result = construct_and_gate_entry(
        "DEC-5", _constructed_trade(), "NIFTY", 75, journal, [], {}, _LIMITS, 100000.0, clock=_clock(),
    )
    # result.order_requests is empty today (real VETO) -- but to prove
    # dispatch_via_paper_broker() itself works correctly end-to-end
    # against the real PaperBroker, hand-construct the same shape a
    # hypothetical future ALLOW would produce.
    from bujji.core.models import OptionContract, OrderRequest
    from bujji.core.enums import OptionType, Side

    contract = OptionContract(symbol="NIFTY26AUG24800CE", underlying="NIFTY", strike=24800,
                               option_type=OptionType.CE, expiry="2026-08-27", lot_size=75)
    order = OrderRequest(contract=contract, side=Side.SELL, quantity=75,
                          client_order_id="TEST-COID-1", reference_price=100.0)
    broker = PaperBroker()
    results = await dispatch_via_paper_broker((order,), broker)
    assert len(results) == 1
    assert results[0].client_order_id == "TEST-COID-1"
    assert results[0].status in (OrderStatus.FILLED, OrderStatus.PARTIAL)


# --------------------------------------------------------------------- #
# Audit regressions
# --------------------------------------------------------------------- #

def test_missing_premium_fails_closed_never_zeroed(tmp_path):
    """Audit finding: a leg with premium=None (a real, disclosed-as-
    possible MSI state) must make the group's exposure UNTRUSTED, never
    silently zero -- confirmed via the real fail-closed veto path
    (EXPOSURE_DATA_MISSING), not a fabricated new one."""
    journal = _journal(tmp_path)
    import dataclasses
    trade = dataclasses.replace(
        _constructed_trade(),
        legs=(
            _leg("SHORT", "CE", 24800, "SELL", premium=None),
            _leg("LONG", "CE", 24900, "BUY", premium=None),
        ),
    )
    result = construct_and_gate_entry(
        "DEC-MISSING-PREMIUM", trade, "NIFTY", 75, journal, [], {}, _LIMITS, 100000.0, clock=_clock(),
    )
    assert result.verdict.decision == "VETO"
    assert "PORTFOLIO_LIMITS_EXPOSURE_DATA_MISSING" in result.verdict.failed_checks
    assert result.order_requests == ()


def test_non_positive_ratio_rejected_before_minting(tmp_path):
    """Audit finding: leg.ratio <= 0 used to silently produce a negative/
    zero requested_quantity that corrupted downstream exposure math with
    no validation at all."""
    journal = _journal(tmp_path)
    import dataclasses
    trade = dataclasses.replace(
        _constructed_trade(),
        legs=(
            _leg("SHORT", "CE", 24800, "SELL", ratio=-2),
            _leg("LONG", "CE", 24900, "BUY", ratio=1),
        ),
    )
    result = construct_and_gate_entry(
        "DEC-NEG-RATIO", trade, "NIFTY", 75, journal, [], {}, _LIMITS, 100000.0, clock=_clock(),
    )
    assert result.position_group_id is None
    assert result.verdict is None
    assert "non-positive ratio" in result.decision_trace
    assert journal.read_all_group_ids() == []


def test_allowed_order_limit_price_matches_what_was_risk_assessed(tmp_path, monkeypatch):
    """Audit finding: the internal defined-risk stand-in always claims
    order_type='LIMIT', but the real constructed order previously left
    limit_price=None (=> MARKET per bujji.core.models.OrderRequest's own
    docstring) -- a risk assessment computed under a false 'bounded
    LIMIT order' premise must never authorize an actually-unbounded
    MARKET order. Verified by forcing assess() to ALLOW and inspecting
    the real constructed order."""
    from bujji.trading_brain.risk_governor import msi_entry_bridge as bridge_module
    from bujji.trading_brain.risk_governor.engine import RiskVerdict

    def forced_allow(*args, **kwargs):
        return RiskVerdict(decision="ALLOW", blocking_reason="", failed_checks=(),
                            passed_checks=("FORCED",), decision_trace="forced for test",
                            evaluated_at=_clock()())

    monkeypatch.setattr(bridge_module, "assess", forced_allow)

    journal = _journal(tmp_path)
    result = construct_and_gate_entry(
        "DEC-FORCED-ALLOW", _constructed_trade(), "NIFTY", 75, journal, [], {}, _LIMITS, 100000.0, clock=_clock(),
    )
    assert result.verdict.decision == "ALLOW"
    assert len(result.order_requests) == 2
    for order in result.order_requests:
        assert order.limit_price is not None
        assert order.limit_price == order.reference_price
