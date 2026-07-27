"""Production Engineering Sprint 4 -- Stage Interface Extraction.

Unit tests for journal_recording.build_trade_record(), the newly
extracted Journal Recording (trade half). Before this extraction,
TradeRecord construction could only be exercised by driving a full
Position through Orchestrator._journal_trade(). Now it is a pure
function, testable with plain values and no live TradeJournal.
"""
from datetime import datetime, timezone

from bujji.core.enums import Direction, OptionType, Side
from bujji.core.journal_recording import build_trade_record
from bujji.core.models import OptionContract, Position


def _contract(symbol, strike, option_type):
    return OptionContract(symbol=symbol, underlying="NIFTY", strike=strike, expiry="WEEKLY",
                          option_type=option_type, lot_size=75)


def _position(**overrides):
    ce = _contract("NIFTY22000CE", 22000, OptionType.CE)
    pe = _contract("NIFTY22000PE", 22000, OptionType.PE)
    defaults = dict(
        contract=ce, direction=Direction.NEUTRAL, entry_side=Side.SELL, quantity=75,
        entry_price=162.0, entry_spot=22005.0,
        entry_time=datetime(2026, 7, 13, 9, 20, tzinfo=timezone.utc), orb=None,
        ce_contract=ce, pe_contract=pe, decision_id="DEC-20260713092000",
    )
    defaults.update(overrides)
    return Position(**defaults)


def test_holding_time_computed_from_entry_and_exit():
    pos = _position()
    exit_time = datetime(2026, 7, 13, 9, 30, tzinfo=timezone.utc)
    record = build_trade_record(pos, exit_time, 22012.0, 163.0, "premium_vwap")
    assert record.holding_time_min == 10.0


def test_daily_result_uses_positions_mtm_seller_convention():
    pos = _position(entry_price=162.0, quantity=75)
    exit_time = datetime(2026, 7, 13, 9, 30, tzinfo=timezone.utc)
    record = build_trade_record(pos, exit_time, 22012.0, 163.0, "premium_vwap")
    # Seller: profit accrues as premium FALLS -- exit premium (163) > entry (162)
    # is a loss of (162-163)*75 = -75.0, matching Position.mtm()'s own convention.
    assert record.daily_result == -75.0


def test_trade_id_derived_from_entry_time_and_primary_contract_symbol():
    pos = _position()
    record = build_trade_record(pos, pos.entry_time, 22012.0, 163.0, "x")
    assert record.trade_id == "20260713092000-NIFTY22000CE"


def test_decision_id_carried_through_unchanged():
    pos = _position(decision_id="DEC-EXPLICIT-1")
    record = build_trade_record(pos, pos.entry_time, 22012.0, 163.0, "x")
    assert record.decision_id == "DEC-EXPLICIT-1"


def test_ce_pe_symbols_from_straddle_legs():
    pos = _position()
    record = build_trade_record(pos, pos.entry_time, 22012.0, 163.0, "x")
    assert record.ce_symbol == "NIFTY22000CE"
    assert record.pe_symbol == "NIFTY22000PE"


def test_pe_symbol_empty_when_position_has_no_pe_leg_single_leg_case():
    pos = _position(pe_contract=None)
    record = build_trade_record(pos, pos.entry_time, 22012.0, 163.0, "x")
    assert record.pe_symbol == ""
    assert record.ce_symbol == pos.contract.symbol  # Falls back to primary contract.


def test_capital_decision_fields_parsed_with_defaults_when_none():
    pos = _position(capital_decision=None)
    record = build_trade_record(pos, pos.entry_time, 22012.0, 163.0, "x")
    assert record.capital_status == ""
    assert record.approved_lots == 0
    assert record.capital_utilization == ""


def test_capital_decision_fields_parsed_when_present():
    pos = _position(capital_decision={"status": "SAFE", "approved_lots": "1", "capital_utilization": "0.01"})
    record = build_trade_record(pos, pos.entry_time, 22012.0, 163.0, "x")
    assert record.capital_status == "SAFE"
    assert record.approved_lots == "1"
    assert record.capital_utilization == "0.01"


def test_excursion_and_orb_fields_rounded_and_defaulted():
    pos = _position(max_profit_seen=1.005, max_loss_seen=-2.005,
                    max_favourable_excursion=3.005, max_adverse_excursion=-4.005, orb=None)
    record = build_trade_record(pos, pos.entry_time, 22012.0, 163.0, "x")
    assert record.orb_high == 0.0
    assert record.orb_low == 0.0
    assert record.max_profit_seen == round(1.005, 2)
    assert record.max_loss_seen == round(-2.005, 2)


def test_exit_reason_and_exit_fields_passed_through():
    pos = _position()
    exit_time = datetime(2026, 7, 13, 9, 30, tzinfo=timezone.utc)
    record = build_trade_record(pos, exit_time, 22012.5, 163.5, "hard_exit")
    assert record.exit_reason == "hard_exit"
    assert record.exit_spot == 22012.5
    assert record.exit_premium == 163.5
    assert record.exit_time == exit_time.isoformat()


def test_pure_function_no_journal_or_orchestrator_dependency():
    """The whole point of the extraction: build_trade_record takes only a
    Position and exit values -- no TradeJournal, no Orchestrator. This
    test locks in the module has no such import."""
    import ast
    from pathlib import Path
    tree = ast.parse((Path(__file__).resolve().parent.parent / "bujji/core/journal_recording.py").read_text())
    imported_modules = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any("orchestrator" in m for m in imported_modules)
    assert "journal.journal" in imported_modules  # Imports the TradeRecord TYPE only, never .record().
