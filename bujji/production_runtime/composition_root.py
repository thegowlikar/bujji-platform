"""Composition Root — BUJJI Options OS v3, Engineering Series 54.

Constructs the complete BUJJI Options OS v3 object graph in one
process. This module contains zero business logic, zero strategy
logic, and zero new runtime architecture -- it only instantiates
already-frozen modules (Series 31-53) and wires them together via
explicit constructor injection. No globals, no hidden singletons:
every dependency is constructed here and passed explicitly to whatever
needs it.

A discovered integration boundary, disclosed rather than concealed
(see docs/PRODUCTION_COMPOSITION_ROOT.md for the full write-up): MIC v2
lives in a completely separate Python environment
(`/opt/bujji-mic-v2/`, its own venv, its own repo) from this project's
own package (`/opt/bujji/app/`). It cannot be imported or instantiated
in this process. This composition root's pipeline therefore begins at
the Evidence Interpreter (Series 32), consuming already-published MIC
v2 classification strings -- exactly the same contract Series 32 has
always used -- rather than constructing MIC v2 objects directly.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from ..authentication.models import AuthenticationPolicy
from ..broker.paper import PaperBroker
from ..core.config import AppConfig, BrokerConfig
from ..execution.engine import ExecutionEngine
from ..integration.authentication_adapter import ProductionAuthenticationAdapter
from ..integration.execution_adapter import ProductionExecutionAdapter
from ..runtime_safety.models import RuntimeSafetyPolicy
from ..runtime_session.models import RuntimeSessionPolicy
from ..trading_brain.order_construction.models import ExecutionPolicy, TradingConfiguration
from ..trading_brain.position_sizing.config import PositionSizingConfig
from ..trading_brain.position_sizing.models import CapitalPolicy, LotSpecification
from .config import RuntimeConfig


class CompositionError(Exception):
    """Raised when the object graph cannot be constructed. Never
    swallowed silently -- startup.py surfaces this explicitly rather
    than letting a raw, uninformative exception propagate.
    """


@dataclass(frozen=True)
class CompositionRoot:
    """The complete, explicitly-wired object graph for one runtime
    process. Every field here was constructed by
    `build_composition_root()` -- nothing is a global, nothing is a
    lazily-initialized singleton.
    """

    config: RuntimeConfig
    logger: logging.Logger
    broker: Any
    execution_engine: ExecutionEngine
    authentication_adapter: ProductionAuthenticationAdapter
    execution_adapter: ProductionExecutionAdapter
    authentication_policy: AuthenticationPolicy
    runtime_session_policy: RuntimeSessionPolicy
    runtime_safety_policy: RuntimeSafetyPolicy
    capital_policy: CapitalPolicy
    lot_spec: LotSpecification
    sizing_config: PositionSizingConfig
    execution_policy: ExecutionPolicy
    trading_config: TradingConfiguration


def _build_broker(config: RuntimeConfig, logger: logging.Logger) -> Any:
    """Construct, never connect. `FyersBroker.__init__` (production,
    unmodified) performs no network I/O of its own -- it only builds an
    internal `FyersTokenManager` instance -- so constructing it here,
    even in Mode 3 (Production Ready), never touches a live broker.
    """
    if config.broker_name == "paper":
        return PaperBroker()
    if config.broker_name == "fyers":
        from ..broker.fyers import FyersBroker

        broker_config = BrokerConfig(name="fyers")
        return FyersBroker(broker_config, logger)
    raise CompositionError(f"Unrecognized broker_name: {config.broker_name!r}")


def build_composition_root(
    config: RuntimeConfig, logger: Optional[logging.Logger] = None
) -> CompositionRoot:
    """Construct the complete object graph for one runtime process.

    Never connects to a broker, never authenticates, never places an
    order -- construction only. Raises `CompositionError` (never a raw,
    uninformative exception) if any dependency cannot be built, so
    `startup.py` can report a clean, diagnosable failure.
    """
    log = logger or logging.getLogger("bujji.app")

    try:
        broker = _build_broker(config, log)
    except CompositionError:
        raise
    except Exception as exc:  # noqa: BLE001 - always wrapped, never swallowed
        raise CompositionError(f"Failed to construct broker: {exc!r}") from exc

    try:
        app_config = AppConfig(broker=BrokerConfig(name=config.broker_name))
        execution_engine = ExecutionEngine(broker, app_config, log)
    except Exception as exc:  # noqa: BLE001
        raise CompositionError(f"Failed to construct ExecutionEngine: {exc!r}") from exc

    try:
        authentication_adapter = ProductionAuthenticationAdapter(
            broker, broker_identity=config.broker_display_name
        )
        execution_adapter = ProductionExecutionAdapter(execution_engine, lot_size=config.lot_size)
    except Exception as exc:  # noqa: BLE001
        raise CompositionError(f"Failed to construct integration adapters: {exc!r}") from exc

    try:
        return CompositionRoot(
            config=config,
            logger=log,
            broker=broker,
            execution_engine=execution_engine,
            authentication_adapter=authentication_adapter,
            execution_adapter=execution_adapter,
            authentication_policy=AuthenticationPolicy(
                policy_version=config.policy_version, session_ttl_seconds=config.session_ttl_seconds
            ),
            runtime_session_policy=RuntimeSessionPolicy(
                policy_version=config.policy_version, allow_pause_resume=config.allow_pause_resume
            ),
            runtime_safety_policy=RuntimeSafetyPolicy(
                policy_version=config.policy_version,
                allow_authorized_with_warnings=config.allow_authorized_with_warnings,
            ),
            capital_policy=CapitalPolicy(policy=config.capital_policy_value),
            lot_spec=LotSpecification(
                underlying="NIFTY", lot_size=config.lot_size, effective_date="1970-01-01"
            ),
            sizing_config=PositionSizingConfig(),
            execution_policy=ExecutionPolicy(policy="MARKET"),
            trading_config=TradingConfiguration(
                product="MIS",
                validity="DAY",
                session_id="COMPOSITION-ROOT",
                pipeline_version=config.policy_version,
                qualification_fingerprint=config.qualification_fingerprint,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        raise CompositionError(f"Failed to construct policy objects: {exc!r}") from exc
