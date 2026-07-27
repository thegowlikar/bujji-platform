"""Replay broker — a deterministic :class:`Broker` for historical replay.

It implements the identical broker contract used live, so replayed candles flow
through the *exact same* Execution Engine and Orchestrator. Fills are immediate;
option premium is priced with a simple, deterministic intrinsic + decaying
time-value model that reacts to spot, which is sufficient to exercise entries,
holds, exits, and journaling without a live feed.

Capital simulation: `starting_capital`/`margin_per_lot` are constants unless a
`capital_schedule`/`margin_schedule` is supplied — a dict keyed by candle
index (0-based, incrementing once per `set_market()` call, i.e. once per
candle processed) mapping to the value that becomes active from that candle
onward. This makes "capital changed mid-day" / "margin requirement changed
mid-day" scenarios exactly reproducible: the SAME candle sequence + the SAME
schedule always produces the SAME sizing decision at the SAME point, with no
wall-clock or random dependency.
"""
from __future__ import annotations

from typing import Optional

from ..core.clock import now_ist
from ..core.enums import OptionType, OrderStatus, Side
from ..core.models import Candle, OptionContract, OrderRequest, OrderResult
from ..broker.base import Broker
from ..broker.errors import AuthenticationError


class ReplayBroker(Broker):
    name = "replay"

    def __init__(self, base_time_value: float = 80.0,
                 decay_per_candle: float = 1.5, *,
                 starting_capital: float = 1_00_00_000.0,
                 starting_margin: float = 1_00_00_000.0,
                 margin_per_lot: float = 1_00_000.0,
                 capital_schedule: Optional[dict[int, float]] = None,
                 margin_schedule: Optional[dict[int, float]] = None,
                 lot_size_schedule: Optional[dict[int, int]] = None,
                 option_volume: float = 5_000_000.0,
                 option_volume_schedule: Optional[dict[int, float]] = None) -> None:
        self._spot = 0.0
        self._time_value = base_time_value
        self._decay = decay_per_candle
        self._candles_seen = 0
        self._orders: dict[str, OrderResult] = {}
        self._positions: dict[str, dict] = {}
        self._auth_expired = False  # E1/E2 fault-injection hook (test-only).
        # Capital Management Engine deterministic simulation.
        self._starting_capital = starting_capital
        self._starting_margin = starting_margin
        self._margin_per_lot = margin_per_lot
        self._capital_schedule = capital_schedule or {}
        self._margin_schedule = margin_schedule or {}
        self._lot_size_schedule = lot_size_schedule or {}
        self._option_volume = option_volume
        self._option_volume_schedule = option_volume_schedule or {}

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

    def _scheduled(self, schedule: dict[int, float], default):
        """Most recent schedule entry at or before the current candle index —
        a deterministic function of candles_seen alone, never wall-clock."""
        applicable = [v for k, v in schedule.items() if k <= self._candles_seen]
        keys_applicable = [k for k in schedule if k <= self._candles_seen]
        if not keys_applicable:
            return default
        latest_key = max(keys_applicable)
        return schedule[latest_key]

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
        effective_lot_size = int(self._scheduled(self._lot_size_schedule, lot_size))
        return OptionContract(symbol, underlying, strike, opt, "WEEKLY", effective_lot_size)

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

    async def get_option_candles(self, contract: OptionContract, minutes: int,
                                 count: int) -> list[Candle]:
        """Synthetic single completed candle, schedule-driven volume for
        deterministic replay -- see docs/CAPITAL_MANAGEMENT_ENGINE.md's
        capital-schedule pattern for the same idea applied here. Timestamp
        is a placeholder (real time, not backtest time) -- ONLY the close
        price and volume are consumed by the caller; the timestamp itself
        is never used for VWAP/decision logic in this path.
        """
        self._check_auth()
        price = self._price(contract)
        volume = self._scheduled(self._option_volume_schedule, self._option_volume)
        return [Candle(now_ist(), price, price, price, price, volume)]

    async def get_funds(self) -> Optional[dict]:
        self._check_auth()
        equity = self._scheduled(self._capital_schedule, self._starting_capital)
        margin = self._scheduled(self._capital_schedule, self._starting_margin)
        return {
            "account_equity": equity,
            "available_funds": margin,
            "available_margin": margin,
            "cash_balance": equity,
            "collateral": 0.0,
            "used_margin": 0.0,
            "available_exposure": margin,
            "peak_margin": margin,
        }

    async def get_order_margin(self, ce_contract, pe_contract) -> Optional[dict]:
        self._check_auth()
        margin = self._scheduled(self._margin_schedule, self._margin_per_lot)
        return {"margin_per_lot": margin, "verified": True, "source": "replay_synthetic"}

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
