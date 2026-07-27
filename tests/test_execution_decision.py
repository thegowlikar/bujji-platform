"""Production Engineering Sprint 2 -- Stage Interface Extraction.

Unit tests for execution_decision.build_execution_decision(), the newly
extracted Execution Decision stage. These are NEW tests made possible by
this sprint: before the extraction, this logic could only be exercised
indirectly through a full Orchestrator._enter() call (broker, capital
engine, event bus, and all). Now it is a pure function, testable with
plain values and no mocks.
"""
from datetime import datetime, timezone

from bujji.core.enums import Direction, OptionType, Side
from bujji.core.execution_decision import build_execution_decision
from bujji.core.models import Candle, OptionContract, TradeIntention


def _candle():
    return Candle(datetime(2026, 7, 13, 9, 20, tzinfo=timezone.utc), 22000, 22010, 21990, 22005, 1000)


def _intention(decision_id=""):
    return TradeIntention(
        direction=Direction.BEARISH, strategy_type="PREMIUM_VWAP_STRADDLE", thesis="",
        evidence_refs={}, as_of=_candle().timestamp, decision_id=decision_id,
    )


def _contract(symbol, strike, option_type):
    return OptionContract(symbol=symbol, underlying="NIFTY", strike=strike, expiry="WEEKLY",
                          option_type=option_type, lot_size=75)


def test_decision_id_derived_from_candle_timestamp_when_intention_has_none():
    result = build_execution_decision(
        _candle(), _intention(decision_id=""),
        _contract("NIFTY22000CE", 22000, OptionType.CE), _contract("NIFTY22000PE", 22000, OptionType.PE),
        75, {}, "paper",
    )
    assert result.decision_id == "DEC-20260713092000"


def test_decision_id_preserved_when_intention_already_has_one():
    result = build_execution_decision(
        _candle(), _intention(decision_id="DEC-EXPLICIT-123"),
        _contract("NIFTY22000CE", 22000, OptionType.CE), _contract("NIFTY22000PE", 22000, OptionType.PE),
        75, {}, "paper",
    )
    assert result.decision_id == "DEC-EXPLICIT-123"
    assert result.snapshot.decision_id == "DEC-EXPLICIT-123"
    assert result.plan.decision_id == "DEC-EXPLICIT-123"


def test_client_order_ids_are_derived_from_candle_timestamp_and_leg():
    result = build_execution_decision(
        _candle(), _intention(),
        _contract("NIFTY22000CE", 22000, OptionType.CE), _contract("NIFTY22000PE", 22000, OptionType.PE),
        75, {}, "paper",
    )
    assert result.ce_cid == "ENTRY-CE-20260713092000"
    assert result.pe_cid == "ENTRY-PE-20260713092000"
    assert result.plan.idempotency_keys == {"ce": result.ce_cid, "pe": result.pe_cid}


def test_snapshot_captures_planned_contracts_and_structure():
    ce = _contract("NIFTY22000CE", 22000, OptionType.CE)
    pe = _contract("NIFTY22000PE", 22000, OptionType.PE)
    result = build_execution_decision(_candle(), _intention(), ce, pe, 75, {"regime": "trending"}, "paper")
    assert result.snapshot.planned_contracts == {"ce": "NIFTY22000CE", "pe": "NIFTY22000PE", "strike": 22000}
    assert result.snapshot.planned_structure == "ATM_STRADDLE"
    assert result.snapshot.broker_session == "paper"
    assert result.snapshot.intelligence_snapshot == {"regime": "trending"}
    assert result.snapshot.replay_reference == _candle().timestamp.isoformat()


def test_plan_carries_both_legs_sell_side_and_execution_sequence():
    ce = _contract("NIFTY22000CE", 22000, OptionType.CE)
    pe = _contract("NIFTY22000PE", 22000, OptionType.PE)
    result = build_execution_decision(_candle(), _intention(), ce, pe, 75, {}, "paper")
    assert result.plan.contracts == {"ce": ce, "pe": pe}
    assert result.plan.side_per_leg == {"ce": Side.SELL, "pe": Side.SELL}
    assert result.plan.quantities == {"ce": 75, "pe": 75}
    assert result.plan.execution_sequence == ["ce", "pe"]
    assert result.plan.as_of == _candle().timestamp


def test_execution_id_is_derived_from_candle_timestamp():
    result = build_execution_decision(
        _candle(), _intention(), _contract("NIFTY22000CE", 22000, OptionType.CE),
        _contract("NIFTY22000PE", 22000, OptionType.PE), 75, {}, "paper",
    )
    assert result.plan.execution_id == "EXEC-20260713092000"


def test_snapshot_and_plan_are_immutable():
    import pytest
    result = build_execution_decision(
        _candle(), _intention(), _contract("NIFTY22000CE", 22000, OptionType.CE),
        _contract("NIFTY22000PE", 22000, OptionType.PE), 75, {}, "paper",
    )
    with pytest.raises(Exception):
        result.snapshot.decision_id = "changed"
    with pytest.raises(Exception):
        result.plan.decision_id = "changed"


def test_pure_function_no_broker_or_orchestrator_dependency():
    """This is the whole point of the extraction: build_execution_decision
    takes no Orchestrator, no broker, no capital engine -- just data in,
    data out. Confirmed by successfully calling it with nothing but plain
    values above; this test locks in the module has no such import."""
    import ast
    from pathlib import Path
    tree = ast.parse((Path(__file__).resolve().parent.parent / "bujji/core/execution_decision.py").read_text())
    imported_modules = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any("orchestrator" in m for m in imported_modules)
    assert not any("broker" in m for m in imported_modules)
