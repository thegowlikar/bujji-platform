"""Order Planning — Production Pipeline Entry 11, Layer 6.

Converts an approved TradeIntention into concrete, executable contracts and
an approved quantity. This is an EXTRACTION, not a rewrite: every line of
logic here is identical to what previously ran inline inside
Orchestrator._enter() -- same two resolve_atm_contract calls, same single
CapitalManagementEngine.approve_trade call, same CapitalRejectedError on
refusal. No threshold, no sizing rule, no resolution order has changed.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..capital.exceptions import CapitalRejectedError
from .enums import Direction
from .models import OptionContract


@dataclass(frozen=True)
class PlannedOrder:
    """Order Planning Layer's output -- concrete contracts plus an approved
    quantity, ready for the Broker Layer. Still carries no order IDs (those
    are assigned at submission, not planning time)."""

    ce_contract: OptionContract
    pe_contract: OptionContract
    quantity: int
    capital_decision: object  # CapitalManagementEngine's SizingDecision -- opaque here by design.


async def plan_straddle(exec_resolve, capital_engine, status, spot: float) -> PlannedOrder:
    """`exec_resolve(direction, spot)` and `capital_engine.approve_trade(ce, pe)`
    are passed in rather than imported, so this module has no dependency on
    the Orchestrator or a live Broker connection -- only on the same two
    calls _enter() already made, in the same order.
    """
    ce_contract = await exec_resolve(Direction.BEARISH, spot)
    pe_contract = await exec_resolve(Direction.BULLISH, spot)

    capital_decision = await capital_engine.approve_trade(ce_contract, pe_contract)
    from ..capital.health import publish_to_dashboard
    publish_to_dashboard(status, capital_decision)
    if not capital_decision.approved:
        raise CapitalRejectedError(capital_decision.reason)

    return PlannedOrder(
        ce_contract=ce_contract, pe_contract=pe_contract,
        quantity=capital_decision.quantity, capital_decision=capital_decision,
    )
