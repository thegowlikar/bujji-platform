"""Tests — MSI-to-PaperBroker entry-order-construction bridge."""
from __future__ import annotations

import ast
import dataclasses
import inspect
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from bujji.core.models import OrderResult
from bujji.core.enums import OrderStatus
from bujji.broker.paper import PaperBroker
from bujji.journal.position_group_journal import PositionGroupJournal
from bujji.trading_brain.nifty_contract_builder.models import NiftyOptionContract
from bujji.trading_brain.risk_governor import msi_entry_bridge
from bujji.trading_brain.risk_governor.defined_risk import StrategyRiskProfile, assess_defined_risk
from bujji.trading_brain.risk_governor.msi_entry_bridge import (
    construct_and_gate_entry,
    dispatch_via_paper_broker,
)
from bujji.trading_brain.risk_governor.position_group_fold import fold
from bujji.trading_brain.risk_governor.position_group_mint import mint_position_group_id
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


# --------------------------------------------------------------------- #
# LONG_DIRECTIONAL — the first real, reviewed defined-risk formula
# --------------------------------------------------------------------- #

def _long_directional_trade(premium=150.0, ratio=1):
    return dataclasses.replace(
        _constructed_trade(strategy_family="LONG_DIRECTIONAL"),
        legs=(_leg("LONG", "CE", 24800, "BUY", premium=premium, ratio=ratio),),
    )


def test_long_directional_defined_risk_allows_with_correct_max_loss(tmp_path):
    """The central proof: a real MSI strategy family now reaches a
    genuine DEFINED_RISK ALLOW, computed from the actual formula
    (premium * quantity), not a placeholder. Capital remains the sole
    real blocker, isolated and correctly named -- proving the two
    veto reasons are now independent, not conflated."""
    journal = _journal(tmp_path)
    result = construct_and_gate_entry(
        "DEC-LONG-1", _long_directional_trade(premium=150.0, ratio=2), "NIFTY", 75,
        journal, [], {}, _LIMITS, 100000.0, clock=_clock(),
    )
    assert result.verdict.decision == "VETO"  # still vetoed overall -- capital is the ONLY remaining reason
    assert "DEFINED_RISK_WITHIN_BOUNDS" in result.verdict.passed_checks
    assert not any(f.startswith("DEFINED_RISK_") for f in result.verdict.failed_checks)
    assert "CAPITAL_MARGIN_NOT_CERTIFIED" in result.verdict.failed_checks


def test_long_directional_max_loss_computed_correctly_via_defined_risk_directly(tmp_path):
    """Bypass the Governor's overall VETO (capital) and inspect the real
    DefinedRiskAssessment the bridge produced, to prove the formula's
    actual arithmetic -- premium(150.0) * quantity(2 lots * 75) = 22500.0."""
    journal = _journal(tmp_path)
    trade = _long_directional_trade(premium=150.0, ratio=2)
    pg = mint_position_group_id(journal, "DEC-LONG-2", trade.strategy_family, "NIFTY", clock=_clock())
    journal.append_event(
        pg.position_group_id, "CONSTRUCTED", f"{pg.position_group_id}:CONSTRUCTED:0",
        {"contract_client_order_map": {"C0": f"{pg.position_group_id}-LEG-0"},
         "requested_quantities": {f"{pg.position_group_id}-LEG-0": 150},
         "actions": {}, "target_position_group_ids": {}, "target_contract_ids": {}, "flip_link_ids": {}},
        clock=_clock(),
    )
    state = fold(journal.read_events(pg.position_group_id))
    coid = f"{pg.position_group_id}-LEG-0"
    contract = NiftyOptionContract(
        contract_id="C0", underlying="NIFTY", expiry="2026-08-27", strike=24800, option_type="CE", side="BUY",
        contract_symbol="NIFTY26AUG24800CE", capital_intent="STANDARD", strategy_id="LONG_DIRECTIONAL",
        selection_reason="t", construction_trace="t", timestamp="2026-08-03T09:15:00+00:00", version="1.0.0",
    )
    order = SimpleNamespace(order_type="LIMIT", reference_price=150.0)
    profile = StrategyRiskProfile(
        strategy_id="LONG_DIRECTIONAL", required_leg_roles=("LONG_LEG",),
        formula="LONG_OPTION_PREMIUM_PAID", lot_size=75,
    )
    result = assess_defined_risk(
        state, {coid: contract}, {coid: order}, {coid: "LONG_LEG"}, profile, clock=_clock(),
    )
    assert result.decision == "ALLOW"
    assert result.max_loss == pytest.approx(150.0 * 150)  # premium * requested_quantity


def test_long_directional_zero_ratio_still_rejected_as_malformed(tmp_path):
    journal = _journal(tmp_path)
    trade = _long_directional_trade(ratio=0)
    result = construct_and_gate_entry(
        "DEC-LONG-3", trade, "NIFTY", 75, journal, [], {}, _LIMITS, 100000.0, clock=_clock(),
    )
    assert result.position_group_id is None
    assert "non-positive ratio" in result.decision_trace


def test_long_directional_missing_premium_fails_closed(tmp_path):
    journal = _journal(tmp_path)
    trade = _long_directional_trade(premium=None)
    result = construct_and_gate_entry(
        "DEC-LONG-4", trade, "NIFTY", 75, journal, [], {}, _LIMITS, 100000.0, clock=_clock(),
    )
    assert result.verdict.decision == "VETO"
    assert "PORTFOLIO_LIMITS_EXPOSURE_DATA_MISSING" in result.verdict.failed_checks


def test_other_strategy_families_still_have_no_formula(tmp_path):
    """Regression guard: families with NO formula wired at all must
    still VETO UNDEFINED_RISK_NO_STRESS_MODEL, unconditionally --
    confirming coverage hasn't accidentally widened beyond what was
    actually reviewed and tested. IRON_CONDOR, IRON_FLY, and BUTTERFLY
    are excluded from this generic loop now that each has a real
    formula -- see their own dedicated positive tests instead."""
    journal = _journal(tmp_path)
    for family in (
        "COVERED", "RATIO", "SHORT_DIRECTIONAL", "SYNTHETIC",
        "NEUTRAL_PREMIUM_SELLING", "VOLATILITY_COMPRESSION", "CALENDAR",
    ):
        trade = dataclasses.replace(_constructed_trade(strategy_family=family))
        result = construct_and_gate_entry(
            f"DEC-{family}", trade, "NIFTY", 75, journal, [], {}, _LIMITS, 100000.0, clock=_clock(),
        )
        assert "DEFINED_RISK_UNDEFINED_RISK_NO_STRESS_MODEL" in result.verdict.failed_checks, family


# --------------------------------------------------------------------- #
# VOLATILITY_EXPANSION — third real, reviewed defined-risk formula
# (mechanically identical to NEUTRAL_PREMIUM_BUYING's shape/formula)
# --------------------------------------------------------------------- #

def _volatility_expansion_trade(ce_premium=120.0, pe_premium=110.0, ratio=1):
    return dataclasses.replace(
        _constructed_trade(strategy_family="VOLATILITY_EXPANSION"),
        legs=(
            _leg("LONG", "CE", 24800, "BUY", premium=ce_premium, ratio=ratio),
            _leg("LONG", "PE", 24800, "BUY", premium=pe_premium, ratio=ratio),
        ),
    )


def test_volatility_expansion_now_wired_defined_risk_allows(tmp_path):
    """VOLATILITY_EXPANSION now correctly reuses the same
    MULTI_LEG_LONG_PREMIUM_PAID formula as NEUTRAL_PREMIUM_BUYING --
    same shape, same reviewed math, no new formula needed."""
    journal = _journal(tmp_path)
    result = construct_and_gate_entry(
        "DEC-VOLEXP-1", _volatility_expansion_trade(ce_premium=120.0, pe_premium=110.0, ratio=2),
        "NIFTY", 75, journal, [], {}, _LIMITS, 100000.0, clock=_clock(),
    )
    assert result.verdict.decision == "VETO"  # capital remains the sole real blocker
    assert "DEFINED_RISK_WITHIN_BOUNDS" in result.verdict.passed_checks
    assert not any(f.startswith("DEFINED_RISK_") for f in result.verdict.failed_checks)
    assert "CAPITAL_MARGIN_NOT_CERTIFIED" in result.verdict.failed_checks


def test_volatility_expansion_max_loss_sums_both_legs_correctly(tmp_path):
    journal = _journal(tmp_path)
    trade = _volatility_expansion_trade(ce_premium=120.0, pe_premium=110.0, ratio=3)
    pg = mint_position_group_id(journal, "DEC-VOLEXP-2", trade.strategy_family, "NIFTY", clock=_clock())
    coid_ce = f"{pg.position_group_id}-LEG-0"
    coid_pe = f"{pg.position_group_id}-LEG-1"
    journal.append_event(
        pg.position_group_id, "CONSTRUCTED", f"{pg.position_group_id}:CONSTRUCTED:0",
        {"contract_client_order_map": {"C0": coid_ce, "C1": coid_pe},
         "requested_quantities": {coid_ce: 225, coid_pe: 225},
         "actions": {}, "target_position_group_ids": {}, "target_contract_ids": {}, "flip_link_ids": {}},
        clock=_clock(),
    )
    state = fold(journal.read_events(pg.position_group_id))
    contract_ce = NiftyOptionContract(
        contract_id="C0", underlying="NIFTY", expiry="2026-08-27", strike=24800, option_type="CE", side="BUY",
        contract_symbol="NIFTY26AUG24800CE", capital_intent="STANDARD", strategy_id="VOLATILITY_EXPANSION",
        selection_reason="t", construction_trace="t", timestamp="2026-08-05T09:15:00+00:00", version="1.0.0",
    )
    contract_pe = NiftyOptionContract(
        contract_id="C1", underlying="NIFTY", expiry="2026-08-27", strike=24800, option_type="PE", side="BUY",
        contract_symbol="NIFTY26AUG24800PE", capital_intent="STANDARD", strategy_id="VOLATILITY_EXPANSION",
        selection_reason="t", construction_trace="t", timestamp="2026-08-05T09:15:00+00:00", version="1.0.0",
    )
    profile = StrategyRiskProfile(
        strategy_id="VOLATILITY_EXPANSION", required_leg_roles=("LONG_LEG_CE", "LONG_LEG_PE"),
        formula="MULTI_LEG_LONG_PREMIUM_PAID", lot_size=75,
    )
    orders = {
        coid_ce: SimpleNamespace(order_type="LIMIT", reference_price=120.0),
        coid_pe: SimpleNamespace(order_type="LIMIT", reference_price=110.0),
    }
    result = assess_defined_risk(
        state, {coid_ce: contract_ce, coid_pe: contract_pe}, orders,
        {coid_ce: "LONG_LEG_CE", coid_pe: "LONG_LEG_PE"}, profile, clock=_clock(),
    )
    assert result.decision == "ALLOW"
    assert result.max_loss == pytest.approx((120.0 * 225) + (110.0 * 225))


def test_volatility_compression_the_naked_twin_still_vetoes(tmp_path):
    """VOLATILITY_COMPRESSION (SELL both legs -- the naked-short twin
    from the SAME _build_legs branch) must never be treated as
    defined-risk. Sharing a construction branch does not mean sharing
    defined-risk eligibility."""
    journal = _journal(tmp_path)
    trade = dataclasses.replace(
        _constructed_trade(strategy_family="VOLATILITY_COMPRESSION"),
        legs=(
            _leg("SHORT", "CE", 24800, "SELL", premium=120.0),
            _leg("SHORT", "PE", 24800, "SELL", premium=110.0),
        ),
    )
    result = construct_and_gate_entry(
        "DEC-VOLCOMP", trade, "NIFTY", 75, journal, [], {}, _LIMITS, 100000.0, clock=_clock(),
    )
    assert "DEFINED_RISK_UNDEFINED_RISK_NO_STRESS_MODEL" in result.verdict.failed_checks


# --------------------------------------------------------------------- #
# NEUTRAL_PREMIUM_BUYING — the second real, reviewed defined-risk formula
# --------------------------------------------------------------------- #

def _neutral_premium_buying_trade(ce_premium=120.0, pe_premium=110.0, ratio=1):
    return dataclasses.replace(
        _constructed_trade(strategy_family="NEUTRAL_PREMIUM_BUYING"),
        legs=(
            _leg("LONG", "CE", 24900, "BUY", premium=ce_premium, ratio=ratio),
            _leg("LONG", "PE", 24700, "BUY", premium=pe_premium, ratio=ratio),
        ),
    )


def test_neutral_premium_buying_defined_risk_allows_with_correct_max_loss(tmp_path):
    """Both legs share the SAME MSI role ('LONG') -- the central proof
    that per-leg role disambiguation (by option_type) works correctly
    and neither leg silently overwrites the other."""
    journal = _journal(tmp_path)
    result = construct_and_gate_entry(
        "DEC-STRANGLE-1", _neutral_premium_buying_trade(ce_premium=120.0, pe_premium=110.0, ratio=2),
        "NIFTY", 75, journal, [], {}, _LIMITS, 100000.0, clock=_clock(),
    )
    assert result.verdict.decision == "VETO"  # capital remains the sole real blocker
    assert "DEFINED_RISK_WITHIN_BOUNDS" in result.verdict.passed_checks
    assert not any(f.startswith("DEFINED_RISK_") for f in result.verdict.failed_checks)
    assert "CAPITAL_MARGIN_NOT_CERTIFIED" in result.verdict.failed_checks


def test_neutral_premium_buying_max_loss_sums_both_legs_correctly(tmp_path):
    """Direct arithmetic proof: max_loss == (CE premium + PE premium) *
    quantity, confirming both legs actually contributed (not just one,
    which is exactly the bug the role-collision finding would have
    caused if left unfixed)."""
    journal = _journal(tmp_path)
    trade = _neutral_premium_buying_trade(ce_premium=120.0, pe_premium=110.0, ratio=2)
    pg = mint_position_group_id(journal, "DEC-STRANGLE-2", trade.strategy_family, "NIFTY", clock=_clock())
    coid_ce = f"{pg.position_group_id}-LEG-0"
    coid_pe = f"{pg.position_group_id}-LEG-1"
    journal.append_event(
        pg.position_group_id, "CONSTRUCTED", f"{pg.position_group_id}:CONSTRUCTED:0",
        {"contract_client_order_map": {"C0": coid_ce, "C1": coid_pe},
         "requested_quantities": {coid_ce: 150, coid_pe: 150},
         "actions": {}, "target_position_group_ids": {}, "target_contract_ids": {}, "flip_link_ids": {}},
        clock=_clock(),
    )
    state = fold(journal.read_events(pg.position_group_id))
    contract_ce = NiftyOptionContract(
        contract_id="C0", underlying="NIFTY", expiry="2026-08-27", strike=24900, option_type="CE", side="BUY",
        contract_symbol="NIFTY26AUG24900CE", capital_intent="STANDARD", strategy_id="NEUTRAL_PREMIUM_BUYING",
        selection_reason="t", construction_trace="t", timestamp="2026-08-05T09:15:00+00:00", version="1.0.0",
    )
    contract_pe = NiftyOptionContract(
        contract_id="C1", underlying="NIFTY", expiry="2026-08-27", strike=24700, option_type="PE", side="BUY",
        contract_symbol="NIFTY26AUG24700PE", capital_intent="STANDARD", strategy_id="NEUTRAL_PREMIUM_BUYING",
        selection_reason="t", construction_trace="t", timestamp="2026-08-05T09:15:00+00:00", version="1.0.0",
    )
    profile = StrategyRiskProfile(
        strategy_id="NEUTRAL_PREMIUM_BUYING", required_leg_roles=("LONG_LEG_CE", "LONG_LEG_PE"),
        formula="MULTI_LEG_LONG_PREMIUM_PAID", lot_size=75,
    )
    orders = {
        coid_ce: SimpleNamespace(order_type="LIMIT", reference_price=120.0),
        coid_pe: SimpleNamespace(order_type="LIMIT", reference_price=110.0),
    }
    result = assess_defined_risk(
        state, {coid_ce: contract_ce, coid_pe: contract_pe}, orders,
        {coid_ce: "LONG_LEG_CE", coid_pe: "LONG_LEG_PE"}, profile, clock=_clock(),
    )
    assert result.decision == "ALLOW"
    assert result.max_loss == pytest.approx((120.0 * 150) + (110.0 * 150))


def test_neutral_premium_selling_the_naked_twin_still_vetoes(tmp_path):
    """Regression guard: NEUTRAL_PREMIUM_SELLING (short strangle, both
    legs naked-short) must NEVER be treated as defined-risk -- confirms
    the new formula wiring didn't accidentally widen to the SELL-side
    sibling family, which shares the same 2-leg CE+PE shape but has
    genuinely unbounded risk."""
    journal = _journal(tmp_path)
    trade = dataclasses.replace(
        _constructed_trade(strategy_family="NEUTRAL_PREMIUM_SELLING"),
        legs=(
            _leg("SHORT", "CE", 24900, "SELL", premium=120.0),
            _leg("SHORT", "PE", 24700, "SELL", premium=110.0),
        ),
    )
    result = construct_and_gate_entry(
        "DEC-SHORT-STRANGLE", trade, "NIFTY", 75, journal, [], {}, _LIMITS, 100000.0, clock=_clock(),
    )
    assert "DEFINED_RISK_UNDEFINED_RISK_NO_STRESS_MODEL" in result.verdict.failed_checks


# --------------------------------------------------------------------- #
# IRON_CONDOR — fourth real, reviewed defined-risk formula
# --------------------------------------------------------------------- #

def _iron_condor_trade(
    short_call_strike=24900, short_put_strike=24700, long_call_strike=25000, long_put_strike=24600,
    sc_premium=60.0, sp_premium=55.0, lc_premium=20.0, lp_premium=18.0, ratio=1,
):
    return dataclasses.replace(
        _constructed_trade(strategy_family="IRON_CONDOR"),
        legs=(
            _leg("SHORT", "CE", short_call_strike, "SELL", premium=sc_premium, ratio=ratio),
            _leg("SHORT", "PE", short_put_strike, "SELL", premium=sp_premium, ratio=ratio),
            StrikeLeg(role="WING_UPPER", option_type="CE", strike=long_call_strike, expiry="2026-08-27",
                      delta=0.1, premium=lc_premium, open_interest=500.0, side="BUY", ratio=ratio,
                      reasoning=("t",)),
            StrikeLeg(role="WING_LOWER", option_type="PE", strike=long_put_strike, expiry="2026-08-27",
                      delta=0.1, premium=lp_premium, open_interest=500.0, side="BUY", ratio=ratio,
                      reasoning=("t",)),
        ),
    )


def test_iron_condor_symmetric_wings_defined_risk_allows(tmp_path):
    """Symmetric wings (both 100 pts wide): total_credit = (60-20) +
    (55-18) = 40 + 37 = 77. max_loss = 100*75 - 77*75 = (100-77)*75 =
    1725.0 -- proves the four-leg role disambiguation (SHORT_LEG_CE vs
    SHORT_LEG_PE, and WING_UPPER/WING_LOWER mapping to LONG_LEG_CE/PE)
    works correctly, since a mixed-up leg would produce a different,
    wrong figure."""
    journal = _journal(tmp_path)
    trade = _iron_condor_trade(
        short_call_strike=24900, long_call_strike=25000,  # 100pt call wing
        short_put_strike=24700, long_put_strike=24600,     # 100pt put wing
        sc_premium=60.0, lc_premium=20.0, sp_premium=55.0, lp_premium=18.0,
    )
    result = construct_and_gate_entry(
        "DEC-CONDOR-1", trade, "NIFTY", 75, journal, [], {}, _LIMITS, 100000.0, clock=_clock(),
    )
    assert result.verdict.decision == "VETO"  # capital remains the sole real blocker
    assert "DEFINED_RISK_WITHIN_BOUNDS" in result.verdict.passed_checks
    assert not any(f.startswith("DEFINED_RISK_") for f in result.verdict.failed_checks)
    assert "CAPITAL_MARGIN_NOT_CERTIFIED" in result.verdict.failed_checks


def test_iron_condor_max_loss_computed_correctly_symmetric_wings(tmp_path):
    journal = _journal(tmp_path)
    trade = _iron_condor_trade(
        short_call_strike=24900, long_call_strike=25000,
        short_put_strike=24700, long_put_strike=24600,
        sc_premium=60.0, lc_premium=20.0, sp_premium=55.0, lp_premium=18.0,
    )
    pg = mint_position_group_id(journal, "DEC-CONDOR-2", trade.strategy_family, "NIFTY", clock=_clock())
    pg_id = pg.position_group_id
    coids = {f"{pg_id}-LEG-{i}": leg for i, leg in enumerate(trade.legs)}
    journal.append_event(
        pg_id, "CONSTRUCTED", f"{pg_id}:CONSTRUCTED:0",
        {"contract_client_order_map": {f"C{i}": coid for i, coid in enumerate(coids)},
         "requested_quantities": {coid: 75 for coid in coids},
         "actions": {}, "target_position_group_ids": {}, "target_contract_ids": {}, "flip_link_ids": {}},
        clock=_clock(),
    )
    state = fold(journal.read_events(pg_id))

    role_map = {}
    contracts = {}
    orders = {}
    for coid, leg in coids.items():
        gate_b_role = (
            f"SHORT_LEG_{leg.option_type}" if leg.role == "SHORT"
            else "LONG_LEG_CE" if leg.role == "WING_UPPER"
            else "LONG_LEG_PE"
        )
        role_map[coid] = gate_b_role
        contracts[coid] = NiftyOptionContract(
            contract_id=coid, underlying="NIFTY", expiry="2026-08-27", strike=leg.strike,
            option_type=leg.option_type, side=leg.side, contract_symbol=f"NIFTY{coid}",
            capital_intent="STANDARD", strategy_id="IRON_CONDOR", selection_reason="t",
            construction_trace="t", timestamp="2026-08-05T09:15:00+00:00", version="1.0.0",
        )
        orders[coid] = SimpleNamespace(order_type="LIMIT", reference_price=leg.premium)

    profile = StrategyRiskProfile(
        strategy_id="IRON_CONDOR",
        required_leg_roles=("SHORT_LEG_CE", "SHORT_LEG_PE", "LONG_LEG_CE", "LONG_LEG_PE"),
        formula="IRON_CONDOR_MAX_WING_WIDTH_MINUS_TOTAL_CREDIT", lot_size=75,
    )
    result = assess_defined_risk(state, contracts, orders, role_map, profile, clock=_clock())
    assert result.decision == "ALLOW"
    # width=100 both sides, total_credit=(60-20)+(55-18)=77, qty=75
    assert result.max_loss == pytest.approx((100 * 75) - (77.0 * 75))


def test_iron_condor_asymmetric_wings_uses_max_not_sum_or_average(tmp_path):
    """The formula must take the WIDER wing, not sum or average both --
    a wrong implementation using (call_width+put_width) or their
    average would produce a materially different, incorrect figure
    here, since the wings are deliberately very different widths."""
    journal = _journal(tmp_path)
    trade = _iron_condor_trade(
        short_call_strike=24900, long_call_strike=25200,   # 300pt call wing (wide)
        short_put_strike=24700, long_put_strike=24650,      # 50pt put wing (narrow)
        sc_premium=60.0, lc_premium=10.0, sp_premium=30.0, lp_premium=25.0,
    )
    pg = mint_position_group_id(journal, "DEC-CONDOR-3", trade.strategy_family, "NIFTY", clock=_clock())
    pg_id = pg.position_group_id
    coids = {f"{pg_id}-LEG-{i}": leg for i, leg in enumerate(trade.legs)}
    journal.append_event(
        pg_id, "CONSTRUCTED", f"{pg_id}:CONSTRUCTED:0",
        {"contract_client_order_map": {f"C{i}": coid for i, coid in enumerate(coids)},
         "requested_quantities": {coid: 75 for coid in coids},
         "actions": {}, "target_position_group_ids": {}, "target_contract_ids": {}, "flip_link_ids": {}},
        clock=_clock(),
    )
    state = fold(journal.read_events(pg_id))
    role_map, contracts, orders = {}, {}, {}
    for coid, leg in coids.items():
        gate_b_role = (
            f"SHORT_LEG_{leg.option_type}" if leg.role == "SHORT"
            else "LONG_LEG_CE" if leg.role == "WING_UPPER"
            else "LONG_LEG_PE"
        )
        role_map[coid] = gate_b_role
        contracts[coid] = NiftyOptionContract(
            contract_id=coid, underlying="NIFTY", expiry="2026-08-27", strike=leg.strike,
            option_type=leg.option_type, side=leg.side, contract_symbol=f"NIFTY{coid}",
            capital_intent="STANDARD", strategy_id="IRON_CONDOR", selection_reason="t",
            construction_trace="t", timestamp="2026-08-05T09:15:00+00:00", version="1.0.0",
        )
        orders[coid] = SimpleNamespace(order_type="LIMIT", reference_price=leg.premium)
    profile = StrategyRiskProfile(
        strategy_id="IRON_CONDOR",
        required_leg_roles=("SHORT_LEG_CE", "SHORT_LEG_PE", "LONG_LEG_CE", "LONG_LEG_PE"),
        formula="IRON_CONDOR_MAX_WING_WIDTH_MINUS_TOTAL_CREDIT", lot_size=75,
    )
    result = assess_defined_risk(state, contracts, orders, role_map, profile, clock=_clock())
    assert result.decision == "ALLOW"
    # call_width=300, put_width=50 -> max=300. total_credit=(60-10)+(30-25)=55. qty=75.
    expected = (300 * 75) - (55.0 * 75)
    wrong_sum = ((300 + 50) * 75) - (55.0 * 75)
    wrong_avg = (175 * 75) - (55.0 * 75)
    assert result.max_loss == pytest.approx(expected)
    assert result.max_loss != pytest.approx(wrong_sum)
    assert result.max_loss != pytest.approx(wrong_avg)


def test_iron_condor_missing_wing_price_fails_closed(tmp_path):
    journal = _journal(tmp_path)
    trade = _iron_condor_trade(lc_premium=None)
    result = construct_and_gate_entry(
        "DEC-CONDOR-4", trade, "NIFTY", 75, journal, [], {}, _LIMITS, 100000.0, clock=_clock(),
    )
    # missing premium on one leg -> exposure untrusted -> portfolio-limits
    # fail-closed veto fires before defined-risk math would even be needed
    assert "PORTFOLIO_LIMITS_EXPOSURE_DATA_MISSING" in result.verdict.failed_checks


# --------------------------------------------------------------------- #
# IRON_FLY — fifth real, reviewed defined-risk formula (mechanically
# identical to IRON_CONDOR's shape/formula; both shorts collapsed to
# the same ATM strike, so the two wings are asymmetric by construction)
# --------------------------------------------------------------------- #

def test_iron_fly_same_shape_now_wired_defined_risk_allows(tmp_path):
    """A real IRON_FLY leg shape: both shorts at the SAME ATM strike
    (24800), wings at +/-100pts. call_width=100, put_width=100,
    total_credit=(70-15)+(65-12)=108. max_loss=(100-108)*75=-600 would
    be negative (an implausibly rich credit for a 100pt-wide fly) --
    use realistic premiums instead: sc=45, lc=10, sp=42, lp=9 ->
    total_credit=35+33=68, max_loss=(100-68)*75=2400."""
    journal = _journal(tmp_path)
    trade = _iron_condor_trade(
        short_call_strike=24800, long_call_strike=24900,   # 100pt call wing
        short_put_strike=24800, long_put_strike=24700,      # 100pt put wing, SAME short strike
        sc_premium=45.0, lc_premium=10.0, sp_premium=42.0, lp_premium=9.0,
    )
    trade = dataclasses.replace(trade, strategy_family="IRON_FLY")
    result = construct_and_gate_entry(
        "DEC-FLY-1", trade, "NIFTY", 75, journal, [], {}, _LIMITS, 100000.0, clock=_clock(),
    )
    assert result.verdict.decision == "VETO"  # capital remains the sole real blocker
    assert "DEFINED_RISK_WITHIN_BOUNDS" in result.verdict.passed_checks
    assert not any(f.startswith("DEFINED_RISK_") for f in result.verdict.failed_checks)
    assert "CAPITAL_MARGIN_NOT_CERTIFIED" in result.verdict.failed_checks


def test_iron_fly_asymmetric_wings_from_unequal_width_still_uses_max(tmp_path):
    """IRON_FLY's real construction anchors the short strike ATM and
    derives BOTH wings from the same expected-move width, so symmetric
    wings are the common case -- but nothing in the formula assumes
    symmetry, and a data/construction anomaly producing unequal wings
    must still take the max, not sum or average."""
    journal = _journal(tmp_path)
    trade = _iron_condor_trade(
        short_call_strike=24800, long_call_strike=25050,   # 250pt call wing
        short_put_strike=24800, long_put_strike=24730,      # 70pt put wing
        sc_premium=45.0, lc_premium=8.0, sp_premium=40.0, lp_premium=15.0,
    )
    trade = dataclasses.replace(trade, strategy_family="IRON_FLY")
    pg = mint_position_group_id(journal, "DEC-FLY-2", trade.strategy_family, "NIFTY", clock=_clock())
    pg_id = pg.position_group_id
    coids = {f"{pg_id}-LEG-{i}": leg for i, leg in enumerate(trade.legs)}
    journal.append_event(
        pg_id, "CONSTRUCTED", f"{pg_id}:CONSTRUCTED:0",
        {"contract_client_order_map": {f"C{i}": coid for i, coid in enumerate(coids)},
         "requested_quantities": {coid: 75 for coid in coids},
         "actions": {}, "target_position_group_ids": {}, "target_contract_ids": {}, "flip_link_ids": {}},
        clock=_clock(),
    )
    state = fold(journal.read_events(pg_id))
    role_map, contracts, orders = {}, {}, {}
    for coid, leg in coids.items():
        gate_b_role = (
            f"SHORT_LEG_{leg.option_type}" if leg.role == "SHORT"
            else "LONG_LEG_CE" if leg.role == "WING_UPPER"
            else "LONG_LEG_PE"
        )
        role_map[coid] = gate_b_role
        contracts[coid] = NiftyOptionContract(
            contract_id=coid, underlying="NIFTY", expiry="2026-08-27", strike=leg.strike,
            option_type=leg.option_type, side=leg.side, contract_symbol=f"NIFTY{coid}",
            capital_intent="STANDARD", strategy_id="IRON_FLY", selection_reason="t",
            construction_trace="t", timestamp="2026-08-05T09:15:00+00:00", version="1.0.0",
        )
        orders[coid] = SimpleNamespace(order_type="LIMIT", reference_price=leg.premium)
    profile = StrategyRiskProfile(
        strategy_id="IRON_FLY",
        required_leg_roles=("SHORT_LEG_CE", "SHORT_LEG_PE", "LONG_LEG_CE", "LONG_LEG_PE"),
        formula="IRON_CONDOR_MAX_WING_WIDTH_MINUS_TOTAL_CREDIT", lot_size=75,
    )
    result = assess_defined_risk(state, contracts, orders, role_map, profile, clock=_clock())
    assert result.decision == "ALLOW"
    # call_width=250, put_width=70 -> max=250. total_credit=(45-8)+(40-15)=62. qty=75.
    expected = (250 * 75) - (62.0 * 75)
    wrong_sum = ((250 + 70) * 75) - (62.0 * 75)
    assert result.max_loss == pytest.approx(expected)
    assert result.max_loss != pytest.approx(wrong_sum)


def test_iron_fly_missing_wing_price_fails_closed(tmp_path):
    journal = _journal(tmp_path)
    trade = _iron_condor_trade(
        short_call_strike=24800, short_put_strike=24800, lc_premium=None,
    )
    trade = dataclasses.replace(trade, strategy_family="IRON_FLY")
    result = construct_and_gate_entry(
        "DEC-FLY-3", trade, "NIFTY", 75, journal, [], {}, _LIMITS, 100000.0, clock=_clock(),
    )
    assert "PORTFOLIO_LIMITS_EXPOSURE_DATA_MISSING" in result.verdict.failed_checks


# --------------------------------------------------------------------- #
# BUTTERFLY — sixth real, reviewed defined-risk formula. Genuinely
# different payoff shape from the iron condor family: single option
# type, three legs, 1x/2x/1x ratio, net-debit-paid formula.
# --------------------------------------------------------------------- #

def _butterfly_trade(
    lower_strike=24700, body_strike=24800, upper_strike=24900,
    lower_premium=30.0, body_premium=18.0, upper_premium=8.0,
):
    return dataclasses.replace(
        _constructed_trade(strategy_family="BUTTERFLY"),
        legs=(
            _leg("WING_LOWER", "CE", lower_strike, "BUY", premium=lower_premium, ratio=1),
            _leg("BODY", "CE", body_strike, "SELL", premium=body_premium, ratio=2),
            _leg("WING_UPPER", "CE", upper_strike, "BUY", premium=upper_premium, ratio=1),
        ),
    )


def test_butterfly_defined_risk_allows(tmp_path):
    """wing_cost = 30 + 8 = 38, body_credit = 2 * 18 = 36 -> net debit
    2 per unit -- a modest, economically ordinary long butterfly."""
    journal = _journal(tmp_path)
    trade = _butterfly_trade()
    result = construct_and_gate_entry(
        "DEC-FLY-BODY-1", trade, "NIFTY", 75, journal, [], {}, _LIMITS, 100000.0, clock=_clock(),
    )
    assert result.verdict.decision == "VETO"  # capital remains the sole real blocker
    assert "DEFINED_RISK_WITHIN_BOUNDS" in result.verdict.passed_checks
    assert not any(f.startswith("DEFINED_RISK_") for f in result.verdict.failed_checks)
    assert "CAPITAL_MARGIN_NOT_CERTIFIED" in result.verdict.failed_checks


def test_butterfly_max_loss_computed_correctly(tmp_path):
    journal = _journal(tmp_path)
    trade = _butterfly_trade(lower_premium=30.0, body_premium=18.0, upper_premium=8.0)
    pg = mint_position_group_id(journal, "DEC-FLY-BODY-2", trade.strategy_family, "NIFTY", clock=_clock())
    pg_id = pg.position_group_id
    coids = {f"{pg_id}-LEG-{i}": leg for i, leg in enumerate(trade.legs)}
    journal.append_event(
        pg_id, "CONSTRUCTED", f"{pg_id}:CONSTRUCTED:0",
        {"contract_client_order_map": {f"C{i}": coid for i, coid in enumerate(coids)},
         "requested_quantities": {coid: (150 if leg.role == "BODY" else 75) for coid, leg in coids.items()},
         "actions": {}, "target_position_group_ids": {}, "target_contract_ids": {}, "flip_link_ids": {}},
        clock=_clock(),
    )
    state = fold(journal.read_events(pg_id))
    role_map, contracts, orders = {}, {}, {}
    for coid, leg in coids.items():
        role_map[coid] = leg.role
        contracts[coid] = NiftyOptionContract(
            contract_id=coid, underlying="NIFTY", expiry="2026-08-27", strike=leg.strike,
            option_type=leg.option_type, side=leg.side, contract_symbol=f"NIFTY{coid}",
            capital_intent="STANDARD", strategy_id="BUTTERFLY", selection_reason="t",
            construction_trace="t", timestamp="2026-08-05T09:15:00+00:00", version="1.0.0",
        )
        orders[coid] = SimpleNamespace(order_type="LIMIT", reference_price=leg.premium)
    profile = StrategyRiskProfile(
        strategy_id="BUTTERFLY", required_leg_roles=("WING_LOWER", "BODY", "WING_UPPER"),
        formula="BUTTERFLY_NET_DEBIT_PAID", lot_size=75,
    )
    result = assess_defined_risk(state, contracts, orders, role_map, profile, clock=_clock())
    assert result.decision == "ALLOW"
    # wing_cost=(30+8)*75=2850, body_credit=18*150=2700, max_loss=150
    assert result.max_loss == pytest.approx((30.0 + 8.0) * 75 - 18.0 * 150)


def test_butterfly_missing_body_price_fails_closed(tmp_path):
    journal = _journal(tmp_path)
    trade = _butterfly_trade(body_premium=None)
    result = construct_and_gate_entry(
        "DEC-FLY-BODY-3", trade, "NIFTY", 75, journal, [], {}, _LIMITS, 100000.0, clock=_clock(),
    )
    assert "PORTFOLIO_LIMITS_EXPOSURE_DATA_MISSING" in result.verdict.failed_checks


def test_butterfly_rich_body_credit_exceeding_wing_cost_fails_closed(tmp_path):
    """Audit check: an implausibly rich body premium relative to the
    wings (data anomaly, or a violation of strike convexity) must
    still fail closed via the shared negative-max_loss guard, exactly
    like the vertical spread and iron condor formulas."""
    journal = _journal(tmp_path)
    trade = _butterfly_trade(lower_premium=5.0, body_premium=100.0, upper_premium=3.0)
    result = construct_and_gate_entry(
        "DEC-FLY-BODY-4", trade, "NIFTY", 75, journal, [], {}, _LIMITS, 100000.0, clock=_clock(),
    )
    assert result.verdict.decision == "VETO"
    assert "DEFINED_RISK_INCOMPLETE_DEFINED_RISK_GROUP" in result.verdict.failed_checks


# --------------------------------------------------------------------- #
# RATIO — explicitly reviewed and confirmed PERMANENT veto, not a
# pending gap. A 1x long ATM CE + 2x short OTM CE nets to a naked short
# call above the short strike: genuinely unbounded upside loss, no
# finite max_loss formula can honestly describe it.
# --------------------------------------------------------------------- #

def test_ratio_naked_short_tail_stays_permanently_vetoed(tmp_path):
    """Regression pin: RATIO must NEVER be given a defined-risk formula.
    Netting 1x long ATM CE against 2x short OTM CE leaves a net naked
    short call above the short strike -- unbounded loss as the
    underlying rises without limit. Unlike IRON_CONDOR/IRON_FLY/
    BUTTERFLY (all genuinely bounded), no formula can honestly assign
    this position a finite max_loss."""
    journal = _journal(tmp_path)
    trade = dataclasses.replace(
        _constructed_trade(strategy_family="RATIO"),
        legs=(
            _leg("LONG", "CE", 24800, "BUY", premium=100.0, ratio=1),
            _leg("SHORT", "CE", 25000, "SELL", premium=40.0, ratio=2),
        ),
    )
    result = construct_and_gate_entry(
        "DEC-RATIO-1", trade, "NIFTY", 75, journal, [], {}, _LIMITS, 100000.0, clock=_clock(),
    )
    assert "DEFINED_RISK_UNDEFINED_RISK_NO_STRESS_MODEL" in result.verdict.failed_checks
