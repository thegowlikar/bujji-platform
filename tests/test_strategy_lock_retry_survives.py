"""A locked session must be able to RE-ATTEMPT entry instead of dying.

`entry_control`'s own docstring states the contract: "STRATEGY_ALREADY_DEPLOYED,
not 'strategy is locked,' is the condition that blocks a second entry; being
locked is a PRECONDITION of the single allowed entry, not a reason to block it."

`select_and_lock_strategy` broke that by calling `lock()` on every cycle, and
`lock()` raises. In a continuous session (96 cycles, 300s apart) any entry that
locked and then failed downstream killed the whole session on the next stable
cycle -- including sessions holding live orphaned legs.

Removing that crash EXPOSES a second defect, so both are pinned here: an orphan
left the state at STRATEGY_LOCKED, which is the one state `can_enter_trade`
returns ALLOWED for. Nothing but the crash was stopping a SECOND entry stacked
on top of live naked legs.
"""
from __future__ import annotations

import ast
import io
import os
from unittest.mock import MagicMock

import pytest

from bujji.production_runtime.trading_session_governor.entry_control import (
    REASON_ALLOWED, REASON_STRATEGY_ALREADY_DEPLOYED, can_enter_trade,
)
from bujji.production_runtime.trading_session_governor.session_governor import (
    TradingSessionGovernor,
)
from bujji.production_runtime.trading_session_governor.session_trading_state import (
    IllegalTradingSessionTransition, TradingSessionState,
)
from bujji.production_runtime.trading_session_governor.strategy_lock import (
    StrategyAlreadyLockedError, StrategyDecision, StrategyLock,
)
from bujji.production_runtime.trading_session_governor.exit_policy import ExitPolicyConfig
from bujji.trading_brain.risk_governor.market_regime_adapter import (
    TREND_SIDEWAYS, TREND_TRENDING_UP, VOL_HIGH, VOL_LOW,
)

from datetime import datetime


def _clock():
    return datetime(2026, 1, 5, 10, 0, 0)


def _governor(bus=None):
    return TradingSessionGovernor(
        session_id="sess-retry", trading_brain_runtime=MagicMock(), registry=MagicMock(),
        lifecycle_runtime=MagicMock(), executor=MagicMock(),
        exit_policy_config=ExitPolicyConfig(max_loss_fraction=1.0),
        clock=_clock, event_bus=bus,
    )


def _stages(bus):
    return [c.args[0].payload.get("stage") for c in bus.publish_nowait.call_args_list
            if getattr(c.args[0], "payload", None)]


# --------------------------------------------------------------- A: the retry
def test_second_select_and_lock_does_not_raise():
    g = _governor()
    g.begin_market_analysis()
    g.select_and_lock_strategy(TREND_SIDEWAYS, VOL_LOW)
    assert g.strategy_lock.is_locked()
    g.select_and_lock_strategy(TREND_SIDEWAYS, VOL_LOW)   # crashed before this fix
    assert g.state == TradingSessionState.STRATEGY_LOCKED


def test_second_select_returns_the_locked_strategy_not_the_fresh_one():
    g = _governor()
    g.begin_market_analysis()
    first = g.select_and_lock_strategy(TREND_SIDEWAYS, VOL_LOW)
    second = g.select_and_lock_strategy(TREND_TRENDING_UP, VOL_HIGH)
    # PRECONDITION -- if these regimes ever map to the same family this test
    # proves nothing, so it fails loudly rather than passing vacuously.
    from bujji.production_runtime.trading_session_governor.strategy_selector import select_strategy
    fresh = select_strategy(TREND_TRENDING_UP, VOL_HIGH, _clock)
    assert fresh.selected_strategy != first.selected_strategy, "regimes no longer diverge"
    assert second.selected_strategy == first.selected_strategy


def test_second_select_does_not_replace_the_locked_decision():
    g = _governor()
    g.begin_market_analysis()
    g.select_and_lock_strategy(TREND_SIDEWAYS, VOL_LOW)
    original = g.strategy_lock.decision
    g.select_and_lock_strategy(TREND_TRENDING_UP, VOL_HIGH)
    assert g.strategy_lock.decision is original


def test_divergence_is_published_when_the_regime_has_moved():
    bus = MagicMock()
    g = _governor(bus=bus)
    g.begin_market_analysis()
    g.select_and_lock_strategy(TREND_SIDEWAYS, VOL_LOW)
    g.select_and_lock_strategy(TREND_TRENDING_UP, VOL_HIGH)
    assert "STRATEGY_SELECTION_DIVERGED" in _stages(bus)


def test_no_divergence_published_when_the_regime_is_unchanged():
    bus = MagicMock()
    g = _governor(bus=bus)
    g.begin_market_analysis()
    g.select_and_lock_strategy(TREND_SIDEWAYS, VOL_LOW)
    g.select_and_lock_strategy(TREND_SIDEWAYS, VOL_LOW)
    assert "STRATEGY_SELECTION_DIVERGED" not in _stages(bus)


def test_retry_is_allowed_by_entry_control_after_a_failed_entry():
    """The whole point: entry fails, the session survives, the next cycle retries."""
    g = _governor()
    g.begin_market_analysis()
    g.select_and_lock_strategy(TREND_SIDEWAYS, VOL_LOW)
    g._trading_brain_runtime.process_entry_cycle.return_value = MagicMock(filled=False)
    _result, decision = g.attempt_entry()
    assert decision.allowed and decision.reason == REASON_ALLOWED
    assert g.state == TradingSessionState.STRATEGY_LOCKED     # not filled -> still locked
    g.select_and_lock_strategy(TREND_SIDEWAYS, VOL_LOW)       # the cycle that used to die
    _result2, decision2 = g.attempt_entry()
    assert decision2.allowed


def test_the_lock_primitive_itself_still_raises():
    """The structural guarantee is UNCHANGED -- only the second CALL was removed."""
    lock = StrategyLock()
    d = StrategyDecision(session_id="s", timestamp=_clock(), trend_regime=TREND_SIDEWAYS,
                         volatility_regime=VOL_LOW, selected_strategy="IRON_CONDOR",
                         reasoning="t", confidence="HIGH")
    lock.lock(d)
    with pytest.raises(StrategyAlreadyLockedError):
        lock.lock(d)


# ------------------------------------------------- B: an orphan is a position
def test_mark_position_deployed_moves_out_of_strategy_locked():
    g = _governor()
    g.begin_market_analysis()
    g.select_and_lock_strategy(TREND_SIDEWAYS, VOL_LOW)
    assert can_enter_trade(g.state, g.strategy_lock).allowed
    g.mark_position_deployed("orphaned_legs_registered")
    assert g.state == TradingSessionState.POSITION_ACTIVE


def test_a_deployed_orphan_blocks_a_second_entry():
    g = _governor()
    g.begin_market_analysis()
    g.select_and_lock_strategy(TREND_SIDEWAYS, VOL_LOW)
    g.mark_position_deployed("orphaned_legs_registered")
    decision = can_enter_trade(g.state, g.strategy_lock)
    assert not decision.allowed
    assert decision.reason == REASON_STRATEGY_ALREADY_DEPLOYED


# ------------------------------------------------------- runner-side wiring
RUNNER_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "bujji_options_os_runner.py")
RUNNER_SRC = io.open(RUNNER_PATH, encoding="utf-8").read()
RUNNER_AST = ast.parse(RUNNER_SRC)


def _method(name: str) -> str:
    for node in ast.walk(RUNNER_AST):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(f"{name} not found in the runner")


def test_both_orphan_paths_mark_the_position_deployed():
    """Success path AND the never-raises except handler. Missing either one
    leaves a session that can stack a second entry on live legs."""
    src = _method("_register_orphaned_legs")
    assert src.count("_mark_orphan_deployed()") == 2, src.count("_mark_orphan_deployed()")


def test_mark_orphan_deployed_never_raises():
    from bujji_options_os_runner import OptionsOSRunner
    runner = object.__new__(OptionsOSRunner)
    runner._governor = MagicMock()
    runner._governor.mark_position_deployed.side_effect = IllegalTradingSessionTransition("boom")
    runner._logger = MagicMock()
    runner._mark_orphan_deployed()          # must swallow -- the caller documents never-raises
    assert runner._logger.critical.called


# --------------------------------------------------- C: the loop reads it
def _orphan_break_ifs(src: str):
    """Every `if self._orphan_position_live: ... break` in `src`.

    The If test must be EXACTLY that attribute. A first version of this
    helper matched on whether the unparsed test CONTAINED the name, and a
    negative control proved it vacuous: `if False and self._orphan_position_live:`
    still contains it, so a fully disabled guard passed. An inverted guard
    (`if not self._orphan_position_live:`) passed too. Both are now rejected
    structurally rather than textually.
    """
    out = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.If):
            continue
        t = node.test
        if (isinstance(t, ast.Attribute) and t.attr == "_orphan_position_live"
                and isinstance(t.value, ast.Name) and t.value.id == "self"
                and any(isinstance(b, ast.Break) for b in ast.walk(node))):
            out.append(node)
    return out


def test_continuous_entry_loop_breaks_on_a_live_orphan():
    """`_orphan_position_live` was written three times and read nowhere. The
    entry loop runs to observe_until (15:30), so without this the orphan waited
    while `_position_management`'s own monitor_until (15:15) went past."""
    assert _orphan_break_ifs(_method("_continuous_session")), \
        "no `if self._orphan_position_live: ... break` with that exact condition"


def test_orphan_break_is_inside_the_entry_loop_not_after_it():
    """A break placed after the loop would be dead code that still matches a
    search. It has to sit in the same loop as the _attempt_entry call."""
    for node in ast.walk(ast.parse(_method("_continuous_session"))):
        if isinstance(node, ast.While):
            body = ast.unparse(node)
            if "_attempt_entry" in body and _orphan_break_ifs(body):
                return
    raise AssertionError("the orphan break is not in the loop that calls _attempt_entry")
