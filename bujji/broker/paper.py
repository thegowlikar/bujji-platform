"""In-memory paper-trading broker.

Simulates fills at the last traded premium, nets positions (a BUY closes a
SELL), and serves as the reference implementation of the :class:`Broker`
contract. It also exposes small, explicit fault-injection hooks so the
capital-protection paths (idempotency, partial fills, recovery) can be tested
deterministically. All hooks default to benign behaviour.
"""
from __future__ import annotations

import random
from datetime import timedelta
from typing import Optional

from ..core.clock import now_ist
from ..core.enums import Direction, OptionType, OrderStatus, Side
from ..core.models import Candle, OptionContract, OrderRequest, OrderResult
from .base import Broker
from .errors import AuthenticationError


class PaperBroker(Broker):
    name = "paper"

    def __init__(
        self,
        seed: int = 42,
        base_spot: float = 22000.0,
        *,
        partial_fill_qty: Optional[int] = None,
        raise_on_place_after_record: bool = False,
    ) -> None:
        self._rng = random.Random(seed)
        self._spot = base_spot
        self._orders: dict[str, OrderResult] = {}
        self._positions: dict[str, dict] = {}
        self._premium: dict[str, float] = {}
        # Fault-injection hooks (test-only; benign by default).
        self._partial_fill_qty = partial_fill_qty
        self._raise_on_place_after_record = raise_on_place_after_record
        self._place_calls = 0
        self._auth_expired = False  # E1/E2 simulation.
        self._auth_error_calls = 0

    # -- Test/inspection helpers --------------------------------------- #
    @property
    def place_calls(self) -> int:
        """Number of times place_order actually reached the 'exchange'."""
        return self._place_calls

    @property
    def auth_error_calls(self) -> int:
        """Number of calls that raised the simulated auth failure."""
        return self._auth_error_calls

    def seed_position(self, symbol: str, side: str, qty: int,
                      avg_price: float = 120.0) -> None:
        """Inject a pre-existing broker position (simulates a live holding)."""
        self._positions[symbol] = {
            "symbol": symbol, "side": side, "qty": qty, "avg_price": avg_price,
        }
        self._premium.setdefault(symbol, avg_price)

    def simulate_auth_expiry(self, expired: bool = True) -> None:
        """Make every subsequent broker call raise AuthenticationError (E1/E2).

        Simulates a token expiring (or a session being invalidated) mid-run.
        Call with ``expired=False`` to simulate a human refreshing the token.
        """
        self._auth_expired = expired

    def _check_auth(self) -> None:
        if self._auth_expired:
            self._auth_error_calls += 1
            raise AuthenticationError(
                "simulated: access token expired / session invalidated"
            )

    # -- Broker contract ------------------------------------------------ #
    async def connect(self) -> None:
        self._check_auth()
        return None

    async def get_spot(self, underlying: str) -> float:
        self._check_auth()
        self._spot += self._rng.uniform(-5, 5)
        return round(self._spot, 2)

    async def get_recent_candles(
        self, underlying: str, minutes: int, count: int
    ) -> list[Candle]:
        self._check_auth()
        candles: list[Candle] = []
        now = now_ist().replace(second=0, microsecond=0)
        price = self._spot
        for i in range(count, 0, -1):
            o = price
            price += self._rng.uniform(-10, 10)
            h = max(o, price) + self._rng.uniform(0, 5)
            low = min(o, price) - self._rng.uniform(0, 5)
            candles.append(
                Candle(now - timedelta(minutes=minutes * i), o, h, low, price, 1000)
            )
        return candles

    async def resolve_atm_contract(
        self, underlying, spot, direction, strike_interval, lot_size
    ) -> OptionContract:
        self._check_auth()
        strike = self.atm_strike(spot, strike_interval)
        opt = self.option_type_for(direction)
        symbol = f"{underlying}{strike}{opt.value}"
        self._premium.setdefault(symbol, 120.0)
        return OptionContract(symbol, underlying, strike, opt, "WEEKLY", lot_size)

    async def get_ltp(self, contract: OptionContract) -> float:
        self._check_auth()
        cur = self._premium.get(contract.symbol, 120.0)
        cur = max(0.5, cur + self._rng.uniform(-3, 3))
        self._premium[contract.symbol] = round(cur, 2)
        return self._premium[contract.symbol]

    async def place_order(self, request: OrderRequest) -> OrderResult:
        self._check_auth()
        # Idempotent: a known client id returns the same result, never re-nets.
        if request.client_order_id in self._orders:
            return self._orders[request.client_order_id]

        filled = request.quantity
        status = OrderStatus.FILLED
        if self._partial_fill_qty is not None:
            filled = min(request.quantity, self._partial_fill_qty)
            status = (OrderStatus.FILLED if filled >= request.quantity
                      else OrderStatus.PARTIAL)

        price = request.limit_price or self._premium.get(
            request.contract.symbol, 120.0
        )
        result = OrderResult(
            client_order_id=request.client_order_id,
            status=status,
            broker_order_id=f"PAPER-{len(self._orders) + 1}",
            filled_quantity=filled,
            average_price=price,
            message="paper_fill" if status is OrderStatus.FILLED else "paper_partial",
        )
        # Record the order BEFORE any simulated fault, so a subsequent lookup
        # (get_order) can find it — this is exactly what proves idempotency.
        self._orders[request.client_order_id] = result
        self._apply_fill(request, filled, price)
        self._place_calls += 1

        if self._raise_on_place_after_record:
            # Simulate: exchange accepted the order, but our response was lost.
            self._raise_on_place_after_record = False
            raise RuntimeError("simulated_network_error_after_accept")
        return result

    def _apply_fill(self, request: OrderRequest, filled: int, price: float) -> None:
        """Net the filled quantity into the position book (BUY closes SELL)."""
        symbol = request.contract.symbol
        prev = self._positions.get(symbol)
        prev_net = 0
        if prev:
            prev_net = prev["qty"] if prev["side"] == Side.BUY.value else -prev["qty"]
        delta = filled if request.side is Side.BUY else -filled
        net = prev_net + delta
        if net == 0:
            self._positions.pop(symbol, None)
        else:
            self._positions[symbol] = {
                "symbol": symbol,
                "side": Side.BUY.value if net > 0 else Side.SELL.value,
                "qty": abs(net),
                "avg_price": price,
            }

    async def get_order(self, client_order_id: str) -> OrderResult:
        self._check_auth()
        return self._orders.get(
            client_order_id,
            OrderResult(client_order_id, OrderStatus.UNKNOWN, message="not_found"),
        )

    async def cancel_order(self, client_order_id: str) -> OrderResult:
        self._check_auth()
        return OrderResult(client_order_id, OrderStatus.CANCELLED)

    async def get_open_positions(self) -> list[dict]:
        self._check_auth()
        return list(self._positions.values())
