"""Lifecycle Order Builder -- BUJJI Options OS v3, Gate F.4 Part 3.

Pure, stateless validation + construction. Every generated order is
checked BEFORE construction -- symbol exists, quantity positive, side
correctly reduces/increases, quantity never exceeds current holding
for a reduction. No strategy/risk decision is made here: `reduce_
quantity` and `hedge_instruction` are always caller-supplied (D.4
never states a number, and this module never invents one -- the same
"never fabricate a figure no upstream module produced" discipline
established across every prior gate this session).
"""
from __future__ import annotations

from typing import Optional

from bujji.core.enums import Side
from bujji.core.models import OptionContract, OrderRequest


class IllegalLifecycleOrderError(Exception):
    """Raised on any structurally invalid lifecycle order request --
    never silently coerced, never partially constructed."""


def build_reduce_order(
    current_position: dict, contract: OptionContract, reduce_quantity: int, client_order_id: str,
    reference_price: Optional[float] = None,
) -> OrderRequest:
    """`current_position`: PaperBroker's own position dict for this
    symbol (symbol/side/qty/avg_price/entry_timestamp), read fresh by
    the caller -- never cached here. Fails closed on: no position
    (nothing to reduce), zero/negative reduce_quantity, or a
    reduce_quantity exceeding the current holding (a reduction can
    only ever shrink exposure, never flip or increase it).

    `reference_price`: the current observed market price for this
    symbol, caller-supplied (e.g. from F.3's own Portfolio Reality
    valuation), used as PaperBroker's simulated fill basis for the
    closing leg. Optional and defaults to the position's own entry
    `avg_price` ONLY to preserve every existing caller's exact prior
    behavior byte-for-byte (this module never silently changes
    behavior for a caller that hasn't opted in) -- but pinning a
    reduce/exit fill to the entry price on every real call means the
    position never realizes real market movement no matter how far
    price has actually moved, so every real caller managing a live
    position should supply the current market price explicitly."""
    if current_position is None:
        raise IllegalLifecycleOrderError("no current position exists for this symbol -- nothing to reduce")
    if contract is None:
        raise IllegalLifecycleOrderError("no contract is registered for this symbol -- cannot construct an order")
    if contract.symbol != current_position["symbol"]:
        raise IllegalLifecycleOrderError(
            f"contract symbol {contract.symbol!r} does not match current_position symbol "
            f"{current_position['symbol']!r}"
        )
    if reduce_quantity <= 0:
        raise IllegalLifecycleOrderError(f"reduce_quantity must be positive, got {reduce_quantity!r}")
    current_qty = current_position["qty"]
    if reduce_quantity > current_qty:
        raise IllegalLifecycleOrderError(
            f"reduce_quantity ({reduce_quantity}) exceeds current holding ({current_qty}) for "
            f"{contract.symbol!r} -- a reduction can never exceed the existing position"
        )

    current_side = current_position["side"]
    # A reduction always trades the OPPOSITE side of the current
    # holding: a SELL (short) position is reduced by a BUY; a BUY
    # (long) position is reduced by a SELL. This is the one place this
    # module "decides" anything, and it is a structural fact about
    # what "reduce" means, not a trading judgment.
    reducing_side = Side.BUY if current_side == Side.SELL.value else Side.SELL

    resolved_reference_price = reference_price if reference_price is not None else current_position["avg_price"]
    return OrderRequest(
        contract=contract, side=reducing_side, quantity=reduce_quantity, client_order_id=client_order_id,
        limit_price=None, reference_price=resolved_reference_price, tag="LIFECYCLE:REDUCE_SIZE",
    )


def build_hedge_order(hedge_instruction: dict, client_order_id: str) -> OrderRequest:
    """`hedge_instruction`: an already-decided, fully-specified
    caller-supplied instruction -- {"contract": OptionContract,
    "side": "BUY"|"SELL", "quantity": int, "reference_price":
    Optional[float]}. This function NEVER decides which instrument to
    hedge with, only validates and constructs the order for an
    instruction that already fully exists -- "F.4 does NOT decide
    hedge instrument," per this gate's own explicit scope."""
    if hedge_instruction is None:
        raise IllegalLifecycleOrderError("no hedge_instruction was supplied -- cannot construct a hedge order")
    contract = hedge_instruction.get("contract")
    side_str = hedge_instruction.get("side")
    quantity = hedge_instruction.get("quantity")
    if contract is None or not isinstance(contract, OptionContract):
        raise IllegalLifecycleOrderError("hedge_instruction['contract'] must be a real OptionContract")
    if side_str not in ("BUY", "SELL"):
        raise IllegalLifecycleOrderError(f"hedge_instruction['side'] must be 'BUY' or 'SELL', got {side_str!r}")
    if quantity is None or quantity <= 0:
        raise IllegalLifecycleOrderError(f"hedge_instruction['quantity'] must be positive, got {quantity!r}")

    side = Side.BUY if side_str == "BUY" else Side.SELL
    return OrderRequest(
        contract=contract, side=side, quantity=quantity, client_order_id=client_order_id,
        limit_price=None, reference_price=hedge_instruction.get("reference_price"), tag="LIFECYCLE:ADD_HEDGE",
    )
