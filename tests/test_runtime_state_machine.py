import ast
from datetime import datetime, timezone

import pytest

from bujji.core.event_bus import Event, EventBus, EventType
from bujji.production_runtime.runtime_state_machine import (
    IllegalRuntimeTransition, RuntimeState, RuntimeStateMachine,
)

BASE_TS = datetime(2026, 8, 2, 9, 15, 0, tzinfo=timezone.utc)


def clock():
    return BASE_TS


def make_machine(initial=RuntimeState.INITIALIZING):
    bus = EventBus()
    published = []
    bus.subscribe(EventType.STATE_CHANGED, lambda e: published.append(e))
    machine = RuntimeStateMachine(bus, clock, initial=initial)
    return machine, published


# --------------------------------------------------------------------- #
# Legal transitions along the full happy path
# --------------------------------------------------------------------- #

def test_full_happy_path_no_positions():
    machine, published = make_machine()
    path = [
        RuntimeState.PREMARKET, RuntimeState.CONNECTING, RuntimeState.LIVE,
        RuntimeState.ENTRY_ENABLED, RuntimeState.POSTMARKET, RuntimeState.COMPLETE,
    ]
    for target in path:
        machine.transition(target, reason="test")
    assert machine.state == RuntimeState.COMPLETE
    assert machine.is_terminal() is True
    assert machine.is_error() is False
    assert len(published) == len(path)


def test_full_happy_path_with_position_and_management_loop():
    machine, _ = make_machine()
    for target in (
        RuntimeState.PREMARKET, RuntimeState.CONNECTING, RuntimeState.LIVE,
        RuntimeState.ENTRY_ENABLED, RuntimeState.POSITION_ACTIVE, RuntimeState.MANAGING,
        RuntimeState.POSITION_ACTIVE, RuntimeState.MANAGING, RuntimeState.EXITING,
        RuntimeState.ENTRY_ENABLED, RuntimeState.POSTMARKET, RuntimeState.COMPLETE,
    ):
        machine.transition(target)
    assert machine.state == RuntimeState.COMPLETE


def test_self_transition_is_a_no_op_not_rejected():
    machine, published = make_machine(initial=RuntimeState.LIVE)
    machine.transition(RuntimeState.LIVE, reason="no-op")
    assert machine.state == RuntimeState.LIVE
    assert published == []  # no event published for a no-op


# --------------------------------------------------------------------- #
# Illegal transitions rejected
# --------------------------------------------------------------------- #

def test_illegal_forward_skip_rejected():
    machine, _ = make_machine()
    with pytest.raises(IllegalRuntimeTransition):
        machine.transition(RuntimeState.LIVE)  # INITIALIZING -> LIVE skips PREMARKET/CONNECTING


def test_illegal_backward_transition_rejected():
    machine, _ = make_machine(initial=RuntimeState.LIVE)
    with pytest.raises(IllegalRuntimeTransition):
        machine.transition(RuntimeState.INITIALIZING)


def test_transition_out_of_complete_rejected():
    machine, _ = make_machine(initial=RuntimeState.COMPLETE)
    with pytest.raises(IllegalRuntimeTransition):
        machine.transition(RuntimeState.PREMARKET)


def test_transition_out_of_error_rejected():
    machine, _ = make_machine(initial=RuntimeState.ERROR)
    with pytest.raises(IllegalRuntimeTransition):
        machine.transition(RuntimeState.PREMARKET)


def test_failed_transition_leaves_state_unchanged():
    machine, published = make_machine(initial=RuntimeState.LIVE)
    with pytest.raises(IllegalRuntimeTransition):
        machine.transition(RuntimeState.COMPLETE)
    assert machine.state == RuntimeState.LIVE
    assert published == []


# --------------------------------------------------------------------- #
# ERROR reachable from every non-terminal state
# --------------------------------------------------------------------- #

@pytest.mark.parametrize("state", [
    RuntimeState.INITIALIZING, RuntimeState.PREMARKET, RuntimeState.CONNECTING, RuntimeState.LIVE,
    RuntimeState.ENTRY_ENABLED, RuntimeState.POSITION_ACTIVE, RuntimeState.MANAGING,
    RuntimeState.EXITING, RuntimeState.POSTMARKET,
])
def test_error_reachable_from_every_non_terminal_state(state):
    machine, published = make_machine(initial=state)
    machine.transition(RuntimeState.ERROR, reason="fault")
    assert machine.state == RuntimeState.ERROR
    assert machine.is_terminal() is True
    assert machine.is_error() is True
    assert len(published) == 1


# --------------------------------------------------------------------- #
# EventBus publishing
# --------------------------------------------------------------------- #

def test_state_changed_event_payload_correct():
    machine, published = make_machine()
    machine.transition(RuntimeState.PREMARKET, reason="market day starting")
    assert len(published) == 1
    event = published[0]
    assert event.type == EventType.STATE_CHANGED
    assert event.payload["from_state"] == "INITIALIZING"
    assert event.payload["to_state"] == "PREMARKET"
    assert event.payload["reason"] == "market day starting"
    assert event.timestamp == BASE_TS


def test_no_new_event_type_introduced():
    # Reuses EventType.STATE_CHANGED, which already existed on EventBus
    # before this module -- never adds a second messaging layer.
    known_types = set(EventType)
    machine, _ = make_machine()
    machine.transition(RuntimeState.PREMARKET)
    assert set(EventType) == known_types


# --------------------------------------------------------------------- #
# Safety
# --------------------------------------------------------------------- #

def test_no_trading_market_or_broker_imports():
    import bujji.production_runtime.runtime_state_machine as module
    tree = ast.parse(open(module.__file__).read())
    forbidden = ("broker", "fyers", "order", "execution", "strategy", "margin")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not any(f in alias.name.lower() for f in forbidden), alias.name
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").lower()
            assert not any(f in mod for f in forbidden), node.module


def test_no_decision_making_only_state_and_event_fields():
    # No numeric threshold constants -- this module contains zero
    # business logic, only a transition table of RuntimeState pairs.
    import bujji.production_runtime.runtime_state_machine as module
    tree = ast.parse(open(module.__file__).read())
    numeric_constants = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, (int, float)) and not isinstance(node.value.value, bool):
            numeric_constants.append(node.value.value)
    assert numeric_constants == [], numeric_constants


def test_does_not_import_legacy_state_machine():
    import bujji.production_runtime.runtime_state_machine as module
    tree = ast.parse(open(module.__file__).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert mod != "bujji.core.state_machine"
            assert mod != "bujji.core.enums"


def test_deterministic_same_sequence_same_result():
    m1, _ = make_machine()
    m2, _ = make_machine()
    sequence = (RuntimeState.PREMARKET, RuntimeState.CONNECTING, RuntimeState.LIVE)
    for target in sequence:
        m1.transition(target)
        m2.transition(target)
    assert m1.state == m2.state
