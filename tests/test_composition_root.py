"""Tests — Engineering Series 54: composition root."""
import ast
from pathlib import Path

import pytest

from bujji.production_runtime.composition_root import CompositionError, CompositionRoot, build_composition_root
from bujji.production_runtime.config import (
    InvalidRuntimeConfig,
    RuntimeConfig,
    RUNTIME_MODE_PRODUCTION_READY,
    RUNTIME_MODE_READ_ONLY,
    RUNTIME_MODE_SHADOW,
)
from bujji.broker.errors import LiveExecutionDisabledError
from bujji.broker.paper import PaperBroker
from bujji.execution.engine import ExecutionEngine
from bujji.integration.authentication_adapter import ProductionAuthenticationAdapter
from bujji.integration.execution_adapter import ProductionExecutionAdapter


def test_builds_full_object_graph_with_paper_broker():
    root = build_composition_root(RuntimeConfig(broker_name="paper"))
    assert isinstance(root, CompositionRoot)
    assert isinstance(root.broker, PaperBroker)
    assert isinstance(root.execution_engine, ExecutionEngine)
    assert isinstance(root.authentication_adapter, ProductionAuthenticationAdapter)
    assert isinstance(root.execution_adapter, ProductionExecutionAdapter)


def test_builds_full_object_graph_with_real_fyers_broker_construction_only():
    root = build_composition_root(
        RuntimeConfig(mode=RUNTIME_MODE_PRODUCTION_READY, broker_name="fyers")
    )
    from bujji.broker.fyers import FyersBroker

    assert isinstance(root.broker, FyersBroker)
    # Construction only -- never connected.
    assert not hasattr(root.broker, "_connected") or root.broker.__dict__.get("_connected") in (
        None,
        False,
    )


def test_unrecognized_broker_name_rejected_at_config_layer():
    from bujji.production_runtime.config import InvalidRuntimeConfig

    with pytest.raises(InvalidRuntimeConfig):
        RuntimeConfig(broker_name="not_a_broker")


def test_every_field_is_explicitly_constructed_no_globals():
    root = build_composition_root(RuntimeConfig())
    for field_name in CompositionRoot.__dataclass_fields__:
        assert getattr(root, field_name) is not None or field_name == "config"


def test_graceful_failure_when_dependency_construction_fails(monkeypatch):
    import bujji.production_runtime.composition_root as cr_module

    def _boom(config, logger):
        raise RuntimeError("simulated broker construction failure")

    monkeypatch.setattr(cr_module, "_build_broker", _boom)
    with pytest.raises(CompositionError):
        build_composition_root(RuntimeConfig())


def test_composition_root_is_frozen():
    root = build_composition_root(RuntimeConfig())
    with pytest.raises(Exception):
        root.broker = None  # type: ignore[misc]


def test_no_trading_logic_reimplemented_in_composition_root():
    src = Path(cr_source_path()).read_text()
    tree = ast.parse(src)
    body_without_docstring = [
        n for n in tree.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))
    ]
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    forbidden = {"select", "assess", "authorize", "size_position", "construct_orders", "orchestrate"}
    called_names = set()
    for c in calls:
        if isinstance(c.func, ast.Attribute):
            called_names.add(c.func.attr)
        elif isinstance(c.func, ast.Name):
            called_names.add(c.func.id)
    assert not (called_names & forbidden)


def cr_source_path():
    import bujji.production_runtime.composition_root as m

    return m.__file__


# ---------------------------------------------------------------------- #
# P1-1 fix (TODO.md): a live-capable broker must never be constructible
# outside Mode 3 (PRODUCTION_READY). Before this fix, the exact line below
# constructed successfully and produced a real, unguarded FyersBroker with
# a live place_order method -- confirmed by direct execution, not a
# hypothetical. These tests reproduce that original repro exactly, then
# prove both fix layers independently.
# ---------------------------------------------------------------------- #

def test_fyers_broker_rejected_in_shadow_mode_at_config_layer():
    """The original P1-1 repro from TODO.md: this must now raise."""
    with pytest.raises(InvalidRuntimeConfig):
        RuntimeConfig(mode=RUNTIME_MODE_SHADOW, broker_name="fyers")


def test_fyers_broker_rejected_in_read_only_mode_at_config_layer():
    with pytest.raises(InvalidRuntimeConfig):
        RuntimeConfig(mode=RUNTIME_MODE_READ_ONLY, broker_name="fyers")


def test_fyers_broker_still_permitted_in_production_ready_mode():
    """The fix must not regress the one case this broker is meant for."""
    config = RuntimeConfig(mode=RUNTIME_MODE_PRODUCTION_READY, broker_name="fyers")
    assert config.broker_name == "fyers"


def test_paper_broker_permitted_in_every_mode():
    """The fix is scoped to `broker_name="fyers"` only -- paper must be
    unaffected in all three modes."""
    for mode in (RUNTIME_MODE_READ_ONLY, RUNTIME_MODE_SHADOW, RUNTIME_MODE_PRODUCTION_READY):
        config = RuntimeConfig(mode=mode, broker_name="paper")
        assert config.broker_name == "paper"


def test_defense_in_depth_layer_disables_live_execution_if_config_layer_bypassed():
    """Layer 2 (`composition_root._build_broker`): even if a future
    RuntimeConfig subclass or a manually-bypassed dataclass skips
    `__post_init__` and reaches `_build_broker` with `broker_name="fyers"`
    outside Mode 3, the returned broker's execution methods must still be
    neutered -- not by relying on the config check alone.

    Constructed via `object.__new__` + `object.__setattr__` specifically to
    bypass `__post_init__` and isolate this layer, simulating exactly the
    bypass scenario this layer exists to defend against.
    """
    from bujji.production_runtime.composition_root import _build_broker

    bypassed_config = object.__new__(RuntimeConfig)
    for field_name, field in RuntimeConfig.__dataclass_fields__.items():
        object.__setattr__(bypassed_config, field_name, field.default)
    object.__setattr__(bypassed_config, "mode", RUNTIME_MODE_SHADOW)
    object.__setattr__(bypassed_config, "broker_name", "fyers")

    broker = _build_broker(bypassed_config, logger=__import__("logging").getLogger("test"))

    import asyncio

    with pytest.raises(LiveExecutionDisabledError):
        asyncio.run(broker.place_order(None))


def test_defense_in_depth_layer_does_not_neuter_broker_in_production_ready_mode():
    """The Layer-2 guard must only ever apply outside Mode 3 -- a real
    Production Ready FyersBroker must keep its real execution methods."""
    from bujji.production_runtime.composition_root import _build_broker
    from bujji.broker.fyers import FyersBroker

    config = RuntimeConfig(mode=RUNTIME_MODE_PRODUCTION_READY, broker_name="fyers")
    broker = _build_broker(config, logger=__import__("logging").getLogger("test"))
    assert isinstance(broker, FyersBroker)
    assert broker.place_order.__name__ != "place_order_disabled"
