"""Production Engineering Sprint 5 -- Stage Interface Extraction.

Unit tests for strategy_evaluation.evaluate_entry_gate(), the newly
extracted Strategy Evaluation gating decision. Before this extraction,
the is_trade/max-trades/clock-trust gate could only be exercised by
driving a full Orchestrator through _handle_pre_position(). Now it is a
pure function of four plain values.
"""
import pytest

from bujji.core.strategy_evaluation import (
    CLOCK_UNTRUSTED, MAX_TRADES_REACHED, NO_TRADE, PROCEED, evaluate_entry_gate,
)


def test_no_trade_when_signal_is_not_a_trade():
    result = evaluate_entry_gate(is_trade=False, trades_taken=0, max_trades_per_day=1, clock_trusted=True)
    assert result.action == NO_TRADE


def test_max_trades_reached_takes_priority_over_clock_check():
    # trades_taken >= max AND clock untrusted -- max-trades must win, matching
    # the original code's check ORDER (is_trade -> max_trades -> clock_trust).
    result = evaluate_entry_gate(is_trade=True, trades_taken=1, max_trades_per_day=1, clock_trusted=False)
    assert result.action == MAX_TRADES_REACHED


def test_clock_untrusted_blocks_when_trades_remain():
    result = evaluate_entry_gate(is_trade=True, trades_taken=0, max_trades_per_day=1, clock_trusted=False)
    assert result.action == CLOCK_UNTRUSTED


def test_proceed_when_all_gates_pass():
    result = evaluate_entry_gate(is_trade=True, trades_taken=0, max_trades_per_day=1, clock_trusted=True)
    assert result.action == PROCEED


def test_max_trades_boundary_exactly_at_limit_is_reached():
    # trades_taken == max_trades_per_day -- the original code used ">=".
    result = evaluate_entry_gate(is_trade=True, trades_taken=1, max_trades_per_day=1, clock_trusted=True)
    assert result.action == MAX_TRADES_REACHED


def test_max_trades_boundary_one_below_limit_proceeds():
    result = evaluate_entry_gate(is_trade=True, trades_taken=0, max_trades_per_day=1, clock_trusted=True)
    assert result.action == PROCEED


def test_result_is_immutable():
    result = evaluate_entry_gate(is_trade=False, trades_taken=0, max_trades_per_day=1, clock_trusted=True)
    with pytest.raises(Exception):
        result.action = "changed"


def test_pure_function_no_orchestrator_or_fsm_dependency():
    """The whole point of the extraction: evaluate_entry_gate takes no
    FSM, no EventBus, no Orchestrator -- just four plain values. This
    test locks in the module has no such import."""
    import ast
    from pathlib import Path
    tree = ast.parse((Path(__file__).resolve().parent.parent / "bujji/core/strategy_evaluation.py").read_text())
    imported_modules = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any("orchestrator" in m or "state_machine" in m or "event_bus" in m for m in imported_modules)


# ---------------------------------------------------------------------- #
# Integration: the real orchestrator wires the gate correctly end-to-end
# ---------------------------------------------------------------------- #
from bujji.core.enums import State  # noqa: E402
from bujji.replay.engine import ReplayEngine  # noqa: E402
from tests.conftest import c  # noqa: E402


def _cfg(config, tmp_path):
    config.paths.journal_csv = tmp_path / "j.csv"
    config.paths.database = tmp_path / "b.db"
    config.paths.state_file = tmp_path / "s.json"
    config.broker.order_timeout_seconds = 0.05
    config.broker.poll_interval_seconds = 0.01
    return config


@pytest.mark.asyncio
async def test_max_trades_reached_gate_blocks_entry_end_to_end(config, logger, tmp_path):
    _cfg(config, tmp_path)
    config.strategy.max_trades_per_day = 0  # Zero trades allowed -> gate must reject immediately.
    candles = [c(9, 20, 22000, 22010, 21990, 22005, vol=1000)]
    engine = ReplayEngine(config, logger)
    result = await engine.run(candles)
    assert result.final_state == "DONE_FOR_DAY"
    assert len(result.trades) == 0


@pytest.mark.asyncio
async def test_clock_untrusted_gate_blocks_entry_end_to_end(config, logger, tmp_path):
    _cfg(config, tmp_path)
    candles = [c(9, 20, 22000, 22010, 21990, 22005, vol=1000)]
    engine = ReplayEngine(config, logger)
    engine.orchestrator.set_clock_trust(False, "test_forced_distrust")
    result = await engine.run(candles)
    assert len(result.trades) == 0
    assert engine.orchestrator.state is not State.IN_POSITION  # Entry never attempted.
