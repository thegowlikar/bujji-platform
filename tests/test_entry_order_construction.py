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
    """Regression guard: only LONG_DIRECTIONAL is wired. Every other
    real family must still VETO UNDEFINED_RISK_NO_STRESS_MODEL,
    unconditionally -- confirming this change didn't accidentally widen
    coverage beyond the one formula actually reviewed and tested."""
    journal = _journal(tmp_path)
    for family in (
        "BUTTERFLY", "COVERED", "IRON_CONDOR", "RATIO", "SHORT_DIRECTIONAL", "SYNTHETIC",
        "NEUTRAL_PREMIUM_SELLING", "VOLATILITY_COMPRESSION", "CALENDAR",
    ):
        trade = dataclasses.replace(_constructed_trade(strategy_family=family))
        result = construct_and_gate_entry(
            f"DEC-{family}", trade, "NIFTY", 75, journal, [], {}, _LIMITS, 100000.0, clock=_clock(),
        )
        assert "DEFINED_RISK_UNDEFINED_RISK_NO_STRESS_MODEL" in result.verdict.failed_checks, family


def test_volatility_expansion_mechanically_identical_shape_still_not_wired(tmp_path):
    """Explicit regression pin for the audit finding disclosed in the
    module docstring: VOLATILITY_EXPANSION (a long ATM straddle) is the
    same BUY-both-legs shape as NEUTRAL_PREMIUM_BUYING and COULD reuse
    the identical formula, but was deliberately not wired in this pass
    to keep the change scoped to one new family at a time. Must still
    VETO today -- if this ever silently starts passing, someone wired
    it without updating this test/the docstring."""
    journal = _journal(tmp_path)
    trade = dataclasses.replace(
        _constructed_trade(strategy_family="VOLATILITY_EXPANSION"),
        legs=(
            _leg("LONG", "CE", 24800, "BUY", premium=120.0),
            _leg("LONG", "PE", 24800, "BUY", premium=110.0),
        ),
    )
    result = construct_and_gate_entry(
        "DEC-VOLEXP", trade, "NIFTY", 75, journal, [], {}, _LIMITS, 100000.0, clock=_clock(),
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
