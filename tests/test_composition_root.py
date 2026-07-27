"""Tests — Engineering Series 54: composition root."""
import ast
from pathlib import Path

import pytest

from bujji.production_runtime.composition_root import CompositionError, CompositionRoot, build_composition_root
from bujji.production_runtime.config import RuntimeConfig, RUNTIME_MODE_PRODUCTION_READY, RUNTIME_MODE_SHADOW
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
