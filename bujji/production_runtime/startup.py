"""Startup sequence — BUJJI Options OS v3, Engineering Series 54.

Deterministic sequence: load config -> construct object graph ->
initialize runtime -> initialize broker objects -> initialize
execution engine -> verify authentication path -> verify execution
path -> Runtime READY. No market activity occurs during startup.

Health verification is a one-shot `StartupReport` produced here, not a
new health subsystem (explicitly out of scope for this sprint).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, List, Optional

from ..runtime_execution.engine import ExecutionEngineInterface
from ..authentication.engine import AuthenticationProviderInterface
from .composition_root import CompositionError, CompositionRoot, build_composition_root
from .config import RuntimeConfig

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now()


@dataclass(frozen=True)
class StartupReport:
    ready: bool
    config_valid: bool
    dependencies_constructed: bool
    authentication_path_reachable: bool
    execution_path_reachable: bool
    runtime_initialized: bool
    failure_reason: Optional[str]
    steps: List[str] = field(default_factory=list)
    timestamp: str = ""


def startup(
    config_values: dict,
    logger: Optional[logging.Logger] = None,
    clock: Clock = _real_clock,
):
    """Run the deterministic startup sequence.

    Returns a tuple `(root, report)`. `root` is `None` if construction
    failed -- the report always explains why, never a raw traceback.
    """
    log = logger or logging.getLogger("bujji.app")
    steps: List[str] = []
    timestamp = clock().isoformat()

    steps.append("load_config: start")
    try:
        config = RuntimeConfig(**config_values)
        steps.append(f"load_config: ok (mode={config.mode}, broker={config.broker_name})")
    except Exception as exc:  # noqa: BLE001
        steps.append(f"load_config: FAILED ({exc!r})")
        return None, StartupReport(
            ready=False,
            config_valid=False,
            dependencies_constructed=False,
            authentication_path_reachable=False,
            execution_path_reachable=False,
            runtime_initialized=False,
            failure_reason=f"Invalid configuration: {exc!r}",
            steps=steps,
            timestamp=timestamp,
        )

    steps.append("construct_object_graph: start")
    try:
        root: CompositionRoot = build_composition_root(config, log)
        steps.append("construct_object_graph: ok")
    except CompositionError as exc:
        steps.append(f"construct_object_graph: FAILED ({exc!r})")
        return None, StartupReport(
            ready=False,
            config_valid=True,
            dependencies_constructed=False,
            authentication_path_reachable=False,
            execution_path_reachable=False,
            runtime_initialized=False,
            failure_reason=str(exc),
            steps=steps,
            timestamp=timestamp,
        )

    steps.append("initialize_broker_objects: ok (construction only, no connection)")
    steps.append("initialize_execution_engine: ok (construction only)")

    auth_reachable = isinstance(root.authentication_adapter, AuthenticationProviderInterface)
    steps.append(f"verify_authentication_path: {'ok' if auth_reachable else 'FAILED'} (structural isinstance check, no authenticate() call)")

    exec_reachable = isinstance(root.execution_adapter, ExecutionEngineInterface)
    steps.append(f"verify_execution_path: {'ok' if exec_reachable else 'FAILED'} (structural isinstance check, no submit_and_confirm() call)")

    runtime_initialized = auth_reachable and exec_reachable
    steps.append(f"runtime: {'READY' if runtime_initialized else 'NOT READY'} (no market activity has occurred)")

    ready = runtime_initialized
    failure_reason = None
    if not auth_reachable:
        failure_reason = "authentication_adapter does not satisfy AuthenticationProviderInterface"
    elif not exec_reachable:
        failure_reason = "execution_adapter does not satisfy ExecutionEngineInterface"

    return root, StartupReport(
        ready=ready,
        config_valid=True,
        dependencies_constructed=True,
        authentication_path_reachable=auth_reachable,
        execution_path_reachable=exec_reachable,
        runtime_initialized=runtime_initialized,
        failure_reason=failure_reason,
        steps=steps,
        timestamp=timestamp,
    )
