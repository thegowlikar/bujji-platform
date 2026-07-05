"""Neuters a broker instance's real order-execution methods (Paper mode safety).

Used exclusively by :mod:`bujji.broker.hybrid` to make it structurally
impossible for the live-data leg of the composite Paper broker to ever place,
modify, cancel, or discover a real order — not by convention or by "we
promise not to call it," but by replacing those specific bound methods, on
that specific instance only, with stubs that raise immediately, before any
network call is attempted, before the object is handed to anything else.

This is instance-level patching (``instance.method = stub``), not class-level
monkeypatching — it affects only the one broker object constructed for the
Paper-mode data feed, never the :class:`FyersBroker` class itself, so a
genuine live-mode broker instance (``broker.name: fyers``) is completely
unaffected.
"""
from __future__ import annotations

from typing import TypeVar

from .base import Broker
from .errors import LiveExecutionDisabledError

# The exact surface that must never be reachable when this broker is used
# purely as a live *data* source for Paper mode.
_EXECUTION_METHODS = ("place_order", "modify_order", "cancel_order",
                      "get_open_positions", "get_order")

B = TypeVar("B", bound=Broker)


def _make_disabled_stub(method_name: str, broker_name: str):
    async def _disabled(*_args, **_kwargs):
        raise LiveExecutionDisabledError(
            f"{broker_name}.{method_name}() is disabled: this broker instance "
            f"is the live *data* leg of Paper Trading mode and must never "
            f"place, modify, cancel, or discover real orders/positions."
        )
    _disabled.__name__ = f"{method_name}_disabled"
    return _disabled


def disable_live_execution(broker: B) -> B:
    """Replace a broker instance's order-execution methods with raising stubs.

    Call this once, immediately after constructing the live broker that will
    serve as Paper mode's market-data source, before it is handed to
    :class:`~bujji.broker.hybrid.HybridPaperBroker` or anything else. The
    object remains fully usable for its data methods (``get_spot``,
    ``get_recent_candles``, ``resolve_atm_contract``, ``get_ltp``, ``connect``)
    — only the execution surface is neutered.
    """
    for method_name in _EXECUTION_METHODS:
        if hasattr(broker, method_name):
            setattr(broker, method_name,
                    _make_disabled_stub(method_name, type(broker).__name__))
    return broker
