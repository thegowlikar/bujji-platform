"""Replay broker — a deterministic :class:`Broker` for historical replay.

It implements the identical broker contract used live, so replayed candles flow
through the *exact same* Execution Engine and Orchestrator. Fills are immediate;
option premium is priced with a simple, deterministic intrinsic + decaying
time-value model that reacts to spot, which is sufficient to exercise entries,
holds, exits, and journaling without a live feed.
"""
from __future__ import annotations

from datetime import datetime

from ..core.enums import OptionType, OrderStatus, Side
from ..core.models import Candle, OptionContract, OrderRequest, OrderResult
from ..broker.base import Broker
from ..broker.errors import AuthenticationError


class ReplayBroker(Broker):
    name = "replay"

    def __init__(self, base_time_value: float = 80.0,
                 decay_per_candle: float = 1.5) -> None:
        self._spot = 0.0
        self._time_value = base_time_value
        self._decay = decay_per_candle
        self._candles_seen = 0
        self._orders: dict[str, OrderResult] = {}
        self._positions: dict[str, dict] = {}
        self._auth_expired = False  # E1/E2 fault-injection hook (test-only).

    # -- Replay control (driven by the ReplayEngine) -------------------- #
    def set_market(self, spot: float) -> None:
        self._spot = spot
        self._candles_seen += 1

    def simulate_auth_expiry(self, expired: bool = True) -> None:
        """Make every subsequent broker call raise AuthenticationError.

        Lets chaos/replay tests exercise a token expiring mid-replay through
        the exact same Execution Engine / Orchestrator path used live.
        """
        self._auth_expired = expired

    def _check_auth(self) -> None:
        if self._auth_expired:
            raise AuthenticationError(
                "simulated: access token expired mid-replay"
            )

    # -- Broker contract ------------------------------------------------ #
    async def connect(self) -> None:
        self._check_auth()
        return None

    async def get_spot(self, underlying: str) -> float:
        self._check_auth()
        return self._spot

    async def get_recent_candles(self, underlying, minutes, count) -> list[Candle]:
        self._check_auth()
        return []  # Replay pushes candles directly into the orchestrator.

    async def resolve_atm_contract(
        self, underlying, spot, direction, strike_interval, lot_size
    ) -> OptionContract:
        self._check_auth()
        strike = self.atm_strike(spot, strike_interval)
        opt = self.option_type_for(direction)
        symbol = f"{underlying}{strike}{opt.value}"
        return OptionContract(symbol, underlying, strike, opt, "WEEKLY", lot_size)

    def _price(self, contract: OptionContract) -> float:
        if contract.option_type is OptionType.PE:
            intrinsic = max(0.0, contract.strike - self._spot)
        else:
            intrinsic = max(0.0, self._spot - contract.strike)
        tv = max(0.0, self._time_value - self._decay * self._candles_seen)
        return round(intrinsic + tv, 2)

    async def get_ltp(self, contract: OptionContract) -> float:
        self._check_auth()
        return self._price(contract)

    async def place_order(self, request: OrderRequest) -> OrderResult:
        self._check_auth()
        if request.client_order_id in self._orders:
            return self._orders[request.client_order_id]
        price = request.limit_price or self._price(request.contract)
        result = OrderResult(
            client_order_id=request.client_order_id,
            status=OrderStatus.FILLED,
            broker_order_id=f"REPLAY-{len(self._orders) + 1}",
            filled_quantity=request.quantity,
            average_price=price,
            message="replay_fill",
        )
        self._orders[request.client_order_id] = result
        self._positions[request.contract.symbol] = {
            "symbol": request.contract.symbol, "qty": request.quantity,
            "side": request.side.value, "avg_price": price,
        }
        return result

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
