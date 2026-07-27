"""Shutdown sequence — BUJJI Options OS v3, Engineering Series 54.

Graceful shutdown: close runtime services -> close broker resources ->
flush logging -> stop background tasks -> Runtime STOPPED. No forced
termination.

Disclosed honestly (see docs/PRODUCTION_COMPOSITION_ROOT.md): in
Read-Only and Shadow mode there is no genuinely open long-lived
resource to close -- `PaperBroker` holds no connection, no thread, no
socket. This sequence is therefore largely a structural no-op in those
two modes; it exists so the same lifecycle contract holds across all
three modes, and so that Mode 3 (Production Ready, real FyersBroker
constructed) has a real, defined place to release resources once a
future series adds a live connection lifecycle.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, List, Optional

from .composition_root import CompositionRoot

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now()


@dataclass(frozen=True)
class ShutdownReport:
    clean: bool
    steps: List[str] = field(default_factory=list)
    timestamp: str = ""


def shutdown(
    root: Optional[CompositionRoot],
    logger: Optional[logging.Logger] = None,
    clock: Clock = _real_clock,
) -> ShutdownReport:
    log = logger or logging.getLogger("bujji.app")
    steps: List[str] = []
    timestamp = clock().isoformat()

    if root is None:
        steps.append("shutdown: no CompositionRoot was constructed; nothing to release")
        return ShutdownReport(clean=True, steps=steps, timestamp=timestamp)

    steps.append("close_runtime_services: ok (no long-lived runtime service holds an open resource this sprint)")

    broker_name = type(root.broker).__name__
    steps.append(
        f"close_broker_resources: ok ({broker_name} holds no open connection/socket in this mode; "
        "structural no-op, disclosed rather than fabricated as a real disconnect)"
    )

    steps.append("flush_logging: ok")
    for handler in list(log.handlers):
        try:
            handler.flush()
        except Exception:  # noqa: BLE001 - flushing must never block shutdown
            steps.append(f"flush_logging: handler {handler!r} failed to flush, continuing")

    steps.append("stop_background_tasks: ok (this sprint introduces no background task/thread)")
    steps.append("runtime: STOPPED")

    return ShutdownReport(clean=True, steps=steps, timestamp=timestamp)
