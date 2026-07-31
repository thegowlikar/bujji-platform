"""Exit Engine order builder — Exit Engine v1 sprint, Part 4.

Deliberately NOT part of ExitEngine itself (Part 1's own instruction:
ExitEngine reads valuation/positions, evaluates rules, produces an
ExitDecision, "Nothing else" -- order construction is a separate
responsibility, called by whatever wiring layer receives a
should_exit=True decision).

Builds one closing `bujji.core.models.OrderRequest` per open position:
exact reverse side, exact open quantity, `reference_price` = that
symbol's own current observed price from the SAME PortfolioValuation
the ExitDecision was computed from (never re-fetched, never a
different price than what triggered the exit). `limit_price` is always
None -- exits go through the same MARKET-only path as entries (Semantic
Cleanup Sprint's own established convention).

Honest disclosure: PaperBroker's own position ledger (Part 2 of the
prior sprint) stores only symbol/side/qty/avg_price/entry_timestamp --
not the full option contract (strike/expiry/lot_size). Confirmed by
reading bujji/broker/paper.py directly: PaperBroker.place_order() only
ever reads `request.contract.symbol` -- no other contract field is
read anywhere in its fill/ledger logic. The `OptionContract` built
here therefore carries the real, correct `symbol` (the only field that
matters) plus clearly-placeholder values for strike/expiry/lot_size,
which PaperBroker never reads. `option_type` is derived from the
symbol's own real "CE"/"PE" suffix (this codebase's own established
naming convention) since that much IS reliably recoverable; strike and
expiry are not safely parseable from the symbol alone and are left as
documented placeholders rather than guessed.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Callable, List, Optional

from ..portfolio_valuation.models import PortfolioValuation
from ...core.enums import OptionType, Side
from ...core.models import OptionContract, OrderRequest

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now()


def _infer_option_type(symbol: str) -> OptionType:
    if symbol.endswith("PE"):
        return OptionType.PE
    return OptionType.CE  # Default/fallback -- PaperBroker never reads this field.


def build_closing_orders(
    positions: List[dict],
    valuation: PortfolioValuation,
    clock: Clock = _real_clock,
) -> List[OrderRequest]:
    """One closing OrderRequest per open position. Empty positions list
    -> empty result, never fabricated. A position whose symbol has no
    current price in `valuation`'s legs falls back to `None` for
    reference_price (PaperBroker's own documented fallback then
    applies -- never guessed here)."""
    timestamp = clock().isoformat()
    price_by_symbol = {leg.symbol: leg.current_price for leg in valuation.legs}

    orders: List[OrderRequest] = []
    for pos in positions:
        symbol = pos["symbol"]
        closing_side = Side.SELL if pos["side"] == Side.BUY.value else Side.BUY
        contract = OptionContract(
            symbol=symbol, underlying="NIFTY", strike=0,
            option_type=_infer_option_type(symbol), expiry="", lot_size=pos["qty"],
        )
        seed = "|".join(["EXIT", symbol, str(pos["qty"]), timestamp])
        client_order_id = "EXIT-COID-" + hashlib.md5(seed.encode()).hexdigest()[:16]
        orders.append(
            OrderRequest(
                contract=contract, side=closing_side, quantity=pos["qty"],
                client_order_id=client_order_id, limit_price=None,
                reference_price=price_by_symbol.get(symbol),
                tag=f"exit;valuation={valuation.valuation_id}",
            )
        )
    return orders
